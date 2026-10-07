package main

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"closedsource/gateway/internal/voice"
)

// The source is constructed on the server, from an offline SDK projection.
// Its input is the declared arguments only; Python then reconstructs pinned SDK
// types, while the unchanged fixed numerical executor handles the tool call.
func proposalFixtureSource(t *testing.T) voice.ProposalSource {
	t.Helper()
	return proposalSourceFunc(func(_ context.Context, arguments []byte) ([]byte, error) {
		var args map[string]any
		if err := json.Unmarshal(arguments, &args); err != nil {
			return nil, err
		}
		if len(args) != 2 || args["arrival_replacements"] == nil || args["assignments"] == nil {
			t.Errorf("source received more than declared arguments: %s", arguments)
		}
		return json.Marshal(map[string]any{"proposal_version": "1", "sdk_response": map[string]any{"candidates": []any{map[string]any{"finish_reason": "STOP", "content": map[string]any{"role": "model", "parts": []any{map[string]any{"function_call": map[string]any{"name": "compare_receipt_recovery", "args": args}}}}}}}})
	})
}

func TestProposalGatewayRevisionChangeDuringSourcePreventsExecution(t *testing.T) {
	h, db := productionTestHandler(t)
	state := h.(*stateServer)
	entered := make(chan struct{})
	release := make(chan struct{})
	var releaseOnce sync.Once
	var numericalCalls atomic.Int32
	source := proposalSourceFunc(func(context.Context, []byte) ([]byte, error) {
		close(entered)
		<-release // deliberately noncooperative; its worker slot must remain occupied
		return []byte(`{"proposal_version":"1","sdk_response":{}}`), nil
	})
	mapper := proposalMapperFunc(func(context.Context, []byte) ([]byte, error) {
		return []byte(`{"name":"compare_receipt_recovery","arguments":{}}`), nil
	})
	adapter, err := newVoiceHTTPAdapterWithProposal(voiceExecutorFunc(func(context.Context, []byte) ([]byte, error) { numericalCalls.Add(1); return nil, nil }), source, mapper, time.Now)
	if err != nil {
		t.Fatal(err)
	}
	state.voice = adapter
	adapter.production = state
	t.Cleanup(func() { releaseOnce.Do(func() { close(release) }); _ = adapter.Shutdown(context.Background()) })
	var fixture map[string]any
	if err := json.Unmarshal(voiceFixture(t, "voice_http_recovery_session.json"), &fixture); err != nil {
		t.Fatal(err)
	}
	created := productionCall(state, "POST", "/v2/workspaces/W/production-contexts", "editor", map[string]any{"contract_version": stateVersion, "snapshot": fixture["snapshot"]})
	if created.Code != 201 {
		t.Fatalf("context create %d %s", created.Code, created.Body.String())
	}
	contextID := voiceConnectedJSON(t, created.Body.Bytes())["context"].(map[string]any)["context_id"].(string)
	t.Cleanup(func() {
		_, _ = db.Exec(context.Background(), `DELETE FROM cs_production_contexts.contexts WHERE context_id=$1`, contextID)
	})
	saved := productionCall(state, "POST", savedVoicePath, "viewer", map[string]any{"contract_version": stateVersion, "context_id": contextID, "expected_revision": 1})
	if saved.Code != 201 {
		t.Fatalf("saved create %d %s", saved.Code, saved.Body.String())
	}
	session := voiceConnectedJSON(t, saved.Body.Bytes())
	id := session["session_id"].(string)
	turn := productionCall(state, "POST", voicePath+"/"+id+"/turns", "viewer", voiceConnectedJSON(t, voiceTurnBody(t, session["context_version"].(string))))
	if turn.Code != 202 {
		t.Fatalf("turn %d %s", turn.Code, turn.Body.String())
	}
	turnID := voiceConnectedJSON(t, turn.Body.Bytes())["turn_id"].(string)
	select {
	case <-entered:
	case <-time.After(5 * time.Second):
		t.Fatal("source not reached")
	}
	updated := productionCall(state, "PUT", "/v2/production-contexts/"+contextID, "editor", map[string]any{"contract_version": stateVersion, "expected_revision": 1, "snapshot": fixture["snapshot"]})
	if updated.Code != 200 {
		t.Fatalf("context replace %d %s", updated.Code, updated.Body.String())
	}
	releaseOnce.Do(func() { close(release) })
	for deadline := time.Now().Add(5 * time.Second); time.Now().Before(deadline); time.Sleep(10 * time.Millisecond) {
		adapter.mu.Lock()
		active := adapter.active
		adapter.mu.Unlock()
		if active == 0 {
			break
		}
	}
	if got := numericalCalls.Load(); got != 0 {
		t.Fatalf("stale proposal executed numerical tool %d times", got)
	}
	poll := productionCall(state, "GET", voicePath+"/"+id+"/turns/"+turnID+"/result", "viewer", nil)
	if poll.Code != 409 {
		t.Fatalf("stale result %d %s", poll.Code, poll.Body.String())
	}
	closed := productionCall(state, "DELETE", voicePath+"/"+id, "viewer", nil)
	if closed.Code != 200 {
		t.Fatalf("cleanup close %d", closed.Code)
	}
}

