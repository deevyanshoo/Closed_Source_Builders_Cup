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

func (s *stateServer) plans(w http.ResponseWriter, r *http.Request, ctx context.Context, c *pgx.Conn, p principal) {
	switch r.Method {
	case "GET":
		rows, e := c.Query(ctx, `SELECT p.saved_plan_id,p.workspace_id,p.current_revision,v.snapshot,p.created_at,p.updated_at FROM `+s.cfg.Schema+`.plans p JOIN `+s.cfg.Schema+`.plan_revisions v ON v.saved_plan_id=p.saved_plan_id AND v.revision=p.current_revision WHERE p.workspace_id=$1 ORDER BY p.updated_at DESC`, p.Workspace)
		if e != nil {
			dbFailure(w)
			return
		}
		defer rows.Close()
		plans := []contract.SavedPlan{}
		for rows.Next() {
			plan, e := scanPlan(rows)
			if e != nil {
				dbFailure(w)
				return
			}
			plans = append(plans, plan)
		}
		if rows.Err() != nil {
			dbFailure(w)
			return
		}
		write(w, 200, contract.SavedPlansResponse{ContractVersion: stateVersion, Plans: plans})
	case "POST":
		if !editor(w, p) {
			return
		}
		var q contract.CreateSavedPlanRequest
		if stateBody(r, &q) != nil || q.ContractVersion != stateVersion || !validPlanning(q.PlanningRequest, p.Workspace) {
			invalid(w, "Invalid saved plan request")
			return
		}
		snapshot, status, msg := s.computedSnapshot(q.PlanningRequest)
		if status != 0 {
			stateError(w, status, "invalid_snapshot", msg)
			return
		}
		id := newID()
		tx, e := c.Begin(ctx)
		if e != nil {
			dbFailure(w)
			return
		}
		defer tx.Rollback(ctx)
		_, e = tx.Exec(ctx, `INSERT INTO `+s.cfg.Schema+`.plans(saved_plan_id,workspace_id,current_revision,created_by) VALUES($1,$2,1,$3)`, id, p.Workspace, p.ID)
		if e == nil {
			_, e = tx.Exec(ctx, `INSERT INTO `+s.cfg.Schema+`.plan_revisions(workspace_id,saved_plan_id,revision,plan_version,snapshot,created_by) VALUES($1,$2,1,$3,$4,$5)`, p.Workspace, id, snapshot.Plan.PlanVersion, marshal(snapshot), p.ID)
		}
		if e == nil {
			_, e = tx.Exec(ctx, `INSERT INTO `+s.cfg.Schema+`.audit_events(audit_id,workspace_id,saved_plan_id,revision,event_type,principal_id) VALUES($1,$2,$3,1,'plan_saved',$4)`, newID(), p.Workspace, id, p.ID)
		}
		if e != nil || tx.Commit(ctx) != nil {
			dbFailure(w)
			return
		}
		// Read database timestamps rather than accepting client or local clock values.
		var created, updated time.Time
		if e = c.QueryRow(ctx, `SELECT created_at,updated_at FROM `+s.cfg.Schema+`.plans WHERE saved_plan_id=$1`, id).Scan(&created, &updated); e != nil {
			dbFailure(w)
			return
		}
		write(w, 201, contract.SavedPlanResponse{ContractVersion: stateVersion, Plan: contract.SavedPlan{SavedPlanId: id, WorkspaceId: p.Workspace, Revision: 1, Snapshot: snapshot, CreatedAt: created.UTC().Format(time.RFC3339Nano), UpdatedAt: updated.UTC().Format(time.RFC3339Nano)}})
	default:
		stateError(w, 405, "method_not_allowed", "Method not allowed")
	}
}

type rowScanner interface{ Scan(...any) error }

