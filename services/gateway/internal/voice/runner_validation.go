package voice

import (
	"bytes"
	"encoding/json"
	"reflect"
	"strings"
	"time"

	"closedsource/gateway/internal/contract"
)

func objectValue(value any) (map[string]any, bool) {
	object, ok := value.(map[string]any)
	return object, ok
}

func stringArray(value any, max int) ([]string, bool) {
	items, ok := value.([]any)
	if !ok || len(items) > max {
		return nil, false
	}
	result := make([]string, len(items))
	for i, item := range items {
		s, ok := item.(string)
		if !ok || len(s) > 256 {
			return nil, false
		}
		result[i] = s
	}
	return result, true
}

func referenceSet(snapshot map[string]any, field, idField string) (map[string]bool, bool) {
	rows, ok := snapshot[field].([]any)
	if !ok {
		return nil, false
	}
	set := make(map[string]bool, len(rows))
	for _, row := range rows {
		object, ok := objectValue(row)
		if !ok {
			return nil, false
		}
		id, ok := object[idField].(string)
		if !ok || !validID(id) || set[id] {
			return nil, false
		}
		set[id] = true
	}
	return set, true
}

func exactSet(values []string, allowed map[string]bool) bool {
	if len(values) != len(allowed) {
		return false
	}
	seen := make(map[string]bool, len(values))
	for _, value := range values {
		if !allowed[value] || seen[value] {
			return false
		}
		seen[value] = true
	}
	return true
}

// Generated contract structs mirror required fields. Check presence as well as
// typed decoding because Go zero values alone cannot distinguish omission.
func requiredFields(value any, typ reflect.Type) bool {
	if typ.Kind() == reflect.Slice {
		items, ok := value.([]any)
		if !ok {
			return false
		}
		for _, item := range items {
			if !requiredFields(item, typ.Elem()) {
				return false
			}
		}
		return true
	}
	if typ.Kind() == reflect.Struct {
		object, ok := value.(map[string]any)
		if !ok {
			return false
		}
		for i := 0; i < typ.NumField(); i++ {
			field := typ.Field(i)
			if !field.IsExported() {
				continue
			}
			name := strings.Split(field.Tag.Get("json"), ",")[0]
			item, present := object[name]
			if name == "" || !present || !requiredFields(item, field.Type) {
				return false
			}
		}
		return true
	}
	switch typ.Kind() {
	case reflect.String:
		_, ok := value.(string)
		return ok
	case reflect.Bool:
		_, ok := value.(bool)
		return ok
	default:
		return false
	}
}

func validateCompared(value any, captured *RecoveryContext) bool {
	var comparison contract.ReceiptRecoveryComparison
	if !requiredFields(value, reflect.TypeOf(comparison)) {
		return false
	}
	encoded, err := json.Marshal(value)
	if err != nil {
		return false
	}
	decoder := json.NewDecoder(bytes.NewReader(encoded))
	decoder.DisallowUnknownFields()
	if decoder.Decode(&comparison) != nil {
		return false
	}
	asOf, err := time.Parse(time.RFC3339Nano, comparison.AsOf)
	if err != nil {
		return false
	}
	capturedAsOf, err := time.Parse(time.RFC3339Nano, captured.asOf)
	if err != nil || !asOf.Equal(capturedAsOf) {
		return false
	}
	if comparison.Status != "compared" || !comparison.Synthetic ||
		comparison.WorkspaceId != captured.owner.WorkspaceID ||
		comparison.SnapshotId != captured.metadata.SnapshotID ||
		comparison.PlanVersion != captured.metadata.PlanVersion ||
		comparison.ScenarioId != captured.metadata.ScenarioID {
		return false
	}
	orders, ok := referenceSet(captured.snapshot, "orders", "order_id")
	if !ok {
		return false
	}
	components, ok := referenceSet(captured.snapshot, "inventory", "component_id")
	if !ok {
		return false
	}
	products, ok := referenceSet(captured.snapshot, "products", "product_id")
	if !ok {
		return false
	}
	resources, ok := referenceSet(captured.snapshot, "resources", "resource_id")
	if !ok {
		return false
	}
	periods, ok := referenceSet(captured.snapshot, "periods", "period_id")
	if !ok {
		return false
	}
	receipts, ok := referenceSet(captured.snapshot, "receipts", "receipt_id")
	if !ok {
		return false
	}
	for _, schedule := range []contract.ProductionScheduleScreen{
		comparison.OriginalBaseline, comparison.DisruptedBaseline, comparison.Recovery,
	} {
		ids := make([]string, 0, len(schedule.Orders))
		for _, order := range schedule.Orders {
			if !orders[order.OrderId] || !products[order.ProductId] ||
				!resources[order.ResourceId] || !periods[order.PeriodId] {
				return false
			}
			ids = append(ids, order.OrderId)
			for _, demand := range order.Demands {
				if !components[demand.ComponentId] {
					return false
				}
			}
		}
		if !exactSet(ids, orders) {
			return false
		}
		for _, capacity := range schedule.Capacity {
			if !resources[capacity.ResourceId] || !periods[capacity.PeriodId] {
				return false
			}
		}
		for _, balance := range schedule.MaterialBalances {
			if !components[balance.ComponentId] {
				return false
			}
		}
	}
	seenReceipts := make(map[string]bool)
	for _, change := range comparison.ReceiptChanges {
		if !receipts[change.ReceiptId] || seenReceipts[change.ReceiptId] {
			return false
		}
		seenReceipts[change.ReceiptId] = true
	}
	for _, ids := range [][]string{
		comparison.PotentiallyExposedOrderIds, comparison.DisruptionChangedOrderIds,
		comparison.RecoveryChangedOrderIds,
	} {
		seen := make(map[string]bool)
		for _, id := range ids {
			if !orders[id] || seen[id] {
				return false
			}
			seen[id] = true
		}
	}
	return true
}

