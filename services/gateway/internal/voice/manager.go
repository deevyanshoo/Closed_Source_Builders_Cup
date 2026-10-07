// Package voice provides process-local, owner-bound session and turn leases.
// It does not authenticate owners or make external delivery atomic.
package voice

import (
	"context"
	"crypto/rand"
	"encoding/base64"
	"errors"
	"math"
	"strconv"
	"strings"
	"sync"
	"time"
	"unicode"
	"unicode/utf8"
)

var (
	ErrInvalid     = errors.New("invalid voice lifecycle input")
	ErrUnavailable = errors.New("voice session unavailable")
	ErrCapacity    = errors.New("voice lifecycle capacity exhausted")
)

type Owner struct {
	PrincipalID string
	WorkspaceID string
}

type Config struct {
	MaxSessions         int
	MaxSessionsPerOwner int
	IdleTimeout         time.Duration
	MaxQueuedMessages   int
	MaxPayloadBytes     int
	MaxQueuedBytes      int
}

type Manager struct {
	mu          sync.Mutex
	config      Config
	now         func() time.Time
	lastSample  time.Time
	sessions    map[string]*session
	ownerCounts map[Owner]int
	shutdown    bool
}

type session struct {
	owner        Owner
	id           string
	lastActivity time.Time
	generation   uint64
	current      *Lease
	queue        [][]byte
	queueBytes   int
}

// Lease is an immutable handle to one turn. A later turn invalidates it.
type Lease struct {
	manager   *Manager
	sessionID string
	turnID    string
	ctx       context.Context
	cancel    context.CancelFunc
}

func (l *Lease) SessionID() string {
	if l == nil {
		return ""
	}
	return l.sessionID
}

func (l *Lease) TurnID() string {
	if l == nil {
		return ""
	}
	return l.turnID
}

func (l *Lease) Context() context.Context {
	if l == nil {
		return nil
	}
	return l.ctx
}

type Output struct {
	SessionID string
	TurnID    string
	Payload   []byte
}

func validID(value string) bool {
	if value == "" || len(value) > 256 || !utf8.ValidString(value) || strings.TrimSpace(value) != value {
		return false
	}
	for _, r := range value {
		if unicode.IsControl(r) {
			return false
		}
	}
	return true
}

func validOwner(owner Owner) bool {
	return validID(owner.PrincipalID) && validID(owner.WorkspaceID)
}

func validConfig(c Config) bool {
	return c.MaxSessions > 0 && c.MaxSessions <= 4096 &&
		c.MaxSessionsPerOwner > 0 && c.MaxSessionsPerOwner <= c.MaxSessions &&
		c.IdleTimeout > 0 && c.IdleTimeout <= 24*time.Hour &&
		c.MaxQueuedMessages > 0 && c.MaxQueuedMessages <= 256 &&
		c.MaxPayloadBytes > 0 && c.MaxPayloadBytes <= 2<<20 &&
		c.MaxQueuedBytes >= c.MaxPayloadBytes && c.MaxQueuedBytes <= 8<<20 &&
		int64(c.MaxSessions)*int64(c.MaxQueuedBytes) <= 64<<20
}

func NewManager(config Config, now func() time.Time) (*Manager, error) {
	if !validConfig(config) || now == nil {
		return nil, ErrInvalid
	}
	return &Manager{config: config, now: now,
		sessions: make(map[string]*session), ownerCounts: make(map[Owner]int)}, nil
}

func randomSessionID() (string, error) {
	var data [24]byte
	if _, err := rand.Read(data[:]); err != nil {
		return "", ErrUnavailable
	}
	return base64.RawURLEncoding.EncodeToString(data[:]), nil
}

func runCancels(cancels []context.CancelFunc) {
	for _, cancel := range cancels {
		cancel()
	}
}

// enter samples the host clock before locking. A backward sample is clamped.
func (m *Manager) enter(sample time.Time) []context.CancelFunc {
	m.mu.Lock()
	if sample.Before(m.lastSample) {
		sample = m.lastSample
	} else {
		m.lastSample = sample
	}
	var cancels []context.CancelFunc
	if m.shutdown {
		return cancels
	}
	for id, s := range m.sessions {
		if !sample.Before(s.lastActivity.Add(m.config.IdleTimeout)) {
			if s.current != nil {
				cancels = append(cancels, s.current.cancel)
			}
			m.removeLocked(id, s)
			continue
		}
		if s.current != nil && s.current.ctx.Err() != nil {
			cancels = append(cancels, s.current.cancel)
			s.current = nil
			s.queue = nil
			s.queueBytes = 0
		}
	}
	return cancels
}

func (m *Manager) leave(cancels []context.CancelFunc) {
	m.mu.Unlock()
	runCancels(cancels)
}

func (m *Manager) removeLocked(id string, s *session) {
	delete(m.sessions, id)
	m.ownerCounts[s.owner]--
	if m.ownerCounts[s.owner] == 0 {
		delete(m.ownerCounts, s.owner)
	}
	s.current = nil
	s.queue = nil
	s.queueBytes = 0
}

func (m *Manager) getLocked(owner Owner, id string) *session {
	if !validOwner(owner) || !validID(id) {
		return nil
	}
	s := m.sessions[id]
	if s == nil || s.owner != owner {
		return nil
	}
	return s
}

func (m *Manager) currentLocked(owner Owner, lease *Lease) *session {
	if lease == nil || lease.manager != m {
		return nil
	}
	s := m.getLocked(owner, lease.sessionID)
	if s == nil || s.current != lease || lease.ctx == nil || lease.ctx.Err() != nil {
		return nil
	}
	return s
}

