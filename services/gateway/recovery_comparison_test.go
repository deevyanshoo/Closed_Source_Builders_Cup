package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"
)

func recoveryFixture(t *testing.T, name string) []byte {
	t.Helper()
	b, err := os.ReadFile(filepath.Join("../../contracts/examples", name))
	if err != nil {
		t.Fatal(err)
	}
	return b
}

func TestRecoveryComparisonUsesAuthenticatedUpstream(t *testing.T) {
	request := recoveryFixture(t, "recovery_request.json")
	up := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.Write(recoveryFixture(t, "recovery_response.json"))
	}))
	defer up.Close()
	s := &stateServer{cfg: stateConfig{Upstream: up.URL}, outbound: up.Client()}
	r := httptest.NewRequest("POST", "/v2/production/receipt-recovery/comparisons", bytes.NewReader(request))
	r.Header.Set("Content-Type", "application/json")
	r.RemoteAddr = "127.0.0.1:12345"
	w := httptest.NewRecorder()
	s.recoveryComparison(w, r)
	if w.Code != 401 {
		t.Fatalf("anonymous status %d, want 401", w.Code)
	}
}

func recoveryServer(t *testing.T, upstream http.HandlerFunc) (*stateServer, *atomic.Int32) {
	t.Helper()
	calls := &atomic.Int32{}
	up := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		upstream(w, r)
	}))
	t.Cleanup(up.Close)
	identities := filepath.Join(t.TempDir(), "identities.json")
	if err := os.WriteFile(identities, []byte(`{"identities":[{"token":"valid","principal_id":"p1","workspace_id":"W","role":"viewer"}]}`), 0600); err != nil {
		t.Fatal(err)
	}
	s := newLocalStateServer(stateConfig{IdentitiesFile: identities, Upstream: up.URL, AllowedOrigin: "http://localhost:5173"})
	return s, calls
}

func recoveryCall(s *stateServer, token, fixture string, alter func(*http.Request)) *httptest.ResponseRecorder {
	r := httptest.NewRequest("POST", recoveryPath, bytes.NewReader([]byte(fixture)))
	r.RemoteAddr = "127.0.0.1:12345"
	r.Header.Set("Content-Type", "application/json")
	if token != "" {
		r.Header.Set("Authorization", "Bearer "+token)
	}
	if alter != nil {
		alter(r)
	}
	w := httptest.NewRecorder()
	s.recoveryComparison(w, r)
	return w
}

func TestRecoveryComparisonValidatesAuthAndWorkspaceBeforeUpstream(t *testing.T) {
	s, calls := recoveryServer(t, func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.Write(recoveryFixture(t, "recovery_response.json"))
	})
	request := string(recoveryFixture(t, "recovery_request.json"))
	if w := recoveryCall(s, "", request, nil); w.Code != 401 {
		t.Fatalf("anonymous: %d", w.Code)
	}
	if w := recoveryCall(s, "valid", request, func(r *http.Request) { r.Header.Add("Authorization", "Bearer other") }); w.Code != 422 {
		t.Fatalf("duplicate auth: %d", w.Code)
	}
	if w := recoveryCall(s, "valid", request, func(r *http.Request) { r.Header.Set("X-Workspace-Id", "W-A") }); w.Code != 422 {
		t.Fatalf("spoofed workspace: %d", w.Code)
	}
	if w := recoveryCall(s, "valid", request, func(r *http.Request) { r.Header.Set("Origin", "https://attacker.test") }); w.Code != 403 {
		t.Fatalf("origin: %d", w.Code)
	}
	var requestObject map[string]any
	if err := json.Unmarshal([]byte(request), &requestObject); err != nil {
		t.Fatal(err)
	}
	for _, field := range []string{"snapshot", "scenario", "recovery"} {
		object := requestObject[field].(map[string]any)
		original := object["workspace_id"]
		object["workspace_id"] = "other-workspace"
		body, _ := json.Marshal(requestObject)
		if w := recoveryCall(s, "valid", string(body), nil); w.Code != 403 {
			t.Fatalf("%s workspace: %d", field, w.Code)
		}
		object["workspace_id"] = original
	}
	if calls.Load() != 0 {
		t.Fatalf("unauthorized requests reached upstream: %d", calls.Load())
	}
	w := recoveryCall(s, "valid", request, nil)
	if w.Code != 200 {
		t.Fatalf("valid status %d: %s", w.Code, w.Body.String())
	}
	if calls.Load() != 1 {
		t.Fatalf("upstream calls: %d", calls.Load())
	}
	if !bytes.Equal(bytes.TrimSpace(w.Body.Bytes()), bytes.TrimSpace(recoveryFixture(t, "recovery_response.json"))) {
		t.Fatal("validated upstream payload changed")
	}
}

