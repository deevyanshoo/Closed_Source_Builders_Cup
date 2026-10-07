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
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

type voiceExecutorFunc func(context.Context, []byte) ([]byte, error)

func (fn voiceExecutorFunc) Execute(ctx context.Context, request []byte) ([]byte, error) {
	return fn(ctx, request)
}

func TestVoiceHTTPCreateCapturesSyntheticSession(t *testing.T) {
	adapter, err := newVoiceHTTPAdapter(voiceExecutorFunc(func(context.Context, []byte) ([]byte, error) {
		return nil, nil
	}), time.Now)
	if err != nil {
		t.Fatal(err)
	}
	defer adapter.Shutdown(nil)
	fixture, err := os.ReadFile(filepath.Join("..", "..", "contracts", "examples", "voice_http_recovery_session.json"))
	if err != nil {
		t.Fatal(err)
	}
	req := httptest.NewRequest(http.MethodPost, "/v2/voice/sessions", bytes.NewReader(fixture))
	req.Header.Set("Content-Type", "application/json")
	rec := httptest.NewRecorder()
	adapter.ServeHTTP(rec, req, principal{ID: "p", Workspace: "W", Role: "viewer"})
	if rec.Code != http.StatusCreated {
		t.Fatalf("create status=%d body=%s", rec.Code, rec.Body.String())
	}
}

