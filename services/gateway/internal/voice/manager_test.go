package voice

import (
	"context"
	"errors"
	"math"
	"strings"
	"sync"
	"testing"
	"time"
)

type testClock struct {
	mu sync.Mutex
	at time.Time
}

func (c *testClock) now() time.Time {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.at
}

func (c *testClock) advance(d time.Duration) {
	c.mu.Lock()
	c.at = c.at.Add(d)
	c.mu.Unlock()
}

func newTestManager(t *testing.T, c *testClock, config Config) *Manager {
	t.Helper()
	m, err := NewManager(config, c.now)
	if err != nil {
		t.Fatal(err)
	}
	return m
}

func mustCreate(t *testing.T, m *Manager, owner Owner) string {
	t.Helper()
	id, err := m.Create(owner)
	if err != nil {
		t.Fatal(err)
	}
	return id
}

func mustTurn(t *testing.T, m *Manager, owner Owner, id string) *Lease {
	t.Helper()
	lease, err := m.StartTurn(context.Background(), owner, id)
	if err != nil {
		t.Fatal(err)
	}
	return lease
}

func testConfig() Config {
	return Config{MaxSessions: 2, MaxSessionsPerOwner: 1, IdleTimeout: time.Minute,
		MaxQueuedMessages: 2, MaxPayloadBytes: 8, MaxQueuedBytes: 12}
}

func TestCurrentTurnFIFOAndReplacement(t *testing.T) {
	owner := Owner{"principal", "workspace"}
	m, err := NewManager(testConfig(), func() time.Time { return time.Unix(100, 0) })
	if err != nil {
		t.Fatal(err)
	}
	id, err := m.Create(owner)
	if err != nil {
		t.Fatal(err)
	}
	first, err := m.StartTurn(context.Background(), owner, id)
	if err != nil {
		t.Fatal(err)
	}
	if err := m.Enqueue(owner, first, []byte("first")); err != nil {
		t.Fatal(err)
	}
	out, ok, err := m.Dequeue(owner, id)
	if err != nil || !ok || string(out.Payload) != "first" || out.SessionID != id || out.TurnID != first.TurnID() {
		t.Fatalf("current output = %+v, %v, %v", out, ok, err)
	}
	if err := m.Enqueue(owner, first, []byte("stale")); err != nil {
		t.Fatal(err)
	}
	second, err := m.StartTurn(context.Background(), owner, id)
	if err != nil {
		t.Fatal(err)
	}
	if m.IsCurrent(owner, first) || !m.IsCurrent(owner, second) || first.TurnID() == second.TurnID() {
		t.Fatal("replacement did not invalidate first turn")
	}
	if _, ok, err := m.Dequeue(owner, id); err != nil || ok {
		t.Fatalf("stale output delivered: %v %v", ok, err)
	}
}

func TestBoundedQueueCopiesAndCurrentTurnFIFO(t *testing.T) {
	owner := Owner{"p", "w"}
	m := newTestManager(t, &testClock{at: time.Unix(1, 0)}, testConfig())
	id := mustCreate(t, m, owner)
	lease := mustTurn(t, m, owner, id)
	input := []byte("12345")
	if err := m.Enqueue(owner, lease, input); err != nil {
		t.Fatal(err)
	}
	input[0] = 'X'
	if err := m.Enqueue(owner, lease, []byte("678901")); err != nil {
		t.Fatal(err)
	}
	if err := m.Enqueue(owner, lease, []byte("z")); !errors.Is(err, ErrCapacity) {
		t.Fatalf("queue count: %v", err)
	}
	first, ok, err := m.Dequeue(owner, id)
	if err != nil || !ok || string(first.Payload) != "12345" {
		t.Fatalf("copied FIFO: %+v %v %v", first, ok, err)
	}
	first.Payload[0] = 'Y'
	second, ok, err := m.Dequeue(owner, id)
	if err != nil || !ok || string(second.Payload) != "678901" {
		t.Fatalf("second FIFO: %+v %v %v", second, ok, err)
	}
	if _, ok, err := m.Dequeue(owner, id); err != nil || ok {
		t.Fatalf("empty queue: %v %v", ok, err)
	}
	if err := m.Enqueue(owner, lease, nil); !errors.Is(err, ErrInvalid) {
		t.Fatalf("empty payload: %v", err)
	}
	if err := m.Enqueue(owner, lease, []byte("123456789")); !errors.Is(err, ErrInvalid) {
		t.Fatalf("large payload: %v", err)
	}
	if err := m.Enqueue(owner, lease, []byte("12345678")); err != nil {
		t.Fatal(err)
	}
	if err := m.Enqueue(owner, lease, []byte("12345")); !errors.Is(err, ErrCapacity) {
		t.Fatalf("byte limit: %v", err)
	}
}

