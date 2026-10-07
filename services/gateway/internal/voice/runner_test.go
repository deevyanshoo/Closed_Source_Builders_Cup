package voice

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"sync"
	"testing"
	"time"
)

type stubExecutor struct {
	response []byte
	calls    int
}

func (e *stubExecutor) Execute(context.Context, []byte) ([]byte, error) {
	e.calls++
	return e.response, nil
}

type functionExecutor func(context.Context, []byte) ([]byte, error)

func (fn functionExecutor) Execute(ctx context.Context, request []byte) ([]byte, error) {
	return fn(ctx, request)
}

func rejectedProtocolResponse(request []byte) []byte {
	var envelope struct {
		HostContextJSON string `json:"host_context_json"`
	}
	_ = json.Unmarshal(request, &envelope)
	var host map[string]any
	_ = json.Unmarshal([]byte(envelope.HostContextJSON), &host)
	digest := sha256.Sum256([]byte(envelope.HostContextJSON))
	binding := map[string]any{}
	for _, key := range []string{"principal_id", "workspace_id", "snapshot_id",
		"plan_version", "scenario_id", "session_id", "turn_id"} {
		binding[key] = host[key]
	}
	binding["context_sha256"] = hex.EncodeToString(digest[:])
	response, _ := json.Marshal(map[string]any{"bridge_version": "1", "binding": binding,
		"outcome": map[string]any{"tool_name": localToolName, "read_only": true,
			"status": "rejected", "reason": "invalid_arguments",
			"ownership": map[string]any{"session_id": host["session_id"], "turn_id": host["turn_id"]}}})
	return response
}

