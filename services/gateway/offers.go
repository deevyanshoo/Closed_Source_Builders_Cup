package main

// The quote adapter is intentionally test-injected. No runtime configuration
// installs it until the tenant-scoped Python service and key lifecycle are reviewed.
import (
	"bytes"
	"crypto/hmac"
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"mime"
	"net/http"
	"net/url"
	"regexp"
	"strconv"
	"strings"
	"time"
)

const (
	quoteUploadLimit             = 12 << 20
	quoteDocumentLimit           = 8 << 20
	quoteMutationLimit           = 64 << 10
	quoteExtractionResponseLimit = 24 << 20
	quoteResponseLimit           = 8 << 20
	quoteQueryLimit              = 4096
	quoteHeaderLimit             = 4096
	quoteHeadersTotalLimit       = 16384
)

type quoteAdapter struct {
	upstream string // a literal loopback origin; populated only by tests in this increment
	key      []byte
	client   *http.Client
}

type quoteRoute struct {
	path, kind               string
	method                   string
	query                    map[string]bool
	bodyLimit, responseLimit int
	mutates, idempotent      bool
}

var quoteID = regexp.MustCompile(`^[A-Za-z0-9_-]{1,128}$`)

func quoteRouteCandidate(path string) bool {
	return path == "/v2/offers" || strings.HasPrefix(path, "/v2/offers/")
}

func quoteRouteFor(path string) (quoteRoute, bool) {
	base := quoteRoute{responseLimit: quoteResponseLimit}
	switch path {
	case "/v2/offers":
		base.path, base.kind, base.method, base.query = "/offers", "list", http.MethodGet, map[string]bool{"status": true, "as_of": true}
		return base, true
	case "/v2/offers/sourcing-candidates":
		base.path, base.kind, base.method, base.query = "/offers/sourcing-candidates", "sourcing", http.MethodGet, map[string]bool{"as_of": true}
		return base, true
	case "/v2/offers/extractions":
		base.path, base.kind, base.method, base.bodyLimit, base.mutates, base.responseLimit = "/offers/extractions", "upload", http.MethodPost, quoteUploadLimit, true, quoteExtractionResponseLimit
		return base, true
	}
	parts := strings.Split(path, "/")
	if len(parts) == 5 && parts[0] == "" && parts[1] == "v2" && parts[2] == "offers" && parts[3] == "extractions" && quoteID.MatchString(parts[4]) {
		base.path, base.kind, base.method, base.responseLimit = "/offers/extractions/"+parts[4], "extraction", http.MethodGet, quoteExtractionResponseLimit
		return base, true
	}
	if len(parts) >= 4 && parts[0] == "" && parts[1] == "v2" && parts[2] == "offers" && quoteID.MatchString(parts[3]) {
		base.path = "/offers/" + parts[3]
		if len(parts) == 4 {
			base.kind, base.method, base.query = "detail", http.MethodGet, map[string]bool{"include_events": true, "as_of": true}
			return base, true
		}
		if len(parts) == 5 && (parts[4] == "corrections" || parts[4] == "save") {
			base.path += "/" + parts[4]
			base.kind, base.method, base.bodyLimit, base.mutates, base.idempotent = parts[4], http.MethodPost, quoteMutationLimit, true, true
			return base, true
		}
	}
	return quoteRoute{}, false
}

func quoteError(w http.ResponseWriter, status int, code, message string) {
	w.Header().Set("Cache-Control", "no-store")
	stateError(w, status, code, message)
}

// count all differently-cased map keys, not merely Header.Values' canonical spelling.
func quoteHeader(r *http.Request, name string) (string, int) {
	count, value := 0, ""
	for key, values := range r.Header {
		if strings.EqualFold(key, name) {
			count += len(values)
			if len(values) > 0 {
				value = values[0]
			}
		}
	}
	return value, count
}

func quoteHeadersValid(r *http.Request) bool {
	total := 0
	for key, values := range r.Header {
		total += len(key)
		for _, value := range values {
			if len(value) > quoteHeaderLimit {
				return false
			}
			total += len(value)
			if total > quoteHeadersTotalLimit {
				return false
			}
		}
	}
	for _, name := range []string{"Authorization", "Origin", "Content-Type", "Idempotency-Key"} {
		_, n := quoteHeader(r, name)
		if n > 1 {
			return false
		}
	}
	for key, values := range r.Header {
		if len(values) == 0 {
			continue
		}
		k := strings.ToLower(key)
		if strings.HasPrefix(k, "x-quote-") || k == "x-actor" || k == "x-role" || k == "x-principal-id" || k == "x-workspace-id" || k == "x-principal" || k == "x-workspace" {
			return false
		}
	}
	return true
}

func quoteQuery(r *http.Request, route quoteRoute) (string, bool) {
	if r.URL.ForceQuery || len(r.URL.RawQuery) > quoteQueryLimit {
		return "", false
	}
	if r.URL.RawQuery == "" {
		return "", true
	}
	values, err := url.ParseQuery(r.URL.RawQuery)
	if err != nil {
		return "", false
	}
	for key, items := range values {
		if !route.query[key] || len(items) != 1 || items[0] == "" {
			return "", false
		}
		switch key {
		case "as_of":
			if _, err := time.Parse("2006-01-02", items[0]); err != nil {
				return "", false
			}
		case "include_events":
			if items[0] != "true" && items[0] != "false" {
				return "", false
			}
		case "status":
			if len(items[0]) > 40 || !quoteID.MatchString(items[0]) {
				return "", false
			}
		}
	}
	return values.Encode(), true
}