func TestProposalGatewayRevisionChangeDuringNumericalExecutionPreventsPublication(t *testing.T) {
	python := os.Getenv("VOICE_LOCAL_PYTHON")
	if python == "" {
		t.Skip("trusted offline Python path unset")
	}
	h, db := productionTestHandler(t)
	state := h.(*stateServer)
	sourcePath, err := filepath.Abs("../../procurement-engine/src")
	if err != nil {
		t.Fatal(err)
	}
	config := voice.LocalPythonConfig{PythonPath: python, SourcePath: sourcePath}
	fixed, err := voice.NewLocalPythonExecutor(config)
	if err != nil {
		t.Fatal(err)
	}
	mapper, err := voice.NewLocalPythonProposalMapper(config)
	if err != nil {
		t.Fatal(err)
	}
	entered := make(chan struct{})
	release := make(chan struct{})
	var releaseOnce sync.Once
	executor := voiceExecutorFunc(func(ctx context.Context, request []byte) ([]byte, error) {
		close(entered)
		<-release
		return fixed.Execute(ctx, request)
	})
	adapter, err := newVoiceHTTPAdapterWithProposal(executor, proposalFixtureSource(t), mapper, time.Now)
	if err != nil {
		t.Fatal(err)
	}
	state.voice = adapter
	adapter.production = state
	t.Cleanup(func() { releaseOnce.Do(func() { close(release) }); _ = adapter.Shutdown(context.Background()) })
	var fixture map[string]any
	if err := json.Unmarshal(voiceFixture(t, "voice_http_recovery_session.json"), &fixture); err != nil {
		t.Fatal(err)
	}
	created := productionCall(state, "POST", "/v2/workspaces/W/production-contexts", "editor", map[string]any{"contract_version": stateVersion, "snapshot": fixture["snapshot"]})
	if created.Code != 201 {
		t.Fatalf("context create %d %s", created.Code, created.Body.String())
	}
	contextID := voiceConnectedJSON(t, created.Body.Bytes())["context"].(map[string]any)["context_id"].(string)
	t.Cleanup(func() {
		_, _ = db.Exec(context.Background(), `DELETE FROM cs_production_contexts.contexts WHERE context_id=$1`, contextID)
	})
	saved := productionCall(state, "POST", savedVoicePath, "viewer", map[string]any{"contract_version": stateVersion, "context_id": contextID, "expected_revision": 1})
	if saved.Code != 201 {
		t.Fatalf("saved create %d %s", saved.Code, saved.Body.String())
	}
	session := voiceConnectedJSON(t, saved.Body.Bytes())
	id := session["session_id"].(string)
	turn := productionCall(state, "POST", voicePath+"/"+id+"/turns", "viewer", voiceConnectedJSON(t, voiceTurnBody(t, session["context_version"].(string))))
	if turn.Code != 202 {
		t.Fatalf("turn %d %s", turn.Code, turn.Body.String())
	}
	turnID := voiceConnectedJSON(t, turn.Body.Bytes())["turn_id"].(string)
	select {
	case <-entered:
	case <-time.After(5 * time.Second):
		t.Fatal("numerical executor was not reached")
	}
	updated := productionCall(state, "PUT", "/v2/production-contexts/"+contextID, "editor", map[string]any{"contract_version": stateVersion, "expected_revision": 1, "snapshot": fixture["snapshot"]})
	if updated.Code != 200 {
		t.Fatalf("context replace %d %s", updated.Code, updated.Body.String())
	}
	releaseOnce.Do(func() { close(release) })
	for deadline := time.Now().Add(5 * time.Second); time.Now().Before(deadline); time.Sleep(10 * time.Millisecond) {
		adapter.mu.Lock()
		active := adapter.active
		adapter.mu.Unlock()
		if active == 0 {
			break
		}
	}
	result := productionCall(state, "GET", voicePath+"/"+id+"/turns/"+turnID+"/result", "viewer", nil)
	if result.Code != 409 {
		t.Fatalf("stale numerical output published: %d %s", result.Code, result.Body.String())
	}
	if r := productionCall(state, "DELETE", voicePath+"/"+id, "viewer", nil); r.Code != 200 {
		t.Fatalf("cleanup close %d", r.Code)
	}
}

