package main

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"mime"
	"net/http"
	"reflect"
	"strconv"
	"strings"
	"time"
)

func shipmentRouteCandidate(path string) bool {
	parts := strings.Split(path, "/")
	return len(parts) >= 5 && parts[0] == "" && parts[1] == "v2" && parts[2] == "workspaces" && parts[4] == "shipment-recovery"
}

func shipmentPath(path string) (string, string, bool) {
	parts := strings.Split(path, "/")
	if len(parts) != 6 || parts[0] != "" || parts[1] != "v2" || parts[2] != "workspaces" || parts[4] != "shipment-recovery" ||
		(parts[5] != "contexts" && parts[5] != "comparisons") || !shipmentText(parts[3]) || strings.ContainsAny(parts[3], "%?#") {
		return "", "", false
	}
	return parts[3], parts[5], true
}

func shipmentHeaders(r *http.Request) bool {
	if r.URL.RawQuery != "" || r.URL.ForceQuery || r.URL.EscapedPath() != r.URL.Path {
		return false
	}
	for _, key := range []string{"Authorization", "Origin", "Content-Type"} {
		if len(r.Header.Values(key)) > 1 {
			return false
		}
	}
	for _, key := range []string{"X-Actor", "X-Role", "X-Principal-Id", "X-Workspace-Id", "X-Principal", "X-Workspace"} {
		if len(r.Header.Values(key)) > 0 {
			return false
		}
	}
	return true
}

func shipmentErr(w http.ResponseWriter, status int, code, message string) {
	w.Header().Set("Cache-Control", "no-store")
	stateError(w, status, code, message)
}

func shipmentJSONResponse(w http.ResponseWriter, status int, value any, limit int) {
	data, err := shipmentMarshal(value)
	if err != nil || len(data) > limit {
		shipmentErr(w, 502, "upstream_invalid_response", "Invalid shipment recovery response")
		return
	}
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Cache-Control", "no-store")
	w.WriteHeader(status)
	_, _ = w.Write(data)
}