// A token walk rejects duplicate keys at every nesting level. It also bounds
// nesting and entry count before the normal JSON shape checks below.
func quoteJSON(body []byte, maxDepth, maxEntries int) (any, error) {
	if !shipmentValidStrings(body) {
		return nil, errors.New("invalid JSON text")
	}
	d := json.NewDecoder(bytes.NewReader(body))
	d.UseNumber()
	entries := 0
	var parse func(int) (any, error)
	parse = func(depth int) (any, error) {
		if depth > maxDepth {
			return nil, errors.New("JSON depth")
		}
		tok, err := d.Token()
		if err != nil {
			return nil, err
		}
		switch tok {
		case json.Delim('{'):
			obj := map[string]any{}
			for d.More() {
				k, e := d.Token()
				if e != nil {
					return nil, e
				}
				key, ok := k.(string)
				if !ok {
					return nil, errors.New("JSON key")
				}
				if _, exists := obj[key]; exists {
					return nil, errors.New("duplicate JSON key")
				}
				entries++
				if entries > maxEntries {
					return nil, errors.New("JSON entries")
				}
				val, e := parse(depth + 1)
				if e != nil {
					return nil, e
				}
				obj[key] = val
			}
			_, err = d.Token()
			return obj, err
		case json.Delim('['):
			arr := []any{}
			for d.More() {
				entries++
				if entries > maxEntries {
					return nil, errors.New("JSON entries")
				}
				val, e := parse(depth + 1)
				if e != nil {
					return nil, e
				}
				arr = append(arr, val)
			}
			_, err = d.Token()
			return arr, err
		case json.Delim('}'), json.Delim(']'):
			return nil, errors.New("unexpected JSON delimiter")
		default:
			return tok, nil
		}
	}
	v, err := parse(0)
	if err != nil {
		return nil, err
	}
	if _, err = d.Token(); err != io.EOF {
		return nil, errors.New("trailing JSON")
	}
	return v, nil
}

func quoteObject(v any) (map[string]any, bool) { o, ok := v.(map[string]any); return o, ok }
func quoteOnlyKeys(o map[string]any, required []string, optional ...string) bool {
	allowed := map[string]bool{}
	for _, k := range required {
		allowed[k] = true
		if _, ok := o[k]; !ok {
			return false
		}
	}
	for _, k := range optional {
		allowed[k] = true
	}
	for k := range o {
		if !allowed[k] {
			return false
		}
	}
	return true
}
func quoteString(v any) (string, bool) { s, ok := v.(string); return s, ok && s != "" }
func quoteVersion(v any) bool {
	n, ok := v.(json.Number)
	if !ok || strings.ContainsAny(string(n), ".eE") {
		return false
	}
	i, e := strconv.ParseInt(string(n), 10, 32)
	return e == nil && i > 0
}

func quoteBodyValid(body []byte, kind string) bool {
	v, err := quoteJSON(body, 32, 100000)
	if err != nil {
		return false
	}
	if quoteIdentityField(v, 0) {
		return false
	}
	o, ok := quoteObject(v)
	if !ok {
		return false
	}
	switch kind {
	case "upload":
		if !quoteOnlyKeys(o, []string{"filename", "content_base64", "mode", "synthetic"}, "reporting_currency") {
			return false
		}
		name, ok := quoteString(o["filename"])
		if !ok || len(name) > 255 || strings.ContainsAny(name, "/\\\x00") {
			return false
		}
		mode, _ := o["mode"].(string)
		if mode != "rules" || o["synthetic"] != true {
			return false
		}
		encoded, ok := quoteString(o["content_base64"])
		if !ok || base64.StdEncoding.DecodedLen(len(encoded)) > quoteDocumentLimit+2 {
			return false
		}
		decoded, e := base64.StdEncoding.Strict().DecodeString(encoded)
		if e != nil || len(decoded) > quoteDocumentLimit {
			return false
		}
		if currency, exists := o["reporting_currency"]; exists {
			c, ok := currency.(string)
			if !ok || !regexp.MustCompile(`^[A-Z]{3}$`).MatchString(c) {
				return false
			}
		}
	case "corrections":
		if !quoteOnlyKeys(o, []string{"field", "value", "expected_version"}, "evidence", "reason") || !quoteVersion(o["expected_version"]) {
			return false
		}
		field, ok := quoteString(o["field"])
		if !ok || len(field) > 100 || !quoteID.MatchString(field) || o["value"] == nil {
			return false
		}
		if v, exists := o["reason"]; exists {
			if _, ok := v.(string); !ok {
				return false
			}
		}
		if v, exists := o["evidence"]; exists {
			a, ok := v.([]any)
			if !ok || len(a) > 100 {
				return false
			}
			for _, item := range a {
				e, ok := quoteObject(item)
				if !ok || !quoteOnlyKeys(e, []string{"locator"}, "quote", "text_sha256") {
					return false
				}
				if _, ok := quoteString(e["locator"]); !ok {
					return false
				}
				for _, k := range []string{"quote", "text_sha256"} {
					if val, found := e[k]; found && val != nil {
						if _, ok := val.(string); !ok {
							return false
						}
					}
				}
			}
		}
	case "save":
		if !quoteOnlyKeys(o, []string{"expected_version"}) || !quoteVersion(o["expected_version"]) {
			return false
		}
	default:
		return false
	}
	return true
}

