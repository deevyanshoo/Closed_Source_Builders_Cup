package voice

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"reflect"
	"sync"
	"time"
	"unicode/utf8"
)

var ErrExecution = errors.New("voice local execution unavailable")

const (
	localCallLimit     = 128 << 10
	localHostLimit     = 1 << 20
	localRequestLimit  = 3 << 20
	localResponseLimit = 2 << 20
	localDepthLimit    = 16
	localArrayLimit    = 256
	localArrayTotal    = 4096
	localToolName      = "compare_receipt_recovery"
)

type RecoveryMetadata struct {
	SnapshotID  string
	PlanVersion string
	ScenarioID  string
}

// RecoveryContext owns the exact encoded host context and captured lease.
type RecoveryContext struct {
	owner      Owner
	lease      *Lease
	metadata   RecoveryMetadata
	hostJSON   []byte
	digest     string
	asOf       string
	snapshot   map[string]any
	arrayCount int
}

type Executor interface {
	Execute(context.Context, []byte) ([]byte, error)
}

type Runner struct {
	manager  *Manager
	executor Executor
	mu       sync.Mutex
	inflight int
	active   map[*Lease]bool
}

// strictValue rejects duplicates, numbers, null, excessive arrays and depth.
func strictValue(decoder *json.Decoder, depth int, total *int) (any, error) {
	if depth > localDepthLimit {
		return nil, ErrInvalid
	}
	token, err := decoder.Token()
	if err != nil {
		return nil, err
	}
	switch token {
	case json.Delim('{'):
		object := make(map[string]any)
		for decoder.More() {
			key, err := decoder.Token()
			if err != nil {
				return nil, err
			}
			name, ok := key.(string)
			if !ok {
				return nil, ErrInvalid
			}
			if _, exists := object[name]; exists {
				return nil, ErrInvalid
			}
			value, err := strictValue(decoder, depth+1, total)
			if err != nil {
				return nil, err
			}
			object[name] = value
		}
		end, err := decoder.Token()
		if err != nil || end != json.Delim('}') {
			return nil, ErrInvalid
		}
		return object, nil
	case json.Delim('['):
		array := make([]any, 0)
		for decoder.More() {
			if len(array) >= localArrayLimit || *total >= localArrayTotal {
				return nil, ErrInvalid
			}
			*total++
			value, err := strictValue(decoder, depth+1, total)
			if err != nil {
				return nil, err
			}
			array = append(array, value)
		}
		end, err := decoder.Token()
		if err != nil || end != json.Delim(']') {
			return nil, ErrInvalid
		}
		return array, nil
	case json.Delim('}'), json.Delim(']'):
		return nil, ErrInvalid
	default:
		switch value := token.(type) {
		case string:
			if !utf8.ValidString(value) {
				return nil, ErrInvalid
			}
			return value, nil
		case bool:
			return value, nil
		default:
			return nil, ErrInvalid
		}
	}
}

func strictParse(raw []byte, limit int, total *int) (any, error) {
	if len(raw) > limit || !utf8.Valid(raw) || !validEscapedUnicode(raw) {
		return nil, ErrInvalid
	}
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.UseNumber()
	value, err := strictValue(decoder, 0, total)
	if err != nil {
		return nil, ErrInvalid
	}
	if _, err := decoder.Token(); err != io.EOF {
		return nil, ErrInvalid
	}
	return value, nil
}

func hexValue(b byte) (uint16, bool) {
	switch {
	case b >= '0' && b <= '9':
		return uint16(b - '0'), true
	case b >= 'a' && b <= 'f':
		return uint16(b - 'a' + 10), true
	case b >= 'A' && b <= 'F':
		return uint16(b - 'A' + 10), true
	default:
		return 0, false
	}
}

func codeUnit(raw []byte) (uint16, bool) {
	if len(raw) != 4 {
		return 0, false
	}
	var value uint16
	for _, b := range raw {
		nibble, ok := hexValue(b)
		if !ok {
			return 0, false
		}
		value = value<<4 | nibble
	}
	return value, true
}