func (s *stateServer) shipmentHTTPRoute(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Cache-Control", "no-store")
	workspaceID, action, exact := shipmentPath(r.URL.Path)
	if !exact || !shipmentHeaders(r) {
		shipmentErr(w, 422, "invalid_request", "Invalid shipment recovery request")
		return
	}
	var p principal
	if s.cfg.Mode == "private_machine" {
		mapped, ok := r.Context().Value(machinePrincipalKey{}).(principal)
		if !ok {
			shipmentErr(w, 401, "unauthorized", "Valid identity required")
			return
		}
		p = mapped
	} else {
		if s.auth == nil {
			shipmentErr(w, 401, "unauthorized", "Valid identity required")
			return
		}
		mapped, ok := s.auth.Authenticate(w, r)
		if !ok {
			return
		}
		p = mapped
	}
	if p.ID == "" || p.Workspace == "" {
		shipmentErr(w, 401, "unauthorized", "Valid identity required")
		return
	}
	if p.Role != "viewer" && p.Role != "editor" {
		shipmentErr(w, 403, "forbidden", "Shipment comparison role unavailable")
		return
	}
	if workspaceID != p.Workspace {
		shipmentErr(w, 403, "forbidden", "Workspace access denied")
		return
	}
	if s.cfg.Mode == "private_machine" {
		shipmentErr(w, 503, "shipment_recovery_unavailable", "Local shipment recovery unavailable")
		return
	}
	if action == "contexts" {
		if r.Method != http.MethodGet {
			shipmentErr(w, 405, "method_not_allowed", "Method not allowed")
			return
		}
		if r.ContentLength > 0 || len(r.TransferEncoding) > 0 {
			shipmentErr(w, 422, "invalid_request", "Invalid shipment recovery request")
			return
		}
		if s.shipment == nil || !s.shipment.available() {
			shipmentErr(w, 503, "shipment_recovery_unavailable", "Local shipment recovery unavailable")
			return
		}
		shipmentJSONResponse(w, 200, struct {
			ContractVersion string            `json:"contract_version"`
			Contexts        []ContextMetadata `json:"contexts"`
		}{stateVersion, s.shipment.list(workspaceID)}, 64<<10)
		return
	}
	if r.Method != http.MethodPost {
		shipmentErr(w, 405, "method_not_allowed", "Method not allowed")
		return
	}
	media, params, err := mime.ParseMediaType(r.Header.Get("Content-Type"))
	if err != nil || media != "application/json" || len(r.Header.Values("Content-Type")) != 1 || params["charset"] != "" && !strings.EqualFold(params["charset"], "utf-8") {
		shipmentErr(w, 422, "invalid_request", "Invalid shipment recovery request")
		return
	}
	budget, cancel := context.WithTimeout(r.Context(), 10*time.Second)
	defer cancel()
	intakeDeadline := time.Now().Add(5 * time.Second)
	if deadline, ok := budget.Deadline(); ok && deadline.Before(intakeDeadline) {
		intakeDeadline = deadline
	}
	_ = http.NewResponseController(w).SetReadDeadline(intakeDeadline)
	if r.Body == nil {
		shipmentErr(w, 422, "invalid_request", "Invalid shipment recovery request")
		return
	}
	body, err := io.ReadAll(io.LimitReader(r.Body, 4097))
	if err != nil {
		if errors.Is(r.Context().Err(), context.Canceled) {
			return
		}
		if budget.Err() != nil || !time.Now().Before(intakeDeadline) {
			shipmentErr(w, 504, "shipment_recovery_timeout", "Shipment recovery timed out")
			return
		}
		shipmentErr(w, 422, "invalid_request", "Invalid shipment recovery request")
		return
	}
	if len(body) > 4096 {
		shipmentErr(w, 413, "request_too_large", "Shipment request too large")
		return
	}
	value, err := shipmentParse(body, shipmentJSONLimits{4096, 2, 3, 8, 4096, true})
	request, valid := shipmentObject(value, "contract_version", "context_id", "expected_revision")
	if err != nil || !valid || shipmentString(request, "contract_version") != stateVersion || !shipmentID.MatchString(shipmentString(request, "context_id")) {
		shipmentErr(w, 422, "invalid_request", "Invalid shipment recovery request")
		return
	}
	revisionNumber, ok := request["expected_revision"].(json.Number)
	if !ok || strings.ContainsAny(string(revisionNumber), ".eE") {
		shipmentErr(w, 422, "invalid_request", "Invalid shipment recovery request")
		return
	}
	revision, err := strconv.Atoi(string(revisionNumber))
	if err != nil || revision < 1 || revision > 2147483647 {
		shipmentErr(w, 422, "invalid_request", "Invalid shipment recovery request")
		return
	}
	if s.shipment == nil {
		shipmentErr(w, 503, "shipment_recovery_unavailable", "Local shipment recovery unavailable")
		return
	}
	lease, leaseCtx, err := s.shipment.acquire(budget, p.ID, p.Workspace, shipmentString(request, "context_id"), revision)
	if err != nil {
		shipmentAdmissionError(w, err)
		return
	}
	defer s.shipment.release(lease)
	remaining := time.Until(deadlineOf(budget))
	if remaining <= time.Second {
		shipmentErr(w, 504, "shipment_recovery_timeout", "Shipment recovery timed out")
		return
	}
	childCtx, childCancel := context.WithTimeout(leaseCtx, remaining-time.Second)
	defer childCancel()
	requestID, err := shipmentRandomID()
	if err != nil {
		shipmentErr(w, 503, "shipment_recovery_unavailable", "Local shipment recovery unavailable")
		return
	}
	metadata := lease.current.metadata
	host := map[string]any{"principal_id": p.ID, "workspace_id": p.Workspace, "request_id": requestID,
		"context_id": metadata.ContextID, "context_revision": strconv.Itoa(metadata.Revision), "plan_version": metadata.PlanVersion,
		"propagation_snapshot_id": metadata.PropagationSnapshotID, "production_snapshot_id": metadata.ProductionSnapshotID,
		"as_of": metadata.AsOf, "request_sha256": metadata.RequestSHA256}
	hostBytes, err := shipmentMarshal(host)
	if err != nil || len(hostBytes) > 8<<10 {
		shipmentErr(w, 503, "shipment_recovery_unavailable", "Local shipment recovery unavailable")
		return
	}
	stdin, err := shipmentMarshal(map[string]string{"bridge_version": "1", "host_context_json": string(hostBytes), "request_json": string(lease.current.request)})
	if err != nil || len(stdin) > (4<<20)+(32<<10) {
		shipmentErr(w, 422, "invalid_request", "Shipment request too large")
		return
	}
	stdout, err := s.shipment.executor.Execute(childCtx, stdin)
	if err != nil {
		if r.Context().Err() != nil {
			return
		}
		if budget.Err() != nil || errors.Is(err, context.DeadlineExceeded) {
			shipmentErr(w, 504, "shipment_recovery_timeout", "Shipment recovery timed out")
			return
		}
		if !s.shipment.current(lease) {
			shipmentErr(w, 409, "shipment_context_stale", "Shipment context revision changed")
			return
		}
		if errors.Is(err, errShipmentInvalidOutput) {
			shipmentErr(w, 502, "upstream_invalid_response", "Invalid shipment recovery response")
			return
		}
		shipmentErr(w, 503, "shipment_recovery_unavailable", "Local shipment recovery unavailable")
		return
	}
	if r.Context().Err() != nil {
		return
	}
	if budget.Err() != nil || childCtx.Err() == context.DeadlineExceeded {
		shipmentErr(w, 504, "shipment_recovery_timeout", "Shipment recovery timed out")
		return
	}
	if !s.shipment.current(lease) {
		shipmentErr(w, 409, "shipment_context_stale", "Shipment context revision changed")
		return
	}
	outcome, status, valid := shipmentChildResponse(stdout, hostBytes, host, metadata, lease.current.request)
	if !valid {
		shipmentErr(w, 502, "upstream_invalid_response", "Invalid shipment recovery response")
		return
	}
	responseBytes, err := shipmentMarshal(struct {
		ContractVersion string          `json:"contract_version"`
		Context         ContextMetadata `json:"context"`
		OutcomeJSON     string          `json:"outcome_json"`
	}{stateVersion, metadata, outcome})
	if err != nil || len(responseBytes) > shipmentWireLimit {
		shipmentErr(w, 502, "upstream_invalid_response", "Invalid shipment recovery response")
		return
	}
	if r.Context().Err() != nil {
		return
	}
	if s.shipment.publishCurrent(w, r, lease, budget, status, responseBytes) {
		return
	}
	if r.Context().Err() != nil {
		return
	}
	if budget.Err() != nil {
		shipmentErr(w, 504, "shipment_recovery_timeout", "Shipment recovery timed out")
		return
	}
	shipmentErr(w, 409, "shipment_context_stale", "Shipment context revision changed")
}