func quoteIdentityField(v any, depth int) bool {
	if depth > 32 {
		return true
	}
	switch x := v.(type) {
	case map[string]any:
		for k, item := range x {
			switch strings.ToLower(k) {
			case "actor", "role", "principal_id", "workspace_id", "idempotency_key":
				return true
			}
			if quoteIdentityField(item, depth+1) {
				return true
			}
		}
	case []any:
		for _, item := range x {
			if quoteIdentityField(item, depth+1) {
				return true
			}
		}
	}
	return false
}

func quoteKey(r *http.Request, required bool) (string, bool) {
	key, count := quoteHeader(r, "Idempotency-Key")
	if !required {
		return "", count == 0
	}
	if count != 1 || len(key) < 8 || len(key) > 128 {
		return "", false
	}
	for i := 0; i < len(key); i++ {
		if key[i] < 0x21 || key[i] > 0x7e || key[i] == ',' {
			return "", false
		}
	}
	return key, true
}

type quoteAssertion struct {
	Version        int     `json:"v"`
	Method         string  `json:"method"`
	RawPath        string  `json:"raw_path"`
	RawQuery       string  `json:"raw_query"`
	BodySHA256     string  `json:"body_sha256"`
	IssuedAtUnix   int64   `json:"issued_at_unix"`
	Nonce          string  `json:"nonce"`
	PrincipalID    string  `json:"principal_id"`
	WorkspaceID    string  `json:"workspace_id"`
	Role           string  `json:"role"`
	IdempotencyKey *string `json:"idempotency_key"`
}

func quoteSignedHeaders(method, path, query string, body []byte, p principal, key []byte, idempotency string) (string, string, error) {
	if len(key) < 32 {
		return "", "", errors.New("key unavailable")
	}
	var random [16]byte
	if _, err := rand.Read(random[:]); err != nil {
		return "", "", err
	}
	digest := sha256.Sum256(body)
	a := quoteAssertion{1, method, path, query, hex.EncodeToString(digest[:]), time.Now().Unix(), hex.EncodeToString(random[:]), p.ID, p.Workspace, p.Role, nil}
	if idempotency != "" {
		a.IdempotencyKey = &idempotency
	}
	b, err := json.Marshal(a)
	if err != nil || len(b) > 2048 {
		return "", "", errors.New("assertion unavailable")
	}
	mac := hmac.New(sha256.New, key)
	_, _ = mac.Write(b)
	return base64.RawURLEncoding.EncodeToString(b), base64.RawURLEncoding.EncodeToString(mac.Sum(nil)), nil
}

func quoteAdapterReady(a *quoteAdapter) bool {
	if a == nil || len(a.key) < 32 || !localOrigin(a.upstream) || a.client == nil {
		return false
	}
	return true
}

