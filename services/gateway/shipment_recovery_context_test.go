package main

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"strings"
	"testing"
	"time"
)

type shipmentExecutorFunc func(context.Context, []byte) ([]byte, error)

func (f shipmentExecutorFunc) Execute(ctx context.Context, input []byte) ([]byte, error) {
	return f(ctx, input)
}

func shipmentTestSpec(t *testing.T) HostSpec {
	t.Helper()
	data, err := os.ReadFile("../../contracts/examples/recovery_request.json")
	if err != nil {
		t.Fatal(err)
	}
	var example map[string]json.RawMessage
	if err := json.Unmarshal(data, &example); err != nil {
		t.Fatal(err)
	}
	var production any
	if err := json.Unmarshal(example["snapshot"], &production); err != nil {
		t.Fatal(err)
	}
	graph := map[string]any{"workspace_id": "W", "plan_version": "v7", "snapshot_id": "g1", "as_of": "2026-10-09T00:00:00Z", "synthetic": true,
		"nodes": []any{}, "edges": []any{}, "inventory": []any{}, "arrival_lines": []any{}, "commitments": []any{}}
	request := map[string]any{"adapter_version": "1", "raw_records": []any{}, "propagation_snapshot": graph,
		"production_snapshot": production, "joins": []any{}, "recovery_assignments": []any{
			map[string]any{"order_id": "O-A", "period_id": "P2"}, map[string]any{"order_id": "O-B", "period_id": "P2"}, map[string]any{"order_id": "O-U", "period_id": "P1"}}}
	raw, err := shipmentMarshal(request)
	if err != nil {
		t.Fatal(err)
	}
	return HostSpec{WorkspaceID: "W", PlanVersion: "v7", PropagationSnapshotID: "g1", ProductionSnapshotID: "s1", AsOf: "2026-10-09T00:00:00Z", Request: raw}
}

func TestShipmentParserRejectsDuplicateNumberSurrogateTrailingAndBounds(t *testing.T) {
	limits := shipmentInputLimits
	for name, raw := range map[string]string{
		"duplicate": `{"a":null,"a":false}`, "number": `{"a":1}`, "trailing": `{} {}`,
		"surrogate": `{"a":"\ud800"}`, "invalidUTF8": string([]byte{'{', '"', 'a', '"', ':', '"', 0xff, '"', '}'}),
	} {
		if _, err := shipmentParse([]byte(raw), limits); err == nil {
			t.Fatalf("accepted %s", name)
		}
	}
	if got, err := shipmentParse([]byte(`{"a":null,"b":true}`), limits); err != nil || got == nil {
		t.Fatalf("valid null/bool: %v", err)
	}
	limited := shipmentJSONLimits{bytes: 64, depth: 1, array: 1, entries: 1, text: 64}
	for _, raw := range []string{`{"a":{"b":null}}`, `{"a":[null,null]}`, `{"a":null,"b":null}`} {
		if _, err := shipmentParse([]byte(raw), limited); err == nil {
			t.Fatalf("accepted limited %s", raw)
		}
	}
}

func TestShipmentInstallOwnsBytesAndReplaceCancelsOldLease(t *testing.T) {
	spec := shipmentTestSpec(t)
	a := newShipmentAdapter(shipmentExecutorFunc(func(context.Context, []byte) ([]byte, error) { return nil, nil }))
	meta, err := a.InstallHostContext(spec)
	if err != nil {
		t.Fatal(err)
	}
	if !shipmentID.MatchString(meta.ContextID) || meta.Revision != 1 || meta.WorkspaceID != "W" {
		t.Fatalf("metadata %+v", meta)
	}
	original := append([]byte(nil), spec.Request...)
	spec.Request[0] = ' '
	if string(a.contexts["W"][meta.ContextID].request) != string(original) {
		t.Fatal("installed bytes changed with caller")
	}
	spec.Request = original
	lease, ctx, err := a.acquire(context.Background(), "P", "W", meta.ContextID, 1)
	if err != nil {
		t.Fatal(err)
	}
	changed, err := a.ReplaceHostContext("W", meta.ContextID, 1, spec)
	if err != nil {
		t.Fatal(err)
	}
	if changed.Revision != 2 || changed.ContextID != meta.ContextID {
		t.Fatalf("replacement %+v", changed)
	}
	select {
	case <-ctx.Done():
	case <-time.After(time.Second):
		t.Fatal("old lease not canceled")
	}
	if a.current(lease) {
		t.Fatal("old lease remained current")
	}
	a.release(lease)
	if _, _, err := a.acquire(context.Background(), "P", "W", meta.ContextID, 1); !errors.Is(err, errShipmentStale) {
		t.Fatalf("stale=%v", err)
	}
	if _, err := a.ReplaceHostContext("W", meta.ContextID, 1, spec); !errors.Is(err, errShipmentStale) {
		t.Fatalf("CAS=%v", err)
	}
}