func voiceTestAdapter(t *testing.T, executor voiceExecutorFunc) *voiceHTTPAdapter {
	t.Helper()
	adapter, err := newVoiceHTTPAdapter(executor, time.Now)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		ctx, cancel := context.WithTimeout(context.Background(), time.Second)
		defer cancel()
		_ = adapter.Shutdown(ctx)
	})
	return adapter
}
func voiceFixture(t *testing.T, name string) []byte {
	t.Helper()
	b, e := os.ReadFile(filepath.Join("../../contracts/examples", name))
	if e != nil {
		t.Fatal(e)
	}
	return b
}
func voiceDirect(t *testing.T, a *voiceHTTPAdapter, p principal, method, path string, body []byte) *httptest.ResponseRecorder {
	t.Helper()
	r := httptest.NewRequest(method, path, bytes.NewReader(body))
	if body != nil {
		r.Header.Set("Content-Type", "application/json")
	}
	w := httptest.NewRecorder()
	a.ServeHTTP(w, r, p)
	return w
}
func voiceCreated(t *testing.T, a *voiceHTTPAdapter, p principal) map[string]any {
	t.Helper()
	w := voiceDirect(t, a, p, http.MethodPost, voicePath, voiceFixture(t, "voice_http_recovery_session.json"))
	if w.Code != 201 {
		t.Fatalf("create %d %s", w.Code, w.Body.String())
	}
	var v map[string]any
	if err := json.Unmarshal(w.Body.Bytes(), &v); err != nil {
		t.Fatal(err)
	}
	return v
}
func voiceTurnBody(t *testing.T, version string) []byte {
	t.Helper()
	var v map[string]any
	if err := json.Unmarshal(voiceFixture(t, "voice_http_recovery_turn.json"), &v); err != nil {
		t.Fatal(err)
	}
	v["context_version"] = version
	b, err := json.Marshal(v)
	if err != nil {
		t.Fatal(err)
	}
	return b
}
func TestVoiceHTTPStrictCreateAndOwnership(t *testing.T) {
	a := voiceTestAdapter(t, voiceExecutorFunc(func(context.Context, []byte) ([]byte, error) { return nil, nil }))
	p := principal{ID: "p", Workspace: "W", Role: "viewer"}
	fixture := voiceFixture(t, "voice_http_recovery_session.json")
	for name, mutate := range map[string]func(*http.Request){
		"duplicate-authorization": func(r *http.Request) {
			r.Header.Add("Authorization", "Bearer a")
			r.Header.Add("Authorization", "Bearer b")
		},
		"empty-spoof":            func(r *http.Request) { r.Header["X-Actor"] = []string{""} },
		"query":                  func(r *http.Request) { r.URL.RawQuery = "x=1" },
		"duplicate-content-type": func(r *http.Request) { r.Header.Add("Content-Type", "application/json") },
	} {
		t.Run(name, func(t *testing.T) {
			r := httptest.NewRequest("POST", voicePath, bytes.NewReader(fixture))
			r.Header.Set("Content-Type", "application/json")
			mutate(r)
			w := httptest.NewRecorder()
			a.ServeHTTP(w, r, p)
			if w.Code != 422 {
				t.Fatalf("status %d", w.Code)
			}
		})
	}
	for name, payload := range map[string][]byte{
		"duplicate-key":  []byte(`{"contract_version":"0.2.0","contract_version":"0.2.0","snapshot":{}}`),
		"unknown":        append(bytes.TrimSuffix(fixture, []byte("\n")), []byte(" trailing")...),
		"number":         []byte(`{"contract_version":2,"snapshot":{}}`),
		"null":           []byte(`{"contract_version":null,"snapshot":{}}`),
		"lone-surrogate": []byte(`{"contract_version":"0.2.0","snapshot":{"workspace_id":"\ud800"}}`),
	} {
		t.Run(name, func(t *testing.T) {
			if w := voiceDirect(t, a, p, "POST", voicePath, payload); w.Code != 422 {
				t.Fatalf("status %d", w.Code)
			}
		})
	}
	foreign := bytes.Replace(fixture, []byte(`"workspace_id": "W"`), []byte(`"workspace_id": "OTHER"`), 1)
	if w := voiceDirect(t, a, p, "POST", voicePath, foreign); w.Code != 403 {
		t.Fatalf("foreign workspace %d", w.Code)
	}
	created := voiceCreated(t, a, p)
	id := created["session_id"].(string)
	if w := voiceDirect(t, a, principal{ID: "other", Workspace: "W", Role: "viewer"}, "DELETE", voicePath+"/"+id, nil); w.Code != 404 {
		t.Fatalf("foreign same workspace %d", w.Code)
	}
	if w := voiceDirect(t, a, p, "DELETE", voicePath+"/"+id, nil); w.Code != 200 {
		t.Fatalf("close %d", w.Code)
	}
	if w := voiceDirect(t, a, p, "DELETE", voicePath+"/"+id, nil); w.Code != 404 {
		t.Fatalf("repeat close %d", w.Code)
	}
}
func TestVoiceHTTPTurnContextAndCapacity(t *testing.T) {
	entered := make(chan struct{}, 4)
	release := make(chan struct{})
	a := voiceTestAdapter(t, voiceExecutorFunc(func(ctx context.Context, _ []byte) ([]byte, error) {
		entered <- struct{}{}
		<-release
		return nil, errors.New("unavailable")
	}))
	p := principal{ID: "p", Workspace: "W", Role: "editor"}
	c := voiceCreated(t, a, p)
	id := c["session_id"].(string)
	version := c["context_version"].(string)
	bad := voiceTurnBody(t, "wwwwwwwwwwwwwwwwwwwwwwwwwwwwwwww")
	if w := voiceDirect(t, a, p, "POST", voicePath+"/"+id+"/turns", bad); w.Code != 409 {
		t.Fatalf("version %d", w.Code)
	}
	good := voiceTurnBody(t, version)
	w := voiceDirect(t, a, p, "POST", voicePath+"/"+id+"/turns", good)
	if w.Code != 202 {
		t.Fatalf("turn %d %s", w.Code, w.Body.String())
	}
	<-entered
	var accepted map[string]any
	if e := json.Unmarshal(w.Body.Bytes(), &accepted); e != nil {
		t.Fatal(e)
	}
	turnID := accepted["turn_id"].(string)
	if w := voiceDirect(t, a, p, "GET", voicePath+"/"+id+"/turns/"+turnID+"/result", nil); w.Code != 200 || !bytes.Contains(w.Body.Bytes(), []byte(`"pending"`)) {
		t.Fatalf("pending %d %s", w.Code, w.Body.String())
	}
	if w := voiceDirect(t, a, p, "POST", voicePath+"/"+id+"/turns/"+turnID+"/cancel", nil); w.Code != 200 {
		t.Fatalf("cancel %d", w.Code)
	}
	if w := voiceDirect(t, a, p, "GET", voicePath+"/"+id+"/turns/"+turnID+"/result", nil); w.Code != 404 {
		t.Fatalf("stale %d", w.Code)
	}
	close(release)
}

