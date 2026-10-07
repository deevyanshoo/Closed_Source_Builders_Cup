package voice

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"testing"
	"time"
)

func TestConnectedComparedResponseRejectsForeignReferencesAndTime(t *testing.T) {
	python := os.Getenv("VOICE_LOCAL_PYTHON")
	if python == "" {
		t.Fatal("VOICE_LOCAL_PYTHON must name the trusted offline test Python")
	}
	source, err := filepath.Abs("../../../../procurement-engine/src")
	if err != nil {
		t.Fatal(err)
	}
	fixturePath, err := filepath.Abs("../../../../contracts/examples/recovery_request.json")
	if err != nil {
		t.Fatal(err)
	}
	data, err := os.ReadFile(fixturePath)
	if err != nil {
		t.Fatal(err)
	}
	var fixture struct {
		Snapshot map[string]any `json:"snapshot"`
		Scenario map[string]any `json:"scenario"`
		Recovery map[string]any `json:"recovery"`
	}
	if err := json.Unmarshal(data, &fixture); err != nil {
		t.Fatal(err)
	}
	snapshot, _ := json.Marshal(fixture.Snapshot)
	owner := Owner{"synthetic-principal", fixture.Snapshot["workspace_id"].(string)}
	m, err := NewManager(Config{MaxSessions: 1, MaxSessionsPerOwner: 1,
		IdleTimeout: time.Minute, MaxQueuedMessages: 1,
		MaxPayloadBytes: 2 << 20, MaxQueuedBytes: 2 << 20}, time.Now)
	if err != nil {
		t.Fatal(err)
	}
	id, err := m.Create(owner)
	if err != nil {
		t.Fatal(err)
	}
	lease, err := m.StartTurn(context.Background(), owner, id)
	if err != nil {
		t.Fatal(err)
	}
	captured, err := CaptureRecoveryContext(owner, lease, RecoveryMetadata{
		SnapshotID:  fixture.Snapshot["snapshot_id"].(string),
		PlanVersion: fixture.Snapshot["plan_version"].(string),
		ScenarioID:  fixture.Scenario["scenario_id"].(string)}, snapshot)
	if err != nil {
		t.Fatal(err)
	}
	call, _ := json.Marshal(map[string]any{"name": localToolName,
		"arguments": map[string]any{
			"arrival_replacements": fixture.Scenario["arrival_replacements"],
			"assignments":          fixture.Recovery["assignments"]}})
	fixed, err := NewLocalPythonExecutor(LocalPythonConfig{PythonPath: python, SourcePath: source})
	if err != nil {
		t.Fatal(err)
	}
	for _, change := range []struct {
		name   string
		mutate func(map[string]any)
	}{
		{"as_of", func(r map[string]any) { r["as_of"] = "2026-10-10T00:00:00Z" }},
		{"schedule_order", func(r map[string]any) {
			r["recovery"].(map[string]any)["orders"].([]any)[0].(map[string]any)["order_id"] = "foreign"
		}},
		{"summary_order", func(r map[string]any) { r["potentially_exposed_order_ids"] = []any{"foreign"} }},
		{"receipt", func(r map[string]any) {
			r["receipt_changes"].([]any)[0].(map[string]any)["receipt_id"] = "foreign"
		}},
	} {
		t.Run(change.name, func(t *testing.T) {
			executor := functionExecutor(func(ctx context.Context, request []byte) ([]byte, error) {
				raw, err := fixed.Execute(ctx, request)
				if err != nil {
					return nil, err
				}
				var response map[string]any
				if err := json.Unmarshal(raw, &response); err != nil {
					return nil, err
				}
				result := response["outcome"].(map[string]any)["result"].(map[string]any)
				change.mutate(result)
				return json.Marshal(response)
			})
			runner, err := NewRunner(m, executor)
			if err != nil {
				t.Fatal(err)
			}
			if err := runner.Run(owner, lease, captured, call); !errors.Is(err, ErrExecution) {
				t.Fatalf("foreign numerical reference accepted: %v", err)
			}
			if _, ok, err := m.Dequeue(owner, id); err != nil || ok {
				t.Fatalf("tampered result queued: %v %v", ok, err)
			}
		})
	}
}