func (a *shipmentAdapter) publishCurrent(w http.ResponseWriter, r *http.Request, lease *shipmentLease, budget context.Context, status int, responseBytes []byte) bool {
	a.mu.Lock()
	defer a.mu.Unlock()
	_, active := a.leases[lease]
	if a.closed || !active || a.contexts[lease.current.metadata.WorkspaceID][lease.current.metadata.ContextID] != lease.current ||
		r.Context().Err() != nil || budget.Err() != nil {
		return false
	}
	deadline := time.Now().Add(2 * time.Second)
	if budgetDeadline, ok := budget.Deadline(); ok && budgetDeadline.Before(deadline) {
		deadline = budgetDeadline
	}
	_ = http.NewResponseController(w).SetWriteDeadline(deadline)
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Cache-Control", "no-store")
	w.WriteHeader(status)
	_, _ = w.Write(responseBytes)
	return true
}

func deadlineOf(ctx context.Context) time.Time { deadline, _ := ctx.Deadline(); return deadline }

func shipmentAdmissionError(w http.ResponseWriter, err error) {
	switch {
	case errors.Is(err, errShipmentUnknown):
		shipmentErr(w, 404, "shipment_context_unavailable", "Shipment context unavailable")
	case errors.Is(err, errShipmentStale):
		shipmentErr(w, 409, "shipment_context_stale", "Shipment context revision changed")
	case errors.Is(err, errShipmentBusy):
		shipmentErr(w, 429, "shipment_recovery_busy", "Shipment recovery busy")
	default:
		shipmentErr(w, 503, "shipment_recovery_unavailable", "Local shipment recovery unavailable")
	}
}