func TestProposalGatewayUnavailableInvalidTimeoutAndCleanup(t *testing.T) {
	for _, tc := range []struct {
		name    string
		source  voice.ProposalSource
		mapper  voice.ProposalMapper
		timeout time.Duration
	}{
		{"unavailable source", nil, proposalMapperFunc(func(context.Context, []byte) ([]byte, error) { t.Fatal("mapper called"); return nil, nil }), time.Second},
		{"source panic", proposalSourceFunc(func(context.Context, []byte) ([]byte, error) { panic("private source data") }), proposalMapperFunc(func(context.Context, []byte) ([]byte, error) { t.Fatal("mapper called"); return nil, nil }), time.Second},
		{"malformed projection", proposalSourceFunc(func(context.Context, []byte) ([]byte, error) { return []byte(`{}`), nil }), proposalMapperFunc(func(context.Context, []byte) ([]byte, error) { return nil, voice.ErrInvalid }), time.Second},
		{"authority in call", proposalSourceFunc(func(context.Context, []byte) ([]byte, error) { return []byte(`{}`), nil }), proposalMapperFunc(func(context.Context, []byte) ([]byte, error) {
			return []byte(`{"name":"compare_receipt_recovery","arguments":{"workspace_id":"foreign"}}`), nil
		}), time.Second},
		{"deadline", proposalSourceFunc(func(ctx context.Context, _ []byte) ([]byte, error) { <-ctx.Done(); return []byte(`{}`), nil }), proposalMapperFunc(func(context.Context, []byte) ([]byte, error) { t.Fatal("mapper called after timeout"); return nil, nil }), 50 * time.Millisecond},
	} {
		t.Run(tc.name, func(t *testing.T) {
			h, db := productionTestHandler(t)
			state := h.(*stateServer)
			var numericalCalls atomic.Int32
			adapter, err := newVoiceHTTPAdapterWithProposal(voiceExecutorFunc(func(context.Context, []byte) ([]byte, error) { numericalCalls.Add(1); return nil, nil }), tc.source, tc.mapper, time.Now)
			if err != nil {
				t.Fatal(err)
			}
			adapter.proposalTimeout = tc.timeout
			state.voice = adapter
			adapter.production = state
			t.Cleanup(func() { _ = adapter.Shutdown(context.Background()) })
			var fixture map[string]any
			if err := json.Unmarshal(voiceFixture(t, "voice_http_recovery_session.json"), &fixture); err != nil {
				t.Fatal(err)
			}
			created := productionCall(state, "POST", "/v2/workspaces/W/production-contexts", "editor", map[string]any{"contract_version": stateVersion, "snapshot": fixture["snapshot"]})
			if created.Code != 201 {
				t.Fatalf("context %d %s", created.Code, created.Body.String())
			}
			contextID := voiceConnectedJSON(t, created.Body.Bytes())["context"].(map[string]any)["context_id"].(string)
			t.Cleanup(func() {
				_, _ = db.Exec(context.Background(), `DELETE FROM cs_production_contexts.contexts WHERE context_id=$1`, contextID)
			})
			saved := productionCall(state, "POST", savedVoicePath, "viewer", map[string]any{"contract_version": stateVersion, "context_id": contextID, "expected_revision": 1})
			if saved.Code != 201 {
				t.Fatalf("saved %d %s", saved.Code, saved.Body.String())
			}
			session := voiceConnectedJSON(t, saved.Body.Bytes())
			id := session["session_id"].(string)
			turn := productionCall(state, "POST", voicePath+"/"+id+"/turns", "viewer", voiceConnectedJSON(t, voiceTurnBody(t, session["context_version"].(string))))
			if turn.Code != 202 {
				t.Fatalf("turn %d %s", turn.Code, turn.Body.String())
			}
			turnID := voiceConnectedJSON(t, turn.Body.Bytes())["turn_id"].(string)
			var failed *httptest.ResponseRecorder
			for deadline := time.Now().Add(5 * time.Second); time.Now().Before(deadline); time.Sleep(10 * time.Millisecond) {
				result := productionCall(state, "GET", voicePath+"/"+id+"/turns/"+turnID+"/result", "viewer", nil)
				if result.Code != 200 {
					t.Fatalf("poll %d %s", result.Code, result.Body.String())
				}
				if voiceConnectedJSON(t, result.Body.Bytes())["state"] == "failed" {
					failed = result
					break
				}
			}
			if failed == nil {
				t.Fatal("failure did not settle")
			}
			if !bytes.Contains(failed.Body.Bytes(), []byte(`"code":"execution_unavailable"`)) || numericalCalls.Load() != 0 {
				t.Fatalf("failure leaked or executed %s calls=%d", failed.Body.String(), numericalCalls.Load())
			}
			cancel := productionCall(state, "POST", voicePath+"/"+id+"/turns/"+turnID+"/cancel", "viewer", nil)
			if cancel.Code != 200 {
				t.Fatalf("cancel after failure %d", cancel.Code)
			}
			closed := productionCall(state, "DELETE", voicePath+"/"+id, "viewer", nil)
			if closed.Code != 200 {
				t.Fatalf("close after failure %d", closed.Code)
			}
		})
	}
}