func TestRecoveryComparisonRejectsMalformedWireBeforeUpstream(t *testing.T) {
	s, calls := recoveryServer(t, func(w http.ResponseWriter, r *http.Request) { t.Fatal("invalid request reached upstream") })
	request := string(recoveryFixture(t, "recovery_request.json"))
	for _, body := range []string{
		strings.Replace(request, `"contract_version": "0.2.0",`, `"contract_version": "0.2.0", "contract_version": "0.2.0",`, 1),
		strings.Replace(request, `"contract_version": "0.2.0",`, `"contract_version": "0.2.0", "unknown": true,`, 1),
		request + `{}`,
		`[[[[[[[[[[[[[[[[[0]]]]]]]]]]]]]]]]]`,
	} {
		w := recoveryCall(s, "valid", body, nil)
		if w.Code != 422 {
			t.Fatalf("invalid wire accepted: %d", w.Code)
		}
	}
	w := recoveryCall(s, "valid", request, func(r *http.Request) { r.Header.Set("Content-Type", "text/plain") })
	if w.Code != 422 {
		t.Fatalf("content type: %d", w.Code)
	}
	w = recoveryCall(s, "valid", strings.Repeat(" ", recoveryRequestLimit+1), nil)
	if w.Code != 413 {
		t.Fatalf("oversize request: %d", w.Code)
	}
	if calls.Load() != 0 {
		t.Fatalf("invalid wire reached upstream: %d", calls.Load())
	}
}

func TestRecoveryComparisonRejectsUpstreamScopeAndOrderMismatch(t *testing.T) {
	request := string(recoveryFixture(t, "recovery_request.json"))
	for _, mutate := range []func(map[string]any){
		func(v map[string]any) { v["comparison"].(map[string]any)["workspace_id"] = "other" },
		func(v map[string]any) { v["comparison"].(map[string]any)["as_of"] = "2026-10-10T00:00:00+00:00" },
		func(v map[string]any) {
			comparison := v["comparison"].(map[string]any)
			orders := comparison["recovery"].(map[string]any)["orders"].([]any)
			comparison["recovery"].(map[string]any)["orders"] = orders[:len(orders)-1]
		},
	} {
		mutate := mutate
		s, _ := recoveryServer(t, func(w http.ResponseWriter, r *http.Request) {
			var value map[string]any
			json.Unmarshal(recoveryFixture(t, "recovery_response.json"), &value)
			mutate(value)
			w.Header().Set("Content-Type", "application/json")
			json.NewEncoder(w).Encode(value)
		})
		w := recoveryCall(s, "valid", request, nil)
		if w.Code != 502 || !strings.Contains(w.Body.String(), "upstream_invalid_response") {
			t.Fatalf("invalid upstream: %d %s", w.Code, w.Body.String())
		}
	}
}

