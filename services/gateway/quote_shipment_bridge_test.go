package main

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"closedsource/gateway/internal/contract"
)

func bridgeFixtureFor(t *testing.T, expectedPrincipal string, source func(*http.Request) (string, int)) (*stateServer, *ContextMetadata) {
	t.Helper()
	var key []byte
	s, signed := quoteFixture(t, func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/offers/sourcing-candidates" || r.URL.RawQuery != "as_of=2026-10-10" || r.Method != "GET" {
			t.Fatalf("source route %s %s?%s", r.Method, r.URL.Path, r.URL.RawQuery)
		}
		if body := quoteWire(t, r, key, "w-a", expectedPrincipal, ""); len(body) != 0 {
			t.Fatal("signed source body")
		}
		data, status := source(r)
		quoteOK(w, "w-a", data, status)
	})
	key = signed
	s.quoteShipmentNow = func() time.Time { return time.Date(2026, 10, 10, 12, 0, 0, 0, time.UTC) }
	s.shipment = newShipmentAdapter(shipmentExecutorFunc(func(context.Context, []byte) ([]byte, error) { t.Fatal("executor called"); return nil, nil }))
	spec := shipmentTestSpec(t)
	spec.WorkspaceID = "w-a"
	var request map[string]any
	if err := json.Unmarshal(spec.Request, &request); err != nil {
		t.Fatal(err)
	}
	request["propagation_snapshot"].(map[string]any)["workspace_id"] = "w-a"
	request["production_snapshot"].(map[string]any)["workspace_id"] = "w-a"
	spec.Request, _ = shipmentMarshal(request)
	spec.CandidateBinding = &shipmentCandidateBinding{WorkspaceID: "w-a", CandidateID: "offer-a@v3#item0", OfferID: "offer-a", OfferVersion: 3, ItemIndex: 0, DocumentID: "sha256:" + strings.Repeat("a", 64)}
	meta, err := s.shipment.InstallHostContext(spec)
	if err != nil {
		t.Fatal(err)
	}
	return s, &meta
}

func bridgeFixture(t *testing.T, source func(*http.Request) (string, int)) (*stateServer, *ContextMetadata) {
	return bridgeFixtureFor(t, "alice", source)
}

func bridgeSource(candidate string) string {
	return `{"contract":"offers-sourcing-candidates/proposed-0.1","as_of":"2026-10-10","synthetic":true,"candidates":[` + candidate + `],"excluded":[],"notes":[]}`
}

func bridgeCandidate() string {
	return `{"candidate_id":"offer-a@v3#item0","offer_id":"offer-a","offer_version":3,"item_index":0,"document_id":"sha256:` + strings.Repeat("a", 64) + `","offer_status":"saved","synthetic":true,` +
		`"supplier":{"name":"Synthetic Supplier","origin_country":"Chile","origin_country_code":"CL"},"material":"copper","description":null,"grade":null,` +
		`"quantity":{"amount":"250","unit":"tonne","kg":"250000"},"min_order_kg":"25000","capacity":null,"lead_time_days":45,"incoterm":{"code":"CIF","place":"port"},"settlement_currency":"USD",` +
		`"price":{"type":"fixed","unit_price":{"amount":"9412.50","currency":"USD"},"per_unit":"tonne","per_kg":{"amount":"9.4125","currency":"USD"}},` +
		`"landed_price":{"status":"incoterm_price_only","basis":"quoted_price_on_incoterm","incoterm":"CIF","place":"Nhava Sheva","includes":[],"not_included":[],"per_kg":{"amount":"9.4125","currency":"USD"},"reporting":{"currency":"USD","status":"not_needed","per_kg":{"amount":"9.4125","currency":"USD"},"fx":null}},` +
		`"validity":{"state":"valid","valid_until":"2026-10-31","days_left":21,"evidence":[]},` +
		`"payment_terms":{"status":"parsed","text":"on shipment","instalments":[],"unrecognised":[],"evidence":[],"reasons":[],"source":"rules"},` +
		`"evidence":[{"field":"supplier_name","locator":"p1:l2","text_sha256":"` + strings.Repeat("b", 64) + `","document_id":"sha256:` + strings.Repeat("a", 64) + `"}],"field_sources":{}}`
}