func scanPlan(row rowScanner) (contract.SavedPlan, error) {
	var p contract.SavedPlan
	var snapshot []byte
	var created, updated time.Time
	e := row.Scan(&p.SavedPlanId, &p.WorkspaceId, &p.Revision, &snapshot, &created, &updated)
	if e != nil {
		return p, e
	}
	if e = json.Unmarshal(snapshot, &p.Snapshot); e != nil {
		return p, e
	}
	p.CreatedAt = created.UTC().Format(time.RFC3339Nano)
	p.UpdatedAt = updated.UTC().Format(time.RFC3339Nano)
	return p, nil
}
func (s *stateServer) readPlan(ctx context.Context, q rowScanner) (contract.SavedPlan, error) {
	return scanPlan(q)
}
func (s *stateServer) planQuery() string {
	return `SELECT p.saved_plan_id,p.workspace_id,p.current_revision,v.snapshot,p.created_at,p.updated_at FROM ` + s.cfg.Schema + `.plans p JOIN ` + s.cfg.Schema + `.plan_revisions v ON v.saved_plan_id=p.saved_plan_id AND v.revision=p.current_revision WHERE p.workspace_id=$1 AND p.saved_plan_id=$2`
}
func (s *stateServer) plan(w http.ResponseWriter, r *http.Request, ctx context.Context, c *pgx.Conn, p principal, id string) {
	switch r.Method {
	case "GET":
		plan, e := s.readPlan(ctx, c.QueryRow(ctx, s.planQuery(), p.Workspace, id))
		if errors.Is(e, pgx.ErrNoRows) {
			notFound(w)
			return
		}
		if e != nil {
			dbFailure(w)
			return
		}
		write(w, 200, contract.SavedPlanResponse{ContractVersion: stateVersion, Plan: plan})
	case "PUT":
		if !editor(w, p) {
			return
		}
		var q contract.ReviseSavedPlanRequest
		if stateBody(r, &q) != nil || q.ContractVersion != stateVersion || q.ExpectedRevision < 1 || !validPlanning(q.PlanningRequest, p.Workspace) {
			invalid(w, "Invalid plan revision request")
			return
		}
		// Check the principal's plan boundary before asking the legacy service to compute.
		// The locked revision check below remains authoritative for concurrent writers.
		if !s.planExists(ctx, c, p.Workspace, id) {
			notFound(w)
			return
		}
		snapshot, status, msg := s.computedSnapshot(q.PlanningRequest)
		if status != 0 {
			stateError(w, status, "invalid_snapshot", msg)
			return
		}
		tx, e := c.Begin(ctx)
		if e != nil {
			dbFailure(w)
			return
		}
		defer tx.Rollback(ctx)
		var current int
		e = tx.QueryRow(ctx, `SELECT current_revision FROM `+s.cfg.Schema+`.plans WHERE workspace_id=$1 AND saved_plan_id=$2 FOR UPDATE`, p.Workspace, id).Scan(&current)
		if errors.Is(e, pgx.ErrNoRows) {
			notFound(w)
			return
		}
		if e != nil {
			dbFailure(w)
			return
		}
		if current != q.ExpectedRevision {
			conflict(w, "Plan revision is stale")
			return
		}
		newRev := current + 1
		_, e = tx.Exec(ctx, `INSERT INTO `+s.cfg.Schema+`.plan_revisions(workspace_id,saved_plan_id,revision,plan_version,snapshot,created_by) VALUES($1,$2,$3,$4,$5,$6)`, p.Workspace, id, newRev, snapshot.Plan.PlanVersion, marshal(snapshot), p.ID)
		if e == nil {
			_, e = tx.Exec(ctx, `UPDATE `+s.cfg.Schema+`.plans SET current_revision=$3,updated_at=now() WHERE workspace_id=$1 AND saved_plan_id=$2`, p.Workspace, id, newRev)
		}
		if e == nil {
			_, e = tx.Exec(ctx, `INSERT INTO `+s.cfg.Schema+`.audit_events(audit_id,workspace_id,saved_plan_id,revision,event_type,principal_id) VALUES($1,$2,$3,$4,'plan_revised',$5)`, newID(), p.Workspace, id, newRev, p.ID)
		}
		if e != nil || tx.Commit(ctx) != nil {
			dbFailure(w)
			return
		}
		plan, e := s.readPlan(ctx, c.QueryRow(ctx, s.planQuery(), p.Workspace, id))
		if e != nil {
			dbFailure(w)
			return
		}
		write(w, 200, contract.SavedPlanResponse{ContractVersion: stateVersion, Plan: plan})
	default:
		stateError(w, 405, "method_not_allowed", "Method not allowed")
	}
}
