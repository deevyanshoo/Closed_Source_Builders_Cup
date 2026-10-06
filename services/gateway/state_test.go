package main

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"testing"

	"closedsource/gateway/internal/contract"
	"github.com/jackc/pgx/v5"
)

func TestStateRejectsMissingIdentityAndClientSnapshot(t *testing.T) {
	h := newStateHandler(stateConfig{})
	request := []byte(`{"contract_version":"0.2.0","planning_request":` + string(fixture(t, "request.json")) + `}`)
	w := stateCallRaw(h, "POST", "/v2/workspaces/local-workspace/plans", "", request)
	if w.Code != http.StatusUnauthorized || !strings.Contains(w.Body.String(), `"contract_version":"0.2.0"`) {
		t.Fatalf("unauthenticated save: %d %s", w.Code, w.Body.String())
	}
	request = []byte(`{"contract_version":"0.2.0","planning_request":` + string(fixture(t, "request.json")) + `,"snapshot":{}}`)
	w = stateCallRaw(h, "POST", "/v2/workspaces/local-workspace/plans", "", request)
	if w.Code != http.StatusUnauthorized {
		t.Fatalf("unauthenticated pasted snapshot: %d %s", w.Code, w.Body.String())
	}
}

func TestStateHandlerPreservesUnauthenticatedPreview(t *testing.T) {
	f := setupState(t)
	w := stateCallRaw(f.h, "POST", "/v2/plans/recommendations", "", fixture(t, "request.json"))
	assertCode(t, w, 200)
	var result contract.PlanResponse
	if e := json.Unmarshal(w.Body.Bytes(), &result); e != nil || result.Status != "ready" {
		t.Fatalf("preview was intercepted: %s %v", w.Body.String(), e)
	}
}

func TestReadinessFailsWhenDependenciesMissing(t *testing.T) {
	w := stateCall(newStateHandler(stateConfig{}), "GET", "/ready", "", nil)
	assertCode(t, w, 503)
}

func stateCall(h http.Handler, method, path, token string, body any) *httptest.ResponseRecorder {
	var b []byte
	if body != nil {
		b, _ = json.Marshal(body)
	}
	return stateCallRaw(h, method, path, token, b)
}
func stateCallRaw(h http.Handler, method, path, token string, b []byte) *httptest.ResponseRecorder {
	r := httptest.NewRequest(method, path, bytes.NewReader(b))
	r.RemoteAddr = "127.0.0.1:12345"
	if token != "" {
		r.Header.Set("Authorization", "Bearer "+token)
	}
	w := httptest.NewRecorder()
	h.ServeHTTP(w, r)
	return w
}

func testDBFile(t *testing.T) string {
	t.Helper()
	p := os.Getenv("PLAN_ACTIONS_TEST_DATABASE_URL_FILE")
	if p == "" {
		t.Skip("PLAN_ACTIONS_TEST_DATABASE_URL_FILE unset")
	}
	return p
}

type stateFixture struct {
	h      http.Handler
	cfg    stateConfig
	schema string
	db     *pgx.Conn
	calls  *atomic.Int32
}