func TestOwnerAndLeaseBoundariesHideSessionExistence(t *testing.T) {
	owner := Owner{"p", "w"}
	otherPrincipal := Owner{"q", "w"}
	otherWorkspace := Owner{"p", "other"}
	c := &testClock{at: time.Unix(1, 0)}
	m := newTestManager(t, c, testConfig())
	id := mustCreate(t, m, owner)
	lease := mustTurn(t, m, owner, id)
	foreign := newTestManager(t, c, testConfig())
	for _, wrong := range []Owner{otherPrincipal, otherWorkspace, {"", "w"}, {"p", "\n"}} {
		if m.IsCurrent(wrong, lease) {
			t.Fatal("foreign owner observed current lease")
		}
		if err := m.Enqueue(wrong, lease, []byte("x")); !errors.Is(err, ErrUnavailable) {
			t.Fatalf("foreign enqueue: %v", err)
		}
		if _, _, err := m.Dequeue(wrong, id); !errors.Is(err, ErrUnavailable) {
			t.Fatalf("foreign dequeue: %v", err)
		}
		if err := m.Cancel(wrong, lease); !errors.Is(err, ErrUnavailable) {
			t.Fatalf("foreign cancel: %v", err)
		}
		if err := m.Close(wrong, id); !errors.Is(err, ErrUnavailable) {
			t.Fatalf("foreign close: %v", err)
		}
		if _, err := m.StartTurn(context.Background(), wrong, id); !errors.Is(err, ErrUnavailable) {
			t.Fatalf("foreign turn: %v", err)
		}
	}
	if foreign.IsCurrent(owner, lease) {
		t.Fatal("cross-manager lease accepted")
	}
	if err := foreign.Enqueue(owner, lease, []byte("x")); !errors.Is(err, ErrUnavailable) {
		t.Fatalf("cross-manager enqueue: %v", err)
	}
	if m.IsCurrent(owner, nil) || m.IsCurrent(owner, &Lease{}) {
		t.Fatal("nil/zero lease current")
	}
	if err := m.Cancel(owner, &Lease{}); !errors.Is(err, ErrUnavailable) {
		t.Fatalf("zero lease cancel: %v", err)
	}
	if err := m.Close(owner, "missing"); !errors.Is(err, ErrUnavailable) {
		t.Fatalf("unknown close: %v", err)
	}
	if !m.IsCurrent(owner, lease) {
		t.Fatal("foreign operations changed current turn")
	}
}

