package main

import (
	"bytes"
	"context"
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"regexp"
	"strconv"
	"strings"
	"sync"
	"time"
	"unicode/utf8"
)

const (
	shipmentRequestLimit = 2 << 20
	shipmentPartLimit    = 1 << 20
	shipmentCatalogLimit = (8 << 20) + (32 << 10)
	shipmentOutcomeLimit = 4 << 20
	shipmentWireLimit    = (8 << 20) + (32 << 10)
)

var (
	shipmentID               = regexp.MustCompile(`^[0-9a-f]{32}$`)
	shipmentHash             = regexp.MustCompile(`^[0-9a-f]{64}$`)
	shipmentTimestamp        = regexp.MustCompile(`^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$`)
	shipmentCandidateOfferID = regexp.MustCompile(`^[A-Za-z0-9_-]{1,128}$`)
	shipmentDocumentID       = regexp.MustCompile(`^sha256:[0-9a-f]{64}$`)
	errShipmentUnavailable   = errors.New("shipment recovery unavailable")
	errShipmentStale         = errors.New("shipment context stale")
	errShipmentUnknown       = errors.New("shipment context unavailable")
	errShipmentBusy          = errors.New("shipment recovery busy")
	errShipmentInvalidOutput = errors.New("shipment child invalid output")
)

type HostSpec struct {
	WorkspaceID           string                    `json:"workspace_id"`
	PlanVersion           string                    `json:"plan_version"`
	PropagationSnapshotID string                    `json:"propagation_snapshot_id"`
	ProductionSnapshotID  string                    `json:"production_snapshot_id"`
	AsOf                  string                    `json:"as_of"`
	Request               json.RawMessage           `json:"request"`
	CandidateBinding      *shipmentCandidateBinding `json:"candidate_binding,omitempty"`
}

// Host assertion only. A later public bridge must independently verify current sourcing.
type shipmentCandidateBinding struct {
	WorkspaceID  string `json:"workspace_id"`
	CandidateID  string `json:"candidate_id"`
	OfferID      string `json:"offer_id"`
	OfferVersion int    `json:"offer_version"`
	ItemIndex    int    `json:"item_index"`
	DocumentID   string `json:"document_id"`
}

type shipmentCandidateAssociation struct {
	Candidate     shipmentCandidateBinding
	ContextID     string
	Revision      int
	RequestSHA256 string
	PlanVersion   string
	AsOf          string
}

type ContextMetadata struct {
	ContextID             string `json:"context_id"`
	Revision              int    `json:"revision"`
	WorkspaceID           string `json:"workspace_id"`
	PlanVersion           string `json:"plan_version"`
	PropagationSnapshotID string `json:"propagation_snapshot_id"`
	ProductionSnapshotID  string `json:"production_snapshot_id"`
	AsOf                  string `json:"as_of"`
	Synthetic             bool   `json:"synthetic"`
	Source                string `json:"source"`
	Persistence           string `json:"persistence"`
	SourceAuthenticity    string `json:"source_authenticity"`
	RequestSHA256         string `json:"request_sha256"`
}

type ShipmentExecutor interface {
	Execute(context.Context, []byte) ([]byte, error)
}

type shipmentContext struct {
	metadata ContextMetadata
	request  []byte
	binding  *shipmentCandidateAssociation
}

type shipmentLease struct {
	owner   string
	current *shipmentContext
	cancel  context.CancelFunc
}

type shipmentAdapter struct {
	mu           sync.Mutex
	executor     ShipmentExecutor
	contexts     map[string]map[string]*shipmentContext
	leases       map[*shipmentLease]struct{}
	perPrincipal map[string]int
	closed       bool
	ready        bool
}

func newShipmentAdapter(executor ShipmentExecutor) *shipmentAdapter {
	return &shipmentAdapter{executor: executor, contexts: map[string]map[string]*shipmentContext{},
		leases: map[*shipmentLease]struct{}{}, perPrincipal: map[string]int{}, ready: executor != nil}
}

func shipmentText(s string) bool {
	return s != "" && s == strings.TrimSpace(s) && utf8.ValidString(s) && utf8.RuneCountInString(s) <= 256
}

func shipmentTime(s string) bool {
	if len(s) > 40 || !shipmentTimestamp.MatchString(s) {
		return false
	}
	t, err := time.Parse(time.RFC3339Nano, s)
	if err != nil {
		return false
	}
	_, offset := t.Zone()
	return offset == 0
}

