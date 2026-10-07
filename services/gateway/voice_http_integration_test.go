package main

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"sync"
	"testing"
	"time"

	"closedsource/gateway/internal/voice"
)

func voiceConnectedCall(t *testing.T, client *http.Client, base, method, path string, body []byte, token string) (int, []byte) {
	t.Helper()
	req, err := http.NewRequest(method, base+path, bytes.NewReader(body))
	if err != nil {
		t.Fatal(err)
	}
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	if token != "" {
		req.Header.Set("Authorization", "Bearer "+token)
	}
	res, err := client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer res.Body.Close()
	data, err := io.ReadAll(io.LimitReader(res.Body, 2<<20+1))
	if err != nil {
		t.Fatal(err)
	}
	return res.StatusCode, data
}

func TestVoiceHTTPCachedReadyExpiresAfterLastPoll(t *testing.T) {
	python := os.Getenv("VOICE_LOCAL_PYTHON")
	if python == "" {
		t.Fatal("VOICE_LOCAL_PYTHON is required for connected cached-ready expiry proof")
	}
	source, err := filepath.Abs("../../procurement-engine/src")
	if err != nil {
		t.Fatal(err)
	}
	executor, err := voice.NewLocalPythonExecutor(voice.LocalPythonConfig{PythonPath: python, SourcePath: source})
	if err != nil {
		t.Fatal(err)
	}
	var clockMu sync.Mutex
	now := time.Date(2026, 10, 10, 0, 0, 0, 0, time.UTC)
	clock := func() time.Time { clockMu.Lock(); defer clockMu.Unlock(); return now }
	a, err := newVoiceHTTPAdapter(executor, clock)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		ctx, cancel := context.WithTimeout(context.Background(), time.Second)
		defer cancel()
		_ = a.Shutdown(ctx)
	})
	p := principal{ID: "p", Workspace: "W", Role: "viewer"}
	first := voiceCreated(t, a, p)
	voiceCreated(t, a, p)
	id := first["session_id"].(string)
	accepted := voiceDirect(t, a, p, "POST", voicePath+"/"+id+"/turns", voiceTurnBody(t, first["context_version"].(string)))
	if accepted.Code != 202 {
		t.Fatalf("turn %d %s", accepted.Code, accepted.Body.String())
	}
	turnID := voiceConnectedJSON(t, accepted.Body.Bytes())["turn_id"].(string)
	path := voicePath + "/" + id + "/turns/" + turnID + "/result"
	ready := false
	for deadline := time.Now().Add(15 * time.Second); time.Now().Before(deadline); {
		result := voiceDirect(t, a, p, "GET", path, nil)
		if result.Code != 200 {
			t.Fatalf("poll %d %s", result.Code, result.Body.String())
		}
		if value := voiceConnectedJSON(t, result.Body.Bytes()); value["state"] == "ready" {
			if _, ok := value["outcome"].(map[string]any); !ok {
				t.Fatal("ready result missing validated outcome")
			}
			ready = true
			break
		}
		time.Sleep(20 * time.Millisecond)
	}
	if !ready {
		t.Fatal("fixed local Python comparison did not reach ready")
	}
	clockMu.Lock()
	now = now.Add(4 * time.Minute)
	clockMu.Unlock()
	if result := voiceDirect(t, a, p, "GET", path, nil); result.Code != 200 || voiceConnectedJSON(t, result.Body.Bytes())["state"] != "ready" {
		t.Fatalf("ready result lost before idle timeout: %d %s", result.Code, result.Body.String())
	}
	clockMu.Lock()
	now = now.Add(voiceIdle)
	clockMu.Unlock()
	voiceAssertError(t, voiceDirect(t, a, p, "GET", path, nil), 404, "voice_session_unavailable")
	a.mu.Lock()
	retained := len(a.sessions)
	a.mu.Unlock()
	if retained != 0 {
		t.Fatalf("expired registry retained %d sessions", retained)
	}
	voiceCreated(t, a, p)
	voiceCreated(t, a, p)
}
func voiceConnectedJSON(t *testing.T, data []byte) map[string]any {
	t.Helper()
	var value map[string]any
	if err := json.Unmarshal(data, &value); err != nil {
		t.Fatal(err)
	}
	return value
}
func TestVoiceHTTPConnectedFourSyntheticComparisons(t *testing.T) {
	python := os.Getenv("VOICE_LOCAL_PYTHON")
	if python == "" {
		t.Fatal("VOICE_LOCAL_PYTHON is required for connected local Python proof")
	}
	source, err := filepath.Abs("../../procurement-engine/src")
	if err != nil {
		t.Fatal(err)
	}
	identityPath := filepath.Join(t.TempDir(), "voice-identities.json")
	identityJSON := `{"identities":[{"token":"synthetic-owner","principal_id":"owner-a","workspace_id":"W","role":"viewer"},{"token":"synthetic-other","principal_id":"owner-b","workspace_id":"W","role":"viewer"}]}`
	if err := os.WriteFile(identityPath, []byte(identityJSON), 0600); err != nil {
		t.Fatal(err)
	}
	state := newLocalStateServer(stateConfig{IdentitiesFile: identityPath, VoicePythonPath: python, VoiceSourcePath: source})
	if state.voice == nil {
		t.Fatal("trusted local executor not initialized")
	}
	server := httptest.NewServer(state)
	defer server.Close()
	defer state.Shutdown(context.Background())
	for _, name := range []string{"recovery_request.json", "recovery_noop_request.json", "recovery_precision_request.json", "recovery_unsupported_request.json"} {
		t.Run(name, func(t *testing.T) {
			fixture := voiceConnectedJSON(t, voiceFixture(t, name))
			snapshot := fixture["snapshot"]
			create, _ := json.Marshal(map[string]any{"contract_version": stateVersion, "snapshot": snapshot})
			if status, _ := voiceConnectedCall(t, server.Client(), server.URL, "POST", voicePath, create, ""); status != 401 {
				t.Fatalf("anonymous create %d", status)
			}
			status, data := voiceConnectedCall(t, server.Client(), server.URL, "POST", voicePath, create, "synthetic-owner")
			if status != 201 {
				t.Fatalf("create %d: %s", status, data)
			}
			created := voiceConnectedJSON(t, data)
			id := created["session_id"].(string)
			version := created["context_version"].(string)
			scenario := created["scenario_id"].(string)
			if status, _ := voiceConnectedCall(t, server.Client(), server.URL, "DELETE", voicePath+"/"+id, nil, "synthetic-other"); status != 404 {
				t.Fatalf("foreign owner same workspace %d", status)
			}
			recovery := fixture["recovery"].(map[string]any)
			situation := fixture["scenario"].(map[string]any)
			turnReq, _ := json.Marshal(map[string]any{"contract_version": stateVersion, "context_version": version, "call": map[string]any{"name": "compare_receipt_recovery", "arguments": map[string]any{"arrival_replacements": situation["arrival_replacements"], "assignments": recovery["assignments"]}}})
			status, data = voiceConnectedCall(t, server.Client(), server.URL, "POST", voicePath+"/"+id+"/turns", turnReq, "synthetic-owner")
			if status != 202 {
				t.Fatalf("turn %d: %s", status, data)
			}
			accepted := voiceConnectedJSON(t, data)
			turnID := accepted["turn_id"].(string)
			pollPath := voicePath + "/" + id + "/turns/" + turnID + "/result"
			deadline := time.NewTimer(15 * time.Second)
			defer deadline.Stop()
			ticker := time.NewTicker(20 * time.Millisecond)
			defer ticker.Stop()
			var ready map[string]any
			for ready == nil {
				status, data = voiceConnectedCall(t, server.Client(), server.URL, "GET", pollPath, nil, "synthetic-owner")
				if status != 200 {
					t.Fatalf("poll %d: %s", status, data)
				}
				answer := voiceConnectedJSON(t, data)
				switch answer["state"] {
				case "ready":
					ready = answer
				case "pending":
					select {
					case <-ticker.C:
					case <-deadline.C:
						t.Fatal("connected local comparison timed out")
					}
				default:
					t.Fatalf("failed result %s", data)
				}
			}
			if !voiceValidName(ready, "VoiceHTTPResultResponse") {
				t.Fatal("connected response fails authoritative result schema")
			}
			situation["scenario_id"] = scenario
			recovery["scenario_id"] = scenario
			expectedInput, _ := json.Marshal(fixture)
			script := "import json,sys; from procure.production.recovery import compare_receipt_recovery; f=json.load(sys.stdin); print(json.dumps(compare_receipt_recovery(f['snapshot'], f['scenario'], f['recovery']), sort_keys=True))"
			cmd := exec.Command(python, "-c", script)
			cmd.Dir = source
			cmd.Env = []string{"PATH=/usr/bin:/bin", "PYTHONPATH=" + source, "PYTHONDONTWRITEBYTECODE=1", "PYTHONNOUSERSITE=1"}
			cmd.Stdin = bytes.NewReader(expectedInput)
			expectedBytes, err := cmd.Output()
			if err != nil {
				t.Fatalf("independent public comparison: %v", err)
			}
			expected := voiceConnectedJSON(t, expectedBytes)
			outcome := ready["outcome"].(map[string]any)
			if outcome["status"] != expected["status"] || !reflect.DeepEqual(outcome["result"], expected) {
				t.Fatalf("connected result differs from independent public comparison for %s", name)
			}
			if name == "recovery_request.json" {
				result := outcome["result"].(map[string]any)
				disrupted := result["disrupted_baseline"].(map[string]any)
				orders := disrupted["orders"].([]any)
				first := orders[0].(map[string]any)
				if first["demands"].([]any)[0].(map[string]any)["shortfall"] != "4" {
					t.Fatal("expected exact 4 kg shortfall")
				}
				recovered := result["recovery"].(map[string]any)
				if recovered["orders"].([]any)[0].(map[string]any)["late_by_seconds"] != "3600" {
					t.Fatal("expected exact 3600 s lateness")
				}
			}
			fmt.Printf("voice_http_connected %s state=ready exact_public_equal=true\n", name)
			if name == "recovery_request.json" {
				if status, data := voiceConnectedCall(t, server.Client(), server.URL, "POST", voicePath+"/"+id+"/turns/"+turnID+"/cancel", nil, "synthetic-owner"); status != 200 {
					t.Fatalf("cancel ready turn %d: %s", status, data)
				}
				if status, _ := voiceConnectedCall(t, server.Client(), server.URL, "GET", pollPath, nil, "synthetic-owner"); status != 404 {
					t.Fatalf("cached ready result after cancel %d", status)
				}
			}
			status, _ = voiceConnectedCall(t, server.Client(), server.URL, "DELETE", voicePath+"/"+id, nil, "synthetic-owner")
			if status != 200 {
				t.Fatalf("close %d", status)
			}
			if status, _ := voiceConnectedCall(t, server.Client(), server.URL, "GET", pollPath, nil, "synthetic-owner"); status != 404 {
				t.Fatalf("cached result after close %d", status)
			}
		})
	}
}