func TestVoiceHTTPNoConfigurationAuthenticatesBeforeUnavailable(t *testing.T) {
	identityPath := filepath.Join(t.TempDir(), "ids.json")
	if err := os.WriteFile(identityPath, []byte(`{"identities":[{"token":"valid","principal_id":"p","workspace_id":"W","role":"viewer"}]}`), 0600); err != nil {
		t.Fatal(err)
	}
	s := newLocalStateServer(stateConfig{IdentitiesFile: identityPath})
	call := func(token string, alter func(*http.Request)) int {
		r := httptest.NewRequest("POST", voicePath, bytes.NewReader(voiceFixture(t, "voice_http_recovery_session.json")))
		r.RemoteAddr = "127.0.0.1:12345"
		r.Header.Set("Content-Type", "application/json")
		if token != "" {
			r.Header.Set("Authorization", "Bearer "+token)
		}
		if alter != nil {
			alter(r)
		}
		w := httptest.NewRecorder()
		s.ServeHTTP(w, r)
		return w.Code
	}
	if code := call("", nil); code != 401 {
		t.Fatalf("anonymous %d", code)
	}
	if code := call("valid", func(r *http.Request) { r.Header.Set("X-Principal", "") }); code != 422 {
		t.Fatalf("empty spoof %d", code)
	}
	if code := call("valid", nil); code != 503 {
		t.Fatalf("configured auth but no voice %d", code)
	}
}
func TestVoiceHTTPExpiryAndSessionQuota(t *testing.T) {
	now := time.Date(2026, 10, 10, 0, 0, 0, 0, time.UTC)
	var mu sync.Mutex
	clock := func() time.Time { mu.Lock(); defer mu.Unlock(); return now }
	a, err := newVoiceHTTPAdapter(voiceExecutorFunc(func(context.Context, []byte) ([]byte, error) { return nil, nil }), clock)
	if err != nil {
		t.Fatal(err)
	}
	defer a.Shutdown(context.Background())
	p := principal{ID: "p", Workspace: "W", Role: "viewer"}
	first := voiceCreated(t, a, p)
	_ = voiceCreated(t, a, p)
	if w := voiceDirect(t, a, p, "POST", voicePath, voiceFixture(t, "voice_http_recovery_session.json")); w.Code != 429 {
		t.Fatalf("owner quota %d", w.Code)
	}
	mu.Lock()
	now = now.Add(voiceIdle)
	mu.Unlock()
	a.Sweep()
	if w := voiceDirect(t, a, p, "DELETE", voicePath+"/"+first["session_id"].(string), nil); w.Code != 404 {
		t.Fatalf("expired session %d", w.Code)
	}
	_ = voiceCreated(t, a, p)
}
func TestVoiceHTTPAcceptedTurnDetachedFromRequestButExplicitCancelStopsChild(t *testing.T) {
	entered := make(chan context.Context, 1)
	a := voiceTestAdapter(t, voiceExecutorFunc(func(ctx context.Context, _ []byte) ([]byte, error) {
		entered <- ctx
		<-ctx.Done()
		return nil, ctx.Err()
	}))
	p := principal{ID: "p", Workspace: "W", Role: "viewer"}
	created := voiceCreated(t, a, p)
	id := created["session_id"].(string)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	req := httptest.NewRequest("POST", voicePath+"/"+id+"/turns", bytes.NewReader(voiceTurnBody(t, created["context_version"].(string)))).WithContext(ctx)
	req.Header.Set("Content-Type", "application/json")
	rec := httptest.NewRecorder()
	a.ServeHTTP(rec, req, p)
	if rec.Code != 202 {
		t.Fatalf("turn %d", rec.Code)
	}
	child := <-entered
	cancel()
	if child.Err() != nil {
		t.Fatal("accepted turn inherited completed HTTP request cancellation")
	}
	accepted := voiceConnectedJSON(t, rec.Body.Bytes())
	path := voicePath + "/" + id + "/turns/" + accepted["turn_id"].(string) + "/cancel"
	if w := voiceDirect(t, a, p, "POST", path, nil); w.Code != 200 {
		t.Fatalf("explicit cancel %d", w.Code)
	}
	select {
	case <-child.Done():
	case <-time.After(time.Second):
		t.Fatal("child remained live after explicit cancel")
	}
}

