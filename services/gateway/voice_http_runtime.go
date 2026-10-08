package main

import (
	"context"
	"errors"
	"net"
	"net/http"
	"os"
	"os/signal"
	"path/filepath"
	"strings"
	"syscall"
	"time"

	"closedsource/gateway/internal/voice"
)

func initVoiceHTTP(cfg stateConfig) *voiceHTTPAdapter {
	if cfg.Mode != "" && cfg.Mode != "local" {
		return nil
	}
	if cfg.VoicePythonPath == "" || cfg.VoiceSourcePath == "" {
		return nil
	}
	if !filepath.IsAbs(cfg.VoicePythonPath) || !filepath.IsAbs(cfg.VoiceSourcePath) {
		return nil
	}
	executor, err := voice.NewLocalPythonExecutor(voice.LocalPythonConfig{PythonPath: cfg.VoicePythonPath, SourcePath: cfg.VoiceSourcePath})
	if err != nil {
		return nil
	}
	adapter, err := newVoiceHTTPAdapter(executor, time.Now)
	if err != nil {
		return nil
	}
	return adapter
}
func withVoiceLocalFlags(cfg stateConfig, args []string) (stateConfig, error) {
	seen := map[string]bool{}
	for i := 0; i < len(args); i++ {
		arg := args[i]
		var name, value string
		if strings.Contains(arg, "=") {
			parts := strings.SplitN(arg, "=", 2)
			name, value = parts[0], parts[1]
		} else {
			name = arg
			if i+1 >= len(args) {
				return cfg, errors.New("invalid voice local flag")
			}
			i++
			value = args[i]
		}
		if seen[name] || value == "" || strings.HasPrefix(value, "-") {
			return cfg, errors.New("invalid voice local flag")
		}
		seen[name] = true
		switch name {
		case "-voice-local-python":
			cfg.VoicePythonPath = value
		case "-voice-local-source":
			cfg.VoiceSourcePath = value
		default:
			return cfg, errors.New("unknown gateway flag")
		}
	}
	if cfg.VoicePythonPath != "" || cfg.VoiceSourcePath != "" {
		if cfg.Mode == "private_machine" || cfg.VoicePythonPath == "" || cfg.VoiceSourcePath == "" || !filepath.IsAbs(cfg.VoicePythonPath) || !filepath.IsAbs(cfg.VoiceSourcePath) || strings.ContainsRune(cfg.VoicePythonPath, 0) || strings.ContainsRune(cfg.VoiceSourcePath, 0) {
			return cfg, errors.New("unsafe local voice configuration")
		}
	}
	return cfg, nil
}
func serveGateway(addr string, handler http.Handler) error {
	signals, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	listener, err := net.Listen("tcp", addr)
	if err != nil {
		if closer, ok := handler.(interface{ Shutdown(context.Context) error }); ok {
			_ = closer.Shutdown(context.Background())
		}
		return err
	}
	return serveGatewayWithContext(signals, listener, handler, 5*time.Second)
}
func serveGatewayWithContext(parent context.Context, listener net.Listener, handler http.Handler, grace time.Duration) error {
	if parent == nil || listener == nil || handler == nil || grace <= 0 {
		return errors.New("invalid gateway runtime")
	}
	server := &http.Server{Handler: handler, ReadHeaderTimeout: 5 * time.Second}
	done := make(chan error, 1)
	go func() { done <- server.Serve(listener) }()
	var serveErr error
	select {
	case serveErr = <-done:
		ctx, cancel := context.WithTimeout(context.Background(), grace)
		defer cancel()
		if closer, ok := handler.(interface{ Shutdown(context.Context) error }); ok {
			if err := closer.Shutdown(ctx); err != nil {
				_ = server.Close()
				return err
			}
		}
		if err := server.Shutdown(ctx); err != nil {
			_ = server.Close()
			return err
		}
		if errors.Is(serveErr, http.ErrServerClosed) {
			return nil
		}
		return serveErr
	case <-parent.Done():
		ctx, cancel := context.WithTimeout(context.Background(), grace)
		defer cancel()
		var childErr error
		if closer, ok := handler.(interface{ Shutdown(context.Context) error }); ok {
			childErr = closer.Shutdown(ctx)
		}
		drainErr := server.Shutdown(ctx)
		if drainErr != nil {
			_ = server.Close()
		}
		serveErr = <-done
		if childErr != nil {
			return childErr
		}
		if drainErr != nil {
			return drainErr
		}
		if serveErr != nil && !errors.Is(serveErr, http.ErrServerClosed) {
			return serveErr
		}
		return nil
	}
}
func (s *stateServer) Shutdown(ctx context.Context) error {
	if s == nil {
		return nil
	}
	if ctx == nil {
		ctx = context.Background()
	}
	// Start both cancellations before waiting for either component to reap.
	done := make(chan error, 2)
	pending := 0
	if s.shipment != nil {
		pending++
		go func() { done <- s.shipment.Shutdown(ctx) }()
	}
	if s.voice != nil {
		pending++
		go func() { done <- s.voice.Shutdown(ctx) }()
	}
	var result error
	for pending > 0 {
		select {
		case err := <-done:
			result = errors.Join(result, err)
			pending--
		case <-ctx.Done():
			return errors.Join(result, ctx.Err())
		}
	}
	return result
}

func (s *stateServer) voiceHTTPRoute(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Cache-Control", "no-store")
	if !voiceHeaders(r) {
		voiceErr(w, 422, "invalid_request", "Invalid voice request")
		return
	}
	var p principal
	if s.cfg.Mode == "private_machine" {
		mapped, ok := r.Context().Value(machinePrincipalKey{}).(principal)
		if !ok {
			voiceErr(w, 401, "unauthorized", "Valid identity required")
			return
		}
		p = mapped
	} else {
		if s.auth == nil {
			voiceErr(w, 401, "unauthorized", "Valid identity required")
			return
		}
		mapped, ok := s.auth.Authenticate(w, r)
		if !ok {
			return
		}
		p = mapped
	}
	if p.ID == "" || p.Workspace == "" {
		voiceErr(w, 401, "unauthorized", "Valid identity required")
		return
	}
	if p.Role != "viewer" && p.Role != "editor" {
		voiceErr(w, 403, "forbidden", "Voice comparison role unavailable")
		return
	}
	if s.voice == nil {
		voiceErr(w, 503, "voice_unavailable", "Local voice comparison unavailable")
		return
	}
	if r.URL.Path == savedVoicePath {
		s.savedVoiceSession(w, r, p)
		return
	}
	s.voice.ServeHTTP(w, r, p)
}
