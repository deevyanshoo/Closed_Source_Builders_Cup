package main

import (
	"bytes"
	"context"
	"crypto/hmac"
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

type quoteTransport func(*http.Request) (*http.Response, error)

func (f quoteTransport) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }

func quoteFixture(t *testing.T, handler http.HandlerFunc) (*stateServer, []byte) {
	t.Helper()
	identities := filepath.Join(t.TempDir(), "identities.json")
	if e := os.WriteFile(identities, []byte(`{"identities":[{"token":"editor-token","principal_id":"alice","workspace_id":"w-a","role":"editor"},{"token":"viewer-token","principal_id":"bob","workspace_id":"w-a","role":"viewer"},{"token":"other-token","principal_id":"eve","workspace_id":"w-b","role":"editor"}]}`), 0600); e != nil {
		t.Fatal(e)
	}
	key := make([]byte, 32)
	if _, e := rand.Read(key); e != nil {
		t.Fatal(e)
	}
	s := newLocalStateServer(stateConfig{Mode: "local", IdentitiesFile: identities, AllowedOrigin: "http://localhost:5173"})
	client := &http.Client{Transport: quoteTransport(func(r *http.Request) (*http.Response, error) {
		w := httptest.NewRecorder()
		handler(w, r)
		return w.Result(), nil
	})}
	s.quote = &quoteAdapter{upstream: "http://127.0.0.1:8016", key: key, client: client}
	return s, key
}

func quoteCall(h http.Handler, method, path, token, key, body string) *httptest.ResponseRecorder {
	r := httptest.NewRequest(method, path, strings.NewReader(body))
	r.RemoteAddr = "127.0.0.1:12345"
	if token != "" {
		r.Header.Set("Authorization", "Bearer "+token)
	}
	if key != "" {
		r.Header.Set("Idempotency-Key", key)
	}
	if body != "" {
		r.Header.Set("Content-Type", "application/json")
	}
	w := httptest.NewRecorder()
	h.ServeHTTP(w, r)
	return w
}

func quoteWire(t *testing.T, r *http.Request, key []byte, expectedWorkspace, expectedPrincipal, expectedKey string) []byte {
	t.Helper()
	if r.Header.Get("Authorization") != "" {
		t.Fatal("browser bearer forwarded")
	}
	ctx, n := quoteHeader(r, "X-Quote-Context")
	if n != 1 {
		t.Fatal("missing singleton assertion")
	}
	sig, n := quoteHeader(r, "X-Quote-Signature")
	if n != 1 {
		t.Fatal("missing singleton signature")
	}
	b, e := base64.RawURLEncoding.DecodeString(ctx)
	if e != nil || len(b) > 2048 {
		t.Fatal("invalid assertion encoding")
	}
	mac := hmac.New(sha256.New, key)
	_, _ = mac.Write(b)
	signed, e := base64.RawURLEncoding.DecodeString(sig)
	if e != nil || !hmac.Equal(mac.Sum(nil), signed) {
		t.Fatal("bad signature")
	}
	v, e := quoteJSON(b, 4, 30)
	if e != nil {
		t.Fatal(e)
	}
	o, ok := quoteObject(v)
	if !ok || len(o) != 11 {
		t.Fatalf("assertion fields %#v", o)
	}
	if o["v"] != json.Number("1") || o["method"] != r.Method || o["raw_path"] != r.URL.Path || o["raw_query"] != r.URL.RawQuery || o["workspace_id"] != expectedWorkspace || o["principal_id"] != expectedPrincipal {
		t.Fatalf("assertion mismatch %#v", o)
	}
	if o["role"] != "editor" && o["role"] != "viewer" {
		t.Fatal("role missing")
	}
	if expectedKey == "" {
		if o["idempotency_key"] != nil || r.Header.Get("Idempotency-Key") != "" {
			t.Fatal("unexpected key")
		}
	} else if o["idempotency_key"] != expectedKey || r.Header.Get("Idempotency-Key") != expectedKey {
		t.Fatal("key not bound")
	}
	if nonce, ok := o["nonce"].(string); !ok || len(nonce) != 32 {
		t.Fatal("nonce entropy shape")
	}
	issued, ok := o["issued_at_unix"].(json.Number)
	if !ok {
		t.Fatal("issued time")
	}
	issuedUnix, e := issued.Int64()
	if e != nil || time.Since(time.Unix(issuedUnix, 0)) > 5*time.Second {
		t.Fatal("stale assertion")
	}
	body, e := io.ReadAll(r.Body)
	if e != nil {
		t.Fatal(e)
	}
	sum := sha256.Sum256(body)
	if o["body_sha256"] != hex.EncodeToString(sum[:]) {
		t.Fatal("body digest mismatch")
	}
	return body
}

func quoteOK(w http.ResponseWriter, workspace string, data string, status int) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_, _ = io.WriteString(w, `{"workspace_id":"`+workspace+`","data":`+data+`}`)
}