func bridgePath() string {
	return "/v2/workspaces/w-a/shipment-recovery/quote-bridge?candidate_id=offer-a%40v3%23item0&offer_id=offer-a&offer_version=3&item_index=0&document_id=sha256%3A" + strings.Repeat("a", 64)
}

func TestQuoteShipmentBridgeSignedFreshRead(t *testing.T) {
	s, meta := bridgeFixture(t, func(*http.Request) (string, int) { return bridgeSource(bridgeCandidate()), 200 })
	w := quoteCall(s, "GET", bridgePath(), "editor-token", "", "")
	if w.Code != 200 {
		t.Fatalf("status=%d body=%s", w.Code, w.Body.String())
	}
	var result map[string]any
	if err := json.Unmarshal(w.Body.Bytes(), &result); err != nil {
		t.Fatal(err)
	}
	var typed contract.QuoteShipmentBridgeResponse
	if err := json.Unmarshal(w.Body.Bytes(), &typed); err != nil || typed.Bridge.ShipmentContext.ContextId != meta.ContextID {
		t.Fatalf("generated wire shape: %+v %v", typed, err)
	}
	ctx := result["context"].(map[string]any)
	if len(ctx) != 4 || ctx["principal_id"] != "alice" || ctx["workspace_id"] != "w-a" || ctx["identity_mode"] != "development" {
		t.Fatalf("context=%v", ctx)
	}
	bridge := result["bridge"].(map[string]any)
	if bridge["source_as_of"] != "2026-10-10" || bridge["context_as_of"] != "2026-10-09T00:00:00Z" || bridge["host_binding"] != "host_configured_synthetic" || bridge["source_authenticity"] != "not_verified" {
		t.Fatalf("bridge=%v", bridge)
	}
	shipment := bridge["shipment_context"].(map[string]any)
	if shipment["context_id"] != meta.ContextID || shipment["revision"] != float64(1) || shipment["request_sha256"] != meta.RequestSHA256 {
		t.Fatalf("shipment=%v", shipment)
	}
	if strings.Contains(w.Body.String(), "9412.50") || strings.Contains(w.Body.String(), "Nhava Sheva") {
		t.Fatal("raw source leaked")
	}
}

func TestQuoteShipmentBridgeSourceAndBindingFailures(t *testing.T) {
	for name, source := range map[string]func() (string, int){
		"missing": func() (string, int) { return bridgeSource(""), 200 },
		"expired": func() (string, int) {
			return bridgeSource(strings.Replace(bridgeCandidate(), "2026-10-31", "2026-10-09", 1)), 200
		},
		"upstream": func() (string, int) { return `{"code":"unavailable"}`, 503 },
		"bad_as_of": func() (string, int) {
			return strings.Replace(bridgeSource(bridgeCandidate()), "2026-10-10", "2026-10-09", 1), 200
		},
		"missing_contract": func() (string, int) {
			return strings.Replace(bridgeSource(bridgeCandidate()), `"contract":"offers-sourcing-candidates/proposed-0.1",`, "", 1), 200
		},
		"unstated": func() (string, int) {
			return bridgeSource(strings.Replace(bridgeCandidate(), `"state":"valid"`, `"state":"not_stated"`, 1)), 200
		},
		"incomplete": func() (string, int) {
			return bridgeSource(strings.Replace(bridgeCandidate(), `"material":"copper",`, "", 1)), 200
		},
		"duplicate": func() (string, int) {
			c := bridgeCandidate()
			return strings.Replace(bridgeSource(c), c, c+","+c, 1), 200
		},
	} {
		t.Run(name, func(t *testing.T) {
			s, _ := bridgeFixture(t, func(*http.Request) (string, int) { return source() })
			w := quoteCall(s, "GET", bridgePath(), "editor-token", "", "")
			expected := 404
			if name == "upstream" || name == "bad_as_of" || name == "missing_contract" {
				expected = 503
			}
			if w.Code != expected {
				t.Fatalf("status=%d body=%s", w.Code, w.Body.String())
			}
			if !strings.Contains(w.Body.String(), "Quote bridge unavailable") || strings.Contains(w.Body.String(), "offer-a") {
				t.Fatalf("unsafe body=%s", w.Body.String())
			}
		})
	}
}