type voiceFailedWriter struct {
	header http.Header
	status int
}

func (w *voiceFailedWriter) Header() http.Header {
	if w.header == nil {
		w.header = make(http.Header)
	}
	return w.header
}
func (w *voiceFailedWriter) WriteHeader(status int)  { w.status = status }
func (*voiceFailedWriter) Write([]byte) (int, error) { return 0, errors.New("client disconnected") }
func TestVoiceHTTPPreacceptWriteFailureSuppressesExecution(t *testing.T) {
	var calls atomic.Int32
	a := voiceTestAdapter(t, voiceExecutorFunc(func(context.Context, []byte) ([]byte, error) { calls.Add(1); return nil, nil }))
	p := principal{ID: "p", Workspace: "W", Role: "viewer"}
	created := voiceCreated(t, a, p)
	id := created["session_id"].(string)
	req := httptest.NewRequest("POST", voicePath+"/"+id+"/turns", bytes.NewReader(voiceTurnBody(t, created["context_version"].(string))))
	req.Header.Set("Content-Type", "application/json")
	w := &voiceFailedWriter{}
	a.ServeHTTP(w, req, p)
	if w.status != 202 || calls.Load() != 0 {
		t.Fatalf("write failure status=%d executor calls=%d", w.status, calls.Load())
	}
	if res := voiceDirect(t, a, p, "GET", voicePath+"/"+id+"/turns/1/result", nil); res.Code != 404 {
		t.Fatalf("preaccept failed turn visible %d", res.Code)
	}
}

type voiceBlockingWriter struct {
	header  http.Header
	entered chan struct{}
	release chan struct{}
}

func (w *voiceBlockingWriter) Header() http.Header {
	if w.header == nil {
		w.header = make(http.Header)
	}
	return w.header
}
func (*voiceBlockingWriter) WriteHeader(int) {}
func (w *voiceBlockingWriter) Write(p []byte) (int, error) {
	close(w.entered)
	<-w.release
	return len(p), nil
}

func TestVoiceHTTPQuotaResponseDoesNotHoldRegistryLock(t *testing.T) {
	a := voiceTestAdapter(t, voiceExecutorFunc(func(context.Context, []byte) ([]byte, error) { return nil, nil }))
	p := principal{ID: "p", Workspace: "W", Role: "viewer"}
	fixture := voiceFixture(t, "voice_http_recovery_session.json")
	voiceCreated(t, a, p)
	voiceCreated(t, a, p)
	w := &voiceBlockingWriter{entered: make(chan struct{}), release: make(chan struct{})}
	done := make(chan struct{})
	go func() {
		r := httptest.NewRequest("POST", voicePath, bytes.NewReader(fixture))
		r.Header.Set("Content-Type", "application/json")
		a.ServeHTTP(w, r, p)
		close(done)
	}()
	select {
	case <-w.entered:
	case <-time.After(time.Second):
		t.Fatal("quota response did not enter writer")
	}
	created := make(chan int, 1)
	go func() {
		r := httptest.NewRequest("POST", voicePath, bytes.NewReader(fixture))
		r.Header.Set("Content-Type", "application/json")
		rec := httptest.NewRecorder()
		a.ServeHTTP(rec, r, principal{ID: "other", Workspace: "W", Role: "viewer"})
		created <- rec.Code
	}()
	select {
	case code := <-created:
		if code != 201 {
			t.Fatalf("independent create status=%d", code)
		}
	case <-time.After(200 * time.Millisecond):
		close(w.release)
		<-done
		t.Fatal("quota response held registry lock across blocked write")
	}
	close(w.release)
	<-done
}

func TestVoiceHTTPNilAuthFailsClosed(t *testing.T) {
	s := &stateServer{cfg: stateConfig{Mode: "local"}}
	r := httptest.NewRequest("POST", voicePath, bytes.NewReader(voiceFixture(t, "voice_http_recovery_session.json")))
	r.Header.Set("Content-Type", "application/json")
	r.Header.Set("Authorization", "Bearer arbitrary")
	w := httptest.NewRecorder()
	s.voiceHTTPRoute(w, r)
	if w.Code != 401 {
		t.Fatalf("nil auth status=%d", w.Code)
	}
}