func TestQuoteAdapterSignedRoutesAndRoles(t *testing.T) {
	var calls atomic.Int32
	var key []byte
	handler := func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		expectedKey := ""
		if r.Method == "POST" && r.URL.Path != "/offers/extractions" {
			expectedKey = "operation-0001"
		}
		body := quoteWire(t, r, key, "w-a", "alice", expectedKey)
		switch r.URL.Path {
		case "/offers":
			if r.URL.RawQuery != "as_of=2026-10-07&status=saved" {
				t.Fatal(r.URL.RawQuery)
			}
			quoteOK(w, "w-a", `{"offers":[{"offer_id":"abc123","version":2,"status":"saved"}]}`, 200)
		case "/offers/extractions":
			if !bytes.Contains(body, []byte(`"mode":"rules"`)) {
				t.Fatal("wrong upload")
			}
			quoteOK(w, "w-a", `{"offer":{"offer_id":"abc123","version":1},"extraction_id":"ex123"}`, 201)
		case "/offers/abc123/corrections":
			quoteOK(w, "w-a", `{"offer_id":"abc123","version":2,"replayed":false}`, 200)
		case "/offers/abc123/save":
			quoteOK(w, "w-a", `{"offer_id":"abc123","version":3,"status":"saved"}`, 200)
		default:
			t.Fatalf("unexpected upstream %s", r.URL.Path)
		}
	}
	s, k := quoteFixture(t, handler)
	key = k
	for _, tc := range []struct {
		method, path, operation, body string
		status                        int
		field                         string
	}{
		{"GET", "/v2/offers?status=saved&as_of=2026-10-07", "", "", 200, "offers"},
		{"POST", "/v2/offers/extractions", "", `{"filename":"synthetic.csv","content_base64":"YSxiCg==","mode":"rules","synthetic":true}`, 201, "upload"},
		{"POST", "/v2/offers/abc123/corrections", "operation-0001", `{"field":"settlement_currency","value":"USD","expected_version":1}`, 200, "offer"},
		{"POST", "/v2/offers/abc123/save", "operation-0001", `{"expected_version":2}`, 200, "offer"},
	} {
		w := quoteCall(s, tc.method, tc.path, "editor-token", tc.operation, tc.body)
		if w.Code != tc.status || !strings.Contains(w.Body.String(), `"`+tc.field+`"`) || w.Header().Get("Cache-Control") != "no-store" {
			t.Fatalf("%s: %d %s", tc.path, w.Code, w.Body.String())
		}
	}
	before := calls.Load()
	for _, tc := range []struct {
		method, path, token, operation, body string
		code                                 int
	}{
		{"POST", "/v2/offers/abc123/save", "viewer-token", "operation-0001", `{"expected_version":2}`, 403},
		{"POST", "/v2/offers/abc123/save", "", "operation-0001", `{"expected_version":2}`, 401},
		{"GET", "/v2/offers?workspace_id=w-b", "editor-token", "", "", 422},
		{"POST", "/v2/offers/abc123/save", "editor-token", "operation-0001", `{"expected_version":2,"actor":"alice"}`, 422},
		{"POST", "/v2/offers/abc123/save", "editor-token", "operation-0001", `{"expected_version":2,"expected_version":3}`, 422},
		{"POST", "/v2/offers/abc123/save", "editor-token", "", "{" + `"expected_version":2}`, 422},
		{"POST", "/v2/offers/abc123/save", "editor-token", "operation-0001", `{"expected_version":2,"idempotency_key":"operation-0001"}`, 422},
		{"POST", "/v2/offers/abc123/reject", "editor-token", "operation-0001", `{"expected_version":2}`, 404},
	} {
		w := quoteCall(s, tc.method, tc.path, tc.token, tc.operation, tc.body)
		if w.Code != tc.code {
			t.Fatalf("rejection %s: %d %s", tc.path, w.Code, w.Body.String())
		}
	}
	if calls.Load() != before {
		t.Fatal("rejected request reached upstream")
	}
}

func TestQuoteAdapterHeadersCapsAndDisabled(t *testing.T) {
	var calls atomic.Int32
	s, _ := quoteFixture(t, func(w http.ResponseWriter, r *http.Request) { calls.Add(1); quoteOK(w, "w-a", `{"offers":[]}`, 200) })
	for _, modify := range []func(*http.Request){
		func(r *http.Request) { r.Header.Add("Authorization", "Bearer editor-token") },
		func(r *http.Request) { r.Header.Set("X-Quote-Context", "forged") },
		func(r *http.Request) { r.Header.Set("X-Actor", "alice") },
		func(r *http.Request) { r.Header.Add("Idempotency-Key", "duplicate") },
	} {
		r := httptest.NewRequest("GET", "/v2/offers", nil)
		r.RemoteAddr = "127.0.0.1:12345"
		r.Header.Set("Authorization", "Bearer editor-token")
		modify(r)
		w := httptest.NewRecorder()
		s.ServeHTTP(w, r)
		if w.Code != 422 {
			t.Fatalf("header rejection %d", w.Code)
		}
	}
	large := strings.Repeat(" ", quoteMutationLimit+1)
	w := quoteCall(s, "POST", "/v2/offers/a/save", "editor-token", "operation-0001", large)
	if w.Code != 413 {
		t.Fatalf("oversized mutation %d", w.Code)
	}
	w = quoteCall(s, "POST", "/v2/offers/extractions", "editor-token", "", strings.Repeat(" ", quoteUploadLimit+1))
	if w.Code != 413 {
		t.Fatalf("oversized upload %d", w.Code)
	}
	oversizedDocument := base64.StdEncoding.EncodeToString(make([]byte, quoteDocumentLimit+1))
	w = quoteCall(s, "POST", "/v2/offers/extractions", "editor-token", "", `{"filename":"synthetic.csv","content_base64":"`+oversizedDocument+`","mode":"rules","synthetic":true}`)
	if w.Code != 422 {
		t.Fatalf("decoded document limit %d", w.Code)
	}
	r := httptest.NewRequest("POST", "/v2/offers/a/save", strings.NewReader(`{"expected_version":1}`))
	r.RemoteAddr = "127.0.0.1:12345"
	r.Header.Set("Authorization", "Bearer editor-token")
	r.Header.Set("Idempotency-Key", "operation-0001")
	r.Header.Set("Content-Type", "application/json")
	r.ContentLength = quoteMutationLimit + 1
	w = httptest.NewRecorder()
	s.ServeHTTP(w, r)
	if w.Code != 413 {
		t.Fatalf("declared oversize %d", w.Code)
	}
	if calls.Load() != 0 {
		t.Fatal("rejected request reached upstream")
	}
	s.quote = nil
	w = quoteCall(s, "GET", "/v2/offers", "editor-token", "", "")
	if w.Code != 503 {
		t.Fatalf("ordinary local should be disabled: %d", w.Code)
	}
	s.cfg.Mode = "private_machine"
	w = quoteCall(s, "GET", "/v2/offers", "editor-token", "", "")
	if w.Code != 503 {
		t.Fatalf("private route enabled: %d", w.Code)
	}
}

