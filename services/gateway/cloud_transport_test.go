package main

import (
	"bytes"
	"context"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"sync/atomic"
	"testing"

	"closedsource/gateway/internal/contract"
)

func TestCanceledPreviewDoesNotCallUpstream(t *testing.T) {
	var calls atomic.Int32
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		calls.Add(1)
		w.Write([]byte(legacy))
	}))
	defer upstream.Close()
	h := newHandlerWithClient(upstream.URL, "../../contracts/examples", upstream.Client())
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	r := httptest.NewRequest("POST", "/v2/plans/recommendations", bytes.NewReader(fixture(t, "request.json"))).WithContext(ctx)
	w := httptest.NewRecorder()
	h.ServeHTTP(w, r)
	if got := calls.Load(); got != 0 {
		t.Fatalf("canceled caller triggered %d upstream requests", got)
	}
}

func TestCanceledCatalogAndSavedRecomputationDoNotCallUpstream(t *testing.T) {
	var calls atomic.Int32
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		calls.Add(1)
		w.Write([]byte(legacy))
	}))
	defer upstream.Close()
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	h := newHandlerWithClient(upstream.URL, "../../contracts/examples", upstream.Client())
	r := httptest.NewRequest("GET", "/v2/materials", nil).WithContext(ctx)
	h.ServeHTTP(httptest.NewRecorder(), r)
	var q contract.PlanningRequest
	if err := decode(fixture(t, "request.json"), &q); err != nil {
		t.Fatal(err)
	}
	s := &stateServer{cfg: stateConfig{Upstream: upstream.URL}, outbound: upstream.Client()}
	if _, status, _ := s.computedSnapshot(ctx, q); status == 0 {
		t.Fatal("canceled saved recomputation succeeded")
	}
	if got := calls.Load(); got != 0 {
		t.Fatalf("canceled catalog/saved recomputation triggered %d upstream requests", got)
	}
}

func tlsOutbound(t *testing.T, server *httptest.Server, source outboundTokenSource) *http.Client {
	t.Helper()
	client, err := newPrivateOutboundClient(server.URL, source)
	if err != nil {
		t.Fatal(err)
	}
	transport := client.Transport.(authenticatedTransport)
	transport.base = server.Client().Transport
	client.Transport = transport
	return client
}

func TestPrivateOutboundHeadersAndNoRedirect(t *testing.T) {
	if _, err := newPrivateOutboundClient("https://python.example.test", nil); err == nil {
		t.Fatal("accepted missing token source")
	}
	if _, err := newPrivateOutboundClient("http://python.example.test", testTokenSource(func(context.Context) (string, error) { return "x", nil })); err == nil {
		t.Fatal("accepted HTTP upstream")
	}
	var calls atomic.Int32
	upstream := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		if r.Header.Get("Authorization") != "Bearer server-token" ||
			r.Header.Get("X-Serverless-Authorization") != "Bearer server-token" {
			t.Errorf("missing server-owned identity headers")
		}
		http.Redirect(w, r, "https://different.example.test/collect", http.StatusFound)
	}))
	defer upstream.Close()
	client := tlsOutbound(t, upstream, testTokenSource(func(context.Context) (string, error) { return "server-token", nil }))
	request, err := http.NewRequest("GET", upstream.URL+"/materials", nil)
	if err != nil {
		t.Fatal(err)
	}
	request.Header.Set("Authorization", "Bearer inbound-caller-token")
	request.Header.Set("X-Serverless-Authorization", "Bearer inbound-caller-token")
	response, err := client.Do(request)
	if err != nil {
		t.Fatal(err)
	}
	response.Body.Close()
	if response.StatusCode != http.StatusFound || calls.Load() != 1 {
		t.Fatalf("redirect followed or request omitted: %d calls=%d", response.StatusCode, calls.Load())
	}
	unavailable := tlsOutbound(t, upstream, testTokenSource(func(context.Context) (string, error) { return "", errors.New("no token") }))
	if _, _, err := fetch(context.Background(), unavailable, upstream.URL, "GET", "/materials", nil); err == nil {
		t.Fatal("missing token reached upstream")
	}
	if calls.Load() != 1 {
		t.Fatalf("failed token issuance called upstream %d", calls.Load())
	}
	if _, _, err := fetch(context.Background(), client, "http://127.0.0.1:1", "GET", "/materials", nil); err == nil {
		t.Fatal("HTTP downgrade accepted")
	}
}

func TestPrivateCatalogPreviewAndSavedSnapshotShareOutboundIdentity(t *testing.T) {
	var paths []string
	var pathsMu sync.Mutex
	upstream := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer server-token" ||
			r.Header.Get("X-Serverless-Authorization") != "Bearer server-token" {
			t.Errorf("unauthenticated upstream request")
		}
		pathsMu.Lock()
		paths = append(paths, r.URL.Path)
		pathsMu.Unlock()
		if r.URL.Path == "/materials" {
			w.Write([]byte(`[{"key":"copper","display_name":"Copper","source_series":"Copper","source_unit":"USD per metric tonne","keywords":[]}]`))
		} else {
			w.Write([]byte(legacy))
		}
	}))
	defer upstream.Close()
	client := tlsOutbound(t, upstream, testTokenSource(func(context.Context) (string, error) { return "server-token", nil }))
	h := newHandlerWithClient(upstream.URL, "../../contracts/examples", client)
	if got := call(h, "GET", "/v2/materials", nil).Code; got != 200 {
		t.Fatalf("catalog %d", got)
	}
	if got := call(h, "POST", "/v2/plans/recommendations", fixture(t, "request.json")).Code; got != 200 {
		t.Fatalf("preview %d", got)
	}
	var q contract.PlanningRequest
	if e := decode(fixture(t, "request.json"), &q); e != nil {
		t.Fatal(e)
	}
	s := &stateServer{cfg: stateConfig{Upstream: upstream.URL}, outbound: client}
	snapshot, status, msg := s.computedSnapshot(context.Background(), q)
	if status != 0 || snapshot.Status != "ready" {
		t.Fatalf("saved recompute %d %s", status, msg)
	}
	pathsMu.Lock()
	defer pathsMu.Unlock()
	if strings.Join(paths, ",") != "/materials,/recommend,/recommend" {
		t.Fatalf("auth transport did not cover all paths: %v", paths)
	}
}
