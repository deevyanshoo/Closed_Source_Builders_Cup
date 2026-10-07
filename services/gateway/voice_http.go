package main

import (
	"context"
	"crypto/rand"
	"crypto/sha256"
	_ "embed"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"reflect"
	"regexp"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"time"
	"unicode"
	"unicode/utf8"

	"closedsource/gateway/internal/voice"
)

//go:embed internal/contract/voice_http_schema.json
var voiceHTTPSchemaBytes []byte

const voicePath = "/v2/voice/sessions"
const voiceIdle = 5 * time.Minute
const voiceCreateLimit = 1 << 20
const voiceTurnLimit = 128 << 10
const voiceSnapshotLimit = (1 << 20) - 4096
const voiceOutcomeLimit = 2 << 20

var voiceInstantPattern = regexp.MustCompile(`^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]{1,6})?(Z|[+-][0-9]{2}:[0-9]{2})$`)

func voiceInstant(value string) bool {
	if !voiceInstantPattern.MatchString(value) {
		return false
	}
	if !strings.HasSuffix(value, "Z") {
		hour, _ := strconv.Atoi(value[len(value)-5 : len(value)-3])
		minute, _ := strconv.Atoi(value[len(value)-2:])
		if hour > 23 || minute > 59 {
			return false
		}
	}
	parsed, err := time.Parse(time.RFC3339Nano, value)
	return err == nil && parsed.Year() >= 1
}

type voiceRecord struct {
	owner                                                       voice.Owner
	id, version, fingerprint, scenario, snapshotID, planVersion string
	snapshot                                                    []byte
	arrayCount                                                  int
	ctx                                                         context.Context
	cancel                                                      context.CancelFunc
	last                                                        time.Time
	lease                                                       *voice.Lease
	state                                                       string
	outcome                                                     json.RawMessage
}
type voiceHTTPAdapter struct {
	mu       sync.Mutex
	now      func() time.Time
	clock    *atomic.Value
	last     time.Time
	manager  *voice.Manager
	runner   *voice.Runner
	root     context.Context
	cancel   context.CancelFunc
	sessions map[string]*voiceRecord
	active   int
	closed   bool
	done     chan struct{}
	workers  sync.WaitGroup
}