var unsupportedReasons = map[string]bool{
	"invalid_quantity": true, "invalid_timestamp": true, "incomplete_snapshot": true,
	"incomplete_assignment": true, "unsupported_constraint": true,
	"conflicting_snapshot": true, "invalid_scope": true, "incomplete_inventory": true,
	"invalid_snapshot": true, "incomplete_bom": true, "incompatible_unit": true,
	"incomplete_resource": true, "incomplete_routing": true, "incomplete_period": true,
	"pre_as_of": true, "invalid_period": true, "overlapping_periods": true,
	"incomplete_maintenance": true, "incomplete_receipt": true, "incomplete_order": true,
	"resource_mismatch": true, "workspace_mismatch": true, "stale_plan_version": true,
	"snapshot_mismatch": true, "invalid_scenario": true, "invalid_recovery": true,
	"invalid_replacement": true, "duplicate_replacement": true, "unknown_receipt": true,
	"stale_receipt": true,
}

var safeOutcomeReasons = map[string]bool{
	"missing_critical_fields": true, "incomplete_assignments": true,
	"invalid_context": true, "invalid_call": true, "unknown_tool": true,
	"invalid_arguments": true, "context_mismatch": true,
	"duplicate_assignment": true, "unknown_order": true,
	"duplicate_replacement": true, "unknown_receipt": true,
	"invalid_envelope": true, "comparison_unavailable": true, "inactive_turn": true,
}

func validateLocalResponse(raw []byte, captured *RecoveryContext) error {
	total := 0
	value, err := strictParse(raw, localResponseLimit, &total)
	if err != nil {
		return ErrExecution
	}
	response, ok := objectValue(value)
	if !ok || !exactKeys(response, "bridge_version", "binding", "outcome") ||
		response["bridge_version"] != "1" {
		return ErrExecution
	}
	binding, ok := objectValue(response["binding"])
	if !ok || !exactKeys(binding, "principal_id", "workspace_id", "snapshot_id",
		"plan_version", "scenario_id", "session_id", "turn_id", "context_sha256") {
		return ErrExecution
	}
	if binding["principal_id"] != captured.owner.PrincipalID ||
		binding["workspace_id"] != captured.owner.WorkspaceID ||
		binding["snapshot_id"] != captured.metadata.SnapshotID ||
		binding["plan_version"] != captured.metadata.PlanVersion ||
		binding["scenario_id"] != captured.metadata.ScenarioID ||
		binding["session_id"] != captured.lease.SessionID() ||
		binding["turn_id"] != captured.lease.TurnID() ||
		binding["context_sha256"] != captured.digest {
		return ErrExecution
	}
	outcome, ok := objectValue(response["outcome"])
	if !ok {
		return ErrExecution
	}
	for key := range outcome {
		switch key {
		case "tool_name", "read_only", "status", "ownership", "reason", "missing_fields", "result":
		default:
			return ErrExecution
		}
	}
	if outcome["tool_name"] != localToolName || outcome["read_only"] != true {
		return ErrExecution
	}
	ownership, ok := objectValue(outcome["ownership"])
	if !ok || !exactKeys(ownership, "session_id", "turn_id") ||
		ownership["session_id"] != captured.lease.SessionID() ||
		ownership["turn_id"] != captured.lease.TurnID() {
		return ErrExecution
	}
	status, ok := outcome["status"].(string)
	if !ok {
		return ErrExecution
	}
	switch status {
	case "compared":
		if !exactKeys(outcome, "tool_name", "read_only", "status", "ownership", "result") ||
			!validateCompared(outcome["result"], captured) {
			return ErrExecution
		}
	case "unsupported":
		if !exactKeys(outcome, "tool_name", "read_only", "status", "ownership", "result") {
			return ErrExecution
		}
		result, ok := objectValue(outcome["result"])
		if !ok || !exactKeys(result, "status", "reason", "details") ||
			result["status"] != "unsupported" ||
			!unsupportedReasons[stringField(result, "reason")] {
			return ErrExecution
		}
		details, ok := stringArray(result["details"], 0)
		if !ok || len(details) != 0 {
			return ErrExecution
		}
	case "clarification_required":
		if !exactKeys(outcome, "tool_name", "read_only", "status", "ownership", "reason", "missing_fields") ||
			!safeOutcomeReasons[stringField(outcome, "reason")] {
			return ErrExecution
		}
		fields, ok := stringArray(outcome["missing_fields"], 256)
		if !ok || len(fields) == 0 {
			return ErrExecution
		}
		for _, field := range fields {
			if !strings.HasPrefix(field, "arguments.") {
				return ErrExecution
			}
		}
	case "rejected", "unavailable":
		if !exactKeys(outcome, "tool_name", "read_only", "status", "ownership", "reason") ||
			!safeOutcomeReasons[stringField(outcome, "reason")] {
			return ErrExecution
		}
	case "suppressed":
		// The host never queues a suppressed outcome.
		return ErrUnavailable
	default:
		return ErrExecution
	}
	return nil
}
