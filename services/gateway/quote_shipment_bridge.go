package main

// The quote bridge is a local, read-only association check. The quote adapter
// remains disabled in ordinary runtime configuration; tests inject it.
import (
	"bytes"
	"encoding/json"
	"io"
	"mime"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"time"
)

func quoteShipmentRouteCandidate(path string) bool {
	parts := strings.Split(path, "/")
	return len(parts) == 6 && parts[0] == "" && parts[1] == "v2" && parts[2] == "workspaces" && parts[4] == "shipment-recovery" && parts[5] == "quote-bridge"
}

func quoteShipmentError(w http.ResponseWriter, status int) {
	code := "unavailable"
	if status == 503 {
		code = "service_unavailable"
	}
	quoteError(w, status, code, "Quote bridge unavailable")
}

func (s *stateServer) quoteShipmentClock() time.Time {
	if s.quoteShipmentNow != nil {
		return s.quoteShipmentNow().UTC()
	}
	return time.Now().UTC()
}

func quoteShipmentQuery(r *http.Request, workspace string) (shipmentCandidateBinding, bool) {
	var b shipmentCandidateBinding
	if r.URL.ForceQuery || len(r.URL.RawQuery) > quoteQueryLimit || r.URL.RawQuery == "" {
		return b, false
	}
	values, err := url.ParseQuery(r.URL.RawQuery)
	if err != nil || len(values) != 5 {
		return b, false
	}
	for _, k := range []string{"candidate_id", "offer_id", "offer_version", "item_index", "document_id"} {
		if len(values[k]) != 1 || values[k][0] == "" {
			return b, false
		}
	}
	version, errV := strconv.ParseInt(values.Get("offer_version"), 10, 32)
	index, errI := strconv.ParseInt(values.Get("item_index"), 10, 32)
	if errV != nil || errI != nil || strconv.FormatInt(version, 10) != values.Get("offer_version") || strconv.FormatInt(index, 10) != values.Get("item_index") {
		return b, false
	}
	b = shipmentCandidateBinding{WorkspaceID: workspace, CandidateID: values.Get("candidate_id"), OfferID: values.Get("offer_id"), OfferVersion: int(version), ItemIndex: int(index), DocumentID: values.Get("document_id")}
	return b, shipmentValidCandidateBinding(b, workspace)
}

func quoteShipmentSourceCandidate(v any, wanted shipmentCandidateBinding, date string) ([]map[string]string, bool) {
	o, ok := quoteObject(v)
	if !ok || o["candidate_id"] != wanted.CandidateID || o["offer_id"] != wanted.OfferID || o["document_id"] != wanted.DocumentID || o["offer_version"] != json.Number(strconv.Itoa(wanted.OfferVersion)) || o["item_index"] != json.Number(strconv.Itoa(wanted.ItemIndex)) || o["synthetic"] != true {
		return nil, false
	}
	for _, key := range []string{"supplier", "material", "description", "grade", "quantity", "min_order_kg", "capacity", "lead_time_days", "incoterm", "settlement_currency", "price", "landed_price", "validity", "payment_terms", "evidence", "field_sources"} {
		if _, present := o[key]; !present {
			return nil, false
		}
	}
	if _, ok := quoteString(o["material"]); !ok {
		return nil, false
	}
	if o["offer_status"] != "saved" && o["offer_status"] != "used_in_plan" {
		return nil, false
	}
	validity, ok := quoteObject(o["validity"])
	if !ok || validity["state"] != "valid" {
		return nil, false
	}
	until, ok := validity["valid_until"].(string)
	if !ok || len(until) != 10 || until < date {
		return nil, false
	}
	parsed, err := time.Parse("2006-01-02", until)
	if err != nil || parsed.Format("2006-01-02") != until {
		return nil, false
	}
	refs, ok := o["evidence"].([]any)
	if !ok || len(refs) == 0 || len(refs) > 64 {
		return nil, false
	}
	result := make([]map[string]string, 0, len(refs))
	for _, item := range refs {
		ref, ok := quoteObject(item)
		if !ok {
			return nil, false
		}
		field, f := ref["field"].(string)
		locator, l := ref["locator"].(string)
		hash, h := ref["text_sha256"].(string)
		doc, d := ref["document_id"].(string)
		if !f || !l || !h || !d || !quoteFieldName(field) || !shipmentText(locator) || !shipmentHash.MatchString(hash) || doc != wanted.DocumentID {
			return nil, false
		}
		result = append(result, map[string]string{"field": field, "locator": locator, "text_sha256": hash, "document_id": doc})
	}
	return result, true
}