func shipmentChildResponse(raw, hostBytes []byte, host map[string]any, metadata ContextMetadata, request []byte) (string, int, bool) {
	value, err := shipmentParse(raw, shipmentJSONLimits{shipmentWireLimit, 3, 16, 128, shipmentWireLimit, false})
	outer, ok := shipmentObject(value, "bridge_version", "binding", "outcome_json")
	if err != nil || !ok || shipmentString(outer, "bridge_version") != "1" {
		return "", 0, false
	}
	binding, ok := shipmentObject(outer["binding"], "principal_id", "workspace_id", "request_id", "context_id", "context_revision", "host_sha256", "request_sha256")
	if !ok {
		return "", 0, false
	}
	hash := sha256.Sum256(hostBytes)
	for _, key := range []string{"principal_id", "workspace_id", "request_id", "context_id", "context_revision", "request_sha256"} {
		if shipmentString(binding, key) != host[key] {
			return "", 0, false
		}
	}
	if shipmentString(binding, "host_sha256") != hex.EncodeToString(hash[:]) {
		return "", 0, false
	}
	outcomeText, ok := outer["outcome_json"].(string)
	if !ok || len(outcomeText) > shipmentOutcomeLimit {
		return "", 0, false
	}
	status, valid := shipmentValidateOutcome([]byte(outcomeText), metadata, request)
	return outcomeText, status, valid
}

