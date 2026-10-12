package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

type dispatchUpstreamCall struct{ method, path, query string }

func dispatchFixture(t *testing.T, reply func(*http.Request) (int, string, string)) (*stateServer, *[]dispatchUpstreamCall) {
	t.Helper()
	identities := filepath.Join(t.TempDir(), "identities.json")
	body := `{"identities":[{"token":"editor-token","principal_id":"alice","workspace_id":"w-a","role":"editor"},` +
		`{"token":"viewer-token","principal_id":"bob","workspace_id":"w-a","role":"viewer"},` +
		`{"token":"other-token","principal_id":"carol","workspace_id":"w-b","role":"editor"}]}`
	if err := os.WriteFile(identities, []byte(body), 0o600); err != nil {
		t.Fatal(err)
	}
	cfg, rest, err := withDispatchLocalFlags(stateConfig{Mode: "local", IdentitiesFile: identities, AllowedOrigin: "http://localhost:5173"},
		[]string{"-dispatch-local-upstream", "http://127.0.0.1:8016"})
	if err != nil || len(rest) != 0 {
		t.Fatalf("flags: %v %v", rest, err)
	}
	s := newLocalStateServer(cfg)
	if s.dispatch == nil {
		t.Fatal("dispatch connector not configured")
	}
	calls := &[]dispatchUpstreamCall{}
	s.dispatch.client = &http.Client{Transport: quoteTransport(func(r *http.Request) (*http.Response, error) {
		*calls = append(*calls, dispatchUpstreamCall{r.Method, r.URL.Path, r.URL.RawQuery})
		status, contentType, payload := reply(r)
		w := httptest.NewRecorder()
		if contentType != "" {
			w.Header().Set("Content-Type", contentType)
		}
		w.WriteHeader(status)
		_, _ = w.Write([]byte(payload))
		return w.Result(), nil
	})}
	return s, calls
}

func dispatchGet(h http.Handler, method, target, token string) *httptest.ResponseRecorder {
	r := httptest.NewRequest(method, target, nil)
	r.RemoteAddr = "127.0.0.1:12345"
	if token != "" {
		r.Header.Set("Authorization", "Bearer "+token)
	}
	w := httptest.NewRecorder()
	h.ServeHTTP(w, r)
	return w
}

func okReply(*http.Request) (int, string, string) {
	return 200, "application/json", `{"status":"ok","reasons":[],"synthetic":true,"shipments":[{"shipment_id":"SHP-SYN-002","quantity":"3000.000"}]}`
}

func TestDispatchConnectorIsOffByDefaultAndLocalOnly(t *testing.T) {
	cfg, rest, err := withDispatchLocalFlags(stateConfig{Mode: "local"}, nil)
	if err != nil || len(rest) != 0 || initDispatchHTTP(cfg) != nil || newLocalStateServer(cfg).dispatch != nil {
		t.Fatalf("default connector activated: %v %v", rest, err)
	}
	for _, args := range [][]string{
		{"-dispatch-local-upstream", "http://example.com:8016"},
		{"-dispatch-local-upstream", "https://127.0.0.1:8016/path"},
		{"-dispatch-local-upstream", "http://127.0.0.1:8016", "-dispatch-local-upstream", "http://127.0.0.1:8016"},
	} {
		if _, _, err := withDispatchLocalFlags(stateConfig{Mode: "local"}, args); err == nil {
			t.Fatalf("unsafe flags accepted: %v", args)
		}
	}
	if _, _, err := withDispatchLocalFlags(stateConfig{Mode: "private_machine"}, []string{"-dispatch-local-upstream", "http://127.0.0.1:8016"}); err == nil {
		t.Fatal("private machine accepted")
	}
	_, rest, err = withDispatchLocalFlags(stateConfig{Mode: "local"}, []string{"-voice-local-python", "/x"})
	if err != nil || len(rest) != 2 {
		t.Fatalf("other flags not passed through: %v %v", rest, err)
	}
	s := newLocalStateServer(stateConfig{Mode: "local"})
	if got := dispatchGet(s, http.MethodGet, "/v2/workspaces/w-a/dispatch/shipments", "editor-token"); got.Code != 503 {
		t.Fatalf("disabled connector status %d", got.Code)
	}
}