func (s *stateServer) quoteHTTPRoute(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Cache-Control", "no-store")
	if s.cfg.Mode == "private_machine" || !quoteAdapterReady(s.quote) {
		quoteError(w, 503, "service_unavailable", "Quote workspace unavailable")
		return
	}
	if r.URL.EscapedPath() != r.URL.Path || strings.ContainsAny(r.URL.Path, "%?#") || !quoteHeadersValid(r) {
		quoteError(w, 422, "invalid_request", "Invalid quote request")
		return
	}
	route, ok := quoteRouteFor(r.URL.Path)
	if !ok {
		quoteError(w, 404, "not_found", "Resource not found")
		return
	}
	if r.Method != route.method {
		quoteError(w, 405, "method_not_allowed", "Method not allowed")
		return
	}
	query, ok := quoteQuery(r, route)
	if !ok {
		quoteError(w, 422, "invalid_request", "Invalid quote request")
		return
	}
	key, ok := quoteKey(r, route.idempotent)
	if !ok {
		quoteError(w, 422, "invalid_request", "Invalid quote request")
		return
	}
	if s.auth == nil {
		quoteError(w, 401, "unauthorized", "Valid identity required")
		return
	}
	p, ok := s.auth.Authenticate(w, r)
	if !ok {
		return
	}
	if p.ID == "" || p.Workspace == "" || (p.Role != "editor" && p.Role != "viewer") {
		quoteError(w, 403, "forbidden", "Quote workspace role unavailable")
		return
	}
	if route.mutates && p.Role != "editor" {
		quoteError(w, 403, "forbidden", "Editor role required")
		return
	}
	var body []byte
	if route.bodyLimit == 0 {
		if r.ContentLength > 0 || len(r.TransferEncoding) > 0 {
			quoteError(w, 422, "invalid_request", "Invalid quote request")
			return
		}
		if r.Body != nil {
			b, e := io.ReadAll(io.LimitReader(r.Body, 1))
			if e != nil || len(b) != 0 {
				quoteError(w, 422, "invalid_request", "Invalid quote request")
				return
			}
		}
		if _, n := quoteHeader(r, "Content-Type"); n != 0 {
			quoteError(w, 422, "invalid_request", "Invalid quote request")
			return
		}
	} else {
		contentType, n := quoteHeader(r, "Content-Type")
		media, params, e := mime.ParseMediaType(contentType)
		if n != 1 || e != nil || media != "application/json" || len(params) != 0 {
			quoteError(w, 422, "invalid_request", "Invalid quote request")
			return
		}
		if r.ContentLength > int64(route.bodyLimit) {
			quoteError(w, 413, "request_too_large", "Quote request too large")
			return
		}
		if r.Body == nil {
			quoteError(w, 422, "invalid_request", "Invalid quote request")
			return
		}
		body, e = io.ReadAll(io.LimitReader(r.Body, int64(route.bodyLimit)+1))
		if e != nil {
			quoteError(w, 422, "invalid_request", "Invalid quote request")
			return
		}
		if len(body) > route.bodyLimit {
			quoteError(w, 413, "request_too_large", "Quote request too large")
			return
		}
		if r.ContentLength >= 0 && int64(len(body)) != r.ContentLength {
			quoteError(w, 422, "invalid_request", "Invalid quote request")
			return
		}
		if !quoteBodyValid(body, route.kind) {
			quoteError(w, 422, "invalid_request", "Invalid quote request")
			return
		}
	}
	assertion, signature, e := quoteSignedHeaders(r.Method, route.path, query, body, p, s.quote.key, key)
	if e != nil {
		quoteError(w, 503, "service_unavailable", "Quote workspace unavailable")
		return
	}
	upstreamURL := s.quote.upstream + route.path
	if query != "" {
		upstreamURL += "?" + query
	}
	req, e := http.NewRequestWithContext(r.Context(), r.Method, upstreamURL, bytes.NewReader(body))
	if e != nil {
		quoteError(w, 503, "service_unavailable", "Quote workspace unavailable")
		return
	}
	req.Header.Set("X-Quote-Context", assertion)
	req.Header.Set("X-Quote-Signature", signature)
	if route.mutates {
		req.Header.Set("Content-Type", "application/json")
	}
	if key != "" {
		req.Header.Set("Idempotency-Key", key)
	}
	client := *s.quote.client
	client.Timeout = 8 * time.Second
	client.CheckRedirect = func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }
	res, e := client.Do(req)
	if e != nil {
		quoteError(w, 503, "service_unavailable", "Quote workspace unavailable")
		return
	}
	defer res.Body.Close()
	upstreamType, typeCount := quoteHeader(&http.Request{Header: res.Header}, "Content-Type")
	media, params, typeError := mime.ParseMediaType(upstreamType)
	if typeCount != 1 || typeError != nil || media != "application/json" || len(params) != 0 {
		quoteError(w, 502, "upstream_invalid_response", "Invalid quote response")
		return
	}
	if res.StatusCode >= 300 && res.StatusCode < 400 {
		quoteError(w, 502, "upstream_invalid_response", "Invalid quote response")
		return
	}
	limit := route.responseLimit
	if res.StatusCode >= 400 {
		limit = 64 << 10
	}
	if res.ContentLength > int64(limit) {
		quoteError(w, 502, "upstream_invalid_response", "Invalid quote response")
		return
	}
	response, e := io.ReadAll(io.LimitReader(res.Body, int64(limit)+1))
	if e != nil || len(response) > limit {
		quoteError(w, 502, "upstream_invalid_response", "Invalid quote response")
		return
	}
	value, e := quoteJSON(response, 64, 1000000)
	if e != nil {
		quoteError(w, 502, "upstream_invalid_response", "Invalid quote response")
		return
	}
	wrapper, ok := quoteObject(value)
	if !ok || !quoteOnlyKeys(wrapper, []string{"workspace_id", "data"}) || wrapper["workspace_id"] != p.Workspace {
		quoteError(w, 502, "upstream_invalid_response", "Invalid quote response")
		return
	}
	if res.StatusCode >= 400 {
		quoteUpstreamError(w, res.StatusCode, wrapper["data"])
		return
	}
	if res.StatusCode != 200 && !(route.kind == "upload" && res.StatusCode == 201) || !quoteDataValid(route.kind, wrapper["data"]) || !quoteMoneyValid(wrapper["data"], 0) {
		quoteError(w, 502, "upstream_invalid_response", "Invalid quote response")
		return
	}
	name := map[string]string{"list": "offers", "detail": "offer", "extraction": "extraction", "upload": "upload", "corrections": "offer", "save": "offer", "sourcing": "sourcing"}[route.kind]
	publicData := wrapper["data"]
	if route.kind == "list" {
		publicData, _ = wrapper["data"].(map[string]any)["offers"]
	}
	out := map[string]any{
		"contract_version": stateVersion,
		"context": map[string]string{
			"principal_id": p.ID, "workspace_id": p.Workspace,
			"role": p.Role, "identity_mode": "development",
		},
		name: publicData,
	}
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(res.StatusCode)
	_ = json.NewEncoder(w).Encode(out)
}

func quoteDataValid(kind string, v any) bool {
	o, ok := quoteObject(v)
	if !ok {
		return false
	}
	if !quoteResponseShape(v, kind, 0) {
		return false
	}
	switch kind {
	case "list":
		a, ok := o["offers"].([]any)
		if !ok {
			return false
		}
		for _, item := range a {
			if !quoteOfferValid(item) {
				return false
			}
		}
		return true
	case "detail", "corrections", "save":
		return quoteOfferValid(o)
	case "upload":
		offer, ok := o["offer"]
		if !ok || !quoteOfferValid(offer) {
			return false
		}
		_, ok = quoteString(o["extraction_id"])
		return ok
	case "extraction":
		_, ok := quoteString(o["extraction_id"])
		return ok
	case "sourcing":
		candidates, c := o["candidates"].([]any)
		excluded, e := o["excluded"].([]any)
		_, d := quoteString(o["as_of"])
		if !c || !e || !d {
			return false
		}
		for _, item := range candidates {
			candidate, ok := quoteObject(item)
			if !ok {
				return false
			}
			if _, ok := quoteString(candidate["candidate_id"]); !ok {
				return false
			}
			if _, ok := quoteString(candidate["offer_id"]); !ok || !quoteVersion(candidate["offer_version"]) {
				return false
			}
			if _, ok := quoteObject(candidate["price"]); !ok {
				return false
			}
			if _, ok := quoteObject(candidate["landed_price"]); !ok {
				return false
			}
		}
		for _, item := range excluded {
			reason, ok := quoteObject(item)
			if !ok {
				return false
			}
			if _, ok := quoteString(reason["offer_id"]); !ok || !quoteVersion(reason["offer_version"]) {
				return false
			}
			if _, ok := quoteString(reason["reason"]); !ok {
				return false
			}
		}
		return true
	}
	return false
}