func TestVoiceHTTPFullCapacityBeforeLateReplacement(t *testing.T) {
	entered := make(chan struct{}, 5)
	release := make(chan struct{})
	a := voiceTestAdapter(t, voiceExecutorFunc(func(context.Context, []byte) ([]byte, error) {
		entered <- struct{}{}
		<-release
		return nil, errors.New("late executor failed")
	}))
	defer close(release)
	var first map[string]any
	for i := 0; i < 4; i++ {
		p := principal{ID: string(rune('a' + i)), Workspace: "W", Role: "viewer"}
		c := voiceCreated(t, a, p)
		if i == 0 {
			first = c
		}
		path := voicePath + "/" + c["session_id"].(string) + "/turns"
		if w := voiceDirect(t, a, p, "POST", path, voiceTurnBody(t, c["context_version"].(string))); w.Code != 202 {
			t.Fatalf("turn %d: %d", i, w.Code)
		}
		select {
		case <-entered:
		case <-time.After(time.Second):
			t.Fatal("executor did not enter")
		}
	}
	p := principal{ID: "a", Workspace: "W", Role: "viewer"}
	id := first["session_id"].(string)
	path := voicePath + "/" + id + "/turns"
	oldID := "1"
	if w := voiceDirect(t, a, p, "POST", path, voiceTurnBody(t, first["context_version"].(string))); w.Code != 429 {
		t.Fatalf("replacement bypassed physical capacity before cancel: %d", w.Code)
	}
	if w := voiceDirect(t, a, p, "GET", voicePath+"/"+id+"/turns/"+oldID+"/result", nil); w.Code != 200 || !bytes.Contains(w.Body.Bytes(), []byte(`"pending"`)) {
		t.Fatalf("rejected replacement invalidated old turn: %d %s", w.Code, w.Body.String())
	}
	if w := voiceDirect(t, a, p, "POST", voicePath+"/"+id+"/turns/"+oldID+"/cancel", nil); w.Code != 200 {
		t.Fatalf("cancel %d", w.Code)
	}
	if w := voiceDirect(t, a, p, "POST", path, voiceTurnBody(t, first["context_version"].(string))); w.Code != 429 {
		t.Fatalf("replacement bypassed physical capacity: %d", w.Code)
	}
	a.mu.Lock()
	active := a.active
	a.mu.Unlock()
	if active != 4 {
		t.Fatalf("active=%d want=4", active)
	}
}

func TestVoiceHTTPPollRenewsBothIdleClocks(t *testing.T) {
	now := time.Date(2026, 10, 10, 0, 0, 0, 0, time.UTC)
	var clockMu sync.Mutex
	clock := func() time.Time { clockMu.Lock(); defer clockMu.Unlock(); return now }
	a, err := newVoiceHTTPAdapter(voiceExecutorFunc(func(ctx context.Context, _ []byte) ([]byte, error) {
		<-ctx.Done()
		return nil, ctx.Err()
	}), clock)
	if err != nil {
		t.Fatal(err)
	}
	defer a.Shutdown(context.Background())
	p := principal{ID: "p", Workspace: "W", Role: "viewer"}
	c := voiceCreated(t, a, p)
	id := c["session_id"].(string)
	w := voiceDirect(t, a, p, "POST", voicePath+"/"+id+"/turns", voiceTurnBody(t, c["context_version"].(string)))
	if w.Code != 202 {
		t.Fatalf("turn %d", w.Code)
	}
	turnID := voiceConnectedJSON(t, w.Body.Bytes())["turn_id"].(string)
	path := voicePath + "/" + id + "/turns/" + turnID + "/result"
	clockMu.Lock()
	now = now.Add(4 * time.Minute)
	clockMu.Unlock()
	if w := voiceDirect(t, a, p, "GET", path, nil); w.Code != 200 {
		t.Fatalf("early poll %d", w.Code)
	}
	clockMu.Lock()
	now = now.Add(2 * time.Minute)
	clockMu.Unlock()
	if w := voiceDirect(t, a, p, "GET", path, nil); w.Code != 200 {
		t.Fatalf("active poll after original deadline %d", w.Code)
	}
	clockMu.Lock()
	now = now.Add(voiceIdle)
	clockMu.Unlock()
	if w := voiceDirect(t, a, p, "GET", path, nil); w.Code != 404 {
		t.Fatalf("expired after last poll %d", w.Code)
	}
	a.mu.Lock()
	retained := a.sessions[id] != nil
	a.mu.Unlock()
	if retained {
		t.Fatal("registry retained session after idle expiry")
	}
}