func (m *Manager) Create(owner Owner) (string, error) {
	if m == nil || !validOwner(owner) {
		return "", ErrInvalid
	}
	sample := m.now()
	for attempt := 0; attempt < 4; attempt++ {
		id, err := randomSessionID()
		if err != nil {
			return "", err
		}
		cancels := m.enter(sample)
		if m.shutdown {
			m.leave(cancels)
			return "", ErrUnavailable
		}
		if len(m.sessions) >= m.config.MaxSessions || m.ownerCounts[owner] >= m.config.MaxSessionsPerOwner {
			m.leave(cancels)
			return "", ErrCapacity
		}
		if m.sessions[id] != nil {
			m.leave(cancels)
			continue
		}
		m.sessions[id] = &session{owner: owner, id: id, lastActivity: m.lastSample}
		m.ownerCounts[owner]++
		m.leave(cancels)
		return id, nil
	}
	return "", ErrUnavailable
}

func (m *Manager) StartTurn(parent context.Context, owner Owner, sessionID string) (*Lease, error) {
	if m == nil || parent == nil || parent.Err() != nil {
		return nil, ErrInvalid
	}
	ctx, cancel := context.WithCancel(parent)
	sample := m.now()
	cancels := m.enter(sample)
	defer func() { m.leave(cancels) }()
	if m.shutdown {
		cancels = append(cancels, cancel)
		return nil, ErrUnavailable
	}
	s := m.getLocked(owner, sessionID)
	if s == nil {
		cancels = append(cancels, cancel)
		return nil, ErrUnavailable
	}
	if ctx.Err() != nil {
		cancels = append(cancels, cancel)
		return nil, ErrInvalid
	}
	if s.generation == math.MaxUint64 {
		cancels = append(cancels, cancel)
		return nil, ErrCapacity
	}
	s.generation++
	lease := &Lease{manager: m, sessionID: sessionID,
		turnID: strconv.FormatUint(s.generation, 10), ctx: ctx, cancel: cancel}
	if s.current != nil {
		cancels = append(cancels, s.current.cancel)
	}
	s.current = lease
	s.queue = nil
	s.queueBytes = 0
	s.lastActivity = m.lastSample
	return lease, nil
}

func (m *Manager) IsCurrent(owner Owner, lease *Lease) bool {
	if m == nil {
		return false
	}
	cancels := m.enter(m.now())
	defer func() { m.leave(cancels) }()
	return !m.shutdown && m.currentLocked(owner, lease) != nil
}

func (m *Manager) Cancel(owner Owner, lease *Lease) error {
	if m == nil {
		return ErrUnavailable
	}
	cancels := m.enter(m.now())
	defer func() { m.leave(cancels) }()
	if m.shutdown {
		return ErrUnavailable
	}
	s := m.currentLocked(owner, lease)
	if s == nil {
		return ErrUnavailable
	}
	cancels = append(cancels, lease.cancel)
	s.current = nil
	s.queue = nil
	s.queueBytes = 0
	s.lastActivity = m.lastSample
	return nil
}

func (m *Manager) Close(owner Owner, sessionID string) error {
	if m == nil {
		return ErrUnavailable
	}
	cancels := m.enter(m.now())
	defer func() { m.leave(cancels) }()
	if m.shutdown {
		return ErrUnavailable
	}
	s := m.getLocked(owner, sessionID)
	if s == nil {
		return ErrUnavailable
	}
	if s.current != nil {
		cancels = append(cancels, s.current.cancel)
	}
	m.removeLocked(sessionID, s)
	return nil
}

func (m *Manager) Enqueue(owner Owner, lease *Lease, payload []byte) error {
	if m == nil {
		return ErrUnavailable
	}
	if len(payload) == 0 || len(payload) > m.config.MaxPayloadBytes {
		return ErrInvalid
	}
	cancels := m.enter(m.now())
	defer func() { m.leave(cancels) }()
	if m.shutdown {
		return ErrUnavailable
	}
	s := m.currentLocked(owner, lease)
	if s == nil {
		return ErrUnavailable
	}
	if len(s.queue) >= m.config.MaxQueuedMessages || s.queueBytes+len(payload) > m.config.MaxQueuedBytes {
		return ErrCapacity
	}
	s.queue = append(s.queue, append([]byte(nil), payload...))
	s.queueBytes += len(payload)
	s.lastActivity = m.lastSample
	return nil
}

func (m *Manager) Dequeue(owner Owner, sessionID string) (Output, bool, error) {
	if m == nil {
		return Output{}, false, ErrUnavailable
	}
	cancels := m.enter(m.now())
	defer func() { m.leave(cancels) }()
	if m.shutdown {
		return Output{}, false, ErrUnavailable
	}
	s := m.getLocked(owner, sessionID)
	if s == nil {
		return Output{}, false, ErrUnavailable
	}
	s.lastActivity = m.lastSample
	if s.current == nil || len(s.queue) == 0 {
		return Output{}, false, nil
	}
	data := s.queue[0]
	s.queue[0] = nil
	s.queue = s.queue[1:]
	s.queueBytes -= len(data)
	return Output{SessionID: sessionID, TurnID: s.current.turnID, Payload: data}, true, nil
}

func (m *Manager) Sweep() {
	if m == nil {
		return
	}
	cancels := m.enter(m.now())
	m.leave(cancels)
}

func (m *Manager) Shutdown() {
	if m == nil {
		return
	}
	m.mu.Lock()
	var cancels []context.CancelFunc
	if !m.shutdown {
		m.shutdown = true
		for _, s := range m.sessions {
			if s.current != nil {
				cancels = append(cancels, s.current.cancel)
			}
		}
		m.sessions = make(map[string]*session)
		m.ownerCounts = make(map[Owner]int)
	}
	m.mu.Unlock()
	runCancels(cancels)
}