func setupState(t *testing.T) stateFixture {
	t.Helper()
	b, e := os.ReadFile(testDBFile(t))
	if e != nil {
		t.Fatal(e)
	}
	url := strings.TrimSpace(string(b))
	ctx := context.Background()
	db, e := pgx.Connect(ctx, url)
	if e != nil {
		t.Fatal("task database unavailable:", e)
	}
	t.Cleanup(func() { db.Close(context.Background()) })
	schema := "cs_test_" + newID()
	if _, e = db.Exec(ctx, `CREATE SCHEMA `+schema); e != nil {
		t.Fatal(e)
	}
	t.Cleanup(func() { db.Exec(context.Background(), `DROP SCHEMA `+schema+` CASCADE`) })
	migration, e := os.ReadFile("migrations/001_plans_actions.up.sql")
	if e != nil {
		t.Fatal(e)
	}
	sql := strings.ReplaceAll(string(migration), "cs_plan_actions_archived_v1", schema+"_archived_v1")
	sql = strings.ReplaceAll(sql, "cs_plan_actions", schema)
	if _, e = db.Exec(ctx, sql); e != nil {
		t.Fatal("test schema migration:", e)
	}
	identities := filepath.Join(t.TempDir(), "identities.json")
	if e = os.WriteFile(identities, []byte(`{"identities":[{"token":"test-editor","principal_id":"editor-1","workspace_id":"local-workspace","role":"editor"},{"token":"same-workspace-editor","principal_id":"editor-3","workspace_id":"local-workspace","role":"editor"},{"token":"test-viewer","principal_id":"viewer-1","workspace_id":"local-workspace","role":"viewer"},{"token":"other-editor","principal_id":"editor-2","workspace_id":"other-workspace","role":"editor"}]}`), 0600); e != nil {
		t.Fatal(e)
	}
	calls := &atomic.Int32{}
	up := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { calls.Add(1); w.Write([]byte(legacy)) }))
	t.Cleanup(up.Close)
	cfg := stateConfig{DBURLFile: testDBFile(t), IdentitiesFile: identities, AllowedOrigin: "http://localhost:5173", Upstream: up.URL, Schema: schema}
	return stateFixture{h: newStateHandler(cfg), cfg: cfg, schema: schema, db: db, calls: calls}
}
func payload(t *testing.T, name string) map[string]any {
	t.Helper()
	var v map[string]any
	if e := json.Unmarshal(fixture(t, name), &v); e != nil {
		t.Fatal(e)
	}
	return v
}
func assertCode(t *testing.T, w *httptest.ResponseRecorder, code int) {
	t.Helper()
	if w.Code != code {
		t.Fatalf("status %d, want %d: %s", w.Code, code, w.Body.String())
	}
}
func saved(t *testing.T, w *httptest.ResponseRecorder) contract.SavedPlan {
	t.Helper()
	var v contract.SavedPlanResponse
	if e := json.Unmarshal(w.Body.Bytes(), &v); e != nil {
		t.Fatal(e)
	}
	return v.Plan
}
func savePlan(t *testing.T, f stateFixture) contract.SavedPlan {
	t.Helper()
	w := stateCall(f.h, "POST", "/v2/workspaces/local-workspace/plans", "test-editor", map[string]any{"contract_version": stateVersion, "planning_request": payload(t, "request.json")})
	assertCode(t, w, 201)
	return saved(t, w)
}
func argsFrom(p contract.SavedPlan) contract.RfqDraftArguments {
	q := p.Snapshot.Request
	return contract.RfqDraftArguments{BuyerId: q.Buyer.Id, Material: q.Material, Quantity: q.Quantity, DestinationCountry: q.Buyer.DestinationCountry, QuoteCurrency: q.QuoteCurrency, SettlementCurrency: q.SettlementCurrency, ReportingCurrency: q.ReportingCurrency}
}
func confirm(t *testing.T, f stateFixture, p contract.SavedPlan) contract.ActionApproval {
	t.Helper()
	q := contract.ConfirmActionRequest{ContractVersion: stateVersion, ExpectedRevision: p.Revision, ExpectedPlanVersion: p.Snapshot.Plan.PlanVersion, ActionKind: "prepare_rfq", Arguments: argsFrom(p)}
	w := stateCall(f.h, "POST", "/v2/plans/"+p.SavedPlanId+"/approvals", "test-editor", q)
	assertCode(t, w, 201)
	var v contract.ApprovalResponse
	if e := json.Unmarshal(w.Body.Bytes(), &v); e != nil {
		t.Fatal(e)
	}
	return v.Approval
}
func execution(p contract.SavedPlan, a contract.ActionApproval, key string) contract.ExecuteActionRequest {
	return contract.ExecuteActionRequest{ContractVersion: stateVersion, WorkspaceId: p.WorkspaceId, SavedPlanId: p.SavedPlanId, ExpectedRevision: p.Revision, ExpectedPlanVersion: p.Snapshot.Plan.PlanVersion, ApprovalId: a.ApprovalId, ActionKind: "prepare_rfq", Arguments: argsFrom(p), IdempotencyKey: key}
}