func voiceAssertError(t *testing.T, w *httptest.ResponseRecorder, status int, code string) {
	t.Helper()
	if w.Code != status {
		t.Fatalf("status=%d want=%d body=%s", w.Code, status, w.Body.String())
	}
	if w.Header().Get("Cache-Control") != "no-store" {
		t.Fatal("error response is cacheable")
	}
	var value map[string]any
	if err := json.Unmarshal(w.Body.Bytes(), &value); err != nil {
		t.Fatal(err)
	}
	semantic, ok := value["error"].(map[string]any)
	if !ok || semantic["code"] != code {
		t.Fatalf("error=%v want=%s", value["error"], code)
	}
	if _, ok := value["outcome"]; ok {
		t.Fatal("error leaked outcome")
	}
	if _, ok := value["state"]; ok {
		t.Fatal("error leaked state")
	}
}

func TestVoiceHTTPSemanticErrorsAreFailClosed(t *testing.T) {
	a := voiceTestAdapter(t, voiceExecutorFunc(func(context.Context, []byte) ([]byte, error) { return nil, nil }))
	p := principal{ID: "p", Workspace: "W", Role: "viewer"}
	fixture := voiceFixture(t, "voice_http_recovery_session.json")
	voiceAssertError(t, voiceDirect(t, a, principal{}, "POST", voicePath, fixture), 401, "unauthorized")
	voiceAssertError(t, voiceDirect(t, a, principal{ID: "p", Workspace: "W", Role: "admin"}, "POST", voicePath, fixture), 403, "forbidden")
	voiceAssertError(t, voiceDirect(t, a, p, "POST", voicePath, []byte(`{"unknown":true}`)), 422, "invalid_request")
	voiceAssertError(t, voiceDirect(t, a, p, "POST", voicePath, bytes.Repeat([]byte("x"), voiceCreateLimit+1)), 413, "request_too_large")
	c := voiceCreated(t, a, p)
	id := c["session_id"].(string)
	voiceAssertError(t, voiceDirect(t, a, p, "POST", voicePath+"/"+id+"/turns", voiceTurnBody(t, "wwwwwwwwwwwwwwwwwwwwwwwwwwwwwwww")), 409, "context_mismatch")
	voiceAssertError(t, voiceDirect(t, a, p, "GET", voicePath+"/"+id+"/turns/1/result", nil), 404, "voice_session_unavailable")
	voiceCreated(t, a, p)
	voiceAssertError(t, voiceDirect(t, a, p, "POST", voicePath, fixture), 429, "voice_capacity")
	var unavailable *voiceHTTPAdapter
	voiceAssertError(t, voiceDirect(t, unavailable, p, "POST", voicePath, fixture), 503, "voice_unavailable")
}

func TestVoiceHTTPCreateRejectsInvalidAsOf(t *testing.T) {
	a := voiceTestAdapter(t, voiceExecutorFunc(func(context.Context, []byte) ([]byte, error) { return nil, nil }))
	p := principal{ID: "p", Workspace: "W", Role: "viewer"}
	for _, asOf := range []string{"2026-02-30T00:00:00Z", "2026-10-10T00:00:00", "2026-10-10T00:00:00.1234567Z"} {
		var request map[string]any
		if err := json.Unmarshal(voiceFixture(t, "voice_http_recovery_session.json"), &request); err != nil {
			t.Fatal(err)
		}
		request["snapshot"].(map[string]any)["as_of"] = asOf
		body, err := json.Marshal(request)
		if err != nil {
			t.Fatal(err)
		}
		voiceAssertError(t, voiceDirect(t, a, p, "POST", voicePath, body), 422, "invalid_request")
	}
}