func newVoiceHTTPAdapter(executor voice.Executor, now func() time.Time) (*voiceHTTPAdapter, error) {
	if now == nil {
		return nil, voice.ErrInvalid
	}
	initial := now()
	var clock atomic.Value
	clock.Store(initial)
	m, err := voice.NewManager(voice.Config{MaxSessions: 8, MaxSessionsPerOwner: 2, IdleTimeout: voiceIdle, MaxQueuedMessages: 1, MaxPayloadBytes: voiceOutcomeLimit, MaxQueuedBytes: voiceOutcomeLimit}, func() time.Time { return clock.Load().(time.Time) })
	if err != nil {
		return nil, err
	}
	r, err := voice.NewRunner(m, executor)
	if err != nil {
		return nil, err
	}
	ctx, cancel := context.WithCancel(context.Background())
	a := &voiceHTTPAdapter{now: now, clock: &clock, last: initial, manager: m, runner: r, root: ctx, cancel: cancel, sessions: make(map[string]*voiceRecord), done: make(chan struct{})}
	go func() {
		ticker := time.NewTicker(time.Second)
		defer ticker.Stop()
		for {
			select {
			case <-ticker.C:
				a.Sweep()
			case <-a.done:
				return
			}
		}
	}()
	return a, nil
}
func voiceStamp() (string, error) {
	var raw [24]byte
	if _, err := rand.Read(raw[:]); err != nil {
		return "", err
	}
	return base64.RawURLEncoding.EncodeToString(raw[:]), nil
}
func (a *voiceHTTPAdapter) sample() time.Time {
	t := a.now()
	a.mu.Lock()
	if t.Before(a.last) {
		t = a.last
	} else {
		a.last = t
	}
	a.clock.Store(t)
	a.mu.Unlock()
	return t
}
func (a *voiceHTTPAdapter) currentSampleLocked(t time.Time) time.Time {
	if t.Before(a.last) {
		return a.last
	}
	return t
}
func (a *voiceHTTPAdapter) sweepLocked(t time.Time) {
	for id, s := range a.sessions {
		if !t.Before(s.last.Add(voiceIdle)) {
			delete(a.sessions, id)
			s.cancel()
			_ = a.manager.Close(s.owner, id)
		}
	}
}
func (a *voiceHTTPAdapter) Sweep() {
	if a == nil {
		return
	}
	t := a.sample()
	a.mu.Lock()
	t = a.currentSampleLocked(t)
	if !a.closed {
		a.sweepLocked(t)
	}
	a.mu.Unlock()
	a.manager.Sweep()
}
func (a *voiceHTTPAdapter) Shutdown(ctx context.Context) error {
	if a == nil {
		return nil
	}
	a.mu.Lock()
	if !a.closed {
		a.closed = true
		close(a.done)
		a.cancel()
		for id, s := range a.sessions {
			s.cancel()
			delete(a.sessions, id)
		}
	}
	a.mu.Unlock()
	a.manager.Shutdown()
	if ctx == nil {
		return nil
	}
	done := make(chan struct{})
	go func() { a.workers.Wait(); close(done) }()
	select {
	case <-done:
		return nil
	case <-ctx.Done():
		return ctx.Err()
	}
}
func voiceErr(w http.ResponseWriter, status int, code, msg string) {
	w.Header().Set("Cache-Control", "no-store")
	stateError(w, status, code, msg)
}
func voiceWrite(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Cache-Control", "no-store")
	write(w, status, v)
}
func voiceID(s string) bool {
	if s == "" || len(s) > 256 || !utf8.ValidString(s) || strings.TrimSpace(s) != s {
		return false
	}
	for _, r := range s {
		if unicode.IsControl(r) {
			return false
		}
	}
	return true
}
func voiceHeaders(r *http.Request) bool {
	if r.URL.RawQuery != "" || r.URL.ForceQuery {
		return false
	}
	for _, key := range []string{"Authorization", "Origin", "Content-Type"} {
		if len(r.Header.Values(key)) > 1 {
			return false
		}
	}
	for _, key := range []string{"X-Actor", "X-Role", "X-Principal-Id", "X-Workspace-Id", "X-Principal", "X-Workspace"} {
		if _, ok := r.Header[http.CanonicalHeaderKey(key)]; ok {
			return false
		}
	}
	return true
}
func voiceNoBody(r *http.Request) bool {
	if r.ContentLength > 0 {
		return false
	}
	b, err := io.ReadAll(io.LimitReader(r.Body, 1))
	return err == nil && len(b) == 0
}
func voiceBody(r *http.Request, limit int) ([]byte, int) {
	if len(r.Header.Values("Content-Type")) != 1 || r.Header.Get("Content-Type") != "application/json" {
		return nil, 422
	}
	b, err := io.ReadAll(io.LimitReader(r.Body, int64(limit)+1))
	if err != nil {
		return nil, 422
	}
	if len(b) > limit {
		return nil, 413
	}
	if len(b) == 0 {
		return nil, 422
	}
	return b, 0
}
func (a *voiceHTTPAdapter) ServeHTTP(w http.ResponseWriter, r *http.Request, p principal) {
	if a == nil {
		voiceErr(w, 503, "voice_unavailable", "Local voice comparison unavailable")
		return
	}
	if !voiceHeaders(r) {
		voiceErr(w, 422, "invalid_request", "Invalid voice request")
		return
	}
	if p.ID == "" || p.Workspace == "" {
		voiceErr(w, 401, "unauthorized", "Valid identity required")
		return
	}
	if p.Role != "viewer" && p.Role != "editor" {
		voiceErr(w, 403, "forbidden", "Voice comparison role unavailable")
		return
	}
	if r.Context().Err() != nil {
		return
	}
	path := strings.TrimPrefix(r.URL.Path, voicePath)
	if path == r.URL.Path {
		voiceErr(w, 404, "voice_session_unavailable", "Voice session unavailable")
		return
	}
	if path == "" {
		if r.Method != http.MethodPost {
			voiceErr(w, 405, "method_not_allowed", "Method not allowed")
			return
		}
		a.create(w, r, p)
		return
	}
	if !strings.HasPrefix(path, "/") {
		voiceErr(w, 404, "voice_session_unavailable", "Voice session unavailable")
		return
	}
	parts := strings.Split(strings.TrimPrefix(path, "/"), "/")
	if len(parts) == 0 || !voiceID(parts[0]) {
		voiceErr(w, 404, "voice_session_unavailable", "Voice session unavailable")
		return
	}
	switch {
	case len(parts) == 1 && r.Method == http.MethodDelete:
		a.closeSession(w, r, p, parts[0])
	case len(parts) == 2 && parts[1] == "turns" && r.Method == http.MethodPost:
		a.turn(w, r, p, parts[0])
	case len(parts) == 4 && parts[1] == "turns" && voiceID(parts[2]) && parts[3] == "cancel" && r.Method == http.MethodPost:
		a.cancelTurn(w, r, p, parts[0], parts[2])
	case len(parts) == 4 && parts[1] == "turns" && voiceID(parts[2]) && parts[3] == "result" && r.Method == http.MethodGet:
		a.result(w, r, p, parts[0], parts[2])
	case len(parts) == 1 || (len(parts) == 2 && parts[1] == "turns") || (len(parts) == 4 && parts[1] == "turns" && (parts[3] == "cancel" || parts[3] == "result")):
		voiceErr(w, 405, "method_not_allowed", "Method not allowed")
	default:
		voiceErr(w, 404, "voice_session_unavailable", "Voice session unavailable")
	}
}
func (a *voiceHTTPAdapter) currentLocked(owner voice.Owner, id string) *voiceRecord {
	s := a.sessions[id]
	if s == nil || s.owner != owner {
		return nil
	}
	return s
}
func (a *voiceHTTPAdapter) create(w http.ResponseWriter, r *http.Request, p principal) {
	raw, status := voiceBody(r, voiceCreateLimit)
	if status != 0 {
		voiceErr(w, status, voiceBodyCode(status), "Invalid voice request")
		return
	}
	value, ok := voiceParse(raw, 0)
	if !ok || !voiceValidName(value, "VoiceHTTPSessionRequest") {
		voiceErr(w, 422, "invalid_request", "Invalid voice request")
		return
	}
	request := value.(map[string]any)
	snapshot := request["snapshot"].(map[string]any)
	if snapshot["workspace_id"] != p.Workspace {
		voiceErr(w, 403, "forbidden", "Workspace access denied")
		return
	}
	snapshotCount := voiceArrayCount(snapshot)
	owned, err := json.Marshal(snapshot)
	if err != nil || len(owned) > voiceSnapshotLimit || !voiceID(snapshot["snapshot_id"].(string)) || !voiceID(snapshot["plan_version"].(string)) || !voiceInstant(snapshot["as_of"].(string)) {
		voiceErr(w, 422, "invalid_request", "Invalid voice snapshot")
		return
	}
	hash := sha256.Sum256(owned)
	version, err := voiceStamp()
	if err != nil {
		voiceErr(w, 503, "voice_unavailable", "Local voice comparison unavailable")
		return
	}
	owner := voice.Owner{PrincipalID: p.ID, WorkspaceID: p.Workspace}
	now := a.sample()
	a.mu.Lock()
	now = a.currentSampleLocked(now)
	a.sweepLocked(now)
	if a.closed {
		a.mu.Unlock()
		voiceErr(w, 503, "voice_unavailable", "Local voice comparison unavailable")
		return
	}
	if r.Context().Err() != nil {
		a.mu.Unlock()
		return
	}
	id, err := a.manager.Create(owner)
	if err != nil {
		a.mu.Unlock()
		voiceErr(w, voiceManagerStatus(err), voiceManagerCode(err), "Voice capacity unavailable")
		return
	}
	sessionCtx, cancel := context.WithCancel(a.root)
	s := &voiceRecord{owner: owner, id: id, version: version, fingerprint: hex.EncodeToString(hash[:]), scenario: "voice-" + version, snapshotID: snapshot["snapshot_id"].(string), planVersion: snapshot["plan_version"].(string), snapshot: append([]byte(nil), owned...), arrayCount: snapshotCount, ctx: sessionCtx, cancel: cancel, last: now}
	a.sessions[id] = s
	response := map[string]any{"contract_version": stateVersion, "session_id": id, "context_version": version, "context_fingerprint": s.fingerprint, "read_only": true, "synthetic": true, "snapshot_id": s.snapshotID, "plan_version": s.planVersion, "scenario_id": s.scenario, "status": "created"}
	a.mu.Unlock()
	voiceWrite(w, 201, response)
	if r.Context().Err() != nil {
		a.mu.Lock()
		if a.sessions[id] == s {
			delete(a.sessions, id)
			s.cancel()
			_ = a.manager.Close(owner, id)
		}
		a.mu.Unlock()
	}
}
func voiceBodyCode(status int) string {
	if status == 413 {
		return "request_too_large"
	}
	return "invalid_request"
}
func voiceManagerStatus(err error) int {
	if errors.Is(err, voice.ErrCapacity) {
		return 429
	}
	return 503
}
func voiceManagerCode(err error) string {
	if errors.Is(err, voice.ErrCapacity) {
		return "voice_capacity"
	}
	return "voice_unavailable"
}
func (a *voiceHTTPAdapter) closeSession(w http.ResponseWriter, r *http.Request, p principal, id string) {
	if !voiceNoBody(r) {
		voiceErr(w, 422, "invalid_request", "Invalid voice request")
		return
	}
	owner := voice.Owner{PrincipalID: p.ID, WorkspaceID: p.Workspace}
	now := a.sample()
	a.mu.Lock()
	now = a.currentSampleLocked(now)
	a.sweepLocked(now)
	s := a.currentLocked(owner, id)
	if s == nil {
		a.mu.Unlock()
		voiceErr(w, 404, "voice_session_unavailable", "Voice session unavailable")
		return
	}
	delete(a.sessions, id)
	s.cancel()
	_ = a.manager.Close(owner, id)
	a.mu.Unlock()
	voiceWrite(w, 200, map[string]any{"contract_version": stateVersion, "session_id": id, "read_only": true, "synthetic": true, "status": "closed"})
}
func (a *voiceHTTPAdapter) turn(w http.ResponseWriter, r *http.Request, p principal, id string) {
	raw, status := voiceBody(r, voiceTurnLimit)
	if status != 0 {
		voiceErr(w, status, voiceBodyCode(status), "Invalid voice request")
		return
	}
	value, ok := voiceParse(raw, 0)
	if !ok || !voiceValidName(value, "VoiceHTTPTurnRequest") {
		voiceErr(w, 422, "invalid_request", "Invalid voice request")
		return
	}
	request := value.(map[string]any)
	turnCount := voiceArrayCount(value)
	owner := voice.Owner{PrincipalID: p.ID, WorkspaceID: p.Workspace}
	now := a.sample()
	a.mu.Lock()
	now = a.currentSampleLocked(now)
	a.sweepLocked(now)
	s := a.currentLocked(owner, id)
	if s == nil {
		a.mu.Unlock()
		voiceErr(w, 404, "voice_session_unavailable", "Voice session unavailable")
		return
	}
	if turnCount+s.arrayCount > 4096 {
		a.mu.Unlock()
		voiceErr(w, 422, "invalid_request", "Invalid voice request")
		return
	}
	if request["context_version"] != s.version {
		a.mu.Unlock()
		voiceErr(w, 409, "context_mismatch", "Voice context version mismatch")
		return
	}
	if a.closed {
		a.mu.Unlock()
		voiceErr(w, 503, "voice_unavailable", "Local voice comparison unavailable")
		return
	}
	if a.active >= 4 {
		a.mu.Unlock()
		voiceErr(w, 429, "voice_capacity", "Voice execution capacity unavailable")
		return
	}
	if r.Context().Err() != nil {
		a.mu.Unlock()
		return
	}
	call, err := json.Marshal(request["call"])
	if err != nil || len(call) > voiceTurnLimit {
		a.mu.Unlock()
		voiceErr(w, 422, "invalid_request", "Invalid voice call")
		return
	}
	a.active++
	lease, err := a.manager.StartTurn(s.ctx, owner, id)
	if err != nil {
		a.active--
		a.mu.Unlock()
		voiceErr(w, voiceManagerStatus(err), voiceManagerCode(err), "Voice session unavailable")
		return
	}
	capture, err := voice.CaptureRecoveryContext(owner, lease, voice.RecoveryMetadata{SnapshotID: s.snapshotID, PlanVersion: s.planVersion, ScenarioID: s.scenario}, s.snapshot)
	if err != nil {
		_ = a.manager.Cancel(owner, lease)
		a.active--
		a.mu.Unlock()
		voiceErr(w, 422, "invalid_request", "Invalid voice snapshot")
		return
	}
	s.lease = lease
	s.state = "pending"
	s.outcome = nil
	s.last = now
	response := map[string]any{"contract_version": stateVersion, "session_id": id, "context_version": s.version, "context_fingerprint": s.fingerprint, "read_only": true, "synthetic": true, "turn_id": lease.TurnID(), "status": "accepted"}
	data, _ := json.Marshal(response)
	data = append(data, '\n')
	a.workers.Add(1)
	a.mu.Unlock()
	if r.Context().Err() != nil {
		_ = a.manager.Cancel(owner, lease)
		a.releaseBeforeAcceptance(owner, id, lease)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Cache-Control", "no-store")
	w.WriteHeader(202)
	n, writeErr := w.Write(data)
	if writeErr != nil || n != len(data) || r.Context().Err() != nil {
		_ = a.manager.Cancel(owner, lease)
		a.releaseBeforeAcceptance(owner, id, lease)
		return
	}
	go a.run(owner, id, lease, capture, call)
}
func (a *voiceHTTPAdapter) releaseBeforeAcceptance(owner voice.Owner, id string, lease *voice.Lease) {
	defer a.workers.Done()
	a.mu.Lock()
	if a.active > 0 {
		a.active--
	}
	if s := a.currentLocked(owner, id); s != nil && s.lease == lease {
		s.lease = nil
		s.state = ""
		s.outcome = nil
	}
	a.mu.Unlock()
}
func (a *voiceHTTPAdapter) run(owner voice.Owner, id string, lease *voice.Lease, capture *voice.RecoveryContext, call []byte) {
	defer a.workers.Done()
	defer func() { a.mu.Lock(); a.active--; a.mu.Unlock() }()
	err := a.runner.Run(owner, lease, capture, call)
	a.mu.Lock()
	defer a.mu.Unlock()
	s := a.currentLocked(owner, id)
	if s == nil || s.lease != lease || a.closed || !a.manager.IsCurrent(owner, lease) {
		return
	}
	if s.state != "pending" {
		return
	}
	if err != nil {
		if errors.Is(err, voice.ErrUnavailable) {
			return
		}
		s.state = "failed"
		s.last = a.clock.Load().(time.Time)
		return
	}
	output, ready, dequeueErr := a.manager.Dequeue(owner, id)
	if dequeueErr != nil || !ready {
		s.state = "failed"
		return
	}
	a.acceptOutputLocked(s, output)
}
func (a *voiceHTTPAdapter) acceptOutputLocked(s *voiceRecord, output voice.Output) {
	if s.lease == nil || output.TurnID != s.lease.TurnID() || len(output.Payload) > voiceOutcomeLimit {
		s.state = "failed"
		return
	}
	var bridge struct {
		Outcome json.RawMessage `json:"outcome"`
	}
	if json.Unmarshal(output.Payload, &bridge) != nil || len(bridge.Outcome) == 0 {
		s.state = "failed"
		return
	}
	parsed, valid := voiceParse(bridge.Outcome, 0)
	if !valid || !voiceValidName(parsed, "VoiceHTTPOutcome") {
		s.state = "failed"
		return
	}
	s.outcome = append([]byte(nil), bridge.Outcome...)
	s.state = "ready"
	s.last = a.clock.Load().(time.Time)
}
func (a *voiceHTTPAdapter) cancelTurn(w http.ResponseWriter, r *http.Request, p principal, id, turnID string) {
	if !voiceNoBody(r) {
		voiceErr(w, 422, "invalid_request", "Invalid voice request")
		return
	}
	owner := voice.Owner{PrincipalID: p.ID, WorkspaceID: p.Workspace}
	now := a.sample()
	a.mu.Lock()
	now = a.currentSampleLocked(now)
	a.sweepLocked(now)
	s := a.currentLocked(owner, id)
	if s != nil && s.lease != nil && !a.manager.IsCurrent(owner, s.lease) {
		delete(a.sessions, id)
		s.cancel()
		_ = a.manager.Close(owner, id)
		s = nil
	}
	if s == nil || s.lease == nil || s.lease.TurnID() != turnID {
		a.mu.Unlock()
		voiceErr(w, 404, "voice_session_unavailable", "Voice session unavailable")
		return
	}
	lease := s.lease
	_ = a.manager.Cancel(owner, lease)
	s.lease = nil
	s.state = ""
	s.outcome = nil
	s.last = now
	response := map[string]any{"contract_version": stateVersion, "session_id": id, "context_version": s.version, "context_fingerprint": s.fingerprint, "read_only": true, "synthetic": true, "turn_id": turnID, "status": "cancelled"}
	a.mu.Unlock()
	voiceWrite(w, 200, response)
}
func (a *voiceHTTPAdapter) result(w http.ResponseWriter, r *http.Request, p principal, id, turnID string) {
	if !voiceNoBody(r) {
		voiceErr(w, 422, "invalid_request", "Invalid voice request")
		return
	}
	if r.Context().Err() != nil {
		return
	}
	owner := voice.Owner{PrincipalID: p.ID, WorkspaceID: p.Workspace}
	now := a.sample()
	a.mu.Lock()
	now = a.currentSampleLocked(now)
	a.sweepLocked(now)
	s := a.currentLocked(owner, id)
	if s != nil && s.lease != nil && !a.manager.IsCurrent(owner, s.lease) {
		delete(a.sessions, id)
		s.cancel()
		_ = a.manager.Close(owner, id)
		s = nil
	}
	if s == nil || s.lease == nil || s.lease.TurnID() != turnID {
		a.mu.Unlock()
		voiceErr(w, 404, "voice_session_unavailable", "Voice session unavailable")
		return
	}
	output, ready, dequeueErr := a.manager.Dequeue(owner, id)
	if dequeueErr != nil {
		delete(a.sessions, id)
		s.cancel()
		_ = a.manager.Close(owner, id)
		a.mu.Unlock()
		voiceErr(w, 404, "voice_session_unavailable", "Voice session unavailable")
		return
	}
	if ready && s.state == "pending" {
		a.acceptOutputLocked(s, output)
	}
	s.last = now
	response := map[string]any{"contract_version": stateVersion, "session_id": id, "context_version": s.version, "context_fingerprint": s.fingerprint, "read_only": true, "synthetic": true, "turn_id": turnID, "state": s.state}
	if s.state == "ready" {
		response["outcome"] = append(json.RawMessage(nil), s.outcome...)
	}
	if s.state == "failed" {
		response["error"] = map[string]string{"code": "execution_unavailable", "message": "Local voice execution unavailable"}
	}
	a.mu.Unlock()
	if r.Context().Err() != nil {
		return
	}
	a.mu.Lock()
	current := a.currentLocked(owner, id)
	valid := !a.closed && current != nil && current.lease != nil && current.lease.TurnID() == turnID && a.manager.IsCurrent(owner, current.lease)
	a.mu.Unlock()
	if !valid {
		voiceErr(w, 404, "voice_session_unavailable", "Voice session unavailable")
		return
	}
	if r.Context().Err() != nil {
		return
	}
	voiceWrite(w, 200, response)
}

var voiceDefinitions = func() map[string]map[string]any {
	var doc struct {
		Components struct {
			Schemas map[string]map[string]any `json:"schemas"`
		} `json:"components"`
	}
	if json.Unmarshal(voiceHTTPSchemaBytes, &doc) != nil {
		return nil
	}
	return doc.Components.Schemas
}()

func voiceValidName(value any, name string) bool { return voiceValid(value, voiceDefinitions[name], 0) }
func voiceValid(value any, schema map[string]any, depth int) bool {
	if depth > 64 || schema == nil {
		return false
	}
	if ref, ok := schema["$ref"].(string); ok {
		const prefix = "#/components/schemas/"
		if !strings.HasPrefix(ref, prefix) {
			return false
		}
		return voiceValid(value, voiceDefinitions[strings.TrimPrefix(ref, prefix)], depth+1)
	}
	if branches, ok := schema["oneOf"].([]any); ok {
		count := 0
		for _, branch := range branches {
			if entry, ok := branch.(map[string]any); ok && voiceValid(value, entry, depth+1) {
				count++
			}
		}
		return count == 1
	}
	if branches, ok := schema["anyOf"].([]any); ok {
		for _, branch := range branches {
			if entry, ok := branch.(map[string]any); ok && voiceValid(value, entry, depth+1) {
				return true
			}
		}
		return false
	}
	switch schema["type"] {
	case "object":
		obj, ok := value.(map[string]any)
		if !ok {
			return false
		}
		props, ok := schema["properties"].(map[string]any)
		if !ok {
			return false
		}
		if required, ok := schema["required"].([]any); ok {
			for _, name := range required {
				key, ok := name.(string)
				if !ok {
					return false
				}
				if _, has := obj[key]; !has {
					return false
				}
			}
		}
		for key, v := range obj {
			property, has := props[key]
			if !has {
				if schema["additionalProperties"] == false {
					return false
				}
				continue
			}
			definition, ok := property.(map[string]any)
			if !ok || !voiceValid(v, definition, depth+1) {
				return false
			}
		}
	case "array":
		array, ok := value.([]any)
		if !ok {
			return false
		}
		if min, ok := schema["minItems"].(float64); ok && len(array) < int(min) {
			return false
		}
		if max, ok := schema["maxItems"].(float64); ok && len(array) > int(max) {
			return false
		}
		item, ok := schema["items"].(map[string]any)
		if !ok {
			return false
		}
		seen := map[string]bool{}
		for _, v := range array {
			if !voiceValid(v, item, depth+1) {
				return false
			}
			if schema["uniqueItems"] == true {
				b, e := json.Marshal(v)
				if e != nil || seen[string(b)] {
					return false
				}
				seen[string(b)] = true
			}
		}
	case "string":
		str, ok := value.(string)
		if !ok {
			return false
		}
		if min, ok := schema["minLength"].(float64); ok && len([]rune(str)) < int(min) {
			return false
		}
		if max, ok := schema["maxLength"].(float64); ok && len([]rune(str)) > int(max) {
			return false
		}
		if pattern, ok := schema["pattern"].(string); ok {
			matched, err := regexp.MatchString(pattern, str)
			if err != nil || !matched {
				return false
			}
		}
	case "boolean":
		if _, ok := value.(bool); !ok {
			return false
		}
	default:
		return false
	}
	if c, ok := schema["const"]; ok && !reflect.DeepEqual(value, c) {
		return false
	}
	if options, ok := schema["enum"].([]any); ok {
		for _, candidate := range options {
			if reflect.DeepEqual(value, candidate) {
				return true
			}
		}
		return false
	}
	return true
}
func voiceParse(raw []byte, initialCount int) (any, bool) {
	if !utf8.Valid(raw) || !voiceEscapesValid(raw) {
		return nil, false
	}
	value, err := recoveryParse(raw)
	if err != nil {
		return nil, false
	}
	count := initialCount
	if !voiceTree(value, 0, &count) {
		return nil, false
	}
	return value, true
}
func voiceTree(value any, depth int, total *int) bool {
	if depth > 16 {
		return false
	}
	switch x := value.(type) {
	case map[string]any:
		for k, v := range x {
			if !utf8.ValidString(k) || !voiceTree(v, depth+1, total) {
				return false
			}
		}
	case []any:
		if len(x) > 256 || *total+len(x) > 4096 {
			return false
		}
		*total += len(x)
		for _, v := range x {
			if !voiceTree(v, depth+1, total) {
				return false
			}
		}
	case string:
		return utf8.ValidString(x)
	case bool:
		return true
	default:
		return false
	}
	return true
}
func voiceEscapesValid(raw []byte) bool {
	in := false
	for i := 0; i < len(raw); i++ {
		switch raw[i] {
		case '"':
			in = !in
		case '\\':
			if !in {
				continue
			}
			if i+1 >= len(raw) {
				return false
			}
			if raw[i+1] != 'u' {
				i++
				continue
			}
			if i+6 > len(raw) {
				return false
			}
			unit, ok := voiceHex4(raw[i+2 : i+6])
			if !ok {
				return false
			}
			i += 5
			if unit >= 0xdc00 && unit <= 0xdfff {
				return false
			}
			if unit >= 0xd800 && unit <= 0xdbff {
				start := i + 1
				if start+6 > len(raw) || raw[start] != '\\' || raw[start+1] != 'u' {
					return false
				}
				low, ok := voiceHex4(raw[start+2 : start+6])
				if !ok || low < 0xdc00 || low > 0xdfff {
					return false
				}
				i = start + 5
			}
		}
	}
	return true
}
func voiceHex4(raw []byte) (uint16, bool) {
	var n uint16
	for _, b := range raw {
		n <<= 4
		switch {
		case b >= '0' && b <= '9':
			n += uint16(b - '0')
		case b >= 'a' && b <= 'f':
			n += uint16(b - 'a' + 10)
		case b >= 'A' && b <= 'F':
			n += uint16(b - 'A' + 10)
		default:
			return 0, false
		}
	}
	return n, true
}

func voiceArrayCount(value any) int {
	switch x := value.(type) {
	case []any:
		n := len(x)
		for _, v := range x {
			n += voiceArrayCount(v)
		}
		return n
	case map[string]any:
		n := 0
		for _, v := range x {
			n += voiceArrayCount(v)
		}
		return n
	default:
		return 0
	}
}