func TestReplacementAndCancelSuppressCoordinatedProducer(t *testing.T) {
	owner := Owner{"p", "w"}
	m := newTestManager(t, &testClock{at: time.Unix(1, 0)}, testConfig())
	id := mustCreate(t, m, owner)
	old := mustTurn(t, m, owner, id)
	ready := make(chan struct{})
	release := make(chan struct{})
	result := make(chan error, 1)
	go func() {
		close(ready)
		<-release
		result <- m.Enqueue(owner, old, []byte("late"))
	}()
	<-ready
	next := mustTurn(t, m, owner, id)
	select {
	case <-old.Context().Done():
	default:
		t.Fatal("old context not cancelled")
	}
	close(release)
	if err := <-result; !errors.Is(err, ErrUnavailable) {
		t.Fatalf("late producer: %v", err)
	}
	if _, ok, err := m.Dequeue(owner, id); err != nil || ok {
		t.Fatalf("late result delivered: %v %v", ok, err)
	}
	if err := m.Enqueue(owner, next, []byte("current")); err != nil {
		t.Fatal(err)
	}
	if err := m.Cancel(owner, next); err != nil {
		t.Fatal(err)
	}
	select {
	case <-next.Context().Done():
	default:
		t.Fatal("cancelled turn context live")
	}
	if err := m.Cancel(owner, next); !errors.Is(err, ErrUnavailable) {
		t.Fatalf("repeat cancel: %v", err)
	}
	if _, ok, err := m.Dequeue(owner, id); err != nil || ok {
		t.Fatalf("cancelled output delivered: %v %v", ok, err)
	}
}

func TestParentCancellationSuppressesQueuedAndLaterOutput(t *testing.T) {
	owner := Owner{"p", "w"}
	m := newTestManager(t, &testClock{at: time.Unix(1, 0)}, testConfig())
	id := mustCreate(t, m, owner)
	parent, stop := context.WithCancel(context.Background())
	lease, err := m.StartTurn(parent, owner, id)
	if err != nil {
		t.Fatal(err)
	}
	if err := m.Enqueue(owner, lease, []byte("queued")); err != nil {
		t.Fatal(err)
	}
	stop()
	<-lease.Context().Done()
	if m.IsCurrent(owner, lease) {
		t.Fatal("parent-cancelled lease current")
	}
	if err := m.Enqueue(owner, lease, []byte("late")); !errors.Is(err, ErrUnavailable) {
		t.Fatalf("parent cancelled append: %v", err)
	}
	if _, ok, err := m.Dequeue(owner, id); err != nil || ok {
		t.Fatalf("parent-cancelled queue delivered: %v %v", ok, err)
	}
	cancelled, cancel := context.WithCancel(context.Background())
	cancel()
	if _, err := m.StartTurn(cancelled, owner, id); !errors.Is(err, ErrInvalid) {
		t.Fatalf("pre-cancelled parent: %v", err)
	}
	if _, err := m.StartTurn(nil, owner, id); !errors.Is(err, ErrInvalid) {
		t.Fatalf("nil parent: %v", err)
	}
}

func TestCancelDuringProducerWorkAndCloseDropQueuedOutput(t *testing.T) {
	owner := Owner{"p", "w"}
	m := newTestManager(t, &testClock{at: time.Unix(1, 0)}, testConfig())
	id := mustCreate(t, m, owner)
	lease := mustTurn(t, m, owner, id)
	producerStarted := make(chan struct{})
	producerRelease := make(chan struct{})
	producerResult := make(chan error, 1)
	go func() {
		close(producerStarted)
		<-producerRelease
		producerResult <- m.Enqueue(owner, lease, []byte("late"))
	}()
	<-producerStarted
	if err := m.Cancel(owner, lease); err != nil {
		t.Fatal(err)
	}
	close(producerRelease)
	if err := <-producerResult; !errors.Is(err, ErrUnavailable) {
		t.Fatalf("cancelled producer: %v", err)
	}
	if _, ok, err := m.Dequeue(owner, id); err != nil || ok {
		t.Fatalf("cancelled result delivered: %v %v", ok, err)
	}
	next := mustTurn(t, m, owner, id)
	if err := m.Enqueue(owner, next, []byte("queued")); err != nil {
		t.Fatal(err)
	}
	if err := m.Close(owner, id); err != nil {
		t.Fatal(err)
	}
	select {
	case <-next.Context().Done():
	default:
		t.Fatal("close did not cancel context")
	}
	if _, _, err := m.Dequeue(owner, id); !errors.Is(err, ErrUnavailable) {
		t.Fatalf("closed queue visible: %v", err)
	}
	if _, err := m.Create(owner); err != nil {
		t.Fatalf("close did not release quota: %v", err)
	}
}