func TestQuoteAdapterRejectsUnsafeUpstream(t *testing.T) {
	for _, tc := range []struct {
		name    string
		handler http.HandlerFunc
	}{
		{"wrong-workspace", func(w http.ResponseWriter, r *http.Request) { quoteOK(w, "w-b", `{"offers":[]}`, 200) }},
		{"malformed", func(w http.ResponseWriter, r *http.Request) {
			_, _ = io.WriteString(w, `{"workspace_id":"w-a","data":`)
		}},
		{"duplicate", func(w http.ResponseWriter, r *http.Request) {
			_, _ = io.WriteString(w, `{"workspace_id":"w-a","workspace_id":"w-a","data":{"offers":[]}}`)
		}},
		{"shape", func(w http.ResponseWriter, r *http.Request) { quoteOK(w, "w-a", `{"offers":"wrong"}`, 200) }},
		{"redirect", func(w http.ResponseWriter, r *http.Request) {
			w.Header().Set("Location", "/elsewhere")
			w.WriteHeader(302)
		}},
		{"oversize", func(w http.ResponseWriter, r *http.Request) {
			quoteOK(w, "w-a", `{"offers":[]}`, 200)
			_, _ = io.WriteString(w, strings.Repeat(" ", quoteResponseLimit))
		}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			s, _ := quoteFixture(t, tc.handler)
			w := quoteCall(s, "GET", "/v2/offers", "editor-token", "", "")
			if w.Code != 502 || strings.Contains(w.Body.String(), "w-b") {
				t.Fatalf("unsafe upstream %d %s", w.Code, w.Body.String())
			}
		})
	}
	t.Run("timeout", func(t *testing.T) {
		s, _ := quoteFixture(t, func(w http.ResponseWriter, r *http.Request) {})
		s.quote.client = &http.Client{Transport: quoteTransport(func(r *http.Request) (*http.Response, error) { <-r.Context().Done(); return nil, r.Context().Err() })}
		r := httptest.NewRequest("GET", "/v2/offers", nil)
		r.RemoteAddr = "127.0.0.1:12345"
		r.Header.Set("Authorization", "Bearer editor-token")
		ctx, cancel := context.WithTimeout(r.Context(), 20*time.Millisecond)
		defer cancel()
		w := httptest.NewRecorder()
		s.ServeHTTP(w, r.WithContext(ctx))
		if w.Code != 503 {
			t.Fatalf("timeout %d", w.Code)
		}
	})
}

func TestQuoteAdapterWorkspaceSourcingAndSafeError(t *testing.T) {
	var signedKey []byte
	s, key := quoteFixture(t, func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/offers/sourcing-candidates" {
			quoteWire(t, r, signedKey, "w-b", "eve", "")
			quoteOK(w, "w-b", `{"contract":"offers-sourcing-candidates/proposed-0.1","as_of":"2026-10-07","candidates":[],"excluded":[],"notes":[]}`, 200)
			return
		}
		quoteWire(t, r, signedKey, "w-b", "eve", "operation-0002")
		quoteOK(w, "w-b", `{"error":{"code":"not_found","message":"secret foreign offer details"}}`, 404)
	})
	signedKey = key
	w := quoteCall(s, "GET", "/v2/offers/sourcing-candidates?as_of=2026-10-07", "other-token", "", "")
	if w.Code != 200 || !strings.Contains(w.Body.String(), `"sourcing"`) {
		t.Fatalf("sourcing %d %s", w.Code, w.Body.String())
	}
	w = quoteCall(s, "POST", "/v2/offers/foreign/save", "other-token", "operation-0002", `{"expected_version":1}`)
	if w.Code != 404 || strings.Contains(w.Body.String(), "secret") {
		t.Fatalf("opaque error %d %s", w.Code, w.Body.String())
	}
}

func TestQuoteAdapterRejectsDuplicateQueryAndBadMoney(t *testing.T) {
	var calls atomic.Int32
	s, _ := quoteFixture(t, func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		quoteOK(w, "w-a", `{"offers":[{"offer_id":"x","version":1,"price":{"amount":12,"currency":"USD"}}]}`, 200)
	})
	for _, path := range []string{"/v2/offers?status=saved&status=saved", "/v2/offers?principal_id=alice", "/v2/offers?as_of=2026-02-30"} {
		w := quoteCall(s, "GET", path, "editor-token", "", "")
		if w.Code != 422 {
			t.Fatalf("query %s: %d", path, w.Code)
		}
	}
	if calls.Load() != 0 {
		t.Fatal("invalid query reached upstream")
	}
	w := quoteCall(s, "GET", "/v2/offers", "editor-token", "", "")
	if w.Code != 502 {
		t.Fatalf("numeric money accepted: %d", w.Code)
	}
}

