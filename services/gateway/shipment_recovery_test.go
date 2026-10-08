package main

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"
)

type shipmentAuth struct {
	principal principal
	allowed   bool
}

type shipmentBlockingWriter struct {
	*httptest.ResponseRecorder
	entered chan struct{}
	release chan struct{}
	once    sync.Once
}

func (w *shipmentBlockingWriter) WriteHeader(status int) {
	w.once.Do(func() { close(w.entered) })
	<-w.release
	w.ResponseRecorder.WriteHeader(status)
}

func (a shipmentAuth) Authenticate(w http.ResponseWriter, _ *http.Request) (principal, bool) {
	if !a.allowed {
		shipmentErr(w, 401, "unauthorized", "Valid identity required")
		return principal{}, false
	}
	return a.principal, true
}

func shipmentRequest(t *testing.T, method, path, body string) *http.Request {
	t.Helper()
	r := httptest.NewRequest(method, path, strings.NewReader(body))
	r.RemoteAddr = "127.0.0.1:54321"
	if method == "POST" {
		r.Header.Set("Content-Type", "application/json")
	}
	return r
}

func shipmentFakeChild(ctx context.Context, input []byte) ([]byte, error) {
	var outer map[string]string
	if err := json.Unmarshal(input, &outer); err != nil {
		return nil, err
	}
	var host map[string]string
	if err := json.Unmarshal([]byte(outer["host_context_json"]), &host); err != nil {
		return nil, err
	}
	hostHash := sha256.Sum256([]byte(outer["host_context_json"]))
	const zeroHash = "0000000000000000000000000000000000000000000000000000000000000000"
	outcome := map[string]any{"adapter_version": "1", "status": "no_eligible_scenario", "synthetic": true,
		"binding": map[string]any{"adapter_version": "1", "synthetic": true, "workspace_id": host["workspace_id"], "plan_version": host["plan_version"],
			"propagation_snapshot_id": host["propagation_snapshot_id"], "production_snapshot_id": host["production_snapshot_id"], "as_of": host["as_of"],
			"raw_sha256": zeroHash, "propagation_sha256": zeroHash, "production_sha256": zeroHash, "joins_sha256": zeroHash, "assignments_sha256": zeroHash, "binding_sha256": zeroHash},
		"evidence": map[string]any{"scenarios": []any{}, "excluded": []any{}, "normalization_rejected": []any{}, "normalization_duplicates": []any{}}, "comparisons": []any{}}
	encodedOutcome, err := shipmentMarshal(outcome)
	if err != nil {
		return nil, err
	}
	binding := map[string]any{"principal_id": host["principal_id"], "workspace_id": host["workspace_id"], "request_id": host["request_id"],
		"context_id": host["context_id"], "context_revision": host["context_revision"], "host_sha256": hex.EncodeToString(hostHash[:]), "request_sha256": host["request_sha256"]}
	return shipmentMarshal(map[string]any{"bridge_version": "1", "binding": binding, "outcome_json": string(encodedOutcome)})
}

func TestShipmentPathAndAuthorityBeforeUnavailable(t *testing.T) {
	for _, path := range []string{"/v2/workspaces/W/shipment-recovery/contexts/x", "/v2/workspaces/W/shipment-recovery/comparisons/", "/v2/workspaces/W/shipment-recovery/unknown"} {
		if !shipmentRouteCandidate(path) {
			t.Fatalf("candidate missed %s", path)
		}
		if _, _, ok := shipmentPath(path); ok {
			t.Fatalf("accepted %s", path)
		}
	}
	base := "/v2/workspaces/W/shipment-recovery/contexts"
	for _, tc := range []struct {
		name    string
		p       principal
		allowed bool
		path    string
		want    int
	}{
		{"unauthenticated", principal{}, false, base, 401},
		{"foreign_path", principal{"P", "OTHER", "viewer"}, true, base, 403},
		{"bad_role", principal{"P", "W", "owner"}, true, base, 403},
		{"same_workspace_unavailable", principal{"P", "W", "viewer"}, true, base, 503},
	} {
		t.Run(tc.name, func(t *testing.T) {
			s := &stateServer{cfg: stateConfig{}, auth: shipmentAuth{tc.p, tc.allowed}}
			w := httptest.NewRecorder()
			s.shipmentHTTPRoute(w, shipmentRequest(t, "GET", tc.path, ""))
			if w.Code != tc.want || w.Header().Get("Cache-Control") != "no-store" {
				t.Fatalf("status=%d want=%d", w.Code, tc.want)
			}
		})
	}
}