func TestQuoteShipmentBridgeSignedSourceFailureUniform(t *testing.T) {
	var baseline string
	for _, kind := range []string{"non200", "wrong_workspace", "malformed", "content_type", "redirect", "oversize", "timeout"} {
		t.Run(kind, func(t *testing.T) {
			s, _ := bridgeFixture(t, func(*http.Request) (string, int) { t.Fatal("original source used"); return "", 500 })
			key := s.quote.key
			s.quote.client = &http.Client{Transport: quoteTransport(func(r *http.Request) (*http.Response, error) {
				quoteWire(t, r, key, "w-a", "alice", "")
				if kind == "timeout" {
					return nil, errors.New("synthetic timeout")
				}
				status := 200
				contentType := "application/json"
				body := `{"workspace_id":"w-a","data":` + bridgeSource(bridgeCandidate()) + `}`
				length := int64(len(body))
				switch kind {
				case "non200":
					status = 401
				case "wrong_workspace":
					body = strings.Replace(body, `"workspace_id":"w-a"`, `"workspace_id":"w-b"`, 1)
				case "malformed":
					body = `{"workspace_id":"w-a","data":`
				case "content_type":
					contentType = "text/plain"
				case "redirect":
					status = 302
				case "oversize":
					length = quoteResponseLimit + 1
				}
				return &http.Response{StatusCode: status, Header: http.Header{"Content-Type": []string{contentType}}, Body: io.NopCloser(strings.NewReader(body)), ContentLength: length, Request: r}, nil
			})}
			w := quoteCall(s, "GET", bridgePath(), "editor-token", "", "")
			if w.Code != 503 {
				t.Fatalf("status=%d body=%s", w.Code, w.Body.String())
			}
			if baseline == "" {
				baseline = w.Body.String()
			} else if w.Body.String() != baseline {
				t.Fatalf("distinct error %s", w.Body.String())
			}
		})
	}
}

func TestQuoteShipmentBridgeViewerRolloverAndReplacement(t *testing.T) {
	s, _ := bridgeFixtureFor(t, "bob", func(*http.Request) (string, int) {
		return bridgeSource(strings.Replace(bridgeCandidate(), `"offer_status":"saved"`, `"offer_status":"used_in_plan"`, 1)), 200
	})
	w := quoteCall(s, "GET", bridgePath(), "viewer-token", "", "")
	if w.Code != 200 || !strings.Contains(w.Body.String(), `"role":"viewer"`) {
		t.Fatalf("viewer %d %s", w.Code, w.Body.String())
	}

	var reads int
	s.quoteShipmentNow = func() time.Time {
		reads++
		if reads > 1 {
			return time.Date(2026, 10, 11, 0, 0, 0, 0, time.UTC)
		}
		return time.Date(2026, 10, 10, 23, 59, 59, 0, time.UTC)
	}
	w = quoteCall(s, "GET", bridgePath(), "viewer-token", "", "")
	if w.Code != 503 || !strings.Contains(w.Body.String(), `"code":"service_unavailable"`) {
		t.Fatalf("rollover %d %s", w.Code, w.Body.String())
	}

	var owner *stateServer
	var contextID string
	owner, meta := bridgeFixture(t, func(*http.Request) (string, int) {
		owner.shipment.mu.Lock()
		owner.shipment.contexts["w-a"][contextID].metadata.Revision++
		owner.shipment.mu.Unlock()
		return bridgeSource(bridgeCandidate()), 200
	})
	contextID = meta.ContextID
	w = quoteCall(owner, "GET", bridgePath(), "editor-token", "", "")
	if w.Code != 404 || !strings.Contains(w.Body.String(), `"code":"unavailable"`) {
		t.Fatalf("replacement %d %s", w.Code, w.Body.String())
	}
}