func TestQuoteAdapterRejectsUnknownOfferResponseFields(t *testing.T) {
	for _, data := range []string{
		`{"offer_id":"x","version":1,"unexpected_private_text":"foreign workspace document"}`,
		`{"offer_id":"x","version":1,"document":{"document_id":"d","unexpected_private_text":"foreign workspace document"}}`,
	} {
		s, _ := quoteFixture(t, func(w http.ResponseWriter, r *http.Request) { quoteOK(w, "w-a", data, 200) })
		w := quoteCall(s, "GET", "/v2/offers/x", "editor-token", "", "")
		if w.Code != 502 || strings.Contains(w.Body.String(), "foreign workspace") {
			t.Fatalf("unsafe offer response: %d %s", w.Code, w.Body.String())
		}
	}
}

func TestQuoteAdapterRejectsNumericSourcingPricePaths(t *testing.T) {
	for _, price := range []string{
		`"price":{"type":"fixed","unit_price":12,"per_unit":"tonne","per_kg":{"amount":"1","currency":"USD"}}`,
		`"price":{"type":"fixed","unit_price":{"amount":"12","currency":"USD"},"per_unit":"tonne","per_kg":12}`,
		`"price":{"type":"fixed","unit_price":{"amount":"12","currency":"USD"},"per_unit":"tonne","per_kg":{"amount":"1","currency":"USD"}}`,
	} {
		landed := `"landed_price":{"status":"incoterm_price_only","basis":"quoted_price_on_incoterm","incoterm":"CIF","place":null,"includes":[],"not_included":[],"per_kg":{"amount":"1","currency":"USD"},"reporting":{"currency":"USD","status":"not_needed","per_kg":{"amount":"1","currency":"USD"},"fx":null}}`
		if strings.Contains(price, `"per_kg":{"amount":"1"`) && !strings.Contains(price, `"unit_price":12`) {
			landed = `"landed_price":{"status":"incoterm_price_only","basis":"quoted_price_on_incoterm","incoterm":"CIF","place":null,"includes":[],"not_included":[],"per_kg":{"amount":"1","currency":"USD"},"reporting":{"currency":"USD","status":"not_needed","per_kg":12,"fx":null}}`
		}
		data := `{"as_of":"2026-10-07","candidates":[{"candidate_id":"x@v1#item0","offer_id":"x","offer_version":1,` + price + `,` + landed + `}],"excluded":[]}`
		s, _ := quoteFixture(t, func(w http.ResponseWriter, r *http.Request) { quoteOK(w, "w-a", data, 200) })
		w := quoteCall(s, "GET", "/v2/offers/sourcing-candidates", "editor-token", "", "")
		if w.Code != 502 {
			t.Fatalf("numeric sourcing price passed: %d %s", w.Code, w.Body.String())
		}
	}
}

func TestQuoteAdapterRejectsPreparseQueryAndHeaderLimits(t *testing.T) {
	var calls atomic.Int32
	s, _ := quoteFixture(t, func(w http.ResponseWriter, r *http.Request) { calls.Add(1); quoteOK(w, "w-a", `{"offers":[]}`, 200) })
	for _, path := range []string{"/v2/offers?" + strings.Repeat("x", 4097), "/v2/offers?status=" + strings.Repeat("x", 4097)} {
		w := quoteCall(s, "GET", path, "editor-token", "", "")
		if w.Code != 422 {
			t.Fatalf("large query: %d", w.Code)
		}
	}
	r := httptest.NewRequest("GET", "/v2/offers", nil)
	r.RemoteAddr = "127.0.0.1:12345"
	r.Header.Set("Authorization", "Bearer "+strings.Repeat("x", 4097))
	w := httptest.NewRecorder()
	s.ServeHTTP(w, r)
	if w.Code != 422 {
		t.Fatalf("large auth header: %d", w.Code)
	}
	r = httptest.NewRequest("GET", "/v2/offers", nil)
	r.RemoteAddr = "127.0.0.1:12345"
	r.Header.Set("Authorization", "Bearer editor-token")
	r.Header.Set("X-Quote-Context", strings.Repeat("x", 4097))
	w = httptest.NewRecorder()
	s.ServeHTTP(w, r)
	if w.Code != 422 || calls.Load() != 0 {
		t.Fatalf("large spoof header: %d calls %d", w.Code, calls.Load())
	}
}