func shipmentAwareTime(s string) bool {
	if len(s) > 40 || !shipmentTimestamp.MatchString(s) {
		return false
	}
	_, err := time.Parse(time.RFC3339Nano, s)
	return err == nil
}

type shipmentJSONLimits struct {
	bytes, depth, array, entries, text int
	numbers                            bool
}

var shipmentInputLimits = shipmentJSONLimits{shipmentRequestLimit, 16, 256, 16384, 128 << 10, false}
var shipmentOutputLimits = shipmentJSONLimits{shipmentOutcomeLimit, 32, 2097152, 2097152, shipmentOutcomeLimit, true}

// Go's JSON decoder replaces lone escaped surrogates. Refuse them before decoding.
func shipmentValidStrings(body []byte) bool {
	if !utf8.Valid(body) {
		return false
	}
	for i := 0; i < len(body); i++ {
		if body[i] != '"' {
			continue
		}
		i++
		for i < len(body) && body[i] != '"' {
			if body[i] < 0x20 {
				return false
			}
			if body[i] != '\\' {
				i++
				continue
			}
			i++
			if i >= len(body) {
				return false
			}
			if body[i] != 'u' {
				i++
				continue
			}
			if i+4 >= len(body) {
				return false
			}
			hi, err := strconv.ParseUint(string(body[i+1:i+5]), 16, 16)
			if err != nil {
				return false
			}
			i += 5
			if hi >= 0xD800 && hi <= 0xDBFF {
				if i+5 >= len(body) || body[i] != '\\' || body[i+1] != 'u' {
					return false
				}
				lo, err := strconv.ParseUint(string(body[i+2:i+6]), 16, 16)
				if err != nil || lo < 0xDC00 || lo > 0xDFFF {
					return false
				}
				i += 6
			} else if hi >= 0xDC00 && hi <= 0xDFFF {
				return false
			}
		}
		if i >= len(body) {
			return false
		}
	}
	return true
}

func shipmentParse(body []byte, limits shipmentJSONLimits) (any, error) {
	if len(body) > limits.bytes || !shipmentValidStrings(body) {
		return nil, errors.New("invalid JSON bytes")
	}
	d := json.NewDecoder(bytes.NewReader(body))
	d.UseNumber()
	count := 0
	var parse func(int) (any, error)
	parse = func(depth int) (any, error) {
		if depth > limits.depth {
			return nil, errors.New("JSON depth")
		}
		token, err := d.Token()
		if err != nil {
			return nil, err
		}
		switch token {
		case json.Delim('{'):
			object := map[string]any{}
			for d.More() {
				key, err := d.Token()
				if err != nil {
					return nil, err
				}
				name, ok := key.(string)
				if !ok || len(name) > limits.text {
					return nil, errors.New("JSON key")
				}
				if _, exists := object[name]; exists {
					return nil, errors.New("duplicate key")
				}
				count++
				if count > limits.entries {
					return nil, errors.New("JSON entries")
				}
				value, err := parse(depth + 1)
				if err != nil {
					return nil, err
				}
				object[name] = value
			}
			if _, err := d.Token(); err != nil {
				return nil, err
			}
			return object, nil
		case json.Delim('['):
			items := []any{}
			for d.More() {
				if len(items) >= limits.array {
					return nil, errors.New("JSON array")
				}
				count++
				if count > limits.entries {
					return nil, errors.New("JSON entries")
				}
				value, err := parse(depth + 1)
				if err != nil {
					return nil, err
				}
				items = append(items, value)
			}
			if _, err := d.Token(); err != nil {
				return nil, err
			}
			return items, nil
		case json.Delim('}'), json.Delim(']'):
			return nil, errors.New("unexpected delimiter")
		default:
			switch value := token.(type) {
			case string:
				if len(value) > limits.text {
					return nil, errors.New("JSON string")
				}
			case json.Number:
				if !limits.numbers || strings.ContainsAny(string(value), ".eE") || len(value) > 20 {
					return nil, errors.New("JSON number")
				}
				if _, err := strconv.ParseInt(string(value), 10, 64); err != nil {
					return nil, err
				}
			case nil, bool:
			default:
				return nil, errors.New("JSON type")
			}
			return token, nil
		}
	}
	value, err := parse(0)
	if err != nil {
		return nil, err
	}
	if _, err := d.Token(); err != io.EOF {
		return nil, errors.New("trailing JSON")
	}
	return value, nil
}