func runnerFixture(t *testing.T, maxSessions int, executor Executor) (*Manager, *Runner, Owner, string, *Lease, *RecoveryContext, []byte) {
	t.Helper()
	owner := Owner{"p", "w"}
	m, err := NewManager(Config{MaxSessions: maxSessions, MaxSessionsPerOwner: maxSessions,
		IdleTimeout: time.Minute, MaxQueuedMessages: 2,
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
	snapshot := []byte(`{"workspace_id":"w","snapshot_id":"snap","plan_version":"v1","as_of":"2026-10-09T00:00:00Z","synthetic":true,"orders":[]}`)
	captured, err := CaptureRecoveryContext(owner, lease, RecoveryMetadata{"snap", "v1", "s"}, snapshot)
	if err != nil {
		t.Fatal(err)
	}
	runner, err := NewRunner(m, executor)
	if err != nil {
		t.Fatal(err)
	}
	call := []byte(`{"name":"compare_receipt_recovery","arguments":{"arrival_replacements":[],"assignments":[]}}`)
	return m, runner, owner, id, lease, captured, call
}

func TestRunnerDispatchesCurrentSyntheticCallToGuardedQueue(t *testing.T) {
	owner := Owner{"principal", "workspace"}
	m, err := NewManager(Config{MaxSessions: 1, MaxSessionsPerOwner: 1, IdleTimeout: time.Minute,
		MaxQueuedMessages: 2, MaxPayloadBytes: 2 << 20, MaxQueuedBytes: 2 << 20}, time.Now)
	if err != nil {
		t.Fatal(err)
	}
	session, err := m.Create(owner)
	if err != nil {
		t.Fatal(err)
	}
	lease, err := m.StartTurn(context.Background(), owner, session)
	if err != nil {
		t.Fatal(err)
	}
	snapshot, _ := json.Marshal(map[string]any{"workspace_id": "workspace", "snapshot_id": "snap",
		"plan_version": "v1", "as_of": "2026-10-09T00:00:00Z", "synthetic": true, "orders": []any{}})
	captured, err := CaptureRecoveryContext(owner, lease,
		RecoveryMetadata{"snap", "v1", "scenario"}, snapshot)
	if err != nil {
		t.Fatal(err)
	}
	response, _ := json.Marshal(map[string]any{
		"bridge_version": "1",
		"binding": map[string]any{"principal_id": owner.PrincipalID, "workspace_id": owner.WorkspaceID,
			"snapshot_id": "snap", "plan_version": "v1", "scenario_id": "scenario",
			"session_id": session, "turn_id": lease.TurnID(), "context_sha256": captured.digest},
		"outcome": map[string]any{"tool_name": "compare_receipt_recovery", "read_only": true,
			"status": "rejected", "reason": "invalid_arguments",
			"ownership": map[string]any{"session_id": session, "turn_id": lease.TurnID()}},
	})
	executor := &stubExecutor{response: response}
	runner, err := NewRunner(m, executor)
	if err != nil {
		t.Fatal(err)
	}
	call := []byte(`{"name":"compare_receipt_recovery","arguments":{"arrival_replacements":[],"assignments":[]}}`)
	if err := runner.Run(owner, lease, captured, call); err != nil {
		t.Fatal(err)
	}
	if executor.calls != 1 {
		t.Fatalf("executor calls = %d", executor.calls)
	}
	if _, ok, err := m.Dequeue(owner, session); err != nil || !ok {
		t.Fatalf("guarded output: %v %v", ok, err)
	}
}

func TestRunnerGateBoundsPerLeaseAndGlobalInflightAndReleases(t *testing.T) {
	started := make(chan struct{}, 4)
	release := make(chan struct{})
	executor := functionExecutor(func(_ context.Context, request []byte) ([]byte, error) {
		started <- struct{}{}
		<-release
		return rejectedProtocolResponse(request), nil
	})
	m, runner, owner, _, lease, captured, call := runnerFixture(t, 5, executor)
	leases := []*Lease{lease}
	contexts := []*RecoveryContext{captured}
	for range 4 {
		id, err := m.Create(owner)
		if err != nil {
			t.Fatal(err)
		}
		next, err := m.StartTurn(context.Background(), owner, id)
		if err != nil {
			t.Fatal(err)
		}
		snapshot := []byte(`{"workspace_id":"w","snapshot_id":"snap","plan_version":"v1","as_of":"2026-10-09T00:00:00Z","synthetic":true,"orders":[]}`)
		context, err := CaptureRecoveryContext(owner, next, RecoveryMetadata{"snap", "v1", "s"}, snapshot)
		if err != nil {
			t.Fatal(err)
		}
		leases, contexts = append(leases, next), append(contexts, context)
	}
	results := make(chan error, 4)
	for i := range 4 {
		go func(i int) { results <- runner.Run(owner, leases[i], contexts[i], call) }(i)
	}
	for range 4 {
		select {
		case <-started:
		case <-time.After(5 * time.Second):
			t.Fatal("four trusted executions did not start")
		}
	}
	if err := runner.Run(owner, leases[0], contexts[0], call); !errors.Is(err, ErrCapacity) {
		t.Fatalf("same-lease duplicate: %v", err)
	}
	if err := runner.Run(owner, leases[4], contexts[4], call); !errors.Is(err, ErrCapacity) {
		t.Fatalf("fifth in-flight execution: %v", err)
	}
	close(release)
	for range 4 {
		if err := <-results; err != nil {
			t.Fatalf("in-flight execution: %v", err)
		}
	}
	if err := runner.Run(owner, leases[4], contexts[4], call); err != nil {
		t.Fatalf("gate capacity not released: %v", err)
	}
}

func TestRunnerGateRejectsSecondCallOnSameLeaseBeforeDispatch(t *testing.T) {
	started := make(chan struct{}, 2)
	release := make(chan struct{})
	released := false
	defer func() {
		if !released {
			close(release)
		}
	}()
	executor := functionExecutor(func(_ context.Context, request []byte) ([]byte, error) {
		started <- struct{}{}
		<-release
		return rejectedProtocolResponse(request), nil
	})
	_, runner, owner, _, lease, captured, call := runnerFixture(t, 1, executor)
	first := make(chan error, 1)
	go func() { first <- runner.Run(owner, lease, captured, call) }()
	<-started
	second := make(chan error, 1)
	go func() { second <- runner.Run(owner, lease, captured, call) }()
	select {
	case err := <-second:
		if !errors.Is(err, ErrCapacity) {
			t.Fatalf("duplicate lease call: %v", err)
		}
	case <-time.After(time.Second):
		t.Fatal("duplicate lease call dispatched or blocked")
	}
	select {
	case <-started:
		t.Fatal("second execution reached trusted executor")
	default:
	}
	close(release)
	released = true
	if err := <-first; err != nil {
		t.Fatalf("first execution: %v", err)
	}
}

func TestRunnerSuppressesNoncooperativeLateOutputAfterReplacement(t *testing.T) {
	started := make(chan struct{})
	release := make(chan struct{})
	executor := functionExecutor(func(_ context.Context, request []byte) ([]byte, error) {
		close(started)
		<-release
		return rejectedProtocolResponse(request), nil
	})
	m, runner, owner, id, lease, captured, call := runnerFixture(t, 1, executor)
	done := make(chan error, 1)
	go func() { done <- runner.Run(owner, lease, captured, call) }()
	<-started
	if _, err := m.StartTurn(context.Background(), owner, id); err != nil {
		t.Fatal(err)
	}
	close(release)
	if err := <-done; !errors.Is(err, ErrUnavailable) {
		t.Fatalf("late output: %v", err)
	}
	if _, ok, err := m.Dequeue(owner, id); err != nil || ok {
		t.Fatalf("late output queued: %v %v", ok, err)
	}
}

func TestRunnerRejectsForeignAndMalformedCallsBeforeExecutor(t *testing.T) {
	var mu sync.Mutex
	calls := 0
	executor := functionExecutor(func(_ context.Context, request []byte) ([]byte, error) {
		mu.Lock()
		calls++
		mu.Unlock()
		return rejectedProtocolResponse(request), nil
	})
	m, runner, owner, id, lease, captured, good := runnerFixture(t, 1, executor)
	if err := runner.Run(Owner{"foreign", "w"}, lease, captured, good); !errors.Is(err, ErrUnavailable) {
		t.Fatalf("foreign: %v", err)
	}
	if err := runner.Run(owner, &Lease{}, captured, good); !errors.Is(err, ErrUnavailable) {
		t.Fatalf("zero lease: %v", err)
	}
	for _, raw := range [][]byte{
		[]byte(`{"name":"send_email","arguments":{}}`),
		[]byte(`{"name":"compare_receipt_recovery","arguments":{},"apply":true}`),
		[]byte(`{"name":"compare_receipt_recovery","name":"compare_receipt_recovery","arguments":{}}`),
		[]byte(`{"name":"compare_receipt_recovery","arguments":{"assignments":NaN}}`),
		[]byte(`{"name":"compare_receipt_recovery","arguments":{}} trailing`),
		[]byte(`{"name":"compare_receipt_recovery","arguments":{"workspace_id":"foreign"}}`),
		[]byte{0xff},
		make([]byte, localCallLimit+1),
	} {
		if err := runner.Run(owner, lease, captured, raw); !errors.Is(err, ErrInvalid) {
			t.Fatalf("malformed call: %v", err)
		}
	}
	mu.Lock()
	count := calls
	mu.Unlock()
	if count != 0 {
		t.Fatalf("executor invoked %d times on invalid inputs", count)
	}
	if err := m.Close(owner, id); err != nil {
		t.Fatal(err)
	}
	if err := runner.Run(owner, lease, captured, good); !errors.Is(err, ErrUnavailable) {
		t.Fatalf("closed: %v", err)
	}
}

func TestRunnerRejectsReturnedIdentityStatusAndReadonlyMismatch(t *testing.T) {
	var mutate func(map[string]any)
	executor := functionExecutor(func(_ context.Context, request []byte) ([]byte, error) {
		var response map[string]any
		_ = json.Unmarshal(rejectedProtocolResponse(request), &response)
		mutate(response)
		return json.Marshal(response)
	})
	m, runner, owner, id, lease, captured, call := runnerFixture(t, 1, executor)
	for _, change := range []func(map[string]any){
		func(r map[string]any) { r["binding"].(map[string]any)["workspace_id"] = "foreign" },
		func(r map[string]any) { r["binding"].(map[string]any)["context_sha256"] = "0" },
		func(r map[string]any) { r["outcome"].(map[string]any)["read_only"] = false },
		func(r map[string]any) { r["outcome"].(map[string]any)["status"] = "compared" },
		func(r map[string]any) { r["outcome"].(map[string]any)["result"] = map[string]any{} },
		func(r map[string]any) { r["unknown"] = "field" },
	} {
		mutate = change
		if err := runner.Run(owner, lease, captured, call); !errors.Is(err, ErrExecution) {
			t.Fatalf("invalid executor response: %v", err)
		}
		if _, ok, err := m.Dequeue(owner, id); err != nil || ok {
			t.Fatalf("invalid response queued: %v %v", ok, err)
		}
	}
}

func TestRunnerReleasesGateOnExecutorErrorAndPanic(t *testing.T) {
	var fail bool = true
	executor := functionExecutor(func(_ context.Context, request []byte) ([]byte, error) {
		if fail {
			fail = false
			panic("synthetic executor failure")
		}
		return rejectedProtocolResponse(request), nil
	})
	_, runner, owner, _, lease, captured, call := runnerFixture(t, 1, executor)
	if err := runner.Run(owner, lease, captured, call); !errors.Is(err, ErrExecution) {
		t.Fatalf("panic not generic: %v", err)
	}
	if err := runner.Run(owner, lease, captured, call); err != nil {
		t.Fatalf("gate retained after panic: %v", err)
	}
}

func TestRunnerRejectsTypedNilExecutor(t *testing.T) {
	manager, err := NewManager(Config{MaxSessions: 1, MaxSessionsPerOwner: 1,
		IdleTimeout: time.Minute, MaxQueuedMessages: 1, MaxPayloadBytes: 8,
		MaxQueuedBytes: 8}, time.Now)
	if err != nil {
		t.Fatal(err)
	}
	var missing *LocalPythonExecutor
	if _, err := NewRunner(manager, missing); !errors.Is(err, ErrInvalid) {
		t.Fatalf("typed nil executor accepted: %v", err)
	}
}

func TestCaptureAndRunOwnSnapshotAndCallBytes(t *testing.T) {
	entered := make(chan []byte, 1)
	release := make(chan struct{})
	executor := functionExecutor(func(_ context.Context, request []byte) ([]byte, error) {
		entered <- append([]byte(nil), request...)
		<-release
		return rejectedProtocolResponse(request), nil
	})
	m, runner, owner, id, lease, _, call := runnerFixture(t, 1, executor)
	source := []byte(`{"workspace_id":"w","snapshot_id":"snap","plan_version":"v1","as_of":"2026-10-09T00:00:00Z","synthetic":true,"orders":[]}`)
	captured, err := CaptureRecoveryContext(owner, lease, RecoveryMetadata{"snap", "v1", "s"}, source)
	if err != nil {
		t.Fatal(err)
	}
	source[0] = 'x'
	done := make(chan error, 1)
	go func() { done <- runner.Run(owner, lease, captured, call) }()
	request := <-entered
	call[0] = 'x'
	close(release)
	if err := <-done; err != nil {
		t.Fatal(err)
	}
	var envelope struct{ HostContextJSON, CallJSON string }
	// Use the protocol tags explicitly; no model-derived identity is accepted.
	var fields map[string]string
	if err := json.Unmarshal(request, &fields); err != nil {
		t.Fatal(err)
	}
	envelope.HostContextJSON, envelope.CallJSON = fields["host_context_json"], fields["call_json"]
	if envelope.CallJSON[0] != '{' {
		t.Fatal("caller call mutation reached execution")
	}
	var host map[string]any
	if err := json.Unmarshal([]byte(envelope.HostContextJSON), &host); err != nil {
		t.Fatal(err)
	}
	if host["snapshot"].(map[string]any)["snapshot_id"] != "snap" {
		t.Fatal("caller snapshot mutation reached execution")
	}
	if _, ok, err := m.Dequeue(owner, id); err != nil || !ok {
		t.Fatalf("owned output: %v %v", ok, err)
	}
}

func TestCallAndCapturedContextShareAggregateArrayBudget(t *testing.T) {
	owner := Owner{"p", "w"}
	m, err := NewManager(Config{MaxSessions: 1, MaxSessionsPerOwner: 1,
		IdleTimeout: time.Minute, MaxQueuedMessages: 1, MaxPayloadBytes: 2 << 20,
		MaxQueuedBytes: 2 << 20}, time.Now)
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
	filler := make([]any, 16)
	for i := range filler {
		filler[i] = make([]string, 255)
	}
	snapshot, _ := json.Marshal(map[string]any{"workspace_id": "w", "snapshot_id": "snap",
		"plan_version": "v1", "as_of": "2026-10-09T00:00:00Z",
		"synthetic": true, "orders": []any{}, "filler": filler})
	captured, err := CaptureRecoveryContext(owner, lease, RecoveryMetadata{"snap", "v1", "s"}, snapshot)
	if err != nil {
		t.Fatal(err)
	}
	if captured.arrayCount != 4096 {
		t.Fatalf("captured array entries = %d", captured.arrayCount)
	}
	calls := 0
	executor := functionExecutor(func(_ context.Context, request []byte) ([]byte, error) {
		calls++
		return rejectedProtocolResponse(request), nil
	})
	runner, err := NewRunner(m, executor)
	if err != nil {
		t.Fatal(err)
	}
	call := []byte(`{"name":"compare_receipt_recovery","arguments":{"assignments":[{"order_id":"x"}],"arrival_replacements":[]}}`)
	if err := runner.Run(owner, lease, captured, call); !errors.Is(err, ErrInvalid) {
		t.Fatalf("aggregate array limit: %v", err)
	}
	if calls != 0 {
		t.Fatal("oversized aggregate dispatched")
	}
}

func TestLateExecutorCannotQueueAfterCancelCloseExpiryOrShutdown(t *testing.T) {
	for _, ending := range []string{"cancel", "close", "expire", "shutdown"} {
		t.Run(ending, func(t *testing.T) {
			clock := &testClock{at: time.Unix(1, 0)}
			owner := Owner{"p", "w"}
			m, err := NewManager(Config{MaxSessions: 1, MaxSessionsPerOwner: 1,
				IdleTimeout: time.Minute, MaxQueuedMessages: 1,
				MaxPayloadBytes: 2 << 20, MaxQueuedBytes: 2 << 20}, clock.now)
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
			snapshot := []byte(`{"workspace_id":"w","snapshot_id":"snap","plan_version":"v1","as_of":"2026-10-09T00:00:00Z","synthetic":true,"orders":[]}`)
			captured, err := CaptureRecoveryContext(owner, lease, RecoveryMetadata{"snap", "v1", "s"}, snapshot)
			if err != nil {
				t.Fatal(err)
			}
			started := make(chan struct{})
			release := make(chan struct{})
			executor := functionExecutor(func(_ context.Context, request []byte) ([]byte, error) {
				close(started)
				<-release
				return rejectedProtocolResponse(request), nil
			})
			runner, err := NewRunner(m, executor)
			if err != nil {
				t.Fatal(err)
			}
			done := make(chan error, 1)
			call := []byte(`{"name":"compare_receipt_recovery","arguments":{"arrival_replacements":[],"assignments":[]}}`)
			go func() { done <- runner.Run(owner, lease, captured, call) }()
			<-started
			switch ending {
			case "cancel":
				err = m.Cancel(owner, lease)
			case "close":
				err = m.Close(owner, id)
			case "expire":
				clock.advance(time.Minute)
				m.Sweep()
			case "shutdown":
				m.Shutdown()
			}
			if err != nil {
				t.Fatal(err)
			}
			close(release)
			if err := <-done; !errors.Is(err, ErrUnavailable) {
				t.Fatalf("late result: %v", err)
			}
			if _, ok, err := m.Dequeue(owner, id); ok {
				t.Fatalf("late result queued: %v", err)
			}
		})
	}
}

func TestCaptureRejectsInvalidSurrogateInsteadOfReplacingIt(t *testing.T) {
	_, _, owner, _, lease, _, _ := runnerFixture(t, 1,
		functionExecutor(func(context.Context, []byte) ([]byte, error) { return nil, nil }))
	snapshot := []byte(`{"workspace_id":"w","snapshot_id":"snap","plan_version":"v1","as_of":"2026-10-09T00:00:00Z","synthetic":true,"extra":"\ud800"}`)
	if _, err := CaptureRecoveryContext(owner, lease, RecoveryMetadata{"snap", "v1", "s"}, snapshot); !errors.Is(err, ErrInvalid) {
		t.Fatalf("invalid escaped Unicode was converted: %v", err)
	}
	valid := []byte(`{"workspace_id":"w","snapshot_id":"snap","plan_version":"v1","as_of":"2026-10-09T00:00:00Z","synthetic":true,"extra":"\ud83d\ude00"}`)
	if _, err := CaptureRecoveryContext(owner, lease, RecoveryMetadata{"snap", "v1", "s"}, valid); err != nil {
		t.Fatalf("valid surrogate pair rejected: %v", err)
	}
}

func TestSuppressedBridgeOutcomeReturnsUnavailableWithoutQueue(t *testing.T) {
	executor := functionExecutor(func(_ context.Context, request []byte) ([]byte, error) {
		var response map[string]any
		_ = json.Unmarshal(rejectedProtocolResponse(request), &response)
		outcome := response["outcome"].(map[string]any)
		outcome["status"] = "suppressed"
		outcome["reason"] = "inactive_turn"
		return json.Marshal(response)
	})
	m, runner, owner, id, lease, captured, call := runnerFixture(t, 1, executor)
	if err := runner.Run(owner, lease, captured, call); !errors.Is(err, ErrUnavailable) {
		t.Fatalf("suppressed outcome returned %v", err)
	}
	if _, ok, err := m.Dequeue(owner, id); err != nil || ok {
		t.Fatalf("suppressed result queued: %v %v", ok, err)
	}
}

func TestRunnerDeadlineSuppressesValidLateResponseWhileLeaseRemainsCurrent(t *testing.T) {
	calls := 0
	executor := functionExecutor(func(ctx context.Context, request []byte) ([]byte, error) {
		calls++
		if calls == 1 {
			<-ctx.Done()
		}
		return rejectedProtocolResponse(request), nil
	})
	m, runner, owner, id, lease, captured, call := runnerFixture(t, 1, executor)
	runner.timeout = 20 * time.Millisecond // private deterministic test seam; production is 30s
	if err := runner.Run(owner, lease, captured, call); !errors.Is(err, ErrUnavailable) {
		t.Fatalf("expired runner accepted valid late response: %v", err)
	}
	if !m.IsCurrent(owner, lease) {
		t.Fatal("test did not isolate runner deadline from Manager lease")
	}
	if _, ok, err := m.Dequeue(owner, id); err != nil || ok {
		t.Fatalf("expired runner queued output: %v %v", ok, err)
	}
	if err := runner.Run(owner, lease, captured, call); err != nil {
		t.Fatalf("deadline retained gate capacity: %v", err)
	}
	if calls != 2 {
		t.Fatalf("executor calls = %d", calls)
	}
	if _, ok, err := m.Dequeue(owner, id); err != nil || !ok {
		t.Fatalf("fresh current execution did not queue: %v %v", ok, err)
	}
}