func TestDispatchRoutesProxyReadModelsForTheSessionWorkspace(t *testing.T) {
	s, calls := dispatchFixture(t, okReply)
	cases := []struct{ target, upstream, query string }{
		{"/v2/workspaces/w-a/dispatch/shipments?as_of=2026-10-08T12:00:00Z", "/dispatch/shipments", "as_of=2026-10-08T12%3A00%3A00Z"},
		{"/v2/workspaces/w-a/dispatch/shipments/SHP-SYN-002", "/dispatch/shipments/SHP-SYN-002", ""},
		{"/v2/workspaces/w-a/dispatch/events/evt-002-hold/affected?now=2026-10-08T12:00:00Z", "/dispatch/events/evt-002-hold/affected", "now=2026-10-08T12%3A00%3A00Z"},
		{"/v2/workspaces/w-a/dispatch/route-evidence?location=INNSA&location=INMAA", "/dispatch/route-evidence", "location=INNSA&location=INMAA"},
		{"/v2/workspaces/w-a/dispatch/replay/samples", "/dispatch/replay/samples", ""},
		{"/v2/workspaces/w-a/dispatch/replay?sample=innsa-release", "/dispatch/replay", "sample=innsa-release"},
		{"/v2/workspaces/w-a/dispatch/hazards?location=INNSA", "/dispatch/hazards", "location=INNSA"},
	}
	for i, c := range cases {
		token := "editor-token"
		if i%2 == 1 {
			token = "viewer-token"
		}
		got := dispatchGet(s, http.MethodGet, c.target, token)
		if got.Code != 200 || got.Header().Get("Cache-Control") != "no-store" {
			t.Fatalf("%s: %d %s", c.target, got.Code, got.Body.String())
		}
		var wrapper map[string]json.RawMessage
		if err := json.Unmarshal(got.Body.Bytes(), &wrapper); err != nil || string(wrapper["workspace_id"]) != `"w-a"` ||
			string(wrapper["synthetic"]) != "true" || string(wrapper["source"]) != `"local_python_service"` ||
			string(wrapper["contract_version"]) != `"0.2.0"` || !strings.Contains(string(wrapper["data"]), `"quantity":"3000.000"`) {
			t.Fatalf("%s wrapper %s", c.target, got.Body.String())
		}
		last := (*calls)[len(*calls)-1]
		if last.method != http.MethodGet || last.path != c.upstream || last.query != c.query {
			t.Fatalf("%s upstream %+v", c.target, last)
		}
	}
}

func TestDispatchRoutesRefuseBeforeCallingUpstream(t *testing.T) {
	s, calls := dispatchFixture(t, okReply)
	for _, c := range []struct {
		method, target, token string
		status                int
	}{
		{http.MethodGet, "/v2/workspaces/w-a/dispatch/shipments", "", 401},
		{http.MethodGet, "/v2/workspaces/w-a/dispatch/shipments", "other-token", 404},
		{http.MethodPost, "/v2/workspaces/w-a/dispatch/shipments", "editor-token", 405},
		{http.MethodGet, "/v2/workspaces/w-a/dispatch/unknown", "editor-token", 404},
		{http.MethodGet, "/v2/workspaces/w-a/dispatch/shipments/a/b", "editor-token", 404},
		{http.MethodGet, "/v2/workspaces/w-a/dispatch/shipments?sample=x", "editor-token", 422},
		{http.MethodGet, "/v2/workspaces/w-a/dispatch/shipments?as_of=a&as_of=b", "editor-token", 422},
		{http.MethodGet, "/v2/workspaces/w-a/dispatch/shipments/..%2Fsecret", "editor-token", 422},
		{http.MethodGet, "/v2/workspaces/w-a/dispatch/hazards?location=" + strings.Repeat("A", 200), "editor-token", 422},
	} {
		if got := dispatchGet(s, c.method, c.target, c.token); got.Code != c.status {
			t.Fatalf("%s %s: %d %s", c.method, c.target, got.Code, got.Body.String())
		}
	}
	if len(*calls) != 0 {
		t.Fatalf("upstream called for refused requests: %+v", *calls)
	}
}

func TestDispatchRoutesRejectUnlabelledOrInvalidUpstreamAnswers(t *testing.T) {
	for _, c := range []struct {
		name                  string
		status                int
		contentType, payload  string
		want                  int
		passThroughStatusText string
	}{
		{"not json", 200, "application/json", "<html>", 502, ""},
		{"wrong type", 200, "text/html", `{"synthetic":true}`, 502, ""},
		{"not labelled synthetic", 200, "application/json", `{"status":"ok"}`, 502, ""},
		{"synthetic false", 200, "application/json", `{"status":"ok","synthetic":false}`, 502, ""},
		{"server error", 500, "application/json", `{"status":"error","synthetic":true}`, 502, ""},
		{"redirect", 302, "", "", 502, ""},
		{"unknown shipment", 404, "application/json", `{"status":"error","reasons":["unknown_shipment"],"synthetic":true}`, 404, "unknown_shipment"},
		{"bad parameters", 400, "application/json", `{"status":"error","reasons":["invalid_as_of"],"synthetic":true}`, 422, "invalid_as_of"},
	} {
		s, _ := dispatchFixture(t, func(*http.Request) (int, string, string) { return c.status, c.contentType, c.payload })
		got := dispatchGet(s, http.MethodGet, "/v2/workspaces/w-a/dispatch/shipments/SHP-SYN-002", "editor-token")
		if got.Code != c.want || (c.passThroughStatusText != "" && !strings.Contains(got.Body.String(), c.passThroughStatusText)) {
			t.Fatalf("%s: %d %s", c.name, got.Code, got.Body.String())
		}
	}
}