func TestQuoteAdapterPreservesKnownEvidenceAndDecimalShapes(t *testing.T) {
	offer := `{"offer_id":"x","status":"saved","version":3,"synthetic":true,"extraction_id":"e",` +
		`"document":{"document_id":"sha256:d","filename":"synthetic.csv","media_type":"text/csv","byte_size":30,"trust":"untrusted_document_content","metadata":{"delimiter":",","encoding":"utf-8","records":2},"warnings":[]},` +
		`"extractor":"rules","extractor_detail":{"requested":"rules","call":"none"},"as_of":"2026-10-07",` +
		`"fields":{"payment_terms":{"name":"payment_terms","kind":"text","status":"extracted","value":"30 days after shipment","critical":false,"source":"rules","reasons":[],` +
		`"evidence":[{"document_id":"sha256:d","locator":"r1:c1","quote":"30 days after shipment","text_sha256":"abc","segment_text":"30 days after shipment"}],` +
		`"candidates":[{"value":"30 days after shipment","verified":true,"problems":[],"evidence":[],"cited":[{"locator":"r1:c1","quote":"30 days after shipment"}]}]}},"items":[],"review_flags":[],` +
		`"normalized":{"as_of":"2026-10-07","reporting_currency":"USD","origin_country_code":"CL","expiry":"valid","min_order":{"original":{"amount":"10","unit":"tonne"},"kg":"10000","status":"ok"},"capacity":null,` +
		`"payment_terms":{"status":"parsed","text":"30 days after shipment","instalments":[{"percent":"100","percent_basis":"single_term","instrument":"not_stated","anchor":"shipment","days":30,"timing":"on","clause":"30 days after shipment"}],"unrecognised":[],"evidence":[],"reasons":[],"source":"rules"},"items":[]},` +
		`"blocking_reasons":[],"allowed_actions":[],"created_at":"2026-10-07T00:00:00","updated_at":"2026-10-07T00:00:00"}`
	s, _ := quoteFixture(t, func(w http.ResponseWriter, r *http.Request) { quoteOK(w, "w-a", offer, 200) })
	w := quoteCall(s, "GET", "/v2/offers/x", "editor-token", "", "")
	if w.Code != 200 || !strings.Contains(w.Body.String(), `"segment_text"`) {
		t.Fatalf("known evidence refused: %d %s", w.Code, w.Body.String())
	}
	sourcing := `{"contract":"offers-sourcing-candidates/proposed-0.1","as_of":"2026-10-07","synthetic":true,"notes":[],"excluded":[],"candidates":[` +
		`{"candidate_id":"x@v3#item0","offer_id":"x","offer_version":3,"offer_status":"saved","item_index":0,"synthetic":true,"document_id":"sha256:d",` +
		`"supplier":{"name":"Synthetic Supplier","origin_country":"Chile","origin_country_code":"CL"},"material":"copper","description":null,"grade":null,` +
		`"quantity":{"amount":"250","unit":"tonne","kg":"250000"},"min_order_kg":"25000","capacity":{"kg":"500000","period":"month"},"lead_time_days":45,` +
		`"incoterm":{"code":"CIF","place":"port"},"settlement_currency":"USD","price":{"type":"fixed","unit_price":{"amount":"9412.50","currency":"USD"},"per_unit":"tonne","per_kg":{"amount":"9.4125","currency":"USD"}},` +
		`"landed_price":{"status":"incoterm_price_only","basis":"quoted_price_on_incoterm","incoterm":"CIF","place":"port","includes":[],"not_included":[],"per_kg":{"amount":"9.4125","currency":"USD"},"reporting":{"currency":"USD","status":"not_needed","per_kg":{"amount":"9.4125","currency":"USD"},"fx":null}},` +
		`"validity":{"state":"valid","valid_until":"2026-10-31","days_left":24,"evidence":[{"locator":"r1:c2","quote":"2026-10-31"}]},` +
		`"payment_terms":{"status":"parsed","text":"30 days after shipment","instalments":[],"unrecognised":[],"evidence":[],"reasons":[],"source":"rules"},` +
		`"evidence":[{"field":"payment_terms","locator":"r1:c1","text_sha256":"abc","document_id":"sha256:d"}],"field_sources":{"payment_terms":{"status":"extracted","source":"rules"}}}]}`
	s, _ = quoteFixture(t, func(w http.ResponseWriter, r *http.Request) { quoteOK(w, "w-a", sourcing, 200) })
	w = quoteCall(s, "GET", "/v2/offers/sourcing-candidates", "editor-token", "", "")
	if w.Code != 200 || !strings.Contains(w.Body.String(), `"unit_price"`) {
		t.Fatalf("known sourcing refused: %d %s", w.Code, w.Body.String())
	}
}

func TestQuoteAdapterRejectsNullMoneyAndUnknownFieldSource(t *testing.T) {
	for _, data := range []string{
		`{"as_of":"2026-10-07","candidates":[{"candidate_id":"x@v1#item0","offer_id":"x","offer_version":1,"price":{"type":"fixed","unit_price":null,"per_unit":"tonne","per_kg":{"amount":"1","currency":"USD"}},"landed_price":{"status":"incoterm_price_only","basis":"quoted_price_on_incoterm","incoterm":"CIF","place":null,"includes":[],"not_included":[],"per_kg":null,"reporting":null}}],"excluded":[]}`,
		`{"as_of":"2026-10-07","candidates":[{"candidate_id":"x@v1#item0","offer_id":"x","offer_version":1,"price":{"type":"fixed","unit_price":{"amount":null,"currency":"USD"},"per_unit":"tonne","per_kg":{"amount":"1","currency":"USD"}},"landed_price":{}}],"excluded":[]}`,
		`{"as_of":"2026-10-07","candidates":[{"candidate_id":"x@v1#item0","offer_id":"x","offer_version":1,"price":{},"landed_price":{},"field_sources":{"unexpected_private_text":{"status":"extracted","source":"rules"}}}],"excluded":[]}`,
	} {
		s, _ := quoteFixture(t, func(w http.ResponseWriter, r *http.Request) { quoteOK(w, "w-a", data, 200) })
		w := quoteCall(s, "GET", "/v2/offers/sourcing-candidates", "editor-token", "", "")
		if w.Code != 502 {
			t.Fatalf("invalid nested sourcing response passed: %d %s", w.Code, w.Body.String())
		}
	}
}

func TestQuoteAdapterPreservesKnownExpiryAndPlanEvents(t *testing.T) {
	data := `{"offer_id":"x","version":3,"events":[` +
		`{"seq":1,"at":"2026-10-07T00:00:00","actor":"system","action":"expire","field":null,"old":null,"new":{"valid_until":"2026-10-06","checked_on":"2026-10-07"},"from_status":"saved","to_status":"expired"},` +
		`{"seq":2,"at":"2026-10-07T00:00:00","actor":"alice","action":"use_in_plan","field":null,"old":null,"new":{"version":3},"from_status":"saved","to_status":"used_in_plan"}]}`
	s, _ := quoteFixture(t, func(w http.ResponseWriter, r *http.Request) { quoteOK(w, "w-a", data, 200) })
	w := quoteCall(s, "GET", "/v2/offers/x?include_events=true", "editor-token", "", "")
	if w.Code != 200 || !strings.Contains(w.Body.String(), `"checked_on"`) {
		t.Fatalf("known audit events refused: %d %s", w.Code, w.Body.String())
	}
}