func shipmentObject(value any, keys ...string) (map[string]any, bool) {
	object, ok := value.(map[string]any)
	if !ok || len(object) != len(keys) {
		return nil, false
	}
	for _, key := range keys {
		if _, ok := object[key]; !ok {
			return nil, false
		}
	}
	return object, true
}

func shipmentString(object map[string]any, key string) string { s, _ := object[key].(string); return s }

func shipmentKnownFields(object map[string]any, allowed ...string) bool {
	fields := map[string]bool{}
	for _, key := range allowed {
		fields[key] = true
	}
	for key := range object {
		if !fields[key] {
			return false
		}
	}
	return true
}

func shipmentRawShape(rows []any) bool {
	for _, value := range rows {
		record, ok := value.(map[string]any)
		if !ok {
			continue
		} // The accepted factory reports malformed known records.
		if !shipmentKnownFields(record, "event_id", "shipment_id", "type", "occurred_at", "observed_at", "eta", "synthetic", "facility", "lane", "source", "evidence", "quantity") {
			return false
		}
		for key, allowed := range map[string][]string{
			"facility": {"kind", "code", "name"}, "lane": {"origin", "destination", "mode", "name"},
			"source": {"provider_id", "kind", "reference"}, "quantity": {"amount", "unit"},
			"evidence": {"evidence_id", "kind", "locator", "content_sha256", "retrieved_at", "valid_until"},
		} {
			value, exists := record[key]
			if !exists {
				continue
			}
			items := []any{value}
			if key == "evidence" {
				if list, ok := value.([]any); ok {
					items = list
				}
			}
			for _, child := range items {
				if object, ok := child.(map[string]any); ok && !shipmentKnownFields(object, allowed...) {
					return false
				}
			}
		}
	}
	return true
}

func shipmentGraphShape(graph map[string]any) bool {
	type spec struct {
		name    string
		fields  []string
		strings []string
	}
	shapes := []spec{
		{"nodes", []string{"id", "kind"}, []string{"id", "kind"}},
		{"edges", []string{"from", "to"}, []string{"from", "to"}},
		{"inventory", []string{"component_id", "unit", "starting_stock"}, []string{"component_id", "unit", "starting_stock"}},
		{"arrival_lines", []string{"line_id", "shipment_id", "purchase_order_id", "component_id", "unit", "quantity", "arrival_at"}, []string{"line_id", "shipment_id", "purchase_order_id", "component_id", "unit", "quantity", "arrival_at"}},
		{"commitments", []string{"commitment_id", "production_order_id", "required_at", "due_at", "demands"}, []string{"commitment_id", "production_order_id", "required_at", "due_at"}},
	}
	for _, shape := range shapes {
		rows, ok := graph[shape.name].([]any)
		if !ok {
			return false
		}
		for _, value := range rows {
			row, ok := shipmentObject(value, shape.fields...)
			if !ok {
				return false
			}
			for _, key := range shape.strings {
				if _, ok := row[key].(string); !ok {
					return false
				}
			}
			if shape.name == "commitments" {
				demands, ok := row["demands"].([]any)
				if !ok {
					return false
				}
				for _, value := range demands {
					demand, ok := shipmentObject(value, "component_id", "unit", "quantity")
					if !ok {
						return false
					}
					for _, key := range []string{"component_id", "unit", "quantity"} {
						if _, ok := demand[key].(string); !ok {
							return false
						}
					}
				}
			}
		}
	}
	return true
}

func shipmentCanonicalSize(value any) (int, error) {
	serialized, err := shipmentMarshal(value)
	if err != nil {
		return 0, err
	}
	size := len(serialized)
	for i := 0; i+5 < len(serialized); i++ {
		if serialized[i] != '\\' || serialized[i+1] != 'u' ||
			(!bytes.Equal(serialized[i+2:i+6], []byte("2028")) && !bytes.Equal(serialized[i+2:i+6], []byte("2029"))) {
			continue
		}
		backslashes := 0
		for j := i - 1; j >= 0 && serialized[j] == '\\'; j-- {
			backslashes++
		}
		if backslashes%2 == 0 {
			size -= 3
			i += 5
		}
	}
	return size, nil
}