func TestShipmentHostCutoffMustBeUTCButSnapshotOffsetMayMatchInstant(t *testing.T) {
	spec := shipmentTestSpec(t)
	spec.AsOf = "2026-10-09T01:00:00+01:00"
	if _, err := shipmentPreflight(spec); err == nil {
		t.Fatal("non-UTC host cutoff accepted")
	}
	spec.AsOf = "2026-10-09T00:00:00Z"
	var request map[string]any
	if err := json.Unmarshal(spec.Request, &request); err != nil {
		t.Fatal(err)
	}
	request["propagation_snapshot"].(map[string]any)["as_of"] = "2026-10-09T05:30:00+05:30"
	spec.Request, _ = shipmentMarshal(request)
	if _, err := shipmentPreflight(spec); err != nil {
		t.Fatalf("equivalent snapshot cutoff: %v", err)
	}
}

func TestShipmentPreflightRejectsUnknownNestedFieldsBeforeRetainingContext(t *testing.T) {
	for name, mutate := range map[string]func(map[string]any){
		"raw": func(r map[string]any) { r["raw_records"] = []any{map[string]any{"unknown_authority": "x"}} },
		"raw_source": func(r map[string]any) {
			r["raw_records"] = []any{map[string]any{"source": map[string]any{"unknown_authority": "x"}}}
		},
		"raw_evidence": func(r map[string]any) {
			r["raw_records"] = []any{map[string]any{"evidence": []any{map[string]any{"unknown_authority": "x"}}}}
		},
		"graph_node": func(r map[string]any) {
			g := r["propagation_snapshot"].(map[string]any)
			g["nodes"] = []any{map[string]any{"id": "SH", "kind": "shipment", "extra": "x"}}
		},
		"graph_demand": func(r map[string]any) {
			g := r["propagation_snapshot"].(map[string]any)
			g["commitments"] = []any{map[string]any{"commitment_id": "K", "production_order_id": "O-A", "required_at": "2026-10-10T09:00:00Z", "due_at": "2026-10-10T10:00:00Z", "demands": []any{map[string]any{"component_id": "C", "unit": "kg", "quantity": "1", "extra": "x"}}}}
		},
		"join": func(r map[string]any) {
			r["joins"] = []any{map[string]any{"line_id": "L1", "receipt_id": "r-C", "extra": "x"}}
		},
	} {
		t.Run(name, func(t *testing.T) {
			spec := shipmentTestSpec(t)
			var request map[string]any
			if err := json.Unmarshal(spec.Request, &request); err != nil {
				t.Fatal(err)
			}
			mutate(request)
			spec.Request, _ = shipmentMarshal(request)
			a := newShipmentAdapter(shipmentExecutorFunc(func(context.Context, []byte) ([]byte, error) { t.Fatal("child called"); return nil, nil }))
			if _, err := a.InstallHostContext(spec); err == nil {
				t.Fatal("unknown field retained")
			}
			if len(a.list("W")) != 0 {
				t.Fatal("partial context retained")
			}
		})
	}
}