func TestStatePersistenceAuthorizationAndExactAmounts(t *testing.T) {
	f := setupState(t)
	path := "/v2/workspaces/local-workspace/plans"
	req := map[string]any{"contract_version": stateVersion, "planning_request": payload(t, "request.json")}
	assertCode(t, stateCall(f.h, "POST", path, "", req), 401)
	assertCode(t, stateCall(f.h, "POST", path, "test-viewer", req), 403)
	assertCode(t, stateCall(f.h, "POST", path, "other-editor", req), 403)
	bad := map[string]any{"contract_version": stateVersion, "planning_request": payload(t, "request.json"), "snapshot": payload(t, "success.json")}
	assertCode(t, stateCall(f.h, "POST", path, "test-editor", bad), 422)
	var count int
	if e := f.db.QueryRow(context.Background(), `SELECT count(*) FROM `+f.schema+`.plans`).Scan(&count); e != nil || count != 0 {
		t.Fatalf("unauthorized side effect: %d %v", count, e)
	}
	p := savePlan(t, f)
	if p.Snapshot.Request.Quantity.Amount != "50000" || p.Snapshot.Plan == nil || p.Snapshot.Plan.Comparisons[0].ExpectedCost.Amount != "42123456.123456789012345678" {
		t.Fatal("decimal strings changed")
	}
	w := stateCall(f.h, "GET", path, "test-viewer", nil)
	assertCode(t, w, 200)
	var list contract.SavedPlansResponse
	if e := json.Unmarshal(w.Body.Bytes(), &list); e != nil || len(list.Plans) != 1 {
		t.Fatalf("list: %s %v", w.Body.String(), e)
	}
	reopened := newStateHandler(f.cfg)
	w = stateCall(reopened, "GET", "/v2/plans/"+p.SavedPlanId, "test-viewer", nil)
	assertCode(t, w, 200)
	if got := saved(t, w); got.Snapshot.Plan.PlanVersion != p.Snapshot.Plan.PlanVersion {
		t.Fatal("snapshot changed across store reopen")
	}
}

func TestReviseRejectsForeignOrMissingPlanBeforeComputation(t *testing.T) {
	f := setupState(t)
	p := savePlan(t, f)
	before := f.calls.Load()
	other := p.Snapshot.Request
	other.WorkspaceId = "other-workspace"
	q := contract.ReviseSavedPlanRequest{ContractVersion: stateVersion, ExpectedRevision: 1, PlanningRequest: other}
	assertCode(t, stateCall(f.h, "PUT", "/v2/plans/"+p.SavedPlanId, "other-editor", q), 404)
	q.PlanningRequest = p.Snapshot.Request
	assertCode(t, stateCall(f.h, "PUT", "/v2/plans/missing-id", "test-editor", q), 404)
	if got := f.calls.Load(); got != before {
		t.Fatalf("unauthorized revision called Python: before=%d after=%d", before, got)
	}
}

func TestSaveReturnsSemanticValidationWithoutProviderCall(t *testing.T) {
	f := setupState(t)
	q := map[string]any{"contract_version": stateVersion, "planning_request": payload(t, "precision.json")}
	w := stateCall(f.h, "POST", "/v2/workspaces/local-workspace/plans", "test-editor", q)
	assertCode(t, w, 422)
	if f.calls.Load() != 0 {
		t.Fatal("invalid input called Python")
	}
}

func TestStateBodyPreservesRequiredPlanningFields(t *testing.T) {
	mutations := map[string]func(map[string]any){
		"missing lead time":         func(q map[string]any) { delete(q, "lead_time_months") },
		"null lead time":            func(q map[string]any) { q["lead_time_months"] = nil },
		"missing need by month":     func(q map[string]any) { delete(q, "need_by_month") },
		"missing buyer destination": func(q map[string]any) { delete(q["buyer"].(map[string]any), "destination_country") },
		"null quantity amount":      func(q map[string]any) { q["quantity"].(map[string]any)["amount"] = nil },
	}
	for name, mutate := range mutations {
		t.Run(name, func(t *testing.T) {
			for _, revise := range []bool{false, true} {
				q := payload(t, "request.json")
				mutate(q)
				envelope := map[string]any{"contract_version": stateVersion, "planning_request": q}
				if revise {
					envelope["expected_revision"] = 1
				}
				b := marshal(envelope)
				req := httptest.NewRequest("POST", "/", bytes.NewReader(b))
				if revise {
					var got contract.ReviseSavedPlanRequest
					if e := stateBody(req, &got); e == nil {
						t.Fatal("revise accepted missing or null required field")
					}
				} else {
					var got contract.CreateSavedPlanRequest
					if e := stateBody(req, &got); e == nil {
						t.Fatal("save accepted missing or null required field")
					}
				}
			}
		})
	}
	q := payload(t, "request.json")
	q["lead_time_months"] = 0
	b := marshal(map[string]any{"contract_version": stateVersion, "planning_request": q})
	var got contract.CreateSavedPlanRequest
	if e := stateBody(httptest.NewRequest("POST", "/", bytes.NewReader(b)), &got); e != nil || got.PlanningRequest.LeadTimeMonths != 0 {
		t.Fatalf("explicit zero rejected: %v", e)
	}
}