func shipmentPreflight(spec HostSpec) ([]byte, error) {
	if !shipmentText(spec.WorkspaceID) || !shipmentText(spec.PlanVersion) ||
		!shipmentText(spec.PropagationSnapshotID) || !shipmentText(spec.ProductionSnapshotID) || !shipmentTime(spec.AsOf) {
		return nil, errors.New("invalid host metadata")
	}
	if spec.CandidateBinding != nil && !shipmentValidCandidateBinding(*spec.CandidateBinding, spec.WorkspaceID) {
		return nil, errors.New("invalid candidate binding")
	}
	value, err := shipmentParse(spec.Request, shipmentInputLimits)
	if err != nil {
		return nil, err
	}
	request, ok := shipmentObject(value, "adapter_version", "raw_records", "propagation_snapshot", "production_snapshot", "joins", "recovery_assignments")
	if !ok || shipmentString(request, "adapter_version") != "1" {
		return nil, errors.New("invalid adapter request")
	}
	for _, key := range []string{"raw_records", "joins", "recovery_assignments"} {
		if _, ok := request[key].([]any); !ok {
			return nil, errors.New("invalid array")
		}
	}
	graph, ok := request["propagation_snapshot"].(map[string]any)
	if !ok {
		return nil, errors.New("invalid graph")
	}
	production, ok := request["production_snapshot"].(map[string]any)
	if !ok {
		return nil, errors.New("invalid production")
	}
	if !recoveryValid(production, recoveryDefinitions["ProductionSnapshot"], 0) {
		return nil, errors.New("invalid production")
	}
	if !shipmentRawShape(request["raw_records"].([]any)) {
		return nil, errors.New("unknown raw field")
	}
	for _, value := range request["joins"].([]any) {
		join, ok := shipmentObject(value, "line_id", "receipt_id")
		if !ok || !shipmentText(shipmentString(join, "line_id")) || !shipmentText(shipmentString(join, "receipt_id")) {
			return nil, errors.New("invalid join shape")
		}
	}
	for _, value := range request["recovery_assignments"].([]any) {
		if !recoveryValid(value, recoveryDefinitions["ProductionAssignment"], 0) {
			return nil, errors.New("invalid assignment")
		}
	}
	allowedGraph := map[string]bool{"workspace_id": true, "plan_version": true, "snapshot_id": true, "as_of": true, "synthetic": true, "nodes": true, "edges": true, "inventory": true, "arrival_lines": true, "commitments": true, "notice": true}
	if len(graph) < 10 || len(graph) > 11 {
		return nil, errors.New("invalid graph")
	}
	for key := range graph {
		if !allowedGraph[key] {
			return nil, errors.New("invalid graph")
		}
	}
	for _, key := range []string{"nodes", "edges", "inventory", "arrival_lines", "commitments"} {
		if _, ok := graph[key].([]any); !ok {
			return nil, errors.New("invalid graph")
		}
	}
	if notice, exists := graph["notice"]; exists {
		if _, ok := notice.(string); !ok {
			return nil, errors.New("invalid graph")
		}
	}
	if !shipmentGraphShape(graph) {
		return nil, errors.New("invalid graph row")
	}
	for _, row := range []map[string]any{graph, production} {
		if shipmentString(row, "workspace_id") != spec.WorkspaceID || shipmentString(row, "plan_version") != spec.PlanVersion || row["synthetic"] != true ||
			!shipmentAwareTime(shipmentString(row, "as_of")) {
			return nil, errors.New("scope mismatch")
		}
		given, _ := time.Parse(time.RFC3339Nano, shipmentString(row, "as_of"))
		expected, _ := time.Parse(time.RFC3339Nano, spec.AsOf)
		if !given.Equal(expected) {
			return nil, errors.New("cutoff mismatch")
		}
	}
	if shipmentString(graph, "snapshot_id") != spec.PropagationSnapshotID || shipmentString(production, "snapshot_id") != spec.ProductionSnapshotID {
		return nil, errors.New("snapshot mismatch")
	}
	for _, key := range []string{"raw_records", "propagation_snapshot"} {
		size, err := shipmentCanonicalSize(request[key])
		if err != nil || size > shipmentPartLimit {
			return nil, errors.New("oversize adapter part")
		}
	}
	size, err := shipmentCanonicalSize(request)
	if err != nil || size > shipmentRequestLimit {
		return nil, errors.New("oversize adapter request")
	}
	// The exact Python helper owns UTC-equivalence and numerical/join semantics.
	copyBytes := append([]byte(nil), spec.Request...)
	return copyBytes, nil
}