func (s *stateServer) quoteShipmentHTTPRoute(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Cache-Control", "no-store")
	if s.cfg.Mode == "private_machine" || !quoteAdapterReady(s.quote) || s.shipment == nil || !s.shipment.available() {
		quoteShipmentError(w, 503)
		return
	}
	if r.URL.EscapedPath() != r.URL.Path || strings.ContainsAny(r.URL.Path, "%?#") || !quoteHeadersValid(r) {
		quoteError(w, 422, "invalid_request", "Invalid quote bridge request")
		return
	}
	parts := strings.Split(r.URL.Path, "/")
	if len(parts) != 6 || !shipmentText(parts[3]) || strings.ContainsAny(parts[3], "%?#") {
		quoteError(w, 422, "invalid_request", "Invalid quote bridge request")
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
		quoteError(w, 403, "forbidden", "Quote bridge role unavailable")
		return
	}
	if parts[3] != p.Workspace {
		quoteShipmentError(w, 404)
		return
	}
	if r.Method != http.MethodGet {
		quoteError(w, 405, "method_not_allowed", "Method not allowed")
		return
	}
	if r.ContentLength > 0 || len(r.TransferEncoding) > 0 {
		quoteError(w, 422, "invalid_request", "Invalid quote bridge request")
		return
	}
	if r.Body != nil {
		b, err := io.ReadAll(io.LimitReader(r.Body, 1))
		if err != nil || len(b) != 0 {
			quoteError(w, 422, "invalid_request", "Invalid quote bridge request")
			return
		}
	}
	if _, n := quoteHeader(r, "Content-Type"); n != 0 {
		quoteError(w, 422, "invalid_request", "Invalid quote bridge request")
		return
	}
	if _, n := quoteHeader(r, "Idempotency-Key"); n != 0 {
		quoteError(w, 422, "invalid_request", "Invalid quote bridge request")
		return
	}
	wanted, ok := quoteShipmentQuery(r, p.Workspace)
	if !ok {
		quoteError(w, 422, "invalid_request", "Invalid quote bridge request")
		return
	}
	var chosen shipmentCandidateAssociation
	count := 0
	for _, meta := range s.shipment.list(p.Workspace) {
		association, present := s.shipment.boundCandidate(p.Workspace, meta.ContextID, meta.Revision, meta.RequestSHA256)
		if present && association.Candidate == wanted && association.ContextID == meta.ContextID && association.Revision == meta.Revision && association.RequestSHA256 == meta.RequestSHA256 && association.PlanVersion == meta.PlanVersion && association.AsOf == meta.AsOf && meta.Synthetic && meta.Source == "host_configured_synthetic" && meta.SourceAuthenticity == "not_verified" && shipmentTime(meta.AsOf) {
			chosen = association
			count++
		}
	}
	if count != 1 {
		quoteShipmentError(w, 404)
		return
	}
	started := s.quoteShipmentClock()
	date := started.Format("2006-01-02")
	query := "as_of=" + url.QueryEscape(date)
	const path = "/offers/sourcing-candidates"
	assertion, signature, err := quoteSignedHeaders(http.MethodGet, path, query, nil, p, s.quote.key, "")
	if err != nil {
		quoteShipmentError(w, 503)
		return
	}
	req, err := http.NewRequestWithContext(r.Context(), http.MethodGet, s.quote.upstream+path+"?"+query, bytes.NewReader(nil))
	if err != nil {
		quoteShipmentError(w, 503)
		return
	}
	req.Header.Set("X-Quote-Context", assertion)
	req.Header.Set("X-Quote-Signature", signature)
	client := *s.quote.client
	client.Timeout = 8 * time.Second
	client.CheckRedirect = func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }
	response, err := client.Do(req)
	if err != nil {
		quoteShipmentError(w, 503)
		return
	}
	defer response.Body.Close()
	if response.StatusCode != 200 || response.ContentLength > quoteResponseLimit {
		quoteShipmentError(w, 503)
		return
	}
	contentType, n := quoteHeader(&http.Request{Header: response.Header}, "Content-Type")
	media, params, err := mime.ParseMediaType(contentType)
	if n != 1 || err != nil || media != "application/json" || len(params) != 0 {
		quoteShipmentError(w, 503)
		return
	}
	raw, err := io.ReadAll(io.LimitReader(response.Body, quoteResponseLimit+1))
	if err != nil || len(raw) > quoteResponseLimit {
		quoteShipmentError(w, 503)
		return
	}
	parsed, err := quoteJSON(raw, 64, 1000000)
	if err != nil {
		quoteShipmentError(w, 503)
		return
	}
	wrapper, ok := quoteObject(parsed)
	if !ok || !quoteOnlyKeys(wrapper, []string{"workspace_id", "data"}) || wrapper["workspace_id"] != p.Workspace || !quoteDataValid("sourcing", wrapper["data"]) || !quoteMoneyValid(wrapper["data"], 0) {
		quoteShipmentError(w, 503)
		return
	}
	data, _ := quoteObject(wrapper["data"])
	if data["contract"] != "offers-sourcing-candidates/proposed-0.1" || data["as_of"] != date || data["synthetic"] != true {
		quoteShipmentError(w, 503)
		return
	}
	candidates, _ := data["candidates"].([]any)
	var refs []map[string]string
	matches := 0
	for _, item := range candidates {
		o, _ := quoteObject(item)
		if o["candidate_id"] != wanted.CandidateID {
			continue
		}
		matches++
		refs, ok = quoteShipmentSourceCandidate(item, wanted, date)
		if !ok {
			break
		}
	}
	if matches != 1 || !ok {
		quoteShipmentError(w, 404)
		return
	}
	contextTime, err := time.Parse(time.RFC3339Nano, chosen.AsOf)
	if err != nil {
		quoteShipmentError(w, 404)
		return
	}
	checked := s.quoteShipmentClock()
	if checked.Format("2006-01-02") != date || r.Context().Err() != nil {
		quoteShipmentError(w, 503)
		return
	}
	bridge := map[string]any{
		"candidate":        map[string]any{"candidate_id": wanted.CandidateID, "offer_id": wanted.OfferID, "offer_version": wanted.OfferVersion, "item_index": wanted.ItemIndex, "document_id": wanted.DocumentID},
		"shipment_context": map[string]any{"context_id": chosen.ContextID, "revision": chosen.Revision, "request_sha256": chosen.RequestSHA256, "plan_version": chosen.PlanVersion},
		"host_binding":     "host_configured_synthetic", "source_authenticity": "not_verified", "context_as_of": contextTime.UTC().Format(time.RFC3339Nano),
		"source_as_of": date, "source_checked_at": checked.Format(time.RFC3339Nano), "evidence_refs": refs,
	}
	responseBytes, err := shipmentMarshal(map[string]any{"contract_version": stateVersion, "context": map[string]string{"principal_id": p.ID, "workspace_id": p.Workspace, "role": p.Role, "identity_mode": "development"}, "bridge": bridge})
	if err != nil || len(responseBytes) > 64<<10 {
		quoteShipmentError(w, 503)
		return
	}
	if status := s.quoteShipmentPublishCurrent(w, r, p.Workspace, chosen, date, responseBytes); status != 0 {
		quoteShipmentError(w, status)
	}
}