func TestQuoteAdapterPreservesIndexLinkedMoneyObjects(t *testing.T) {
	data := `{"as_of":"2026-10-07","excluded":[],"candidates":[{"candidate_id":"x@v1#item0","offer_id":"x","offer_version":1,` +
		`"price":{"type":"index_linked","index":"LME","averaging_period":"October","premium_per_kg":{"amount":"-0.5","currency":"USD"}},` +
		`"landed_price":{"status":"index_linked_final_price_unknown","basis":"quoted_price_on_incoterm","incoterm":"CIF","place":null,"includes":[],"not_included":[],"per_kg":null,` +
		`"reporting":{"currency":"USD","status":"not_needed","per_kg":null,"premium_per_kg":{"amount":"-0.5","currency":"USD"},"fx":null}}}],"notes":[]}`
	s, _ := quoteFixture(t, func(w http.ResponseWriter, r *http.Request) { quoteOK(w, "w-a", data, 200) })
	w := quoteCall(s, "GET", "/v2/offers/sourcing-candidates", "editor-token", "", "")
	if w.Code != 200 || !strings.Contains(w.Body.String(), `"premium_per_kg"`) {
		t.Fatalf("index-linked money refused: %d %s", w.Code, w.Body.String())
	}
}

func TestQuoteAdapterPreservesActualListSummary(t *testing.T) {
	data := `{"offers":[{"offer_id":"x","status":"saved","version":3,"synthetic":true,"filename":"synthetic.csv","supplier_name":"Synthetic Supplier","materials":["copper",null],"blocking_reasons":0,"updated_at":"2026-10-07T00:00:00"}]}`
	s, _ := quoteFixture(t, func(w http.ResponseWriter, r *http.Request) { quoteOK(w, "w-a", data, 200) })
	w := quoteCall(s, "GET", "/v2/offers", "editor-token", "", "")
	if w.Code != 200 || !strings.Contains(w.Body.String(), `"materials"`) {
		t.Fatalf("known list summary refused: %d %s", w.Code, w.Body.String())
	}
}

func TestQuoteAdapterPreservesExtractionSegments(t *testing.T) {
	data := `{"extraction_id":"e","extraction":{"document":{"document_id":"sha256:d","filename":"synthetic.csv","media_type":"text/csv","byte_size":30,"trust":"untrusted_document_content","metadata":{"delimiter":",","encoding":"utf-8","records":2},"warnings":[]},"extractor":"rules","extractor_detail":{"requested":"rules","call":"none"},"as_of":"2026-10-07","synthetic":true,"fields":{},"items":[],"review_flags":[],"normalized":{"as_of":"2026-10-07","reporting_currency":"USD","origin_country_code":"CL","expiry":"valid","min_order":null,"capacity":null,"payment_terms":null,"items":[]}},"segments":[{"locator":"r1:c1","text":"synthetic offer","text_sha256":"abc","flags":["visible"],"page":null,"line":null,"sheet":null,"cell":null,"row":1,"column":1}]}`
	s, _ := quoteFixture(t, func(w http.ResponseWriter, r *http.Request) { quoteOK(w, "w-a", data, 200) })
	w := quoteCall(s, "GET", "/v2/offers/extractions/e", "editor-token", "", "")
	if w.Code != 200 || !strings.Contains(w.Body.String(), `"segments"`) {
		t.Fatalf("known extraction evidence refused: %d %s", w.Code, w.Body.String())
	}
}

func TestQuoteAdapterRejectsWrongTypedResponseContainers(t *testing.T) {
	basePrice := `"price":{"type":"fixed","unit_price":{"amount":"12","currency":"USD"},"per_unit":"tonne","per_kg":{"amount":"0.012","currency":"USD"}}`
	baseLanded := `"landed_price":{"status":"incoterm_price_only","basis":"quoted_price_on_incoterm","incoterm":"CIF","place":null,"includes":[],"not_included":[],"per_kg":{"amount":"0.012","currency":"USD"},"reporting":{"currency":"USD","status":"not_needed","per_kg":{"amount":"0.012","currency":"USD"},"fx":null}}`
	sourcing := func(extra, price string) string {
		return `{"as_of":"2026-10-07","candidates":[{"candidate_id":"x@v1#item0","offer_id":"x","offer_version":1,` + extra + price + `,` + baseLanded + `}],"excluded":[]}`
	}
	cases := []struct{ name, route, data string }{
		{"sourcing_money_string", "/v2/offers/sourcing-candidates", sourcing(``, `"price":{"type":"fixed","unit_price":"12 USD","per_unit":"tonne","per_kg":{"amount":"0.012","currency":"USD"}}`)},
		{"sourcing_supplier_string", "/v2/offers/sourcing-candidates", sourcing(`"supplier":"private supplier",`, basePrice)},
		{"fixed_item_money_null", "/v2/offers/x", `{"offer_id":"x","version":1,"items":[{"pricing":{"name":"pricing","kind":"pricing","status":"extracted","value":{"type":"fixed","unit_price":null,"per_unit":"tonne"}}}]}`},
		{"quantity_field_string", "/v2/offers/x", `{"offer_id":"x","version":1,"fields":{"min_order":{"name":"min_order","kind":"quantity","status":"extracted","value":"4 tonnes"}}}`},
		{"document_string", "/v2/offers/x", `{"offer_id":"x","version":1,"document":"private document"}`},
		{"normalized_items_object", "/v2/offers/x", `{"offer_id":"x","version":1,"normalized":{"items":{}}}`},
		{"fields_array", "/v2/offers/x", `{"offer_id":"x","version":1,"fields":[]}`},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			s, _ := quoteFixture(t, func(w http.ResponseWriter, r *http.Request) { quoteOK(w, "w-a", tc.data, 200) })
			w := quoteCall(s, "GET", tc.route, "editor-token", "", "")
			if w.Code != 502 || strings.Contains(w.Body.String(), "private") {
				t.Fatalf("malformed upstream container passed: %d %s", w.Code, w.Body.String())
			}
		})
	}
}