func shipmentValidCandidateBinding(binding shipmentCandidateBinding, workspaceID string) bool {
	if binding.WorkspaceID != workspaceID || !shipmentText(binding.WorkspaceID) ||
		!shipmentCandidateOfferID.MatchString(binding.OfferID) ||
		binding.OfferVersion < 1 || binding.OfferVersion > 2147483647 ||
		binding.ItemIndex < 0 || binding.ItemIndex > 2147483647 ||
		!shipmentDocumentID.MatchString(binding.DocumentID) {
		return false
	}
	return binding.CandidateID == fmt.Sprintf("%s@v%d#item%d", binding.OfferID, binding.OfferVersion, binding.ItemIndex)
}

func shipmentOwnAssociation(spec HostSpec, metadata ContextMetadata) *shipmentCandidateAssociation {
	if spec.CandidateBinding == nil {
		return nil
	}
	return &shipmentCandidateAssociation{Candidate: *spec.CandidateBinding, ContextID: metadata.ContextID,
		Revision: metadata.Revision, RequestSHA256: metadata.RequestSHA256, PlanVersion: metadata.PlanVersion, AsOf: metadata.AsOf}
}

func (a *shipmentAdapter) duplicateCandidate(workspaceID, contextID, candidateID string) bool {
	for id, current := range a.contexts[workspaceID] {
		if id != contextID && current.binding != nil && current.binding.Candidate.CandidateID == candidateID {
			return true
		}
	}
	return false
}

func (a *shipmentAdapter) boundCandidate(workspaceID, contextID string, revision int, requestSHA256 string) (shipmentCandidateAssociation, bool) {
	var empty shipmentCandidateAssociation
	if a == nil {
		return empty, false
	}
	a.mu.Lock()
	defer a.mu.Unlock()
	if a.closed || !a.ready {
		return empty, false
	}
	current := a.contexts[workspaceID][contextID]
	if current == nil || current.binding == nil || current.metadata.Revision != revision || current.metadata.RequestSHA256 != requestSHA256 || current.metadata.WorkspaceID != workspaceID {
		return empty, false
	}
	return *current.binding, true
}

func shipmentMarshal(value any) ([]byte, error) {
	var out bytes.Buffer
	e := json.NewEncoder(&out)
	e.SetEscapeHTML(false)
	if err := e.Encode(value); err != nil {
		return nil, err
	}
	return bytes.TrimSuffix(out.Bytes(), []byte{'\n'}), nil
}

func shipmentRandomID() (string, error) {
	var raw [16]byte
	if _, err := rand.Read(raw[:]); err != nil {
		return "", err
	}
	return hex.EncodeToString(raw[:]), nil
}

func (a *shipmentAdapter) InstallHostContext(spec HostSpec) (ContextMetadata, error) {
	var empty ContextMetadata
	if a == nil {
		return empty, errShipmentUnavailable
	}
	owned, err := shipmentPreflight(spec)
	if err != nil {
		return empty, err
	}
	id, err := shipmentRandomID()
	if err != nil {
		return empty, err
	}
	sum := sha256.Sum256(owned)
	metadata := ContextMetadata{ContextID: id, Revision: 1, WorkspaceID: spec.WorkspaceID, PlanVersion: spec.PlanVersion,
		PropagationSnapshotID: spec.PropagationSnapshotID, ProductionSnapshotID: spec.ProductionSnapshotID, AsOf: spec.AsOf,
		Synthetic: true, Source: "host_configured_synthetic", Persistence: "ephemeral", SourceAuthenticity: "not_verified", RequestSHA256: hex.EncodeToString(sum[:])}
	a.mu.Lock()
	defer a.mu.Unlock()
	if a.closed {
		return empty, errShipmentUnavailable
	}
	total := 0
	for _, group := range a.contexts {
		total += len(group)
	}
	if total >= 4 {
		return empty, errShipmentBusy
	}
	if spec.CandidateBinding != nil && a.duplicateCandidate(spec.WorkspaceID, "", spec.CandidateBinding.CandidateID) {
		return empty, errors.New("duplicate candidate binding")
	}
	if a.contexts[spec.WorkspaceID] == nil {
		a.contexts[spec.WorkspaceID] = map[string]*shipmentContext{}
	}
	a.contexts[spec.WorkspaceID][id] = &shipmentContext{metadata: metadata, request: owned, binding: shipmentOwnAssociation(spec, metadata)}
	return metadata, nil
}