// encoding/json replaces lone surrogate escapes with U+FFFD. Reject them so
// captured host bytes cannot silently change identity or source content.
func validEscapedUnicode(raw []byte) bool {
	inString := false
	for i := 0; i < len(raw); i++ {
		switch raw[i] {
		case '"':
			inString = !inString
		case '\\':
			if !inString {
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
			unit, ok := codeUnit(raw[i+2 : i+6])
			if !ok {
				return false
			}
			i += 5
			if unit >= 0xDC00 && unit <= 0xDFFF {
				return false
			}
			if unit >= 0xD800 && unit <= 0xDBFF {
				start := i + 1
				if start+6 > len(raw) || raw[start] != '\\' || raw[start+1] != 'u' {
					return false
				}
				low, ok := codeUnit(raw[start+2 : start+6])
				if !ok || low < 0xDC00 || low > 0xDFFF {
					return false
				}
				i = start + 5
			}
		}
	}
	return true
}

func exactKeys(object map[string]any, names ...string) bool {
	if len(object) != len(names) {
		return false
	}
	for _, name := range names {
		if _, exists := object[name]; !exists {
			return false
		}
	}
	return true
}

func stringField(object map[string]any, name string) string {
	value, _ := object[name].(string)
	return value
}

func CaptureRecoveryContext(owner Owner, lease *Lease, metadata RecoveryMetadata, snapshotJSON []byte) (*RecoveryContext, error) {
	if !validOwner(owner) || lease == nil || lease.manager == nil ||
		!validID(lease.SessionID()) || !validID(lease.TurnID()) ||
		!validID(metadata.SnapshotID) || !validID(metadata.PlanVersion) ||
		!validID(metadata.ScenarioID) || !lease.manager.IsCurrent(owner, lease) {
		return nil, ErrUnavailable
	}
	total := 0
	value, err := strictParse(snapshotJSON, localHostLimit, &total)
	if err != nil {
		return nil, ErrInvalid
	}
	snapshot, ok := value.(map[string]any)
	if !ok || stringField(snapshot, "workspace_id") != owner.WorkspaceID ||
		stringField(snapshot, "snapshot_id") != metadata.SnapshotID ||
		stringField(snapshot, "plan_version") != metadata.PlanVersion ||
		snapshot["synthetic"] != true || stringField(snapshot, "as_of") == "" {
		return nil, ErrInvalid
	}
	canonical, err := json.Marshal(snapshot)
	if err != nil {
		return nil, ErrInvalid
	}
	host := map[string]any{
		"principal_id": owner.PrincipalID, "workspace_id": owner.WorkspaceID,
		"snapshot_id": metadata.SnapshotID, "plan_version": metadata.PlanVersion,
		"scenario_id": metadata.ScenarioID, "session_id": lease.SessionID(),
		"turn_id": lease.TurnID(), "snapshot": json.RawMessage(canonical),
	}
	hostJSON, err := json.Marshal(host)
	if err != nil || len(hostJSON) > localHostLimit {
		return nil, ErrInvalid
	}
	digest := sha256.Sum256(hostJSON)
	return &RecoveryContext{owner: owner, lease: lease, metadata: metadata,
		hostJSON: hostJSON, digest: hex.EncodeToString(digest[:]),
		asOf: stringField(snapshot, "as_of"), snapshot: snapshot, arrayCount: total}, nil
}

func NewRunner(manager *Manager, executor Executor) (*Runner, error) {
	if manager == nil || executor == nil {
		return nil, ErrInvalid
	}
	value := reflect.ValueOf(executor)
	switch value.Kind() {
	case reflect.Chan, reflect.Func, reflect.Interface, reflect.Map, reflect.Ptr, reflect.Slice:
		if value.IsNil() {
			return nil, ErrInvalid
		}
	}
	return &Runner{manager: manager, executor: executor, active: make(map[*Lease]bool)}, nil
}

func (r *Runner) acquire(lease *Lease) bool {
	r.mu.Lock()
	defer r.mu.Unlock()
	if r.inflight >= 4 || r.active[lease] {
		return false
	}
	r.inflight++
	r.active[lease] = true
	return true
}

func (r *Runner) release(lease *Lease) {
	r.mu.Lock()
	delete(r.active, lease)
	r.inflight--
	r.mu.Unlock()
}

func (r *Runner) Run(owner Owner, lease *Lease, captured *RecoveryContext, callJSON []byte) (err error) {
	if r == nil || r.manager == nil || r.executor == nil || captured == nil ||
		lease == nil || captured.lease != lease || captured.owner != owner ||
		lease.manager != r.manager || !r.manager.IsCurrent(owner, lease) {
		return ErrUnavailable
	}
	if !r.acquire(lease) {
		return ErrCapacity
	}
	defer r.release(lease)
	defer func() {
		if recover() != nil {
			err = ErrExecution
		}
	}()
	call := append([]byte(nil), callJSON...)
	total := captured.arrayCount
	value, parseErr := strictParse(call, localCallLimit, &total)
	if parseErr != nil {
		return ErrInvalid
	}
	envelope, ok := value.(map[string]any)
	if !ok || !exactKeys(envelope, "name", "arguments") ||
		envelope["name"] != localToolName {
		return ErrInvalid
	}
	arguments, ok := envelope["arguments"].(map[string]any)
	if !ok {
		return ErrInvalid
	}
	for key := range arguments {
		if key != "arrival_replacements" && key != "assignments" {
			return ErrInvalid
		}
	}
	request, marshalErr := json.Marshal(map[string]any{
		"bridge_version": "1", "host_context_json": string(captured.hostJSON),
		"call_json": string(call),
	})
	if marshalErr != nil || len(request) > localRequestLimit {
		return ErrInvalid
	}
	if !r.manager.IsCurrent(owner, lease) {
		return ErrUnavailable
	}
	ctx, cancel := context.WithTimeout(lease.Context(), 30*time.Second)
	defer cancel()
	response, execErr := r.executor.Execute(ctx, append([]byte(nil), request...))
	if execErr != nil {
		if !r.manager.IsCurrent(owner, lease) {
			return ErrUnavailable
		}
		return ErrExecution
	}
	if !r.manager.IsCurrent(owner, lease) {
		return ErrUnavailable
	}
	if err := validateLocalResponse(response, captured); err != nil {
		if errors.Is(err, ErrUnavailable) {
			return ErrUnavailable
		}
		return ErrExecution
	}
	if !r.manager.IsCurrent(owner, lease) {
		return ErrUnavailable
	}
	if err := r.manager.Enqueue(owner, lease, response); err != nil {
		return err
	}
	return nil
}