// These names come from the pinned local Python offer view, extraction and
// sourcing constructors. Dynamic field maps are restricted to their field names;
// arbitrary upstream object keys never become browser-visible by accident.
func quoteResponseKeys(shape string) string {
	switch shape {
	case "list":
		return "offers"
	case "list-offer":
		return "offer_id status version synthetic filename supplier_name materials blocking_reasons updated_at"
	case "detail", "corrections", "save", "offer":
		return "offer_id status version synthetic extraction_id document extractor extractor_detail as_of fields items review_flags normalized blocking_reasons allowed_actions created_at updated_at events replayed"
	case "upload":
		return "offer extraction_id deduplicated"
	case "extraction":
		return "extraction_id extraction segments"
	case "extraction-body":
		return "document extractor extractor_detail as_of synthetic fields items review_flags normalized"
	case "document":
		return "document_id filename media_type byte_size trust metadata warnings"
	case "metadata":
		return "pages delimiter encoding records sheets hidden_sheets"
	case "extractor-detail":
		return "requested call provider model auth live_calls latency_ms prompt_tokens output_tokens fallback_reason attempted stage error_kind"
	case "live-calls":
		return "used max"
	case "fields":
		return "supplier_name origin_country incoterm settlement_currency payment_terms valid_until lead_time_days min_order capacity shipments"
	case "item-fields":
		return "material description grade quantity pricing"
	case "field":
		return "name kind status value critical source reasons evidence candidates evidence_status corrected_by corrected_at previous"
	case "previous":
		return "status value"
	case "candidate":
		return "value verified problems evidence cited"
	case "evidence-ref":
		return "document_id locator quote text_sha256 segment_text"
	case "cited":
		return "locator quote text_sha256"
	case "review-flag":
		return "flag locator text handling field locators"
	case "normalized":
		return "as_of reporting_currency origin_country_code expiry min_order capacity payment_terms items"
	case "normalized-item":
		return "quantity pricing"
	case "normalized-quantity":
		return "original kg status"
	case "normalized-pricing":
		return "type original status per_kg reporting index averaging_period premium_per_kg"
	case "quantity":
		return "amount unit period kg"
	case "pricing":
		return "type unit_price per_unit premium index averaging_period fx_basis fx_terms fx_conversion quotational_period"
	case "money":
		return "amount currency"
	case "incoterm":
		return "code place"
	case "shipment-lot":
		return "quantity window"
	case "reporting":
		return "currency status fx per_kg premium_per_kg"
	case "fx":
		return "base quote rate rate_date source retrieved_at status message"
	case "payment":
		return "status text instalments unrecognised evidence reasons source"
	case "instalment":
		return "percent percent_basis instrument anchor days timing clause"
	case "payment-evidence":
		return "locator quote"
	case "blocking":
		return "code field message"
	case "event":
		return "seq at actor action field old new from_status to_status"
	case "event-value":
		return "status value evidence_status extraction_id extractor call recommendation_id action_id sent field reason valid_until checked_on version"
	case "segment":
		return "locator text text_sha256 flags page line sheet cell row column"
	case "sourcing":
		return "contract as_of synthetic candidates excluded notes"
	case "sourcing-candidate":
		return "candidate_id offer_id offer_version offer_status item_index synthetic document_id supplier material description grade quantity min_order_kg capacity lead_time_days incoterm settlement_currency price landed_price validity payment_terms evidence field_sources"
	case "supplier":
		return "name origin_country origin_country_code"
	case "sourcing-price":
		return "type unit_price per_unit per_kg index averaging_period premium_per_kg"
	case "landed-price":
		return "status basis incoterm place includes not_included per_kg reporting"
	case "validity":
		return "state valid_until days_left evidence"
	case "sourcing-evidence":
		return "field locator text_sha256 document_id"
	case "field-sources":
		return "dynamic-field-names"
	case "field-source":
		return "status source"
	case "excluded":
		return "offer_id offer_version item_index reason detail"
	}
	return ""
}