func TestStateRoutesRejectMissingFieldsWithoutComputeOrEffects(t *testing.T) {
	f := setupState(t)
	mutations := map[string]func(map[string]any){
		"missing lead time":         func(q map[string]any) { delete(q, "lead_time_months") },
		"null lead time":            func(q map[string]any) { q["lead_time_months"] = nil },
		"missing need by month":     func(q map[string]any) { delete(q, "need_by_month") },
		"missing buyer destination": func(q map[string]any) { delete(q["buyer"].(map[string]any), "destination_country") },
		"null quantity amount":      func(q map[string]any) { q["quantity"].(map[string]any)["amount"] = nil },
	}
	for name, mutate := range mutations {
		t.Run("save "+name, func(t *testing.T) {
			q := payload(t, "request.json")
			mutate(q)
			w := stateCall(f.h, "POST", "/v2/workspaces/local-workspace/plans", "test-editor", map[string]any{"contract_version": stateVersion, "planning_request": q})
			assertCode(t, w, 422)
			if f.calls.Load() != 0 {
				t.Fatal("invalid save called upstream")
			}
			var count int
			if e := f.db.QueryRow(context.Background(), `SELECT count(*) FROM `+f.schema+`.plans`).Scan(&count); e != nil || count != 0 {
				t.Fatalf("invalid save persisted: %d %v", count, e)
			}
		})
	}
	zero := payload(t, "request.json")
	zero["lead_time_months"] = 0
	w := stateCall(f.h, "POST", "/v2/workspaces/local-workspace/plans", "test-editor", map[string]any{"contract_version": stateVersion, "planning_request": zero})
	assertCode(t, w, 201)
	p := saved(t, w)
	if p.Snapshot.Request.LeadTimeMonths != 0 {
		t.Fatal("explicit zero was changed")
	}
	before := f.calls.Load()
	for name, mutate := range mutations {
		t.Run("revise "+name, func(t *testing.T) {
			q := payload(t, "request.json")
			mutate(q)
			w := stateCall(f.h, "PUT", "/v2/plans/"+p.SavedPlanId, "test-editor", map[string]any{"contract_version": stateVersion, "expected_revision": 1, "planning_request": q})
			assertCode(t, w, 422)
			if f.calls.Load() != before {
				t.Fatal("invalid revision called upstream")
			}
			var rev, count int
			f.db.QueryRow(context.Background(), `SELECT current_revision FROM `+f.schema+`.plans WHERE saved_plan_id=$1`, p.SavedPlanId).Scan(&rev)
			f.db.QueryRow(context.Background(), `SELECT count(*) FROM `+f.schema+`.plan_revisions WHERE saved_plan_id=$1`, p.SavedPlanId).Scan(&count)
			if rev != 1 || count != 1 {
				t.Fatalf("invalid revision persisted: current=%d snapshots=%d", rev, count)
			}
		})
	}
	w = stateCall(f.h, "PUT", "/v2/plans/"+p.SavedPlanId, "test-editor", map[string]any{"contract_version": stateVersion, "expected_revision": 1, "planning_request": zero})
	assertCode(t, w, 200)
	if got := saved(t, w); got.Revision != 2 || got.Snapshot.Request.LeadTimeMonths != 0 {
		t.Fatal("explicit zero revision failed")
	}
}