// Publish only while the host's exact association is still current. The lock
// covers the bounded write, so replacement and shutdown cannot win mid-200.
func (s *stateServer) quoteShipmentPublishCurrent(w http.ResponseWriter, r *http.Request, workspace string, chosen shipmentCandidateAssociation, sourceDate string, responseBytes []byte) int {
	a := s.shipment
	a.mu.Lock()
	defer a.mu.Unlock()
	if r.Context().Err() != nil || s.quoteShipmentClock().Format("2006-01-02") != sourceDate {
		return 503
	}
	if a.closed || !a.ready {
		return 404
	}
	current := a.contexts[workspace][chosen.ContextID]
	if current == nil || current.binding == nil || current.metadata.WorkspaceID != workspace ||
		current.metadata.ContextID != chosen.ContextID || current.metadata.Revision != chosen.Revision ||
		current.metadata.RequestSHA256 != chosen.RequestSHA256 || current.metadata.PlanVersion != chosen.PlanVersion ||
		current.metadata.AsOf != chosen.AsOf || !current.metadata.Synthetic ||
		current.metadata.Source != "host_configured_synthetic" || current.metadata.SourceAuthenticity != "not_verified" ||
		*current.binding != chosen {
		return 404
	}
	deadline := time.Now().Add(2 * time.Second)
	if contextDeadline, ok := r.Context().Deadline(); ok && contextDeadline.Before(deadline) {
		deadline = contextDeadline
	}
	controller := http.NewResponseController(w)
	_ = controller.SetWriteDeadline(deadline)
	defer func() { _ = controller.SetWriteDeadline(time.Time{}) }()
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Cache-Control", "no-store")
	w.WriteHeader(200)
	_, _ = w.Write(responseBytes)
	return 0
}