func quoteResponseChild(shape, key string) string {
	switch shape {
	case "list":
		if key == "offers" {
			return "list-offers"
		}
	case "list-offer":
		if key == "materials" {
			return "strings"
		}
	case "detail", "corrections", "save", "offer", "extraction-body":
		switch key {
		case "document":
			return "document"
		case "extractor_detail":
			return "extractor-detail"
		case "fields":
			return "fields"
		case "items":
			return "items"
		case "review_flags":
			return "review-flags"
		case "normalized":
			return "normalized"
		case "blocking_reasons":
			return "blocking-reasons"
		case "allowed_actions":
			return "strings"
		case "events":
			return "events"
		}
	case "upload":
		if key == "offer" {
			return "offer"
		}
	case "extraction":
		if key == "extraction" {
			return "extraction-body"
		}
		if key == "segments" {
			return "segments"
		}
	case "document":
		if key == "metadata" {
			return "metadata"
		}
		if key == "warnings" {
			return "strings"
		}
	case "metadata":
		if key == "sheets" || key == "hidden_sheets" {
			return "strings"
		}
	case "extractor-detail":
		if key == "live_calls" {
			return "live-calls"
		}
	case "fields", "item-fields":
		return "field"
	case "field":
		switch key {
		case "value":
			return "value"
		case "reasons":
			return "strings"
		case "evidence":
			return "evidence-refs"
		case "candidates":
			return "candidates"
		case "previous":
			return "previous"
		}
	case "previous":
		if key == "value" {
			return "value"
		}
	case "candidate":
		switch key {
		case "value":
			return "value"
		case "problems":
			return "strings"
		case "evidence":
			return "evidence-refs"
		case "cited":
			return "cited-list"
		}
	case "review-flag":
		if key == "locators" {
			return "strings"
		}
	case "normalized":
		switch key {
		case "min_order", "capacity":
			return "normalized-quantity"
		case "payment_terms":
			return "payment"
		case "items":
			return "normalized-items"
		}
	case "normalized-item":
		if key == "quantity" {
			return "normalized-quantity"
		}
		if key == "pricing" {
			return "normalized-pricing"
		}
	case "normalized-quantity":
		if key == "original" {
			return "quantity"
		}
	case "normalized-pricing":
		switch key {
		case "original":
			return "value"
		case "per_kg", "premium_per_kg":
			return "money"
		case "reporting":
			return "reporting"
		}
	case "quantity": // Sourcing quantities include kg; original quantities may include period.
	case "pricing":
		if key == "unit_price" || key == "premium" {
			return "money"
		}
	case "shipment-lot":
		if key == "quantity" {
			return "quantity"
		}
	case "reporting":
		if key == "fx" {
			return "fx"
		}
		if key == "per_kg" || key == "premium_per_kg" {
			return "money"
		}
	case "payment":
		switch key {
		case "instalments":
			return "instalments"
		case "unrecognised", "reasons":
			return "strings"
		case "evidence":
			return "payment-evidences"
		}
	case "event":
		if key == "old" || key == "new" {
			return "event-value"
		}
	case "event-value":
		if key == "value" {
			return "value"
		}
	case "segment":
		if key == "flags" {
			return "strings"
		}
	case "sourcing":
		switch key {
		case "candidates":
			return "sourcing-candidates"
		case "excluded":
			return "excluded-list"
		case "notes":
			return "strings"
		}
	case "sourcing-candidate":
		switch key {
		case "supplier":
			return "supplier"
		case "quantity":
			return "quantity"
		case "capacity":
			return "quantity"
		case "incoterm":
			return "incoterm"
		case "price":
			return "sourcing-price"
		case "landed_price":
			return "landed-price"
		case "validity":
			return "validity"
		case "payment_terms":
			return "payment"
		case "evidence":
			return "sourcing-evidences"
		case "field_sources":
			return "field-sources"
		}
	case "sourcing-price":
		if key == "unit_price" || key == "per_kg" || key == "premium_per_kg" {
			return "money"
		}
	case "landed-price":
		if key == "per_kg" {
			return "money"
		}
		if key == "reporting" {
			return "reporting"
		}
		if key == "includes" || key == "not_included" {
			return "strings"
		}
	case "validity":
		if key == "evidence" {
			return "payment-evidences"
		}
	case "field-sources":
		return "field-source"
	}
	return ""
}

func quoteArrayChild(shape string) string {
	switch shape {
	case "list-offers":
		return "list-offer"
	case "items":
		return "item-fields"
	case "review-flags":
		return "review-flag"
	case "blocking-reasons":
		return "blocking"
	case "events":
		return "event"
	case "evidence-refs":
		return "evidence-ref"
	case "candidates":
		return "candidate"
	case "cited-list":
		return "cited"
	case "normalized-items":
		return "normalized-item"
	case "instalments":
		return "instalment"
	case "payment-evidences":
		return "payment-evidence"
	case "segments":
		return "segment"
	case "sourcing-candidates":
		return "sourcing-candidate"
	case "sourcing-evidences":
		return "sourcing-evidence"
	case "excluded-list":
		return "excluded"
	case "shipments":
		return "shipment-lot"
	case "strings":
		return "text"
	}
	return ""
}

var quoteDecimal = regexp.MustCompile(`^-?(0|[1-9][0-9]*)(\.[0-9]+)?$`)
var quoteItemFieldName = regexp.MustCompile(`^items\[[0-9]+\]\.(material|description|grade|quantity|pricing)$`)

func quoteFieldName(name string) bool {
	return strings.Contains(" supplier_name origin_country incoterm settlement_currency payment_terms valid_until lead_time_days min_order capacity shipments ", " "+name+" ") || quoteItemFieldName.MatchString(name)
}

func quoteRequiredFields(shape string, o map[string]any) bool {
	required := ""
	switch shape {
	case "money":
		required = "amount currency"
	case "document":
		required = "document_id filename media_type byte_size trust metadata warnings"
	case "sourcing-price":
		if o["type"] == "fixed" {
			required = "type unit_price per_unit per_kg"
		} else if o["type"] == "index_linked" {
			required = "type index averaging_period premium_per_kg"
		} else {
			return false
		}
	case "landed-price":
		required = "status basis incoterm place includes not_included per_kg reporting"
	case "sourcing-candidate":
		required = "candidate_id offer_id offer_version price landed_price"
	case "supplier":
		required = "name origin_country origin_country_code"
	case "field-source":
		required = "status source"
	}
	for _, key := range strings.Fields(required) {
		if _, exists := o[key]; !exists {
			return false
		}
	}
	return true
}

