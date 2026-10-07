package main

import (
	"context"
	"errors"
	"net"
	"net/http"
	"sync"
	"testing"
	"time"
)

type voiceShutdownHandler struct {
	entered  chan struct{}
	released chan struct{}
	once     sync.Once
}

func TestVoiceHTTPFailedDrainForceClosesConnection(t *testing.T) {
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer listener.Close()
	entered := make(chan struct{})
	released := make(chan struct{})
	handler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		close(entered)
		<-r.Context().Done()
		close(released)
	})
	parent, cancel := context.WithCancel(context.Background())
	defer cancel()
	served := make(chan error, 1)
	go func() { served <- serveGatewayWithContext(parent, listener, handler, 30*time.Millisecond) }()
	requestDone := make(chan struct{})
	go func() {
		res, err := http.Get("http://" + listener.Addr().String())
		if err == nil {
			res.Body.Close()
		}
		close(requestDone)
	}()
	select {
	case <-entered:
	case <-time.After(time.Second):
		t.Fatal("handler did not enter")
	}
	cancel()
	select {
	case err := <-served:
		if !errors.Is(err, context.DeadlineExceeded) {
			t.Fatalf("failed drain error=%v", err)
		}
	case <-time.After(time.Second):
		t.Fatal("failed drain did not return")
	}
	select {
	case <-released:
	case <-time.After(time.Second):
		t.Fatal("force close did not cancel handler")
	}
	select {
	case <-requestDone:
	case <-time.After(time.Second):
		t.Fatal("force close did not release client")
	}
}

func (h *voiceShutdownHandler) ServeHTTP(w http.ResponseWriter, _ *http.Request) {
	close(h.entered)
	<-h.released
	w.WriteHeader(200)
}
func (h *voiceShutdownHandler) Shutdown(context.Context) error {
	h.once.Do(func() { close(h.released) })
	return nil
}
func TestVoiceHTTPShutdownCancelsChildBeforeHTTPDrain(t *testing.T) {
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer listener.Close()
	h := &voiceShutdownHandler{entered: make(chan struct{}), released: make(chan struct{})}
	defer h.Shutdown(context.Background())
	parent, cancel := context.WithCancel(context.Background())
	defer cancel()
	served := make(chan error, 1)
	go func() { served <- serveGatewayWithContext(parent, listener, h, 100*time.Millisecond) }()
	response := make(chan error, 1)
	go func() {
		res, e := http.Get("http://" + listener.Addr().String())
		if e == nil {
			res.Body.Close()
		}
		response <- e
	}()
	select {
	case <-h.entered:
	case <-time.After(2 * time.Second):
		t.Fatal("handler did not enter")
	}
	cancel()
	select {
	case err := <-served:
		if err != nil {
			t.Fatalf("shutdown waited for drain before child cancellation: %v", err)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("shutdown failed to finish")
	}
	select {
	case err := <-response:
		if err != nil {
			t.Fatalf("request failed after child shutdown: %v", err)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("request remained blocked")
	}
}
func TestVoiceHTTPRuntimeFlagsFailClosed(t *testing.T) {
	cfg := stateConfig{Mode: "private_machine"}
	if _, err := withVoiceLocalFlags(cfg, []string{"-voice-local-python=/bin/python", "-voice-local-source=/tmp"}); err == nil {
		t.Fatal("private machine voice flag accepted")
	}
	if _, err := withVoiceLocalFlags(stateConfig{}, []string{"-unknown=x"}); err == nil {
		t.Fatal("unknown flag accepted")
	}
	if value, err := withVoiceLocalFlags(stateConfig{}, nil); err != nil || value.VoicePythonPath != "" {
		t.Fatal("default local voice unexpectedly enabled")
	}
}