func TestShipmentHTTPValidatedNoEligibleAndChildTamper(t *testing.T) {
	spec := shipmentTestSpec(t)
	a := newShipmentAdapter(shipmentExecutorFunc(shipmentFakeChild))
	meta, err := a.InstallHostContext(spec)
	if err != nil {
		t.Fatal(err)
	}
	s := &stateServer{auth: shipmentAuth{principal{"P", "W", "viewer"}, true}, shipment: a}
	w := httptest.NewRecorder()
	s.shipmentHTTPRoute(w, shipmentRequest(t, "GET", "/v2/workspaces/W/shipment-recovery/contexts", ""))
	if w.Code != 200 || !strings.Contains(w.Body.String(), meta.ContextID) {
		t.Fatalf("list=%d", w.Code)
	}
	body := `{"contract_version":"0.2.0","context_id":"` + meta.ContextID + `","expected_revision":1}`
	w = httptest.NewRecorder()
	s.shipmentHTTPRoute(w, shipmentRequest(t, "POST", "/v2/workspaces/W/shipment-recovery/comparisons", body))
	if w.Code != 200 {
		t.Fatalf("comparison status=%d body=%s", w.Code, w.Body.String())
	}
	var response map[string]any
	if err := json.Unmarshal(w.Body.Bytes(), &response); err != nil {
		t.Fatal(err)
	}
	if _, ok := response["outcome_json"].(string); !ok {
		t.Fatal("missing complete JSON-text outcome")
	}
	a.executor = shipmentExecutorFunc(func(ctx context.Context, input []byte) ([]byte, error) {
		result, err := shipmentFakeChild(ctx, input)
		if err != nil {
			return nil, err
		}
		var outer map[string]any
		if err := json.Unmarshal(result, &outer); err != nil {
			return nil, err
		}
		outer["binding"].(map[string]any)["context_id"] = "00000000000000000000000000000000"
		return shipmentMarshal(outer)
	})
	w = httptest.NewRecorder()
	s.shipmentHTTPRoute(w, shipmentRequest(t, "POST", "/v2/workspaces/W/shipment-recovery/comparisons", body))
	if w.Code != 502 || strings.Contains(w.Body.String(), "outcome_json") {
		t.Fatalf("tampered status=%d", w.Code)
	}
	a.executor = shipmentExecutorFunc(func(context.Context, []byte) ([]byte, error) { return nil, errShipmentInvalidOutput })
	w = httptest.NewRecorder()
	s.shipmentHTTPRoute(w, shipmentRequest(t, "POST", "/v2/workspaces/W/shipment-recovery/comparisons", body))
	if w.Code != 502 || strings.Contains(w.Body.String(), "outcome_json") {
		t.Fatalf("overflow status=%d", w.Code)
	}
}

func TestShipmentReplacementWinsBeforeLateFakePublication(t *testing.T) {
	spec := shipmentTestSpec(t)
	entered, release := make(chan struct{}), make(chan struct{})
	a := newShipmentAdapter(shipmentExecutorFunc(func(ctx context.Context, input []byte) ([]byte, error) {
		close(entered)
		<-release // Deliberately ignores cancellation.
		return shipmentFakeChild(ctx, input)
	}))
	meta, err := a.InstallHostContext(spec)
	if err != nil {
		t.Fatal(err)
	}
	s := &stateServer{auth: shipmentAuth{principal{"P", "W", "editor"}, true}, shipment: a}
	body := `{"contract_version":"0.2.0","context_id":"` + meta.ContextID + `","expected_revision":1}`
	done := make(chan *httptest.ResponseRecorder, 1)
	go func() {
		w := httptest.NewRecorder()
		s.shipmentHTTPRoute(w, shipmentRequest(t, "POST", "/v2/workspaces/W/shipment-recovery/comparisons", body))
		done <- w
	}()
	<-entered
	if _, err := a.ReplaceHostContext("W", meta.ContextID, 1, spec); err != nil {
		t.Fatal(err)
	}
	close(release)
	w := <-done
	if w.Code != 409 || strings.Contains(w.Body.String(), "outcome_json") {
		t.Fatalf("late publication status=%d", w.Code)
	}
}

func TestShipmentForceQuerySpoofAndDisconnectAreFailClosed(t *testing.T) {
	spec := shipmentTestSpec(t)
	a := newShipmentAdapter(shipmentExecutorFunc(shipmentFakeChild))
	meta, err := a.InstallHostContext(spec)
	if err != nil {
		t.Fatal(err)
	}
	s := &stateServer{auth: shipmentAuth{principal{"P", "W", "viewer"}, true}, shipment: a}
	for name, makeRequest := range map[string]func() *http.Request{
		"force_query": func() *http.Request {
			return shipmentRequest(t, "GET", "/v2/workspaces/W/shipment-recovery/contexts?", "")
		},
		"empty_spoof": func() *http.Request {
			r := shipmentRequest(t, "GET", "/v2/workspaces/W/shipment-recovery/contexts", "")
			r.Header["X-Actor"] = []string{""}
			return r
		},
	} {
		t.Run(name, func(t *testing.T) {
			w := httptest.NewRecorder()
			s.shipmentHTTPRoute(w, makeRequest())
			if w.Code != 422 {
				t.Fatalf("status=%d", w.Code)
			}
		})
	}
	entered := make(chan struct{})
	a.executor = shipmentExecutorFunc(func(ctx context.Context, _ []byte) ([]byte, error) {
		close(entered)
		<-ctx.Done()
		return nil, ctx.Err()
	})
	body := `{"contract_version":"0.2.0","context_id":"` + meta.ContextID + `","expected_revision":1}`
	parent, cancel := context.WithCancel(context.Background())
	r := shipmentRequest(t, "POST", "/v2/workspaces/W/shipment-recovery/comparisons", body).WithContext(parent)
	done := make(chan *httptest.ResponseRecorder, 1)
	go func() { w := httptest.NewRecorder(); s.shipmentHTTPRoute(w, r); done <- w }()
	<-entered
	cancel()
	w := <-done
	if w.Body.Len() != 0 {
		t.Fatalf("disconnected request wrote body of %d bytes", w.Body.Len())
	}
}