func TestStateIdempotentExecutionAndHistory(t *testing.T) {
	f := setupState(t)
	p := savePlan(t, f)
	a := confirm(t, f, p)
	q := execution(p, a, "same-key")
	var wg sync.WaitGroup
	results := make([]*httptest.ResponseRecorder, 8)
	for i := range results {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			results[i] = stateCall(f.h, "POST", "/v2/actions/prepare_rfq", "test-editor", q)
		}(i)
	}
	wg.Wait()
	var first contract.ActionReceiptResponse
	for i, w := range results {
		assertCode(t, w, 200)
		var got contract.ActionReceiptResponse
		if e := json.Unmarshal(w.Body.Bytes(), &got); e != nil {
			t.Fatal(e)
		}
		if i == 0 {
			first = got
		} else if got.Receipt.ReceiptId != first.Receipt.ReceiptId {
			t.Fatal("concurrent replay created another receipt")
		}
	}
	assertCode(t, stateCall(f.h, "POST", "/v2/actions/prepare_rfq", "same-workspace-editor", q), 409)
	q.Arguments.Quantity.Amount = "1"
	assertCode(t, stateCall(f.h, "POST", "/v2/actions/prepare_rfq", "test-editor", q), 409)
	q = execution(p, a, "other-key")
	assertCode(t, stateCall(f.h, "POST", "/v2/actions/prepare_rfq", "test-editor", q), 409)
	for _, suffix := range []string{"receipts", "approvals", "audit"} {
		w := stateCall(f.h, "GET", "/v2/plans/"+p.SavedPlanId+"/"+suffix, "test-viewer", nil)
		assertCode(t, w, 200)
	}
	var receipts, audits int
	f.db.QueryRow(context.Background(), `SELECT count(*) FROM `+f.schema+`.action_receipts WHERE saved_plan_id=$1`, p.SavedPlanId).Scan(&receipts)
	f.db.QueryRow(context.Background(), `SELECT count(*) FROM `+f.schema+`.audit_events WHERE receipt_id=$1`, first.Receipt.ReceiptId).Scan(&audits)
	if receipts != 1 || audits != 1 {
		t.Fatalf("effects receipts=%d audits=%d", receipts, audits)
	}
}

func TestStateRevisionInvalidatesApprovalAndCommittedReplaySurvives(t *testing.T) {
	f := setupState(t)
	p := savePlan(t, f)
	old := confirm(t, f, p)
	revise := contract.ReviseSavedPlanRequest{ContractVersion: stateVersion, ExpectedRevision: 1, PlanningRequest: p.Snapshot.Request}
	w := stateCall(f.h, "PUT", "/v2/plans/"+p.SavedPlanId, "test-editor", revise)
	assertCode(t, w, 200)
	next := saved(t, w)
	if next.Revision != 2 {
		t.Fatal("revision did not increment")
	}
	assertCode(t, stateCall(f.h, "POST", "/v2/actions/prepare_rfq", "test-editor", execution(p, old, "stale")), 409)
	var count int
	f.db.QueryRow(context.Background(), `SELECT count(*) FROM `+f.schema+`.action_receipts`).Scan(&count)
	if count != 0 {
		t.Fatal("stale approval created receipt")
	}
	current := confirm(t, f, next)
	q := execution(next, current, "committed")
	w = stateCall(f.h, "POST", "/v2/actions/prepare_rfq", "test-editor", q)
	assertCode(t, w, 200)
	var first contract.ActionReceiptResponse
	json.Unmarshal(w.Body.Bytes(), &first)
	revise.ExpectedRevision = 2
	w = stateCall(f.h, "PUT", "/v2/plans/"+p.SavedPlanId, "test-editor", revise)
	assertCode(t, w, 200)
	w = stateCall(f.h, "POST", "/v2/actions/prepare_rfq", "test-editor", q)
	assertCode(t, w, 200)
	var replay contract.ActionReceiptResponse
	json.Unmarshal(w.Body.Bytes(), &replay)
	if first.Receipt.ReceiptId != replay.Receipt.ReceiptId {
		t.Fatal("committed replay changed after revision")
	}
}