func TestAcceptedActivityExtendsIdleLifetime(t *testing.T) {
	owner := Owner{"p", "w"}
	c := &testClock{at: time.Unix(1, 0)}
	m := newTestManager(t, c, testConfig())
	id := mustCreate(t, m, owner)
	lease := mustTurn(t, m, owner, id)
	c.advance(59 * time.Second)
	if err := m.Enqueue(owner, lease, []byte("x")); err != nil {
		t.Fatal(err)
	}
	c.advance(2 * time.Second)
	if !m.IsCurrent(owner, lease) {
		t.Fatal("enqueue did not refresh idle deadline")
	}
	if _, ok, err := m.Dequeue(owner, id); err != nil || !ok {
		t.Fatalf("dequeue: %v %v", ok, err)
	}
	c.advance(59 * time.Second)
	if !m.IsCurrent(owner, lease) {
		t.Fatal("dequeue did not refresh idle deadline")
	}
}

func TestExpiryAndCloseFreeOwnerAndGlobalQuota(t *testing.T) {
	owner, other := Owner{"p", "w"}, Owner{"q", "w"}
	c := &testClock{at: time.Unix(1, 0)}
	m := newTestManager(t, c, testConfig())
	id := mustCreate(t, m, owner)
	lease := mustTurn(t, m, owner, id)
	otherID := mustCreate(t, m, other)
	if _, err := m.Create(owner); !errors.Is(err, ErrCapacity) {
		t.Fatalf("per-owner quota: %v", err)
	}
	if _, err := m.Create(Owner{"third", "w"}); !errors.Is(err, ErrCapacity) {
		t.Fatalf("global quota: %v", err)
	}
	if err := m.Close(other, otherID); err != nil {
		t.Fatal(err)
	}
	if err := m.Close(other, otherID); !errors.Is(err, ErrUnavailable) {
		t.Fatalf("repeat close: %v", err)
	}
	c.advance(time.Minute)
	m.Sweep()
	select {
	case <-lease.Context().Done():
	default:
		t.Fatal("expired lease context live")
	}
	if m.IsCurrent(owner, lease) {
		t.Fatal("expired lease current")
	}
	if _, err := m.Create(owner); err != nil {
		t.Fatalf("expiry did not free owner quota: %v", err)
	}
	if err := m.Close(owner, id); !errors.Is(err, ErrUnavailable) {
		t.Fatalf("expired session still known: %v", err)
	}
}

func TestBackwardClockCannotResurrectExpiredLeaseOrRefreshPredicate(t *testing.T) {
	owner := Owner{"p", "w"}
	c := &testClock{at: time.Unix(1, 0)}
	m := newTestManager(t, c, testConfig())
	id := mustCreate(t, m, owner)
	lease := mustTurn(t, m, owner, id)
	c.advance(59 * time.Second)
	for range 3 {
		if !m.IsCurrent(owner, lease) {
			t.Fatal("early expiry")
		}
	}
	c.advance(time.Second)
	if m.IsCurrent(owner, lease) {
		t.Fatal("predicate refreshed idle deadline")
	}
	c.advance(-time.Minute)
	if m.IsCurrent(owner, lease) {
		t.Fatal("backward clock resurrected lease")
	}
	if err := m.Enqueue(owner, lease, []byte("x")); !errors.Is(err, ErrUnavailable) {
		t.Fatalf("resurrected enqueue: %v", err)
	}
}