func TestShipmentSlowLoopbackBodyHonorsShorterHandlerDeadline(t *testing.T) {
	s := &stateServer{auth: shipmentAuth{principal{"P", "W", "viewer"}, true}}
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		ctx, cancel := context.WithTimeout(r.Context(), 100*time.Millisecond)
		defer cancel()
		s.shipmentHTTPRoute(w, r.WithContext(ctx))
	}))
	defer server.Close()
	reader, writer := io.Pipe()
	request, err := http.NewRequest(http.MethodPost, server.URL+"/v2/workspaces/W/shipment-recovery/comparisons", reader)
	if err != nil {
		t.Fatal(err)
	}
	request.Header.Set("Content-Type", "application/json")
	client := &http.Client{Timeout: 2 * time.Second}
	go func() {
		_, _ = writer.Write([]byte("{"))
		time.Sleep(700 * time.Millisecond)
		_ = writer.Close()
	}()
	response, err := client.Do(request)
	_ = writer.Close()
	if err != nil {
		t.Fatalf("shorter server deadline did not return a response: %v", err)
	}
	defer response.Body.Close()
	if response.StatusCode != 504 {
		t.Fatalf("status=%d want504", response.StatusCode)
	}
}

func TestShipmentKeywordInsideWorkspaceIDPreservesLegacyStateRouting(t *testing.T) {
	s := &stateServer{cfg: stateConfig{Mode: "local"}, identityReady: true,
		identities: []identity{{Token: "synthetic-local-only", PrincipalID: "p1", WorkspaceID: "shipment-recovery-W", Role: "viewer"}}}
	s.auth = s
	r := httptest.NewRequest(http.MethodGet, "/v2/workspaces/shipment-recovery-W/plans", nil)
	r.RemoteAddr = "127.0.0.1:54321"
	r.Header.Set("Authorization", "Bearer synthetic-local-only")
	w := httptest.NewRecorder()
	s.ServeHTTP(w, r)
	if w.Code != 503 {
		t.Fatalf("legacy state route was intercepted: status=%d", w.Code)
	}
}

func TestShipmentFinalPublicationHoldsRevisionThroughBufferedWrite(t *testing.T) {
	for _, mode := range []string{"replace", "shutdown"} {
		t.Run(mode, func(t *testing.T) {
			spec := shipmentTestSpec(t)
			a := newShipmentAdapter(shipmentExecutorFunc(shipmentFakeChild))
			meta, err := a.InstallHostContext(spec)
			if err != nil {
				t.Fatal(err)
			}
			s := &stateServer{auth: shipmentAuth{principal{"P", "W", "viewer"}, true}, shipment: a}
			body := `{"contract_version":"0.2.0","context_id":"` + meta.ContextID + `","expected_revision":1}`
			request := shipmentRequest(t, http.MethodPost, "/v2/workspaces/W/shipment-recovery/comparisons", body)
			writer := &shipmentBlockingWriter{ResponseRecorder: httptest.NewRecorder(), entered: make(chan struct{}), release: make(chan struct{})}
			done := make(chan struct{})
			go func() { s.shipmentHTTPRoute(writer, request); close(done) }()
			<-writer.entered // Successful response reached WriteHeader after all buffering.
			lockHeld := !a.mu.TryLock()
			if !lockHeld {
				a.mu.Unlock()
			}
			operation := make(chan error, 1)
			started := make(chan struct{})
			go func() {
				close(started)
				if mode == "replace" {
					_, err := a.ReplaceHostContext("W", meta.ContextID, 1, spec)
					operation <- err
					return
				}
				ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
				defer cancel()
				operation <- a.Shutdown(ctx)
			}()
			<-started
			if !lockHeld && mode == "replace" {
				// Old code released the publication lock before the write; the
				// host operation can win while the old response is paused.
				<-operation
			}
			close(writer.release)
			<-done
			if lockHeld || mode == "shutdown" {
				if err := <-operation; err != nil {
					t.Fatal(err)
				}
			}
			if !lockHeld {
				t.Fatal("revision lock was released before successful buffered publication")
			}
			if writer.Code != 200 {
				t.Fatalf("published status=%d", writer.Code)
			}
		})
	}
}