func TestShipmentPythonCanonicalUnicodePartBoundDoesNotFalseReject(t *testing.T) {
	spec := shipmentTestSpec(t)
	var request map[string]any
	if err := json.Unmarshal(spec.Request, &request); err != nil {
		t.Fatal(err)
	}
	rows := make([]any, 8)
	for i := range rows {
		rows[i] = map[string]any{"event_id": "event-" + string(rune('a'+i)), "evidence": []any{map[string]any{"locator": strings.Repeat("\u2028", 40000)}}}
	}
	request["raw_records"] = rows
	spec.Request, _ = shipmentMarshal(request)
	if len(spec.Request) > shipmentRequestLimit {
		t.Fatal("test request exceeds raw cap")
	}
	size, err := shipmentCanonicalSize(rows)
	if err != nil || size > shipmentPartLimit {
		t.Fatalf("Python UTF-8 part size=%d err=%v", size, err)
	}
	goEncoding, _ := shipmentMarshal(rows)
	if len(goEncoding) <= shipmentPartLimit {
		t.Fatal("test failed to exercise Go U2028 escaping expansion")
	}
	if _, err := shipmentPreflight(spec); err != nil {
		t.Fatalf("valid Python-sized Unicode rejected: %v", err)
	}
}

func TestShipmentHostIDLimitCountsUnicodeCharacters(t *testing.T) {
	spec := shipmentTestSpec(t)
	spec.WorkspaceID = strings.Repeat("界", 256)
	var request map[string]any
	if err := json.Unmarshal(spec.Request, &request); err != nil {
		t.Fatal(err)
	}
	for _, key := range []string{"propagation_snapshot", "production_snapshot"} {
		request[key].(map[string]any)["workspace_id"] = spec.WorkspaceID
	}
	spec.Request, _ = shipmentMarshal(request)
	if _, err := shipmentPreflight(spec); err != nil {
		t.Fatalf("256-character host ID rejected: %v", err)
	}
	spec.WorkspaceID += "界"
	if _, err := shipmentPreflight(spec); err == nil {
		t.Fatal("257-character host ID accepted")
	}
}

func TestShipmentRegistryCapacityOwnerAndShutdown(t *testing.T) {
	spec := shipmentTestSpec(t)
	a := newShipmentAdapter(shipmentExecutorFunc(func(context.Context, []byte) ([]byte, error) { return nil, nil }))
	var metadata []ContextMetadata
	for i := 0; i < 4; i++ {
		m, err := a.InstallHostContext(spec)
		if err != nil {
			t.Fatal(err)
		}
		metadata = append(metadata, m)
	}
	if _, err := a.InstallHostContext(spec); !errors.Is(err, errShipmentBusy) {
		t.Fatalf("fifth=%v", err)
	}
	l1, ctx1, err := a.acquire(context.Background(), "P", "W", metadata[0].ContextID, 1)
	if err != nil {
		t.Fatal(err)
	}
	if _, _, err := a.acquire(context.Background(), "P", "W", metadata[1].ContextID, 1); !errors.Is(err, errShipmentBusy) {
		t.Fatalf("owner quota=%v", err)
	}
	l2, _, err := a.acquire(context.Background(), "Q", "W", metadata[1].ContextID, 1)
	if err != nil {
		t.Fatal(err)
	}
	if _, _, err := a.acquire(context.Background(), "R", "W", metadata[2].ContextID, 1); !errors.Is(err, errShipmentBusy) {
		t.Fatalf("global quota=%v", err)
	}
	short, cancel := context.WithTimeout(context.Background(), 20*time.Millisecond)
	defer cancel()
	if err := a.Shutdown(short); !errors.Is(err, context.DeadlineExceeded) {
		t.Fatalf("unreaped shutdown=%v", err)
	}
	select {
	case <-ctx1.Done():
	default:
		t.Fatal("shutdown did not cancel")
	}
	a.release(l1)
	a.release(l2)
	if err := a.Shutdown(context.Background()); err != nil {
		t.Fatal(err)
	}
	if _, _, err := a.acquire(context.Background(), "R", "W", metadata[2].ContextID, 1); !errors.Is(err, errShipmentUnavailable) {
		t.Fatalf("admission after shutdown=%v", err)
	}
}