func TestQuoteShipmentBridgeRequestBoundary(t *testing.T) {
	s, _ := bridgeFixture(t, func(*http.Request) (string, int) { t.Fatal("invalid request reached upstream"); return "", 500 })
	for name, path := range map[string]string{
		"foreign_workspace": strings.Replace(bridgePath(), "/w-a/", "/w-b/", 1),
		"old_version":       strings.Replace(bridgePath(), "offer_version=3", "offer_version=2", 1),
		"other_item":        strings.Replace(strings.Replace(bridgePath(), "item0", "item1", 1), "item_index=0", "item_index=1", 1),
		"old_document":      strings.Replace(bridgePath(), strings.Repeat("a", 64), strings.Repeat("b", 64), 1),
		"duplicate":         bridgePath() + "&offer_id=offer-a",
		"extra":             bridgePath() + "&as_of=2026-10-10",
		"oversize":          bridgePath() + "&padding=" + strings.Repeat("x", quoteQueryLimit),
	} {
		t.Run(name, func(t *testing.T) {
			w := quoteCall(s, "GET", path, "editor-token", "", "")
			want := 422
			if name == "foreign_workspace" || name == "old_document" || name == "other_item" {
				want = 404
			}
			if w.Code != want {
				t.Fatalf("status=%d body=%s", w.Code, w.Body.String())
			}
		})
	}
	for name, change := range map[string]func(*http.Request){
		"forged_identity": func(r *http.Request) { r.Header.Set("X-Workspace-Id", "w-a") },
		"wrong_origin":    func(r *http.Request) { r.Header.Set("Origin", "https://foreign.invalid") },
		"nonloopback":     func(r *http.Request) { r.RemoteAddr = "198.51.100.1:1234" },
		"oversize_header": func(r *http.Request) { r.Header.Set("X-Test", strings.Repeat("x", quoteHeaderLimit+1)) },
	} {
		t.Run(name, func(t *testing.T) {
			r := httptest.NewRequest("GET", bridgePath(), nil)
			r.RemoteAddr = "127.0.0.1:1234"
			r.Header.Set("Authorization", "Bearer editor-token")
			change(r)
			w := httptest.NewRecorder()
			s.ServeHTTP(w, r)
			if w.Code == 200 {
				t.Fatal("accepted unsafe request")
			}
		})
	}
	if w := quoteCall(s, "POST", bridgePath(), "editor-token", "", ""); w.Code != 405 {
		t.Fatalf("mutation=%d", w.Code)
	}
	if w := quoteCall(s, "GET", bridgePath(), "", "", ""); w.Code != 401 {
		t.Fatalf("unauthenticated=%d", w.Code)
	}
	other := *s
	other.quote = nil
	if w := quoteCall(&other, "GET", bridgePath(), "editor-token", "", ""); w.Code != 503 {
		t.Fatalf("disabled=%d", w.Code)
	}
	other = *s
	other.cfg.Mode = "private_machine"
	if w := quoteCall(&other, "GET", bridgePath(), "editor-token", "", ""); w.Code != 503 {
		t.Fatalf("private=%d", w.Code)
	}
}

func TestQuoteShipmentBridgePreservesPublicQuoteRoutes(t *testing.T) {
	var key []byte
	s, k := quoteFixture(t, func(w http.ResponseWriter, r *http.Request) {
		expected := ""
		if r.Method == "POST" {
			expected = "operation-0001"
		}
		quoteWire(t, r, key, "w-a", "alice", expected)
		if r.URL.Path == "/offers" {
			quoteOK(w, "w-a", `{"offers":[{"offer_id":"abc123","version":2,"status":"saved"}]}`, 200)
			return
		}
		if r.URL.Path == "/offers/abc123/save" {
			quoteOK(w, "w-a", `{"offer_id":"abc123","version":3,"status":"saved"}`, 200)
			return
		}
		t.Fatal("unexpected public route")
	})
	key = k
	for _, tc := range []struct {
		method, path, operation, body string
		want                          int
	}{
		{"GET", "/v2/offers?status=saved&as_of=2026-10-07", "", "", 200},
		{"POST", "/v2/offers/abc123/save", "operation-0001", `{"expected_version":2}`, 200},
		{"GET", "/v2/offers?workspace_id=w-b", "", "", 422},
		{"POST", "/v2/offers/abc123/save", "", `{"expected_version":2}`, 422},
	} {
		w := quoteCall(s, tc.method, tc.path, "editor-token", tc.operation, tc.body)
		if w.Code != tc.want || w.Header().Get("Cache-Control") != "no-store" {
			t.Fatalf("public %s=%d %s", tc.path, w.Code, w.Body.String())
		}
	}
}