func quoteResponseScalar(shape, key string, v any) bool {
	if v == nil {
		if shape == "money" || (shape == "sourcing-price" && key == "type") {
			return false
		}
		return true
	}
	if shape == "value" {
		switch v.(type) {
		case string, json.Number, bool:
			return true
		}
		return false
	}
	if n, ok := v.(json.Number); ok {
		if !strings.Contains(" version offer_version item_index lead_time_days days_left days seq page line row column byte_size pages records latency_ms prompt_tokens output_tokens used max blocking_reasons ", " "+key+" ") {
			return false
		}
		_, err := strconv.ParseInt(string(n), 10, 64)
		return err == nil
	}
	if _, ok := v.(bool); ok {
		return strings.Contains(" synthetic critical verified deduplicated replayed sent attempted ", " "+key+" ")
	}
	s, ok := v.(string)
	if !ok || len(s) > quoteDocumentLimit {
		return false
	}
	if shape == "money" && key == "currency" {
		return len(s) == 3 && regexp.MustCompile(`^[A-Z]{3}$`).MatchString(s)
	}
	if strings.Contains(" amount kg rate min_order_kg percent ", " "+key+" ") {
		return len(s) <= 128 && quoteDecimal.MatchString(s)
	}
	return true
}

// A missing optional value is different from a null object or array. These
// exceptions follow the local Python constructors; every other typed child
// must be present with its declared container when its key is present.
func quoteResponseNullable(parent, key string, o map[string]any) bool {
	switch parent {
	case "field", "previous", "candidate", "event-value":
		return key == "value" || (parent == "field" && key == "previous")
	case "event":
		return key == "old" || key == "new"
	case "normalized":
		return key == "min_order" || key == "capacity" || key == "payment_terms"
	case "normalized-item":
		return key == "quantity" || key == "pricing"
	case "normalized-pricing":
		return key == "original" || (o["status"] != "ok" &&
			(key == "per_kg" || key == "premium_per_kg" || key == "reporting"))
	case "reporting":
		return key == "fx" || key == "per_kg" || key == "premium_per_kg"
	case "sourcing-candidate":
		return key == "capacity" || key == "incoterm"
	case "landed-price":
		return key == "per_kg" && o["status"] == "index_linked_final_price_unknown"
	}
	return false
}

func quoteSelectedAmount(v any, positive bool) bool {
	s, ok := v.(string)
	if !ok || len(s) > 128 || !quoteDecimal.MatchString(s) {
		return false
	}
	return !positive || (!strings.HasPrefix(s, "-") && strings.Trim(s, "0.") != "")
}

func quoteSelectedQuantity(v any, allowPeriod bool) bool {
	o, ok := quoteObject(v)
	if !ok || !quoteOnlyKeys(o, []string{"amount", "unit"}, "period") || !quoteSelectedAmount(o["amount"], true) {
		return false
	}
	if !allowPeriod {
		if _, exists := o["period"]; exists {
			return false
		}
	}
	unit, ok := o["unit"].(string)
	if !ok || !strings.Contains(" tonne kg lb ", " "+unit+" ") {
		return false
	}
	if period, exists := o["period"]; exists && period != nil {
		p, ok := period.(string)
		if !ok || !strings.Contains(" month quarter year week ", " "+p+" ") {
			return false
		}
	}
	return true
}

func quoteSelectedMoney(v any, positive bool) bool {
	o, ok := quoteObject(v)
	return ok && quoteOnlyKeys(o, []string{"amount", "currency"}) &&
		quoteSelectedAmount(o["amount"], positive) && quoteResponseShape(o, "money", 0)
}

func quoteSelectedPricing(v any) bool {
	o, ok := quoteObject(v)
	if !ok {
		return false
	}
	unit, ok := o["per_unit"].(string)
	if !ok || !strings.Contains(" tonne kg lb ", " "+unit+" ") {
		return false
	}
	switch o["type"] {
	case "fixed":
		return quoteOnlyKeys(o, []string{"type", "unit_price", "per_unit"}) && quoteSelectedMoney(o["unit_price"], true)
	case "index_linked":
		if !quoteOnlyKeys(o, []string{"type", "index", "averaging_period", "premium", "per_unit"},
			"fx_basis", "fx_terms", "fx_conversion", "quotational_period") || !quoteSelectedMoney(o["premium"], false) {
			return false
		}
		for _, key := range []string{"index", "averaging_period"} {
			if _, ok := quoteString(o[key]); !ok {
				return false
			}
		}
		for _, key := range []string{"fx_basis", "fx_terms", "fx_conversion", "quotational_period"} {
			if value, exists := o[key]; exists {
				if _, ok := quoteString(value); !ok {
					return false
				}
			}
		}
		return true
	}
	return false
}

func quoteFieldValueValid(field map[string]any, v any) bool {
	kind, ok := field["kind"].(string)
	if !ok || !strings.Contains(" text date currency material integer quantity incoterm pricing shipments ", " "+kind+" ") {
		return false
	}
	if v == nil {
		return field["status"] == "missing" || field["status"] == "conflict" || field["status"] == "unverified"
	}
	switch kind {
	case "text", "date", "currency", "material":
		_, ok := v.(string)
		return ok
	case "integer":
		n, ok := v.(json.Number)
		if !ok {
			return false
		}
		i, err := strconv.ParseInt(string(n), 10, 64)
		return err == nil && i >= 0 && i <= 3650
	case "quantity":
		return quoteSelectedQuantity(v, field["name"] == "capacity")
	case "incoterm":
		o, ok := quoteObject(v)
		if !ok || !quoteOnlyKeys(o, []string{"code"}, "place") {
			return false
		}
		code, ok := o["code"].(string)
		if !ok || !strings.Contains(" EXW FCA FAS FOB CFR CIF CPT CIP DAP DPU DDP ", " "+code+" ") {
			return false
		}
		if place, exists := o["place"]; exists && place != nil {
			_, ok = place.(string)
			return ok
		}
		return true
	case "pricing":
		return quoteSelectedPricing(v)
	case "shipments":
		lots, ok := v.([]any)
		if !ok || len(lots) == 0 {
			return false
		}
		for _, lot := range lots {
			o, ok := quoteObject(lot)
			if !ok || !quoteOnlyKeys(o, []string{"quantity", "window"}) || !quoteSelectedQuantity(o["quantity"], false) {
				return false
			}
			if _, ok := o["window"].(string); !ok {
				return false
			}
		}
		return true
	}
	return false
}

