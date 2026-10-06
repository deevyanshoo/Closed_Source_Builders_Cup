package main

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"time"

	"closedsource/gateway/internal/contract"
	"github.com/jackc/pgx/v5"
)

func scanApproval(row rowScanner) (contract.ActionApproval, error) {
	var a contract.ActionApproval
	var args []byte
	var ts time.Time
	e := row.Scan(&a.ApprovalId, &a.WorkspaceId, &a.SavedPlanId, &a.Revision, &a.PlanVersion, &a.ActionKind, &args, &a.ApprovedBy, &ts, &a.Status, &a.ReceiptId)
	if e != nil {
		return a, e
	}
	e = json.Unmarshal(args, &a.Arguments)
	a.CreatedAt = ts.UTC().Format(time.RFC3339Nano)
	return a, e
}
func (s *stateServer) approvalQuery() string {
	return `SELECT approval_id,workspace_id,saved_plan_id,revision,plan_version,action_kind,arguments,approved_by,created_at,status,receipt_id FROM ` + s.cfg.Schema + `.approvals`
}
func (s *stateServer) approvals(w http.ResponseWriter, r *http.Request, ctx context.Context, c *pgx.Conn, p principal, id string) {
	switch r.Method {
	case "GET":
		if !s.planExists(ctx, c, p.Workspace, id) {
			notFound(w)
			return
		}
		rows, e := c.Query(ctx, s.approvalQuery()+` WHERE workspace_id=$1 AND saved_plan_id=$2 ORDER BY created_at,approval_id`, p.Workspace, id)
		if e != nil {
			dbFailure(w)
			return
		}
		defer rows.Close()
		out := []contract.ActionApproval{}
		for rows.Next() {
			a, e := scanApproval(rows)
			if e != nil {
				dbFailure(w)
				return
			}
			out = append(out, a)
		}
		if rows.Err() != nil {
			dbFailure(w)
			return
		}
		write(w, 200, contract.ApprovalsResponse{ContractVersion: stateVersion, Approvals: out})
	case "POST":
		if !editor(w, p) {
			return
		}
		var q contract.ConfirmActionRequest
		if stateBody(r, &q) != nil || q.ContractVersion != stateVersion || q.ExpectedRevision < 1 || q.ExpectedPlanVersion == "" || q.ActionKind != "prepare_rfq" {
			invalid(w, "Invalid confirmation request")
			return
		}
		tx, e := c.Begin(ctx)
		if e != nil {
			dbFailure(w)
			return
		}
		defer tx.Rollback(ctx)
		plan, e := s.lockedPlan(ctx, tx, p.Workspace, id)
		if errors.Is(e, pgx.ErrNoRows) {
			notFound(w)
			return
		}
		if e != nil {
			dbFailure(w)
			return
		}
		if plan.Revision != q.ExpectedRevision || plan.Snapshot.Plan == nil || plan.Snapshot.Plan.PlanVersion != q.ExpectedPlanVersion {
			conflict(w, "Plan revision is stale")
			return
		}
		if !validArguments(q.Arguments, plan.Snapshot.Request) {
			invalid(w, "Action arguments differ from saved plan")
			return
		}
		aid := newID()
		_, e = tx.Exec(ctx, `INSERT INTO `+s.cfg.Schema+`.approvals(approval_id,workspace_id,saved_plan_id,revision,plan_version,action_kind,arguments,approved_by) VALUES($1,$2,$3,$4,$5,'prepare_rfq',$6,$7)`, aid, p.Workspace, id, plan.Revision, q.ExpectedPlanVersion, marshal(q.Arguments), p.ID)
		if e == nil {
			_, e = tx.Exec(ctx, `INSERT INTO `+s.cfg.Schema+`.audit_events(audit_id,workspace_id,saved_plan_id,revision,event_type,principal_id) VALUES($1,$2,$3,$4,'action_confirmed',$5)`, newID(), p.Workspace, id, plan.Revision, p.ID)
		}
		if e != nil || tx.Commit(ctx) != nil {
			dbFailure(w)
			return
		}
		a, e := scanApproval(c.QueryRow(ctx, s.approvalQuery()+` WHERE workspace_id=$1 AND approval_id=$2`, p.Workspace, aid))
		if e != nil {
			dbFailure(w)
			return
		}
		write(w, 201, contract.ApprovalResponse{ContractVersion: stateVersion, Approval: a})
	default:
		stateError(w, 405, "method_not_allowed", "Method not allowed")
	}
}
func (s *stateServer) planExists(ctx context.Context, c *pgx.Conn, workspace, id string) bool {
	var exists bool
	return c.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM `+s.cfg.Schema+`.plans WHERE workspace_id=$1 AND saved_plan_id=$2)`, workspace, id).Scan(&exists) == nil && exists
}
func (s *stateServer) lockedPlan(ctx context.Context, tx pgx.Tx, workspace, id string) (contract.SavedPlan, error) {
	var revision int
	e := tx.QueryRow(ctx, `SELECT current_revision FROM `+s.cfg.Schema+`.plans WHERE workspace_id=$1 AND saved_plan_id=$2 FOR UPDATE`, workspace, id).Scan(&revision)
	if e != nil {
		return contract.SavedPlan{}, e
	}
	return scanPlan(tx.QueryRow(ctx, s.planQuery(), workspace, id))
}
func scanReceipt(row rowScanner) (contract.ActionReceipt, error) {
	var b []byte
	var r contract.ActionReceipt
	e := row.Scan(&b)
	if e != nil {
		return r, e
	}
	e = json.Unmarshal(b, &r)
	return r, e
}
func (s *stateServer) receipts(w http.ResponseWriter, r *http.Request, ctx context.Context, c *pgx.Conn, p principal, id string) {
	if r.Method != "GET" {
		stateError(w, 405, "method_not_allowed", "Method not allowed")
		return
	}
	if !s.planExists(ctx, c, p.Workspace, id) {
		notFound(w)
		return
	}
	rows, e := c.Query(ctx, `SELECT receipt FROM `+s.cfg.Schema+`.action_receipts WHERE workspace_id=$1 AND saved_plan_id=$2 ORDER BY created_at,receipt_id`, p.Workspace, id)
	if e != nil {
		dbFailure(w)
		return
	}
	defer rows.Close()
	out := []contract.ActionReceipt{}
	for rows.Next() {
		receipt, e := scanReceipt(rows)
		if e != nil {
			dbFailure(w)
			return
		}
		out = append(out, receipt)
	}
	if rows.Err() != nil {
		dbFailure(w)
		return
	}
	write(w, 200, contract.ActionReceiptsResponse{ContractVersion: stateVersion, Receipts: out})
}
func (s *stateServer) audit(w http.ResponseWriter, r *http.Request, ctx context.Context, c *pgx.Conn, p principal, id string) {
	if r.Method != "GET" {
		stateError(w, 405, "method_not_allowed", "Method not allowed")
		return
	}
	if !s.planExists(ctx, c, p.Workspace, id) {
		notFound(w)
		return
	}
	rows, e := c.Query(ctx, `SELECT audit_id,workspace_id,saved_plan_id,revision,event_type,principal_id,created_at,receipt_id FROM `+s.cfg.Schema+`.audit_events WHERE workspace_id=$1 AND saved_plan_id=$2 ORDER BY created_at,audit_id`, p.Workspace, id)
	if e != nil {
		dbFailure(w)
		return
	}
	defer rows.Close()
	out := []contract.AuditEvent{}
	for rows.Next() {
		var a contract.AuditEvent
		var ts time.Time
		e = rows.Scan(&a.AuditId, &a.WorkspaceId, &a.SavedPlanId, &a.Revision, &a.EventType, &a.PrincipalId, &ts, &a.ReceiptId)
		if e != nil {
			dbFailure(w)
			return
		}
		a.CreatedAt = ts.UTC().Format(time.RFC3339Nano)
		out = append(out, a)
	}
	if rows.Err() != nil {
		dbFailure(w)
		return
	}
	write(w, 200, contract.AuditResponse{ContractVersion: stateVersion, Events: out})
}
func (s *stateServer) execute(w http.ResponseWriter, r *http.Request, ctx context.Context, c *pgx.Conn, p principal) {
	if r.Method != "POST" {
		stateError(w, 405, "method_not_allowed", "Method not allowed")
		return
	}
	if !editor(w, p) {
		return
	}
	var q contract.ExecuteActionRequest
	if stateBody(r, &q) != nil || q.ContractVersion != stateVersion || q.WorkspaceId == "" || q.SavedPlanId == "" || q.ExpectedRevision < 1 || q.ExpectedPlanVersion == "" || q.ApprovalId == "" || q.ActionKind != "prepare_rfq" || len(q.IdempotencyKey) < 1 || len(q.IdempotencyKey) > 128 {
		invalid(w, "Invalid execution request")
		return
	}
	if q.WorkspaceId != p.Workspace {
		stateError(w, 403, "forbidden", "Workspace access denied")
		return
	}
	hash := hashRequest(struct {
		Request   contract.ExecuteActionRequest `json:"request"`
		Principal string                        `json:"principal"`
	}{q, p.ID})
	tx, e := c.Begin(ctx)
	if e != nil {
		dbFailure(w)
		return
	}
	defer tx.Rollback(ctx)
	// Serialize all attempts at a workspace/key before checking a persisted result.
	_, e = tx.Exec(ctx, `SELECT pg_advisory_xact_lock(hashtextextended($1,0))`, q.WorkspaceId+"/"+q.IdempotencyKey)
	if e != nil {
		dbFailure(w)
		return
	}
	var oldHash string
	var oldReceipt []byte
	e = tx.QueryRow(ctx, `SELECT request_hash,receipt FROM `+s.cfg.Schema+`.action_receipts WHERE workspace_id=$1 AND idempotency_key=$2`, q.WorkspaceId, q.IdempotencyKey).Scan(&oldHash, &oldReceipt)
	if e == nil {
		if oldHash != hash {
			conflict(w, "Idempotency key belongs to a different request")
			return
		}
		var receipt contract.ActionReceipt
		if json.Unmarshal(oldReceipt, &receipt) != nil {
			dbFailure(w)
			return
		}
		write(w, 200, contract.ActionReceiptResponse{ContractVersion: stateVersion, Receipt: receipt})
		return
	}
	if !errors.Is(e, pgx.ErrNoRows) {
		dbFailure(w)
		return
	}
	plan, e := s.lockedPlan(ctx, tx, p.Workspace, q.SavedPlanId)
	if errors.Is(e, pgx.ErrNoRows) {
		notFound(w)
		return
	}
	if e != nil {
		dbFailure(w)
		return
	}
	if plan.Revision != q.ExpectedRevision || plan.Snapshot.Plan == nil || plan.Snapshot.Plan.PlanVersion != q.ExpectedPlanVersion {
		conflict(w, "Plan revision is stale")
		return
	}
	if !validArguments(q.Arguments, plan.Snapshot.Request) {
		invalid(w, "Action arguments differ from saved plan")
		return
	}
	var a contract.ActionApproval
	var args []byte
	var approvedBy, status, planVersion string
	var rev int
	e = tx.QueryRow(ctx, `SELECT revision,plan_version,arguments,approved_by,status FROM `+s.cfg.Schema+`.approvals WHERE workspace_id=$1 AND saved_plan_id=$2 AND approval_id=$3 FOR UPDATE`, p.Workspace, q.SavedPlanId, q.ApprovalId).Scan(&rev, &planVersion, &args, &approvedBy, &status)
	if errors.Is(e, pgx.ErrNoRows) {
		notFound(w)
		return
	}
	if e != nil {
		dbFailure(w)
		return
	}
	if json.Unmarshal(args, &a.Arguments) != nil {
		dbFailure(w)
		return
	}
	if rev != q.ExpectedRevision || planVersion != q.ExpectedPlanVersion || approvedBy != p.ID || status != "confirmed" || a.Arguments != q.Arguments {
		conflict(w, "Approval is stale or already consumed")
		return
	}
	id := newID()
	ts := now()
	receipt := contract.ActionReceipt{ReceiptId: id, WorkspaceId: p.Workspace, SavedPlanId: q.SavedPlanId, Revision: rev, PlanVersion: planVersion, ApprovalId: q.ApprovalId, ActionKind: "prepare_rfq", Arguments: q.Arguments, PreparedBy: p.ID, CreatedAt: ts, Status: "draft_prepared", SupplierCommunication: false}
	_, e = tx.Exec(ctx, `INSERT INTO `+s.cfg.Schema+`.action_receipts(receipt_id,workspace_id,saved_plan_id,revision,plan_version,approval_id,action_kind,idempotency_key,request_hash,prepared_by,receipt) VALUES($1,$2,$3,$4,$5,$6,'prepare_rfq',$7,$8,$9,$10)`, id, p.Workspace, q.SavedPlanId, rev, planVersion, q.ApprovalId, q.IdempotencyKey, hash, p.ID, marshal(receipt))
	if e == nil {
		_, e = tx.Exec(ctx, `UPDATE `+s.cfg.Schema+`.approvals SET status='executed',receipt_id=$1,executed_at=now() WHERE approval_id=$2 AND status='confirmed'`, id, q.ApprovalId)
	}
	if e == nil {
		_, e = tx.Exec(ctx, `INSERT INTO `+s.cfg.Schema+`.audit_events(audit_id,workspace_id,saved_plan_id,revision,event_type,principal_id,receipt_id) VALUES($1,$2,$3,$4,'rfq_prepared',$5,$6)`, newID(), p.Workspace, q.SavedPlanId, rev, p.ID, id)
	}
	if e != nil || tx.Commit(ctx) != nil {
		dbFailure(w)
		return
	}
	write(w, 200, contract.ActionReceiptResponse{ContractVersion: stateVersion, Receipt: receipt})
}