func TestProposalGatewayNoncooperativeSourceOccupiesFourSlotsThroughCancelAndClose(t *testing.T) {
	h, db := productionTestHandler(t)
	state := h.(*stateServer)
	entered := make(chan struct{}, 4)
	release := make(chan struct{})
	var releaseOnce sync.Once
	source := proposalSourceFunc(func(context.Context, []byte) ([]byte, error) {
		select {
		case entered <- struct{}{}:
		default:
		}
		<-release
		return []byte(`{}`), nil
	})
	mapper := proposalMapperFunc(func(context.Context, []byte) ([]byte, error) { return nil, voice.ErrInvalid })
	adapter, err := newVoiceHTTPAdapterWithProposal(voiceExecutorFunc(func(context.Context, []byte) ([]byte, error) {
		t.Error("late callback reached numerical execution")
		return nil, nil
	}), source, mapper, time.Now)
	if err != nil {
		t.Fatal(err)
	}
	state.voice = adapter
	adapter.production = state
	t.Cleanup(func() { releaseOnce.Do(func() { close(release) }); _ = adapter.Shutdown(context.Background()) })
	var fixture map[string]any
	if err := json.Unmarshal(voiceFixture(t, "voice_http_recovery_session.json"), &fixture); err != nil {
		t.Fatal(err)
	}
	type turn struct{ id, version, turnID, owner string }
	turns := make([]turn, 0, 4)
	for _, owner := range []string{"viewer", "viewer", "editor", "editor"} {
		created := productionCall(state, "POST", "/v2/workspaces/W/production-contexts", "editor", map[string]any{"contract_version": stateVersion, "snapshot": fixture["snapshot"]})
		if created.Code != 201 {
			t.Fatalf("context %d %s", created.Code, created.Body.String())
		}
		contextID := voiceConnectedJSON(t, created.Body.Bytes())["context"].(map[string]any)["context_id"].(string)
		t.Cleanup(func() {
			_, _ = db.Exec(context.Background(), `DELETE FROM cs_production_contexts.contexts WHERE context_id=$1`, contextID)
		})
		saved := productionCall(state, "POST", savedVoicePath, owner, map[string]any{"contract_version": stateVersion, "context_id": contextID, "expected_revision": 1})
		if saved.Code != 201 {
			t.Fatalf("saved %d %s", saved.Code, saved.Body.String())
		}
		session := voiceConnectedJSON(t, saved.Body.Bytes())
		id := session["session_id"].(string)
		version := session["context_version"].(string)
		accepted := productionCall(state, "POST", voicePath+"/"+id+"/turns", owner, voiceConnectedJSON(t, voiceTurnBody(t, version)))
		if accepted.Code != 202 {
			t.Fatalf("turn %d %s", accepted.Code, accepted.Body.String())
		}
		turns = append(turns, turn{id, version, voiceConnectedJSON(t, accepted.Body.Bytes())["turn_id"].(string), owner})
		select {
		case <-entered:
		case <-time.After(5 * time.Second):
			t.Fatal("source never occupied slot")
		}
	}
	first := turns[0]
	if r := productionCall(state, "POST", voicePath+"/"+first.id+"/turns/"+first.turnID+"/cancel", first.owner, nil); r.Code != 200 {
		t.Fatalf("cancel %d", r.Code)
	}
	second := turns[1]
	if r := productionCall(state, "DELETE", voicePath+"/"+second.id, second.owner, nil); r.Code != 200 {
		t.Fatalf("close %d", r.Code)
	}
	if r := productionCall(state, "POST", voicePath+"/"+first.id+"/turns", first.owner, voiceConnectedJSON(t, voiceTurnBody(t, first.version))); r.Code != 429 {
		t.Fatalf("occupied slots bypassed %d %s", r.Code, r.Body.String())
	}
	adapter.mu.Lock()
	active := adapter.active
	adapter.mu.Unlock()
	if active != 4 {
		t.Fatalf("noncooperative callbacks released slots early: %d", active)
	}
	releaseOnce.Do(func() { close(release) })
	for deadline := time.Now().Add(5 * time.Second); time.Now().Before(deadline); time.Sleep(10 * time.Millisecond) {
		adapter.mu.Lock()
		active = adapter.active
		adapter.mu.Unlock()
		if active == 0 {
			break
		}
	}
	if active != 0 {
		t.Fatalf("source slots not released: %d", active)
	}
	if r := productionCall(state, "POST", voicePath+"/"+first.id+"/turns", first.owner, voiceConnectedJSON(t, voiceTurnBody(t, first.version))); r.Code != 202 {
		t.Fatalf("capacity did not recover %d %s", r.Code, r.Body.String())
	}
}