func TestVoiceHTTPInvalidLeaseReleasesManagerQuota(t *testing.T) {
	a := voiceTestAdapter(t, voiceExecutorFunc(func(ctx context.Context, _ []byte) ([]byte, error) {
		<-ctx.Done()
		return nil, ctx.Err()
	}))
	p := principal{ID: "p", Workspace: "W", Role: "viewer"}
	first := voiceCreated(t, a, p)
	voiceCreated(t, a, p)
	id := first["session_id"].(string)
	w := voiceDirect(t, a, p, "POST", voicePath+"/"+id+"/turns", voiceTurnBody(t, first["context_version"].(string)))
	if w.Code != 202 {
		t.Fatalf("turn %d", w.Code)
	}
	turnID := voiceConnectedJSON(t, w.Body.Bytes())["turn_id"].(string)
	a.mu.Lock()
	a.sessions[id].cancel()
	a.mu.Unlock()
	voiceAssertError(t, voiceDirect(t, a, p, "GET", voicePath+"/"+id+"/turns/"+turnID+"/result", nil), 404, "voice_session_unavailable")
	if w := voiceDirect(t, a, p, "POST", voicePath, voiceFixture(t, "voice_http_recovery_session.json")); w.Code != 201 {
		t.Fatalf("manager quota remained occupied: %d %s", w.Code, w.Body.String())
	}
}

func TestVoiceHTTPAuthenticationErrorsAreNoStore(t *testing.T) {
	identityPath := filepath.Join(t.TempDir(), "ids.json")
	if err := os.WriteFile(identityPath, []byte(`{"identities":[{"token":"valid","principal_id":"p","workspace_id":"W","role":"viewer"}]}`), 0600); err != nil {
		t.Fatal(err)
	}
	s := newLocalStateServer(stateConfig{IdentitiesFile: identityPath})
	for _, tc := range []struct {
		name, remote, origin, token string
		status                      int
		code                        string
	}{
		{"anonymous", "127.0.0.1:1234", "", "", 401, "unauthorized"},
		{"bad-origin", "127.0.0.1:1234", "https://other.example", "valid", 403, "forbidden"},
		{"nonloopback", "192.0.2.1:1234", "", "valid", 403, "forbidden"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			r := httptest.NewRequest("POST", voicePath, bytes.NewReader(voiceFixture(t, "voice_http_recovery_session.json")))
			r.RemoteAddr = tc.remote
			r.Header.Set("Content-Type", "application/json")
			if tc.origin != "" {
				r.Header.Set("Origin", tc.origin)
			}
			if tc.token != "" {
				r.Header.Set("Authorization", "Bearer "+tc.token)
			}
			w := httptest.NewRecorder()
			s.ServeHTTP(w, r)
			voiceAssertError(t, w, tc.status, tc.code)
		})
	}
}

func TestVoiceHTTPLateReplacedExecutorCannotPublish(t *testing.T) {
	var calls atomic.Int32
	entered := make(chan int32, 2)
	release := make(chan struct{})
	var releaseOnce sync.Once
	a := voiceTestAdapter(t, voiceExecutorFunc(func(ctx context.Context, _ []byte) ([]byte, error) {
		n := calls.Add(1)
		entered <- n
		if n == 1 {
			<-release
		} else {
			<-ctx.Done()
		}
		return nil, errors.New("late or canceled")
	}))
	defer releaseOnce.Do(func() { close(release) })
	p := principal{ID: "p", Workspace: "W", Role: "viewer"}
	c := voiceCreated(t, a, p)
	id := c["session_id"].(string)
	path := voicePath + "/" + id + "/turns"
	first := voiceDirect(t, a, p, "POST", path, voiceTurnBody(t, c["context_version"].(string)))
	if first.Code != 202 {
		t.Fatalf("first %d", first.Code)
	}
	if n := <-entered; n != 1 {
		t.Fatalf("first executor=%d", n)
	}
	oldID := voiceConnectedJSON(t, first.Body.Bytes())["turn_id"].(string)
	second := voiceDirect(t, a, p, "POST", path, voiceTurnBody(t, c["context_version"].(string)))
	if second.Code != 202 {
		t.Fatalf("replacement %d", second.Code)
	}
	if n := <-entered; n != 2 {
		t.Fatalf("replacement executor=%d", n)
	}
	newID := voiceConnectedJSON(t, second.Body.Bytes())["turn_id"].(string)
	releaseOnce.Do(func() { close(release) })
	for deadline := time.Now().Add(time.Second); time.Now().Before(deadline); {
		a.mu.Lock()
		active := a.active
		a.mu.Unlock()
		if active == 1 {
			break
		}
		time.Sleep(time.Millisecond)
	}
	voiceAssertError(t, voiceDirect(t, a, p, "GET", voicePath+"/"+id+"/turns/"+oldID+"/result", nil), 404, "voice_session_unavailable")
	if w := voiceDirect(t, a, p, "GET", voicePath+"/"+id+"/turns/"+newID+"/result", nil); w.Code != 200 || !bytes.Contains(w.Body.Bytes(), []byte(`"pending"`)) {
		t.Fatalf("late result corrupted replacement: %d %s", w.Code, w.Body.String())
	}
	voiceDirect(t, a, p, "POST", voicePath+"/"+id+"/turns/"+newID+"/cancel", nil)
}