func (a *shipmentAdapter) ReplaceHostContext(workspaceID, contextID string, expectedRevision int, spec HostSpec) (ContextMetadata, error) {
	var empty ContextMetadata
	if a == nil {
		return empty, errShipmentUnavailable
	}
	if spec.WorkspaceID != workspaceID {
		return empty, errShipmentStale
	}
	owned, err := shipmentPreflight(spec)
	if err != nil {
		return empty, err
	}
	a.mu.Lock()
	defer a.mu.Unlock()
	if a.closed {
		return empty, errShipmentUnavailable
	}
	current := a.contexts[workspaceID][contextID]
	if current == nil {
		return empty, errShipmentUnknown
	}
	if current.metadata.Revision != expectedRevision || expectedRevision >= 2147483647 {
		return empty, errShipmentStale
	}
	if spec.CandidateBinding != nil && a.duplicateCandidate(workspaceID, contextID, spec.CandidateBinding.CandidateID) {
		return empty, errors.New("duplicate candidate binding")
	}
	sum := sha256.Sum256(owned)
	metadata := ContextMetadata{ContextID: contextID, Revision: expectedRevision + 1, WorkspaceID: workspaceID,
		PlanVersion: spec.PlanVersion, PropagationSnapshotID: spec.PropagationSnapshotID, ProductionSnapshotID: spec.ProductionSnapshotID,
		AsOf: spec.AsOf, Synthetic: true, Source: "host_configured_synthetic", Persistence: "ephemeral",
		SourceAuthenticity: "not_verified", RequestSHA256: hex.EncodeToString(sum[:])}
	a.contexts[workspaceID][contextID] = &shipmentContext{metadata: metadata, request: owned, binding: shipmentOwnAssociation(spec, metadata)}
	for lease := range a.leases {
		if lease.current == current {
			lease.cancel()
		}
	}
	return metadata, nil
}

func (a *shipmentAdapter) list(workspaceID string) []ContextMetadata {
	a.mu.Lock()
	defer a.mu.Unlock()
	result := []ContextMetadata{}
	for _, current := range a.contexts[workspaceID] {
		result = append(result, current.metadata)
	}
	sortShipmentMetadata(result)
	return result
}

func (a *shipmentAdapter) available() bool {
	a.mu.Lock()
	defer a.mu.Unlock()
	return a.ready && !a.closed
}

func sortShipmentMetadata(rows []ContextMetadata) {
	for i := 1; i < len(rows); i++ {
		for j := i; j > 0 && rows[j].ContextID < rows[j-1].ContextID; j-- {
			rows[j], rows[j-1] = rows[j-1], rows[j]
		}
	}
}

func (a *shipmentAdapter) acquire(parent context.Context, principalID, workspaceID, contextID string, revision int) (*shipmentLease, context.Context, error) {
	a.mu.Lock()
	defer a.mu.Unlock()
	if a.closed || !a.ready {
		return nil, nil, errShipmentUnavailable
	}
	current := a.contexts[workspaceID][contextID]
	if current == nil {
		return nil, nil, errShipmentUnknown
	}
	if current.metadata.Revision != revision {
		return nil, nil, errShipmentStale
	}
	if len(a.leases) >= 2 || a.perPrincipal[principalID] >= 1 {
		return nil, nil, errShipmentBusy
	}
	ctx, cancel := context.WithCancel(parent)
	lease := &shipmentLease{owner: principalID, current: current, cancel: cancel}
	a.leases[lease] = struct{}{}
	a.perPrincipal[principalID]++
	return lease, ctx, nil
}

func (a *shipmentAdapter) release(lease *shipmentLease) {
	a.mu.Lock()
	defer a.mu.Unlock()
	if _, ok := a.leases[lease]; ok {
		delete(a.leases, lease)
		a.perPrincipal[lease.owner]--
		lease.cancel()
	}
}

func (a *shipmentAdapter) current(lease *shipmentLease) bool {
	a.mu.Lock()
	defer a.mu.Unlock()
	_, active := a.leases[lease]
	return !a.closed && active && a.contexts[lease.current.metadata.WorkspaceID][lease.current.metadata.ContextID] == lease.current
}

func (a *shipmentAdapter) Shutdown(ctx context.Context) error {
	if a == nil {
		return nil
	}
	a.mu.Lock()
	a.closed = true
	for lease := range a.leases {
		lease.cancel()
	}
	a.mu.Unlock()
	ticker := time.NewTicker(10 * time.Millisecond)
	defer ticker.Stop()
	for {
		a.mu.Lock()
		empty := len(a.leases) == 0
		a.mu.Unlock()
		if empty {
			return nil
		}
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-ticker.C:
		}
	}
}