func TestQuoteAdapterPreservesTypedNullableStates(t *testing.T) {
	data := `{"offer_id":"x","version":1,"fields":{"min_order":{"name":"min_order","kind":"quantity","status":"missing","value":null,"evidence":[],"candidates":[]}},"normalized":{"as_of":"2026-10-07","min_order":null,"capacity":null,"payment_terms":null,"items":[{"quantity":null,"pricing":null}]}}`
	s, _ := quoteFixture(t, func(w http.ResponseWriter, r *http.Request) { quoteOK(w, "w-a", data, 200) })
	w := quoteCall(s, "GET", "/v2/offers/x", "editor-token", "", "")
	if w.Code != 200 || !strings.Contains(w.Body.String(), `"payment_terms":null`) {
		t.Fatalf("legitimate nullable paths refused: %d %s", w.Code, w.Body.String())
	}
}

func TestQuoteAdapterRejectsFieldKindAndNormalizedPriceMismatch(t *testing.T) {
	field := func(name, kind, value string) string {
		entry := `{"name":"` + name + `","kind":"` + kind + `","status":"extracted","value":` + value + `}`
		if name == "pricing" {
			return `{"offer_id":"x","version":1,"items":[{"pricing":` + entry + `}]}`
		}
		return `{"offer_id":"x","version":1,"fields":{"` + name + `":` + entry + `}}`
	}
	normalized := func(original string) string {
		return `{"offer_id":"x","version":1,"normalized":{"items":[{"pricing":{"status":"ok","type":"fixed","original":` + original + `}}]}}`
	}
	cases := []struct{ name, data string }{
		{"quantity_as_incoterm", field("min_order", "quantity", `{"code":"FOB","place":"port"}`)},
		{"pricing_as_quantity", field("pricing", "pricing", `{"amount":"3","unit":"kg"}`)},
		{"integer_fraction", field("lead_time_days", "integer", `1.5`)},
		{"integer_exponent", field("lead_time_days", "integer", `1e2`)},
		{"integer_out_of_range", field("lead_time_days", "integer", `3651`)},
		{"unknown_field_kind", field("supplier_name", "secret_kind", `"secret"`)},
		{"ok_price_unknown_type", normalized(`{"type":"bogus"}`)},
		{"ok_price_scalar_original", normalized(`"bad"`)},
		{"ok_price_null_original", normalized(`null`)},
		{"ok_price_mismatched_type", normalized(`{"type":"index_linked","index":"LME","averaging_period":"M+1","premium":{"amount":"-1","currency":"USD"},"per_unit":"tonne"}`)},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			s, _ := quoteFixture(t, func(w http.ResponseWriter, r *http.Request) { quoteOK(w, "w-a", tc.data, 200) })
			w := quoteCall(s, "GET", "/v2/offers/x", "editor-token", "", "")
			if w.Code != 502 {
				t.Fatalf("kind/status mismatch passed: %d %s", w.Code, w.Body.String())
			}
		})
	}
}

func TestQuoteAdapterPreservesValidFieldKindsAndNormalizedPrices(t *testing.T) {
	cases := []struct{ name, data string }{
		{"quantity", `{"fields":{"min_order":{"name":"min_order","kind":"quantity","value":{"amount":"3","unit":"tonne"}}}}`},
		{"incoterm", `{"fields":{"incoterm":{"name":"incoterm","kind":"incoterm","value":{"code":"FOB","place":"port"}}}}`},
		{"shipments", `{"fields":{"shipments":{"name":"shipments","kind":"shipments","value":[{"quantity":{"amount":"3","unit":"tonne"},"window":"November"}]}}}`},
		{"integer", `{"fields":{"lead_time_days":{"name":"lead_time_days","kind":"integer","value":45}}}`},
		{"fixed", `{"items":[{"pricing":{"name":"pricing","kind":"pricing","value":{"type":"fixed","unit_price":{"amount":"3","currency":"USD"},"per_unit":"tonne"}}}]}`},
		{"index_linked", `{"items":[{"pricing":{"name":"pricing","kind":"pricing","value":{"type":"index_linked","index":"LME","averaging_period":"M+1","premium":{"amount":"-1","currency":"USD"},"per_unit":"tonne"}}}]}`},
		{"missing", `{"items":[{"pricing":{"name":"pricing","kind":"pricing","status":"missing","value":null}}]}`},
		{"failed_normalized_original", `{"normalized":{"items":[{"pricing":{"status":"invalid_amount","type":"fixed","original":"bad"}}]}}`},
		{"ok_fixed_unavailable_fx", `{"normalized":{"items":[{"pricing":{"status":"ok","type":"fixed","original":{"type":"fixed","unit_price":{"amount":"3","currency":"USD"},"per_unit":"tonne"},"per_kg":{"amount":"0.003","currency":"USD"},"reporting":{"currency":"INR","status":"unavailable","fx":null,"per_kg":null}}}]}}`},
		{"ok_index_unknown_fx", `{"normalized":{"items":[{"pricing":{"status":"ok","type":"index_linked","original":{"type":"index_linked","index":"LME","averaging_period":"M+1","premium":{"amount":"-1","currency":"USD"},"per_unit":"tonne"},"premium_per_kg":{"amount":"-0.001","currency":"USD"},"reporting":{"currency":"INR","status":"unavailable","fx":null,"premium_per_kg":null}}}]}}`},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			data := `{"offer_id":"x","version":1,` + strings.TrimPrefix(tc.data, "{")
			s, _ := quoteFixture(t, func(w http.ResponseWriter, r *http.Request) { quoteOK(w, "w-a", data, 200) })
			w := quoteCall(s, "GET", "/v2/offers/x", "editor-token", "", "")
			if w.Code != 200 {
				t.Fatalf("valid kind/status refused: %d %s", w.Code, w.Body.String())
			}
		})
	}
}