func quoteResponseShape(v any, shape string, depth int) bool {
	if depth > 64 {
		return false
	}
	if v == nil {
		return shape == "value" || shape == "text"
	}
	// Checking children only when their runtime value happens to be an object or
	// array lets a scalar masquerade as any known nested shape.
	if quoteResponseKeys(shape) != "" {
		if _, ok := quoteObject(v); !ok {
			return false
		}
	} else if quoteArrayChild(shape) != "" {
		if _, ok := v.([]any); !ok {
			return false
		}
	}
	if a, ok := v.([]any); ok {
		child := quoteArrayChild(shape)
		if shape == "value" {
			child = "shipment-lot"
		}
		if child == "" {
			return false
		}
		for _, item := range a {
			if !quoteResponseShape(item, child, depth+1) {
				return false
			}
		}
		return true
	}
	if o, ok := quoteObject(v); ok {
		if shape == "value" {
			_, hasType := o["type"]
			_, hasCurrency := o["currency"]
			_, hasUnit := o["unit"]
			_, hasCode := o["code"]
			switch {
			case hasType:
				shape = "pricing"
			case hasCurrency:
				shape = "money"
			case hasUnit:
				shape = "quantity"
			case hasCode:
				shape = "incoterm"
			default:
				return false
			}
		}
		allowed := quoteResponseKeys(shape)
		if allowed == "" || !quoteRequiredFields(shape, o) {
			return false
		}
		if shape == "normalized-pricing" && o["status"] == "ok" {
			typeName, ok := o["type"].(string)
			if !ok || (typeName != "fixed" && typeName != "index_linked") || !quoteSelectedPricing(o["original"]) {
				return false
			}
			original, _ := quoteObject(o["original"])
			if original["type"] != typeName {
				return false
			}
		}
		for key, item := range o {
			if shape == "field-sources" {
				if !quoteFieldName(key) {
					return false
				}
			} else if !strings.Contains(" "+allowed+" ", " "+key+" ") {
				return false
			}
			if shape == "field" && key == "value" && !quoteFieldValueValid(o, item) {
				return false
			}
			child := quoteResponseChild(shape, key)
			if child != "" {
				if item == nil {
					if !quoteResponseNullable(shape, key, o) {
						return false
					}
					continue
				}
				if !quoteResponseShape(item, child, depth+1) {
					return false
				}
			} else if !quoteResponseScalar(shape, key, item) {
				return false
			}
		}
		return true
	}
	return quoteResponseScalar(shape, "", v)
}

// Money and normalized quantity values stay decimal strings in the public
// envelope. This deliberately permits integer version/index fields elsewhere.
func quoteMoneyValid(v any, depth int) bool {
	if depth > 64 {
		return false
	}
	switch x := v.(type) {
	case map[string]any:
		for k, item := range x {
			if k == "amount" || k == "kg" || k == "rate" || k == "min_order_kg" {
				if item != nil {
					if _, ok := item.(string); !ok {
						return false
					}
				}
			}
			if !quoteMoneyValid(item, depth+1) {
				return false
			}
		}
	case []any:
		for _, item := range x {
			if !quoteMoneyValid(item, depth+1) {
				return false
			}
		}
	}
	return true
}

func quoteOfferValid(v any) bool {
	o, ok := quoteObject(v)
	if !ok {
		return false
	}
	_, ok = quoteString(o["offer_id"])
	if !ok {
		return false
	}
	switch n := o["version"].(type) {
	case json.Number:
		i, e := strconv.ParseInt(string(n), 10, 32)
		return e == nil && i > 0
	default:
		return false
	}
}

func quoteUpstreamError(w http.ResponseWriter, status int, v any) {
	o, ok := quoteObject(v)
	if !ok {
		quoteError(w, 502, "upstream_invalid_response", "Invalid quote response")
		return
	}
	errObj, ok := quoteObject(o["error"])
	if !ok {
		quoteError(w, 502, "upstream_invalid_response", "Invalid quote response")
		return
	}
	code, ok := quoteString(errObj["code"])
	if !ok {
		quoteError(w, 502, "upstream_invalid_response", "Invalid quote response")
		return
	}
	switch status {
	case 400, 422:
		if code == "invalid_request" || code == "invalid_value" || code == "evidence_not_verified" || code == "unsupported_mode" || code == "unknown_field" || code == "document_unreadable" {
			quoteError(w, status, "invalid_request", "Invalid quote request")
			return
		}
	case 404:
		quoteError(w, 404, "not_found", "Resource not found")
		return
	case 409:
		if code == "conflict" || code == "save_blocked" || code == "version_conflict" || code == "stale_version" || code == "invalid_transition" || code == "idempotency_conflict" {
			quoteError(w, 409, "conflict", "Quote state conflict")
			return
		}
	}
	quoteError(w, 502, "upstream_invalid_response", "Invalid quote response")
}