func TestShutdownAndFreshManagerRejectOldHandles(t *testing.T) {
	owner := Owner{"p", "w"}
	c := &testClock{at: time.Unix(1, 0)}
	m := newTestManager(t, c, testConfig())
	id := mustCreate(t, m, owner)
	lease := mustTurn(t, m, owner, id)
	m.Shutdown()
	m.Shutdown()
	select {
	case <-lease.Context().Done():
	default:
		t.Fatal("shutdown did not cancel lease")
	}
	if m.IsCurrent(owner, lease) {
		t.Fatal("shutdown lease current")
	}
	if _, err := m.Create(owner); !errors.Is(err, ErrUnavailable) {
		t.Fatalf("shutdown create: %v", err)
	}
	if _, err := m.StartTurn(context.Background(), owner, id); !errors.Is(err, ErrUnavailable) {
		t.Fatalf("shutdown turn: %v", err)
	}
	if _, _, err := m.Dequeue(owner, id); !errors.Is(err, ErrUnavailable) {
		t.Fatalf("shutdown dequeue: %v", err)
	}
	fresh := newTestManager(t, c, testConfig())
	if fresh.IsCurrent(owner, lease) {
		t.Fatal("fresh manager accepted old lease")
	}
	if _, err := fresh.StartTurn(context.Background(), owner, id); !errors.Is(err, ErrUnavailable) {
		t.Fatalf("fresh manager recognized old session: %v", err)
	}
}

func TestConfigAndOwnerValidationAndTurnGenerationOverflow(t *testing.T) {
	c := &testClock{at: time.Unix(1, 0)}
	bad := []Config{{}, {MaxSessions: 4097}, {MaxSessions: 2, MaxSessionsPerOwner: 3},
		func() Config { x := testConfig(); x.IdleTimeout = 25 * time.Hour; return x }(),
		func() Config { x := testConfig(); x.MaxPayloadBytes = 2<<20 + 1; return x }(),
		func() Config { x := testConfig(); x.MaxQueuedBytes = 4; return x }(),
		func() Config { x := testConfig(); x.MaxQueuedMessages = 257; return x }(),
		Config{MaxSessions: 4096, MaxSessionsPerOwner: 1, IdleTimeout: time.Second,
			MaxQueuedMessages: 1, MaxPayloadBytes: 1, MaxQueuedBytes: 8 << 20}}
	for _, cfg := range bad {
		if _, err := NewManager(cfg, c.now); !errors.Is(err, ErrInvalid) {
			t.Fatalf("accepted bad config %+v: %v", cfg, err)
		}
	}
	if _, err := NewManager(testConfig(), nil); !errors.Is(err, ErrInvalid) {
		t.Fatalf("nil clock: %v", err)
	}
	m := newTestManager(t, c, testConfig())
	for _, invalid := range []Owner{{}, {" p", "w"}, {"p", "w\n"}, {strings.Repeat("x", 257), "w"}, {string([]byte{0xff}), "w"}} {
		if _, err := m.Create(invalid); !errors.Is(err, ErrInvalid) {
			t.Fatalf("invalid owner %+v: %v", invalid, err)
		}
	}
	owner := Owner{"p", "w"}
	id := mustCreate(t, m, owner)
	m.mu.Lock()
	m.sessions[id].generation = math.MaxUint64
	m.mu.Unlock()
	if _, err := m.StartTurn(context.Background(), owner, id); !errors.Is(err, ErrCapacity) {
		t.Fatalf("turn ID wrap: %v", err)
	}
}

func TestConcurrentReplaceAndEnqueueNeverDeliversOldTurn(t *testing.T) {
	owner := Owner{"p", "w"}
	m := newTestManager(t, &testClock{at: time.Unix(1, 0)}, testConfig())
	id := mustCreate(t, m, owner)
	old := mustTurn(t, m, owner, id)
	start := make(chan struct{})
	done := make(chan error, 1)
	go func() {
		<-start
		done <- m.Enqueue(owner, old, []byte("old"))
	}()
	close(start)
	next := mustTurn(t, m, owner, id)
	err := <-done
	if err != nil && !errors.Is(err, ErrUnavailable) {
		t.Fatalf("concurrent append: %v", err)
	}
	if _, ok, err := m.Dequeue(owner, id); err != nil || ok {
		t.Fatalf("old turn survived replacement: %v %v", ok, err)
	}
	if !m.IsCurrent(owner, next) {
		t.Fatal("replacement not current")
	}
}