func shipmentValidateOutcome(raw []byte, metadata ContextMetadata, requestBytes []byte) (int, bool) {
	value, err := shipmentParse(raw, shipmentOutputLimits)
	outcome, ok := value.(map[string]any)
	if err != nil || !ok || shipmentString(outcome, "adapter_version") != "1" || outcome["synthetic"] != true {
		return 0, false
	}
	status := shipmentString(outcome, "status")
	if status == "rejected" {
		row, ok := shipmentObject(outcome, "adapter_version", "status", "synthetic", "stage", "reason", "details", "evidence", "comparisons")
		if !ok || !shipmentEmpty(row["details"]) || !shipmentEmpty(row["comparisons"]) || !shipmentEvidence(row["evidence"]) {
			return 0, false
		}
		stage, reason := shipmentString(row, "stage"), shipmentString(row, "reason")
		switch stage {
		case "input":
			if reason == "invalid_input" || reason == "input_limit" {
				return 422, true
			}
		case "join":
			if reason == "invalid_join" || reason == "inconsistent_join" || reason == "incomplete_assignments" {
				return 422, true
			}
		case "projection":
			if reason == "unsupported_propagation" {
				return 422, true
			}
		case "binding":
			if reason == "binding_mismatch" {
				return 503, true
			}
		case "source":
			if reason == "source_unavailable" {
				return 503, true
			}
		case "runtime":
			if reason == "comparison_unavailable" {
				return 503, true
			}
		case "output":
			if reason == "invalid_result" || reason == "output_limit" {
				return 502, true
			}
		}
		return 0, false
	}
	if status != "evaluated" && status != "no_eligible_scenario" {
		return 0, false
	}
	row, ok := shipmentObject(outcome, "adapter_version", "status", "synthetic", "binding", "evidence", "comparisons")
	if !ok {
		return 0, false
	}
	bound, ok := shipmentObject(row["binding"], "adapter_version", "synthetic", "workspace_id", "plan_version", "propagation_snapshot_id", "production_snapshot_id", "as_of", "raw_sha256", "propagation_sha256", "production_sha256", "joins_sha256", "assignments_sha256", "binding_sha256")
	if !ok || shipmentString(bound, "adapter_version") != "1" || bound["synthetic"] != true || shipmentString(bound, "workspace_id") != metadata.WorkspaceID || shipmentString(bound, "plan_version") != metadata.PlanVersion || shipmentString(bound, "propagation_snapshot_id") != metadata.PropagationSnapshotID || shipmentString(bound, "production_snapshot_id") != metadata.ProductionSnapshotID || shipmentString(bound, "as_of") != metadata.AsOf {
		return 0, false
	}
	for _, key := range []string{"raw_sha256", "propagation_sha256", "production_sha256", "joins_sha256", "assignments_sha256", "binding_sha256"} {
		if !shipmentHash.MatchString(shipmentString(bound, key)) {
			return 0, false
		}
	}
	if !shipmentEvidence(row["evidence"]) || row["evidence"] == nil {
		return 0, false
	}
	comparisons, ok := row["comparisons"].([]any)
	if !ok || len(comparisons) > 16 || status == "no_eligible_scenario" && len(comparisons) != 0 || status == "evaluated" && len(comparisons) == 0 {
		return 0, false
	}
	input, err := shipmentParse(requestBytes, shipmentInputLimits)
	if err != nil {
		return 0, false
	}
	production := shipmentObjectLoose(shipmentObjectLoose(input)["production_snapshot"])
	orders := map[string]bool{}
	for _, value := range shipmentArray(production["orders"]) {
		orders[shipmentString(shipmentObjectLoose(value), "order_id")] = true
	}
	receipts := map[string]string{}
	for _, value := range shipmentArray(production["receipts"]) {
		receipt := shipmentObjectLoose(value)
		receipts[shipmentString(receipt, "receipt_id")] = shipmentString(receipt, "arrival_at")
	}
	lineToReceipt := map[string]string{}
	for _, value := range shipmentArray(shipmentObjectLoose(input)["joins"]) {
		join := shipmentObjectLoose(value)
		lineToReceipt[shipmentString(join, "line_id")] = shipmentString(join, "receipt_id")
	}
	knownScenarios := map[string]map[string]any{}
	for _, value := range shipmentArray(shipmentObjectLoose(row["evidence"])["scenarios"]) {
		source := shipmentObjectLoose(value)
		if _, ok := shipmentObject(value, "event_id", "shipment_id", "basis", "arrival_at", "assumptions", "source_receipt", "scenario"); !ok {
			return 0, false
		}
		knownScenarios[shipmentString(shipmentObjectLoose(source["scenario"]), "scenario_id")] = source
	}
	if len(knownScenarios) != len(comparisons) {
		return 0, false
	}
	last := ""
	for _, value := range comparisons {
		comparison, ok := shipmentObject(value, "shipment_id", "source_scenario_id", "receipt_scenario", "recovery", "projection", "result")
		if !ok {
			return 0, false
		}
		shipment, sourceID := shipmentString(comparison, "shipment_id"), shipmentString(comparison, "source_scenario_id")
		source := knownScenarios[sourceID]
		if source == nil || shipmentString(source, "shipment_id") != shipment || shipment == "" {
			return 0, false
		}
		orderKey := shipment + "\x00" + sourceID
		if last != "" && orderKey <= last {
			return 0, false
		}
		last = orderKey
		scenario, recovery := shipmentObjectLoose(comparison["receipt_scenario"]), shipmentObjectLoose(comparison["recovery"])
		if !shipmentRecoveryScope(scenario, recovery, metadata) || !reflect.DeepEqual(recovery["assignments"], shipmentObjectLoose(input)["recovery_assignments"]) {
			return 0, false
		}
		projection, valid := shipmentObject(comparison["projection"], "status", "synthetic", "workspace_id", "plan_version", "snapshot_id", "event_id", "scenario_id", "as_of", "arrival_changes", "affected_commitments", "component_balances", "limitations")
		if !valid || shipmentString(projection, "status") != "screened" || projection["synthetic"] != true ||
			shipmentString(projection, "workspace_id") != metadata.WorkspaceID || shipmentString(projection, "plan_version") != metadata.PlanVersion ||
			shipmentString(projection, "snapshot_id") != metadata.PropagationSnapshotID || shipmentString(projection, "event_id") != shipmentString(source, "event_id") ||
			shipmentString(projection, "scenario_id") != sourceID {
			return 0, false
		}
		if !shipmentReplacements(scenario, shipmentObjectLoose(source["scenario"]), lineToReceipt, receipts) {
			return 0, false
		}
		result := shipmentObjectLoose(comparison["result"])
		switch shipmentString(result, "status") {
		case "compared":
			if !recoveryValid(result, recoveryDefinitions["ReceiptRecoveryComparison"], 0) || !recoveryKnownReferences(result, production, orders) {
				return 0, false
			}
			if shipmentString(result, "workspace_id") != metadata.WorkspaceID || shipmentString(result, "plan_version") != metadata.PlanVersion || shipmentString(result, "snapshot_id") != metadata.ProductionSnapshotID || shipmentString(result, "scenario_id") != shipmentString(scenario, "scenario_id") {
				return 0, false
			}
		case "unsupported":
			copyResult := map[string]any{"contract_version": stateVersion}
			for key, item := range result {
				copyResult[key] = item
			}
			if !recoveryValid(copyResult, recoveryDefinitions["RecoveryComparisonUnsupportedResponse"], 0) {
				return 0, false
			}
		default:
			return 0, false
		}
	}
	return 200, true
}