func TestVoiceHTTPGlobalSessionQuotaReleasesOnClose(t *testing.T) {
	a := voiceTestAdapter(t, voiceExecutorFunc(func(context.Context, []byte) ([]byte, error) { return nil, nil }))
	var first map[string]any
	for i := 0; i < 8; i++ {
		p := principal{ID: string(rune('a' + i)), Workspace: "W", Role: "viewer"}
		c := voiceCreated(t, a, p)
		if i == 0 {
			first = c
		}
	}
	ninth := principal{ID: "ninth", Workspace: "W", Role: "viewer"}
	voiceAssertError(t, voiceDirect(t, a, ninth, "POST", voicePath, voiceFixture(t, "voice_http_recovery_session.json")), 429, "voice_capacity")
	if w := voiceDirect(t, a, principal{ID: "a", Workspace: "W", Role: "viewer"}, "DELETE", voicePath+"/"+first["session_id"].(string), nil); w.Code != 200 {
		t.Fatalf("close %d", w.Code)
	}
	voiceCreated(t, a, ninth)
}

func TestVoiceHTTPRoutingKeepsAnonymousLegacyPreview(t *testing.T) {
	var upstreamCalls atomic.Int32
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost || r.URL.Path != "/recommend" {
			t.Errorf("unexpected legacy upstream route %s %s", r.Method, r.URL.Path)
		}
		upstreamCalls.Add(1)
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(legacy))
	}))
	defer upstream.Close()
	s := newLocalStateServer(stateConfig{Upstream: upstream.URL})
	s.voice = voiceTestAdapter(t, voiceExecutorFunc(func(context.Context, []byte) ([]byte, error) { return nil, nil }))
	preview := stateCallRaw(s, http.MethodPost, "/v2/plans/recommendations", "", fixture(t, "request.json"))
	if preview.Code != 200 {
		t.Fatalf("anonymous preview %d %s", preview.Code, preview.Body.String())
	}
	var response map[string]any
	if err := json.Unmarshal(preview.Body.Bytes(), &response); err != nil {
		t.Fatal(err)
	}
	if response["contract_version"] != "0.1.0" || response["status"] != "ready" || response["request"] == nil || response["plan"] == nil || response["availability"] == nil {
		t.Fatalf("legacy preview shape changed: %s", preview.Body.String())
	}
	if upstreamCalls.Load() != 1 {
		t.Fatalf("legacy upstream calls=%d", upstreamCalls.Load())
	}
	r := httptest.NewRequest(http.MethodPost, voicePath, bytes.NewReader(voiceFixture(t, "voice_http_recovery_session.json")))
	r.RemoteAddr = "127.0.0.1:12345"
	r.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	s.ServeHTTP(w, r)
	voiceAssertError(t, w, 401, "unauthorized")
	if upstreamCalls.Load() != 1 {
		t.Fatalf("voice request reached legacy upstream: calls=%d", upstreamCalls.Load())
	}
}