func TestStateAuditFailureRollsBackReceiptAndApproval(t *testing.T) {
	f := setupState(t)
	p := savePlan(t, f)
	a := confirm(t, f, p)
	_, e := f.db.Exec(context.Background(), `CREATE FUNCTION `+f.schema+`.deny_audit() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF NEW.event_type='rfq_prepared' THEN RAISE EXCEPTION 'test audit outage'; END IF; RETURN NEW; END $$`)
	if e != nil {
		t.Fatal(e)
	}
	_, e = f.db.Exec(context.Background(), `CREATE TRIGGER fail_rfq_audit BEFORE INSERT ON `+f.schema+`.audit_events FOR EACH ROW EXECUTE FUNCTION `+f.schema+`.deny_audit()`)
	if e != nil {
		t.Fatal(e)
	}
	q := execution(p, a, "audit-outage")
	assertCode(t, stateCall(f.h, "POST", "/v2/actions/prepare_rfq", "test-editor", q), 503)
	var count int
	var status string
	f.db.QueryRow(context.Background(), `SELECT count(*) FROM `+f.schema+`.action_receipts`).Scan(&count)
	f.db.QueryRow(context.Background(), `SELECT status FROM `+f.schema+`.approvals WHERE approval_id=$1`, a.ApprovalId).Scan(&status)
	if count != 0 || status != "confirmed" {
		t.Fatalf("partial effect persisted: receipts=%d approval=%s", count, status)
	}
	if _, e = f.db.Exec(context.Background(), `DROP TRIGGER fail_rfq_audit ON `+f.schema+`.audit_events`); e != nil {
		t.Fatal(e)
	}
	assertCode(t, stateCall(f.h, "POST", "/v2/actions/prepare_rfq", "test-editor", q), 200)
}

func TestStateConcurrentReviseAndExecuteSerialize(t *testing.T) {
	f := setupState(t)
	p := savePlan(t, f)
	a := confirm(t, f, p)
	revise := contract.ReviseSavedPlanRequest{ContractVersion: stateVersion, ExpectedRevision: 1, PlanningRequest: p.Snapshot.Request}
	var wg sync.WaitGroup
	var revised, executed *httptest.ResponseRecorder
	wg.Add(2)
	go func() {
		defer wg.Done()
		revised = stateCall(f.h, "PUT", "/v2/plans/"+p.SavedPlanId, "test-editor", revise)
	}()
	go func() {
		defer wg.Done()
		executed = stateCall(f.h, "POST", "/v2/actions/prepare_rfq", "test-editor", execution(p, a, "race"))
	}()
	wg.Wait()
	assertCode(t, revised, 200)
	if executed.Code != 200 && executed.Code != 409 {
		t.Fatalf("execute race: %d %s", executed.Code, executed.Body.String())
	}
	var receipts int
	f.db.QueryRow(context.Background(), `SELECT count(*) FROM `+f.schema+`.action_receipts`).Scan(&receipts)
	if (executed.Code == 200 && receipts != 1) || (executed.Code == 409 && receipts != 0) {
		t.Fatalf("race effect mismatch: status=%d receipts=%d", executed.Code, receipts)
	}
}

func TestStateMigrationReapplyArchiveRestore(t *testing.T) {
	f := setupState(t)
	p := savePlan(t, f)
	up, e := os.ReadFile("migrations/001_plans_actions.up.sql")
	if e != nil {
		t.Fatal(e)
	}
	down, e := os.ReadFile("migrations/001_plans_actions.down.sql")
	if e != nil {
		t.Fatal(e)
	}
	replace := func(b []byte) string {
		v := strings.ReplaceAll(string(b), "cs_plan_actions_archived_v1", f.schema+"_archived_v1")
		return strings.ReplaceAll(v, "cs_plan_actions", f.schema)
	}
	if _, e = f.db.Exec(context.Background(), replace(up)); e != nil {
		t.Fatal("reapply:", e)
	}
	if _, e = f.db.Exec(context.Background(), replace(down)); e != nil {
		t.Fatal("archive:", e)
	}
	var archived int
	if e = f.db.QueryRow(context.Background(), `SELECT count(*) FROM `+f.schema+`_archived_v1.plans WHERE saved_plan_id=$1`, p.SavedPlanId).Scan(&archived); e != nil || archived != 1 {
		t.Fatalf("archive lost plan: %d %v", archived, e)
	}
	if _, e = f.db.Exec(context.Background(), replace(up)); e != nil {
		t.Fatal("restore:", e)
	}
	var restored int
	if e = f.db.QueryRow(context.Background(), `SELECT count(*) FROM `+f.schema+`.plans WHERE saved_plan_id=$1`, p.SavedPlanId).Scan(&restored); e != nil || restored != 1 {
		t.Fatalf("restore lost plan: %d %v", restored, e)
	}
}