func TestRecoveryComparisonPrivateInjectedIdentityAndOutboundSource(t *testing.T) {
	var calls atomic.Int32
	up := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		if r.URL.Path != recoveryUpstreamPath || r.Header.Get("Authorization") != "Bearer server-token" ||
			r.Header.Get("X-Serverless-Authorization") != "Bearer server-token" ||
			r.Header.Get("X-Workspace-Id") != "" || r.Header.Get("Origin") != "" {
			t.Errorf("private upstream identity/path leakage")
		}
		w.Header().Set("Content-Type", "application/json")
		w.Write(recoveryFixture(t, "recovery_response.json"))
	}))
	defer up.Close()
	cfg := privateFixture(t)
	cfg.Upstream, cfg.PythonAudience = up.URL, up.URL
	if err := os.WriteFile(cfg.MachineIdentitiesFile, []byte(`{"principals":[{"subject":"subject-a","email":"runner@project.iam.gserviceaccount.com","principal_id":"machine-a","workspace_id":"W","role":"editor"}]}`), 0600); err != nil {
		t.Fatal(err)
	}
	// This test uses a synthetic verifier and token source. It does not prove
	// provider identity issuance or real service-account verification.
	auth, err := newMachineAuthenticator(cfg)
	if err != nil {
		t.Fatal(err)
	}
	s := &stateServer{cfg: cfg, auth: auth, outbound: tlsOutbound(t, up,
		testTokenSource(func(context.Context) (string, error) { return "server-token", nil }))}
	w := recoveryCall(s, "valid", string(recoveryFixture(t, "recovery_request.json")), nil)
	if w.Code != 200 {
		t.Fatalf("private response %d: %s", w.Code, w.Body.String())
	}
	if calls.Load() != 1 {
		t.Fatalf("private upstream calls %d", calls.Load())
	}
	missing := &stateServer{cfg: cfg, auth: auth, outbound: tlsOutbound(t, up,
		testTokenSource(func(context.Context) (string, error) { return "", errors.New("no token") }))}
	w = recoveryCall(missing, "valid", string(recoveryFixture(t, "recovery_request.json")), nil)
	if w.Code != 503 || calls.Load() != 1 {
		t.Fatalf("missing token failed open: %d calls=%d", w.Code, calls.Load())
	}
}

func TestRecoveryComparisonNoopPrecisionUnsupportedAndCanceled(t *testing.T) {
	for _, pair := range []struct {
		request, response string
		status            int
	}{
		{"recovery_noop_request.json", "recovery_noop_response.json", 200},
		{"recovery_precision_request.json", "recovery_precision_response.json", 200},
		{"recovery_unsupported_request.json", "recovery_unsupported_response.json", 422},
	} {
		pair := pair
		s, _ := recoveryServer(t, func(w http.ResponseWriter, r *http.Request) {
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(pair.status)
			w.Write(recoveryFixture(t, pair.response))
		})
		w := recoveryCall(s, "valid", string(recoveryFixture(t, pair.request)), nil)
		if w.Code != pair.status {
			t.Fatalf("%s status %d", pair.request, w.Code)
		}
	}
	s, calls := recoveryServer(t, func(w http.ResponseWriter, r *http.Request) { t.Error("canceled request reached upstream") })
	request := httptest.NewRequest("POST", recoveryPath, bytes.NewReader(recoveryFixture(t, "recovery_request.json")))
	request.RemoteAddr = "127.0.0.1:12345"
	request.Header.Set("Content-Type", "application/json")
	request.Header.Set("Authorization", "Bearer valid")
	ctx, cancel := context.WithCancel(request.Context())
	cancel()
	request = request.WithContext(ctx)
	w := httptest.NewRecorder()
	s.recoveryComparison(w, request)
	if w.Code == 200 || calls.Load() != 0 {
		t.Fatalf("canceled request succeeded: %d calls=%d", w.Code, calls.Load())
	}
}

func TestRecoveryComparisonRejectsOversizeRedirectAndMalformedUpstream(t *testing.T) {
	request := string(recoveryFixture(t, "recovery_request.json"))
	for _, response := range []struct {
		status            int
		contentType, body string
		want              int
	}{
		{503, "application/json", `{}`, 503},
		{302, "application/json", `{}`, 502},
		{200, "text/plain", `not-json`, 502},
		{200, "application/json", strings.Repeat(" ", recoveryResponseLimit+1), 502},
		{200, "application/json", `{"contract_version":"0.2.0","contract_version":"0.2.0"}`, 502},
	} {
		response := response
		s, calls := recoveryServer(t, func(w http.ResponseWriter, r *http.Request) {
			w.Header().Set("Content-Type", response.contentType)
			if response.status == 302 {
				w.Header().Set("Location", "http://127.0.0.1:1/collect")
			}
			w.WriteHeader(response.status)
			w.Write([]byte(response.body))
		})
		w := recoveryCall(s, "valid", request, nil)
		if w.Code != response.want || calls.Load() != 1 {
			t.Fatalf("upstream status %d mapped %d calls=%d", response.status, w.Code, calls.Load())
		}
		if strings.Contains(w.Body.String(), response.body) && len(response.body) > 10 {
			t.Fatal("raw upstream response leaked")
		}
	}
}