func TestConnectedLocalPythonRecoveryMatchesIndependentPublicCalculation(t *testing.T) {
	python := os.Getenv("VOICE_LOCAL_PYTHON")
	if python == "" {
		t.Fatal("VOICE_LOCAL_PYTHON must name the trusted offline test Python")
	}
	source, err := filepath.Abs("../../../../procurement-engine/src")
	if err != nil {
		t.Fatal(err)
	}
	fixtures, err := filepath.Abs("../../../../contracts/examples")
	if err != nil {
		t.Fatal(err)
	}
	executor, err := NewLocalPythonExecutor(LocalPythonConfig{PythonPath: python, SourcePath: source})
	if err != nil {
		t.Fatal(err)
	}
	for _, file := range []string{
		"recovery_request.json", "recovery_noop_request.json",
		"recovery_precision_request.json", "recovery_unsupported_request.json",
	} {
		t.Run(file, func(t *testing.T) {
			data, err := os.ReadFile(filepath.Join(fixtures, file))
			if err != nil {
				t.Fatal(err)
			}
			var fixture struct {
				Snapshot json.RawMessage `json:"snapshot"`
				Scenario struct {
					ScenarioID          string          `json:"scenario_id"`
					ArrivalReplacements json.RawMessage `json:"arrival_replacements"`
				} `json:"scenario"`
				Recovery struct {
					Assignments json.RawMessage `json:"assignments"`
				} `json:"recovery"`
			}
			if err := json.Unmarshal(data, &fixture); err != nil {
				t.Fatal(err)
			}
			var snapshot struct {
				WorkspaceID string `json:"workspace_id"`
				SnapshotID  string `json:"snapshot_id"`
				PlanVersion string `json:"plan_version"`
			}
			if err := json.Unmarshal(fixture.Snapshot, &snapshot); err != nil {
				t.Fatal(err)
			}
			owner := Owner{"synthetic-principal", snapshot.WorkspaceID}
			manager, err := NewManager(Config{MaxSessions: 1, MaxSessionsPerOwner: 1,
				IdleTimeout: time.Minute, MaxQueuedMessages: 1,
				MaxPayloadBytes: 2 << 20, MaxQueuedBytes: 2 << 20}, time.Now)
			if err != nil {
				t.Fatal(err)
			}
			id, err := manager.Create(owner)
			if err != nil {
				t.Fatal(err)
			}
			lease, err := manager.StartTurn(context.Background(), owner, id)
			if err != nil {
				t.Fatal(err)
			}
			captured, err := CaptureRecoveryContext(owner, lease, RecoveryMetadata{
				SnapshotID: snapshot.SnapshotID, PlanVersion: snapshot.PlanVersion,
				ScenarioID: fixture.Scenario.ScenarioID}, fixture.Snapshot)
			if err != nil {
				t.Fatal(err)
			}
			call, err := json.Marshal(map[string]any{
				"name": localToolName,
				"arguments": map[string]any{"arrival_replacements": fixture.Scenario.ArrivalReplacements,
					"assignments": fixture.Recovery.Assignments},
			})
			if err != nil {
				t.Fatal(err)
			}
			runner, err := NewRunner(manager, executor)
			if err != nil {
				t.Fatal(err)
			}
			start := time.Now()
			if err := runner.Run(owner, lease, captured, call); err != nil {
				t.Fatal(err)
			}
			elapsed := time.Since(start)
			output, ok, err := manager.Dequeue(owner, id)
			if err != nil || !ok {
				t.Fatalf("missing guarded output: %v %v", ok, err)
			}
			var response struct {
				BridgeVersion string `json:"bridge_version"`
				Outcome       struct {
					Status string `json:"status"`
					Result any    `json:"result"`
				} `json:"outcome"`
			}
			if err := json.Unmarshal(output.Payload, &response); err != nil {
				t.Fatal(err)
			}
			script := "import json,sys; from procure.production.recovery import compare_receipt_recovery; f=json.load(sys.stdin); print(json.dumps(compare_receipt_recovery(f['snapshot'], f['scenario'], f['recovery']), sort_keys=True))"
			cmd := exec.CommandContext(context.Background(), python, "-c", script)
			cmd.Dir = source
			cmd.Env = []string{"PATH=/usr/bin:/bin", "PYTHONPATH=" + source,
				"PYTHONDONTWRITEBYTECODE=1", "PYTHONNOUSERSITE=1"}
			cmd.Stdin = bytes.NewReader(data)
			expectedBytes, err := cmd.Output()
			if err != nil {
				t.Fatalf("independent public comparison: %v", err)
			}
			var expected any
			if err := json.Unmarshal(expectedBytes, &expected); err != nil {
				t.Fatal(err)
			}
			if response.BridgeVersion != "1" || response.Outcome.Status != expected.(map[string]any)["status"] ||
				!reflect.DeepEqual(response.Outcome.Result, expected) {
				t.Fatalf("local outcome differs from independent public comparison for %s", file)
			}
			if file == "recovery_request.json" {
				result := response.Outcome.Result.(map[string]any)
				disrupted := result["disrupted_baseline"].(map[string]any)
				first := disrupted["orders"].([]any)[0].(map[string]any)
				if first["demands"].([]any)[0].(map[string]any)["shortfall"] != "4" {
					t.Fatal("hand material disruption not retained")
				}
				recovered := result["recovery"].(map[string]any)
				if recovered["orders"].([]any)[0].(map[string]any)["late_by_seconds"] != "3600" {
					t.Fatal("hand late completion not retained")
				}
			}
			t.Logf("local fixed-child bridge elapsed=%s, equality=true", elapsed)
		})
	}
}