func shipmentObjectLoose(value any) map[string]any {
	object, _ := value.(map[string]any)
	return object
}
func shipmentArray(value any) []any { rows, _ := value.([]any); return rows }
func shipmentEmpty(value any) bool  { rows, ok := value.([]any); return ok && len(rows) == 0 }
func shipmentEvidence(value any) bool {
	if value == nil {
		return true
	}
	row, ok := shipmentObject(value, "scenarios", "excluded", "normalization_rejected", "normalization_duplicates")
	if !ok {
		return false
	}
	for _, key := range []string{"scenarios", "excluded", "normalization_rejected", "normalization_duplicates"} {
		if _, ok := row[key].([]any); !ok {
			return false
		}
	}
	return true
}
func shipmentRecoveryScope(scenario, recovery map[string]any, metadata ContextMetadata) bool {
	if len(scenario) != 6 || len(recovery) != 6 {
		return false
	}
	for _, row := range []map[string]any{scenario, recovery} {
		if shipmentString(row, "workspace_id") != metadata.WorkspaceID || shipmentString(row, "expected_plan_version") != metadata.PlanVersion || shipmentString(row, "source_snapshot_id") != metadata.ProductionSnapshotID || row["synthetic"] != true || !strings.HasPrefix(shipmentString(row, "scenario_id"), "shipment-recovery:") {
			return false
		}
	}
	if shipmentString(scenario, "scenario_id") != shipmentString(recovery, "scenario_id") {
		return false
	}
	if _, ok := scenario["arrival_replacements"].([]any); !ok {
		return false
	}
	if _, ok := recovery["assignments"].([]any); !ok {
		return false
	}
	return true
}

func shipmentReplacements(receiptScenario, sourceScenario map[string]any, joins, receipts map[string]string) bool {
	changes, ok := sourceScenario["changes"].([]any)
	if !ok || len(changes) == 0 {
		return false
	}
	expected := map[string]string{}
	for _, value := range changes {
		change, ok := shipmentObject(value, "line_id", "arrival_at")
		if !ok {
			return false
		}
		receiptID := joins[shipmentString(change, "line_id")]
		if receiptID == "" || receipts[receiptID] == "" {
			return false
		}
		arrival := shipmentString(change, "arrival_at")
		if arrival == "" {
			return false
		}
		if prior, seen := expected[receiptID]; seen && prior != arrival {
			return false
		}
		expected[receiptID] = arrival
	}
	replacements := shipmentArray(receiptScenario["arrival_replacements"])
	if len(replacements) != len(expected) {
		return false
	}
	seen := map[string]bool{}
	for _, value := range replacements {
		replacement, ok := shipmentObject(value, "receipt_id", "expected_arrival_at", "arrival_at")
		if !ok {
			return false
		}
		id := shipmentString(replacement, "receipt_id")
		if id == "" || seen[id] || shipmentString(replacement, "expected_arrival_at") != receipts[id] || shipmentString(replacement, "arrival_at") != expected[id] {
			return false
		}
		seen[id] = true
	}
	return true
}