func TestProposalGatewaySavedContextFourExactPublicOutcomes(t *testing.T) {
	python := os.Getenv("VOICE_LOCAL_PYTHON")
	if python == "" {
		t.Skip("trusted local Python path unset")
	}
	h, db := productionTestHandler(t)
	serverState := h.(*stateServer)
	sourcePath, err := filepath.Abs("../../procurement-engine/src")
	if err != nil {
		t.Fatal(err)
	}
	config := voice.LocalPythonConfig{PythonPath: python, SourcePath: sourcePath}
	executor, err := voice.NewLocalPythonExecutor(config)
	if err != nil {
		t.Fatal(err)
	}
	mapper, err := voice.NewLocalPythonProposalMapper(config)
	if err != nil {
		t.Fatal(err)
	}
	adapter, err := newVoiceHTTPAdapterWithProposal(executor, proposalFixtureSource(t), mapper, time.Now)
	if err != nil {
		t.Fatal(err)
	}
	serverState.voice = adapter
	adapter.production = serverState
	t.Cleanup(func() { _ = adapter.Shutdown(context.Background()) })
	server := httptest.NewServer(serverState)
	defer server.Close()
	for _, name := range []string{"recovery_request.json", "recovery_noop_request.json", "recovery_precision_request.json", "recovery_unsupported_request.json"} {
		t.Run(name, func(t *testing.T) {
			fixture := voiceConnectedJSON(t, voiceFixture(t, name))
			create, _ := json.Marshal(map[string]any{"contract_version": stateVersion, "snapshot": fixture["snapshot"]})
			status, data := voiceConnectedCall(t, server.Client(), server.URL, "POST", "/v2/workspaces/W/production-contexts", create, "editor")
			if status != 201 {
				t.Fatalf("context create %d %s", status, data)
			}
			created := voiceConnectedJSON(t, data)["context"].(map[string]any)
			contextID := created["context_id"].(string)
			t.Cleanup(func() {
				_, _ = db.Exec(context.Background(), `DELETE FROM cs_production_contexts.contexts WHERE context_id=$1`, contextID)
			})
			savedReq, _ := json.Marshal(map[string]any{"contract_version": stateVersion, "context_id": contextID, "expected_revision": 1})
			status, data = voiceConnectedCall(t, server.Client(), server.URL, "POST", savedVoicePath, savedReq, "viewer")
			if status != 201 {
				t.Fatalf("saved create %d %s", status, data)
			}
			session := voiceConnectedJSON(t, data)
			id := session["session_id"].(string)
			t.Cleanup(func() {
				_, _ = voiceConnectedCall(t, server.Client(), server.URL, http.MethodDelete, voicePath+"/"+id, nil, "viewer")
			})
			args := map[string]any{"arrival_replacements": fixture["scenario"].(map[string]any)["arrival_replacements"], "assignments": fixture["recovery"].(map[string]any)["assignments"]}
			turnReq, _ := json.Marshal(map[string]any{"contract_version": stateVersion, "context_version": session["context_version"], "call": map[string]any{"name": "compare_receipt_recovery", "arguments": args}})
			if name == "recovery_request.json" {
				turnPath := voicePath + "/" + id + "/turns"
				for _, tc := range []struct {
					token string
					want  int
				}{{"", 401}, {"editor", 404}, {"foreign", 404}} {
					status, _ := voiceConnectedCall(t, server.Client(), server.URL, "POST", turnPath, turnReq, tc.token)
					if status != tc.want {
						t.Fatalf("voice owner/auth boundary token=%q status=%d want=%d", tc.token, status, tc.want)
					}
				}
				fake, _ := json.Marshal(map[string]any{"contract_version": stateVersion, "context_version": session["context_version"], "call": map[string]any{"name": "compare_receipt_recovery", "arguments": args}, "sdk_response": map[string]any{"candidates": []any{}}})
				status, _ := voiceConnectedCall(t, server.Client(), server.URL, "POST", turnPath, fake, "viewer")
				if status != 422 {
					t.Fatalf("public fake SDK output accepted: %d", status)
				}
			}
			status, data = voiceConnectedCall(t, server.Client(), server.URL, "POST", voicePath+"/"+id+"/turns", turnReq, "viewer")
			if status != 202 {
				t.Fatalf("turn %d %s", status, data)
			}
			turnID := voiceConnectedJSON(t, data)["turn_id"].(string)
			poll := voicePath + "/" + id + "/turns/" + turnID + "/result"
			var ready map[string]any
			for deadline := time.Now().Add(15 * time.Second); time.Now().Before(deadline); time.Sleep(20 * time.Millisecond) {
				status, data = voiceConnectedCall(t, server.Client(), server.URL, "GET", poll, nil, "viewer")
				if status != 200 {
					t.Fatalf("poll %d %s", status, data)
				}
				answer := voiceConnectedJSON(t, data)
				if answer["state"] == "ready" {
					ready = answer
					break
				}
				if answer["state"] != "pending" {
					t.Fatalf("result %s", data)
				}
			}
			if ready == nil {
				t.Fatal("proposal comparison timed out")
			}
			fixture["snapshot"] = created["snapshot"]
			fixture["scenario"].(map[string]any)["expected_plan_version"] = created["snapshot"].(map[string]any)["plan_version"]
			fixture["scenario"].(map[string]any)["source_snapshot_id"] = created["snapshot"].(map[string]any)["snapshot_id"]
			fixture["scenario"].(map[string]any)["scenario_id"] = session["scenario_id"]
			fixture["recovery"].(map[string]any)["expected_plan_version"] = created["snapshot"].(map[string]any)["plan_version"]
			fixture["recovery"].(map[string]any)["source_snapshot_id"] = created["snapshot"].(map[string]any)["snapshot_id"]
			fixture["recovery"].(map[string]any)["scenario_id"] = session["scenario_id"]
			script := "import json,sys; from procure.production.recovery import compare_receipt_recovery; f=json.load(sys.stdin); print(json.dumps(compare_receipt_recovery(f['snapshot'], f['scenario'], f['recovery']), sort_keys=True))"
			cmd := exec.Command(python, "-c", script)
			cmd.Dir = sourcePath
			cmd.Env = []string{"PATH=/usr/bin:/bin", "PYTHONPATH=" + sourcePath, "PYTHONDONTWRITEBYTECODE=1", "PYTHONNOUSERSITE=1"}
			cmd.Stdin = bytes.NewReader(mustJSON(t, fixture))
			expectedBytes, err := cmd.Output()
			if err != nil {
				t.Fatalf("independent public calculation: %v", err)
			}
			expected := voiceConnectedJSON(t, expectedBytes)
			outcome := ready["outcome"].(map[string]any)
			if outcome["status"] != expected["status"] || !reflect.DeepEqual(outcome["result"], expected) {
				t.Fatalf("public numerical mismatch for %s: got status=%v expected status=%v", name, outcome["status"], expected["status"])
			}
			if name == "recovery_request.json" {
				result := outcome["result"].(map[string]any)
				baseline := result["disrupted_baseline"].(map[string]any)
				first := baseline["orders"].([]any)[0].(map[string]any)
				if first["demands"].([]any)[0].(map[string]any)["shortfall"] != "4" {
					t.Fatal("expected exact 4 kg shortfall")
				}
				recovered := result["recovery"].(map[string]any)
				if recovered["orders"].([]any)[0].(map[string]any)["late_by_seconds"] != "3600" {
					t.Fatal("expected exact 3600 s lateness")
				}
			}
			actualBytes, _ := json.Marshal(outcome["result"])
			canonicalExpected, _ := json.Marshal(expected)
			fmt.Printf("proposal_gateway_case=%s state=ready exact_public_equal=true actual_sha256=%x expected_sha256=%x\n", name, sha256.Sum256(actualBytes), sha256.Sum256(canonicalExpected))
			if name == "recovery_request.json" {
				status, data = voiceConnectedCall(t, server.Client(), server.URL, "POST", voicePath+"/"+id+"/turns", turnReq, "viewer")
				if status != 202 {
					t.Fatalf("replacement turn %d %s", status, data)
				}
				replacementID := voiceConnectedJSON(t, data)["turn_id"].(string)
				status, _ = voiceConnectedCall(t, server.Client(), server.URL, "GET", poll, nil, "viewer")
				if status != 404 {
					t.Fatalf("replaced turn remained public: %d", status)
				}
				status, _ = voiceConnectedCall(t, server.Client(), server.URL, "POST", voicePath+"/"+id+"/turns/"+replacementID+"/cancel", nil, "viewer")
				if status != 200 {
					t.Fatalf("cancel replacement %d", status)
				}
				status, _ = voiceConnectedCall(t, server.Client(), server.URL, "GET", voicePath+"/"+id+"/turns/"+replacementID+"/result", nil, "viewer")
				if status != 404 {
					t.Fatalf("cancelled turn remained public: %d", status)
				}
			}
			status, _ = voiceConnectedCall(t, server.Client(), server.URL, http.MethodDelete, voicePath+"/"+id, nil, "viewer")
			if status != 200 {
				t.Fatalf("close %d", status)
			}
		})
	}
}