func TestQuoteAdapterSuccessContextBoundToAuthenticatedRequest(t *testing.T) {
	routes := []struct {
		name, method, path, operation, body, data, envelope string
		status                                              int
	}{
		{"list", "GET", "/v2/offers", "", "", `{"offers":[]}`, "offers", 200},
		{"detail", "GET", "/v2/offers/x", "", "", `{"offer_id":"x","version":1}`, "offer", 200},
		{"upload", "POST", "/v2/offers/extractions", "", `{"filename":"synthetic.csv","content_base64":"YSxiCg==","mode":"rules","synthetic":true}`, `{"offer":{"offer_id":"x","version":1},"extraction_id":"e"}`, "upload", 201},
		{"extraction", "GET", "/v2/offers/extractions/e", "", "", `{"extraction_id":"e","extraction":{},"segments":[]}`, "extraction", 200},
		{"correction", "POST", "/v2/offers/x/corrections", "operation-0001", `{"field":"settlement_currency","value":"USD","expected_version":1}`, `{"offer_id":"x","version":2,"replayed":false}`, "offer", 200},
		{"save", "POST", "/v2/offers/x/save", "operation-0001", `{"expected_version":1}`, `{"offer_id":"x","version":2,"status":"saved"}`, "offer", 200},
		{"sourcing", "GET", "/v2/offers/sourcing-candidates", "", "", `{"as_of":"2026-10-07","candidates":[],"excluded":[]}`, "sourcing", 200},
	}
	identities := []struct{ token, principal, workspace, role string }{
		{"editor-token", "alice", "w-a", "editor"},
		{"viewer-token", "bob", "w-a", "viewer"},
		{"other-token", "eve", "w-b", "editor"},
	}
	for _, identity := range identities {
		for _, route := range routes {
			if identity.role == "viewer" && route.method == "POST" {
				continue
			}
			t.Run(identity.principal+"/"+route.name, func(t *testing.T) {
				s, key := quoteFixture(t, nil)
				s.quote.client = &http.Client{Transport: quoteTransport(func(r *http.Request) (*http.Response, error) {
					quoteWire(t, r, key, identity.workspace, identity.principal, route.operation)
					w := httptest.NewRecorder()
					quoteOK(w, identity.workspace, route.data, route.status)
					return w.Result(), nil
				})}
				w := quoteCall(s, route.method, route.path, identity.token, route.operation, route.body)
				if w.Code != route.status {
					t.Fatalf("unexpected status %d: %s", w.Code, w.Body.String())
				}
				var response map[string]any
				if err := json.Unmarshal(w.Body.Bytes(), &response); err != nil {
					t.Fatal(err)
				}
				context, ok := response["context"].(map[string]any)
				if !ok || len(context) != 4 || context["principal_id"] != identity.principal || context["workspace_id"] != identity.workspace || context["role"] != identity.role || context["identity_mode"] != "development" {
					t.Fatalf("wrong authenticated response context: %#v", context)
				}
				if response["contract_version"] != stateVersion {
					t.Fatalf("contract version changed: %#v", response["contract_version"])
				}
				var expected any
				if err := json.Unmarshal([]byte(route.data), &expected); err != nil {
					t.Fatal(err)
				}
				if route.name == "list" {
					expected = expected.(map[string]any)["offers"]
				}
				if got, err := json.Marshal(response[route.envelope]); err != nil || string(got) != mustQuoteJSON(t, expected) {
					t.Fatalf("route data changed: %s, %v", got, err)
				}
			})
		}
	}
}

func mustQuoteJSON(t *testing.T, v any) string {
	t.Helper()
	b, err := json.Marshal(v)
	if err != nil {
		t.Fatal(err)
	}
	return string(b)
}

func TestQuoteAdapterRejectsUpstreamContextOverrideAndForeignWorkspace(t *testing.T) {
	for _, tc := range []struct{ name, wrapper string }{
		{"context_override", `{"workspace_id":"w-a","data":{"offers":[]},"context":{"principal_id":"eve","workspace_id":"w-b","role":"editor","identity_mode":"development"}}`},
		{"foreign_workspace", `{"workspace_id":"w-b","data":{"offers":[]}}`},
	} {
		t.Run(tc.name, func(t *testing.T) {
			s, _ := quoteFixture(t, func(w http.ResponseWriter, r *http.Request) {
				w.Header().Set("Content-Type", "application/json")
				_, _ = io.WriteString(w, tc.wrapper)
			})
			w := quoteCall(s, "GET", "/v2/offers", "editor-token", "", "")
			if w.Code != 502 || strings.Contains(w.Body.String(), `"context"`) {
				t.Fatalf("unsafe upstream wrapper accepted: %d %s", w.Code, w.Body.String())
			}
		})
	}
}
