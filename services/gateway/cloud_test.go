package main

import (
	"context"
	"errors"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

type testVerifier func(context.Context, string, string) (verifiedIdentity, error)

func (f testVerifier) Verify(ctx context.Context, token, audience string) (verifiedIdentity, error) {
	return f(ctx, token, audience)
}

type testTokenSource func(context.Context) (string, error)

func (f testTokenSource) Token(ctx context.Context) (string, error) { return f(ctx) }

func privateFixture(t *testing.T) stateConfig {
	t.Helper()
	dir := t.TempDir()
	identities := filepath.Join(dir, "machines.json")
	if err := os.WriteFile(identities, []byte(`{"principals":[{"subject":"subject-a","email":"runner@project.iam.gserviceaccount.com","principal_id":"machine-a","workspace_id":"workspace-a","role":"editor"}]}`), 0600); err != nil {
		t.Fatal(err)
	}
	db := filepath.Join(dir, "database-url")
	if err := os.WriteFile(db, []byte("postgres://task:secret@127.0.0.1:1/task"), 0600); err != nil {
		t.Fatal(err)
	}
	assets := filepath.Join(dir, "assets")
	if err := os.Mkdir(assets, 0700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(assets, "index.html"), []byte("<html>private app</html>"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(assets, "app.js"), []byte("console.log('app')"), 0600); err != nil {
		t.Fatal(err)
	}
	now := time.Now().Unix()
	return stateConfig{
		Mode: "private_machine", DBURLFile: db, MachineIdentitiesFile: identities,
		GatewayAudience: "https://gateway.example.test", Upstream: "https://python.example.test",
		PythonAudience: "https://python.example.test", AssetsDir: assets,
		Verifier: testVerifier(func(_ context.Context, token, audience string) (verifiedIdentity, error) {
			if token != "valid" || audience != "https://gateway.example.test" {
				return verifiedIdentity{}, errors.New("invalid token")
			}
			return verifiedIdentity{Issuer: "https://accounts.google.com", Audience: audience, Subject: "subject-a",
				Email: "runner@project.iam.gserviceaccount.com", EmailVerified: true, IssuedAt: now - 10, Expires: now + 3600}, nil
		}),
		OutboundTokenSource: testTokenSource(func(context.Context) (string, error) { return "server-token", nil }),
	}
}

func privateCall(h http.Handler, method, path, token string) *httptest.ResponseRecorder {
	r := httptest.NewRequest(method, path, nil)
	if token != "" {
		r.Header.Set("Authorization", "Bearer "+token)
	}
	w := httptest.NewRecorder()
	h.ServeHTTP(w, r)
	return w
}

func TestPrivateRuntimeRequiresCompleteSafeConfig(t *testing.T) {
	incomplete := newStateHandler(stateConfig{Mode: "private_machine"})
	if got := privateCall(incomplete, "GET", "/v2/contracts/examples/request", "").Code; got != 503 {
		t.Fatalf("direct private constructor failed open: %d", got)
	}
	env := map[string]string{"GATEWAY_MODE": "private_machine", "PORT": "8080", "GATEWAY_AUDIENCE": "https://gateway.example.test",
		"PYTHON_SERVICE_URL": "https://python.example.test", "PYTHON_SERVICE_AUDIENCE": "https://python.example.test",
		"MACHINE_IDENTITIES_FILE": "/private/machines", "DATABASE_URL_FILE": "/private/db", "WEB_ASSETS_DIR": "/private/assets"}
	get := func(k string) string { return env[k] }
	_, addr, err := runtimeFromEnv(get)
	if err != nil || addr != "0.0.0.0:8080" {
		t.Fatalf("private addr %q %v", addr, err)
	}
	for key, bad := range map[string]string{"PORT": "0", "GATEWAY_AUDIENCE": "http://gateway.example.test",
		"PYTHON_SERVICE_URL": "http://python.example.test", "PYTHON_SERVICE_AUDIENCE": "https://other.example.test",
		"DEV_IDENTITIES_FILE": "/tmp/dev.json"} {
		copy := map[string]string{}
		for k, v := range env {
			copy[k] = v
		}
		copy[key] = bad
		_, _, err := runtimeFromEnv(func(k string) string { return copy[k] })
		if err == nil {
			t.Errorf("accepted %s", key)
		}
	}
	_, local, err := runtimeFromEnv(func(string) string { return "" })
	if err != nil || !strings.HasPrefix(local, "127.0.0.1:") {
		t.Fatalf("unsafe local default %q %v", local, err)
	}
}

func TestLocalConfiguredProductionAssetsServeWithoutSwallowingAPI(t *testing.T) {
	dir := t.TempDir()
	if err := os.WriteFile(filepath.Join(dir, "index.html"), []byte("<html>local build</html>"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, "app.js"), []byte("console.log('local')"), 0600); err != nil {
		t.Fatal(err)
	}
	h := newStateHandler(stateConfig{AssetsDir: dir, Upstream: "http://127.0.0.1:1"})
	if got := call(h, "GET", "/", nil); got.Code != 200 || !strings.Contains(got.Body.String(), "local build") {
		t.Fatalf("local production index %d %s", got.Code, got.Body.String())
	}
	if got := call(h, "GET", "/app.js", nil); got.Code != 200 || !strings.Contains(got.Body.String(), "console.log") {
		t.Fatalf("local production asset %d %s", got.Code, got.Body.String())
	}
	if got := call(h, "GET", "/missing.js", nil).Code; got != 404 {
		t.Errorf("missing asset %d", got)
	}
	if got := call(h, "GET", "/v2/materials", nil); got.Code != 200 ||
		!strings.Contains(got.Body.String(), "provider_unavailable") {
		t.Fatalf("API swallowed by assets %d %s", got.Code, got.Body.String())
	}
	if got := call(h, "GET", "/v2/not-found", nil).Code; got != 404 {
		t.Errorf("API fallback %d", got)
	}
	invalid := newStateHandler(stateConfig{AssetsDir: filepath.Join(dir, "missing")})
	if got := call(invalid, "GET", "/", nil).Code; got != 503 {
		t.Errorf("invalid asset config %d", got)
	}
}

func TestAssetsRejectIndexSymlinkOutsideRoot(t *testing.T) {
	dir := t.TempDir()
	outside := filepath.Join(t.TempDir(), "private.html")
	if err := os.WriteFile(outside, []byte("private contents"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(outside, filepath.Join(dir, "index.html")); err != nil {
		t.Fatal(err)
	}
	if _, err := newAssetsHandler(dir); err == nil {
		t.Fatal("accepted index symlink outside configured asset root")
	}
	configured := newStateHandler(stateConfig{AssetsDir: dir})
	req := httptest.NewRequest("GET", "/planning", nil)
	req.Header.Set("Accept", "text/html")
	reply := httptest.NewRecorder()
	configured.ServeHTTP(reply, req)
	if reply.Code != 503 || strings.Contains(reply.Body.String(), "private contents") {
		t.Fatalf("unsafe configured index fallback: %d %q", reply.Code, reply.Body.String())
	}
}

func TestPrivateIngressGuardsLegacyStateAndAssets(t *testing.T) {
	cfg := privateFixture(t)
	h, err := newPrivateStateHandler(cfg)
	if err != nil {
		t.Fatal(err)
	}
	for _, path := range []string{"/v2/materials", "/v2/contracts/examples/request", "/v2/plans/recommendations",
		"/v2/session", "/ready", "/", "/app.js", "/v2/actions/not_supported"} {
		if got := privateCall(h, "GET", path, "").Code; got != 401 {
			t.Errorf("anonymous %s: %d", path, got)
		}
		if got := privateCall(h, "GET", path, "wrong").Code; got != 401 {
			t.Errorf("wrong caller %s: %d", path, got)
		}
	}
	if got := privateCall(h, "GET", "/health", "").Code; got != 200 {
		t.Fatalf("health %d", got)
	}
	if got := privateCall(h, "GET", "/v2/session", "valid"); got.Code != 200 ||
		!strings.Contains(got.Body.String(), `"development_only":false`) ||
		!strings.Contains(got.Body.String(), `"identity_mode":"private_machine"`) {
		t.Fatalf("session %d %s", got.Code, got.Body.String())
	}
	if got := privateCall(h, "GET", "/", "valid"); got.Code != 200 || !strings.Contains(got.Body.String(), "private app") {
		t.Fatalf("asset %d %s", got.Code, got.Body.String())
	}
	if got := privateCall(h, "GET", "/missing.js", "valid").Code; got != 404 {
		t.Errorf("missing asset %d", got)
	}
	if got := privateCall(h, "GET", "/v2/not-found", "valid").Code; got != 404 {
		t.Errorf("API fallback %d", got)
	}
	if got := privateCall(h, "GET", "/api/other", "valid").Code; got != 404 {
		t.Errorf("API-like fallback %d", got)
	}
	if got := privateCall(h, "GET", "/../database-url", "valid").Code; got != 404 {
		t.Errorf("traversal %d", got)
	}
	if err := os.Symlink(cfg.DBURLFile, filepath.Join(cfg.AssetsDir, "leak.txt")); err != nil {
		t.Fatal(err)
	}
	if got := privateCall(h, "GET", "/leak.txt", "valid").Code; got != 404 {
		t.Errorf("asset symlink escape %d", got)
	}
	if got := privateCall(h, "GET", "/v2/workspaces/workspace-b/plans", "valid").Code; got != 403 {
		t.Errorf("cross-workspace %d", got)
	}
	otherWorkspacePreview := strings.Replace(
		string(fixture(t, "request.json")), `"workspace_id": "local-workspace"`,
		`"workspace_id": "workspace-b"`, 1)
	if got := stateCallRaw(h, "POST", "/v2/plans/recommendations", "valid",
		[]byte(otherWorkspacePreview)).Code; got != 403 {
		t.Errorf("cross-workspace preview %d", got)
	}
	navigation := httptest.NewRequest("GET", "/planning", nil)
	navigation.Header.Set("Authorization", "Bearer valid")
	navigation.Header.Set("Accept", "text/html")
	navigationReply := httptest.NewRecorder()
	h.ServeHTTP(navigationReply, navigation)
	if navigationReply.Code != 200 || !strings.Contains(navigationReply.Body.String(), "private app") {
		t.Errorf("SPA navigation %d", navigationReply.Code)
	}
}

func TestPrivateRejectsCallerAssertionsAndWrongClaims(t *testing.T) {
	cfg := privateFixture(t)
	good := cfg.Verifier
	for name, mutate := range map[string]func(*verifiedIdentity){
		"issuer":           func(v *verifiedIdentity) { v.Issuer = "https://evil.example" },
		"audience":         func(v *verifiedIdentity) { v.Audience = "https://other.example" },
		"expired":          func(v *verifiedIdentity) { v.Expires = time.Now().Unix() - 1 },
		"future issued":    func(v *verifiedIdentity) { v.IssuedAt = time.Now().Unix() + 3600 },
		"unverified email": func(v *verifiedIdentity) { v.EmailVerified = false },
		"wrong email":      func(v *verifiedIdentity) { v.Email = "other@project.iam.gserviceaccount.com" },
		"wrong subject":    func(v *verifiedIdentity) { v.Subject = "subject-b" },
	} {
		t.Run(name, func(t *testing.T) {
			cfg.Verifier = testVerifier(func(ctx context.Context, token, aud string) (verifiedIdentity, error) {
				v, e := good.Verify(ctx, token, aud)
				mutate(&v)
				return v, e
			})
			h, e := newPrivateStateHandler(cfg)
			if e != nil {
				t.Fatal(e)
			}
			if got := privateCall(h, "GET", "/v2/session", "valid").Code; got != 401 {
				t.Fatalf("accepted %s %d", name, got)
			}
		})
	}
	cfg.Verifier = good
	h, e := newPrivateStateHandler(cfg)
	if e != nil {
		t.Fatal(e)
	}
	r := httptest.NewRequest("GET", "/v2/session", nil)
	r.Header.Set("Authorization", "Bearer valid")
	r.Header.Set("X-Workspace-Id", "workspace-b")
	w := httptest.NewRecorder()
	h.ServeHTTP(w, r)
	if w.Code != 422 {
		t.Fatalf("client workspace assertion %d", w.Code)
	}
	if got := privateCall(h, "GET", "/v2/session?workspace_id=workspace-b", "valid").Code; got != 422 {
		t.Fatalf("client query workspace assertion %d", got)
	}
}

func TestPrivateOversizeBearerRejectedBeforeVerification(t *testing.T) {
	cfg := privateFixture(t)
	calls := 0
	cfg.Verifier = testVerifier(func(context.Context, string, string) (verifiedIdentity, error) {
		calls++
		return verifiedIdentity{}, errors.New("should not be called")
	})
	h, err := newPrivateStateHandler(cfg)
	if err != nil {
		t.Fatal(err)
	}
	if got := privateCall(h, "GET", "/v2/session", strings.Repeat("x", 16<<10+1)).Code; got != 401 {
		t.Fatalf("oversize bearer %d", got)
	}
	if calls != 0 {
		t.Fatal("oversize bearer reached token verifier")
	}
}

func TestPrivateMapAndSecretFilesFailClosed(t *testing.T) {
	cfg := privateFixture(t)
	for _, data := range []string{
		`{"principals":[]}`,
		`{"principals":[{"subject":"subject-a","email":"runner@project.iam.gserviceaccount.com","principal_id":"a","workspace_id":"w","role":"editor"},{"subject":"subject-a","email":"runner@project.iam.gserviceaccount.com","principal_id":"b","workspace_id":"w","role":"viewer"}]}`,
		`{"principals":[{"subject":"subject-a","email":"runner@project.iam.gserviceaccount.com","principal_id":"a","workspace_id":"w","role":"editor"},{"subject":"subject-b","email":"runner@project.iam.gserviceaccount.com","principal_id":"b","workspace_id":"w","role":"viewer"}]}`,
		`{"principals":[{"subject":"subject-a","email":"human@example.com","principal_id":"a","workspace_id":"w","role":"editor"}]}`,
	} {
		if e := os.WriteFile(cfg.MachineIdentitiesFile, []byte(data), 0600); e != nil {
			t.Fatal(e)
		}
		if _, e := newPrivateStateHandler(cfg); e == nil {
			t.Errorf("accepted identity map %s", data)
		}
	}
	if e := os.WriteFile(cfg.MachineIdentitiesFile, []byte(`{"principals":[{"subject":"subject-a","email":"runner@project.iam.gserviceaccount.com","principal_id":"machine-a","workspace_id":"workspace-a","role":"editor"}]}`), 0644); e != nil {
		t.Fatal(e)
	}
	if e := os.Chmod(cfg.MachineIdentitiesFile, 0644); e != nil {
		t.Fatal(e)
	}
	if _, e := newPrivateStateHandler(cfg); e == nil {
		t.Fatal("accepted public identity file")
	}
	if e := os.Chmod(cfg.MachineIdentitiesFile, 0600); e != nil {
		t.Fatal(e)
	}
	if e := os.Chmod(cfg.DBURLFile, 0644); e != nil {
		t.Fatal(e)
	}
	if _, e := newPrivateStateHandler(cfg); e == nil {
		t.Fatal("accepted public database secret file")
	}
}

func TestPrivateMapRejectsSharedPrincipalIDAcrossMachineAccounts(t *testing.T) {
	cfg := privateFixture(t)
	data := `{"principals":[{"subject":"subject-a","email":"runner-a@project.iam.gserviceaccount.com","principal_id":"shared-machine","workspace_id":"workspace-a","role":"editor"},{"subject":"subject-b","email":"runner-b@project.iam.gserviceaccount.com","principal_id":"shared-machine","workspace_id":"workspace-a","role":"editor"}]}`
	if err := os.WriteFile(cfg.MachineIdentitiesFile, []byte(data), 0600); err != nil {
		t.Fatal(err)
	}
	if _, err := newPrivateStateHandler(cfg); err == nil {
		t.Fatal("distinct machine accounts sharing a principal ID were accepted")
	}
}