func TestQuoteShipmentBridgePublicationHoldsCurrentAssociationThroughWrite(t *testing.T) {
	for _, mode := range []string{"replace", "shutdown"} {
		t.Run(mode, func(t *testing.T) {
			s, meta := bridgeFixture(t, func(*http.Request) (string, int) { return bridgeSource(bridgeCandidate()), 200 })
			spec := shipmentTestSpec(t)
			spec.WorkspaceID = "w-a"
			var request map[string]any
			if err := json.Unmarshal(spec.Request, &request); err != nil {
				t.Fatal(err)
			}
			request["propagation_snapshot"].(map[string]any)["workspace_id"] = "w-a"
			request["production_snapshot"].(map[string]any)["workspace_id"] = "w-a"
			spec.Request, _ = shipmentMarshal(request)
			r := httptest.NewRequest(http.MethodGet, bridgePath(), nil)
			r.RemoteAddr = "127.0.0.1:12345"
			r.Header.Set("Authorization", "Bearer editor-token")
			writer := &shipmentBlockingWriter{ResponseRecorder: httptest.NewRecorder(), entered: make(chan struct{}), release: make(chan struct{})}
			defer func() {
				select {
				case <-writer.release:
				default:
					close(writer.release)
				}
			}()
			done := make(chan struct{})
			go func() { s.ServeHTTP(writer, r); close(done) }()
			<-writer.entered
			lockHeld := !s.shipment.mu.TryLock()
			if !lockHeld {
				s.shipment.mu.Unlock()
			}
			operation := make(chan error, 1)
			started := make(chan struct{})
			go func() {
				close(started)
				if mode == "replace" {
					_, err := s.shipment.ReplaceHostContext("w-a", meta.ContextID, meta.Revision, spec)
					operation <- err
					return
				}
				ctx, cancel := context.WithCancel(context.Background())
				defer cancel()
				operation <- s.shipment.Shutdown(ctx)
			}()
			<-started
			if !lockHeld {
				<-operation
			}
			close(writer.release)
			<-done
			if lockHeld {
				if err := <-operation; err != nil {
					t.Fatal(err)
				}
			}
			if !lockHeld {
				t.Fatal("host revision lock was released during success publication")
			}
			if writer.Code != 200 {
				t.Fatalf("published status=%d body=%s", writer.Code, writer.Body.String())
			}
		})
	}
}

func TestQuoteShipmentBridgeOversizeSuccessFailsOpaque(t *testing.T) {
	candidate := map[string]any{}
	if err := json.Unmarshal([]byte(bridgeCandidate()), &candidate); err != nil {
		t.Fatal(err)
	}
	refs := make([]any, 64)
	for i := range refs {
		refs[i] = map[string]any{"field": "supplier_name", "locator": strings.Repeat("😀", 256), "text_sha256": strings.Repeat("b", 64), "document_id": "sha256:" + strings.Repeat("a", 64)}
	}
	candidate["evidence"] = refs
	raw, err := json.Marshal(candidate)
	if err != nil {
		t.Fatal(err)
	}
	s, _ := bridgeFixture(t, func(*http.Request) (string, int) { return bridgeSource(string(raw)), 200 })
	w := quoteCall(s, http.MethodGet, bridgePath(), "editor-token", "", "")
	if w.Code != 503 || !strings.Contains(w.Body.String(), `"code":"service_unavailable"`) || strings.Contains(w.Body.String(), "supplier_name") {
		t.Fatalf("oversize status=%d body=%s", w.Code, w.Body.String())
	}
}

func TestQuoteShipmentBridgePrepublicationCancellationFailsOpaque(t *testing.T) {
	s, _ := bridgeFixture(t, func(*http.Request) (string, int) { return bridgeSource(bridgeCandidate()), 200 })
	r := httptest.NewRequest(http.MethodGet, bridgePath(), nil)
	r.RemoteAddr = "127.0.0.1:12345"
	r.Header.Set("Authorization", "Bearer editor-token")
	ctx, cancel := context.WithCancel(r.Context())
	defer cancel()
	r = r.WithContext(ctx)
	checks := 0
	s.quoteShipmentNow = func() time.Time {
		checks++
		if checks == 2 {
			cancel()
		}
		return time.Date(2026, 10, 10, 12, 0, 0, 0, time.UTC)
	}
	w := httptest.NewRecorder()
	s.ServeHTTP(w, r)
	if w.Code != 503 || !strings.Contains(w.Body.String(), `"code":"service_unavailable"`) {
		t.Fatalf("cancel status=%d body=%s", w.Code, w.Body.String())
	}
}
