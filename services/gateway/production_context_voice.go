package main

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"strconv"
	"time"

	"closedsource/gateway/internal/contract"
	"closedsource/gateway/internal/voice"
	"github.com/jackc/pgx/v5"
)

const savedVoicePath = "/v2/voice/saved-context-sessions"

type productionGuard struct {
	conn   *pgx.Conn
	tx     pgx.Tx
	ctx    context.Context
	cancel context.CancelFunc
}

func (g *productionGuard) close() error {
	if g == nil {
		return nil
	}
	err := g.tx.Commit(g.ctx)
	g.conn.Close(context.Background())
	g.cancel()
	return err
}

func (g *productionGuard) rollback() {
	if g == nil {
		return
	}
	ctx, stop := context.WithTimeout(context.Background(), time.Second)
	defer stop()
	_ = g.tx.Rollback(ctx)
	g.conn.Close(context.Background())
	g.cancel()
}

func (s *stateServer) guardProduction(parent context.Context, p principal, ref contract.ProductionContextRef) (*productionGuard, []byte, int) {
	if s.db == nil {
		return nil, nil, 503
	}
	ctx, cancel := context.WithTimeout(parent, 3*time.Second)
	conn, err := pgx.ConnectConfig(ctx, s.db)
	if err != nil {
		cancel()
		return nil, nil, 503
	}
	tx, err := conn.Begin(ctx)
	if err != nil {
		conn.Close(context.Background())
		cancel()
		return nil, nil, 503
	}
	guard := &productionGuard{conn: conn, tx: tx, ctx: ctx, cancel: cancel}
	var revision int
	var snapshot []byte
	err = tx.QueryRow(ctx, `SELECT revision,snapshot FROM `+productionSchema+`.contexts WHERE context_id=$1 AND workspace_id=$2 FOR SHARE`, ref.ContextId, p.Workspace).Scan(&revision, &snapshot)
	if errors.Is(err, pgx.ErrNoRows) {
		guard.rollback()
		return nil, nil, 404
	}
	if err != nil {
		guard.rollback()
		return nil, nil, 503
	}
	if revision != ref.Revision {
		guard.rollback()
		return nil, nil, 409
	}
	return guard, snapshot, 0
}

func (s *stateServer) savedVoiceSession(w http.ResponseWriter, r *http.Request, p principal) {
	if r.Method != http.MethodPost {
		voiceErr(w, 405, "method_not_allowed", "Method not allowed")
		return
	}
	raw, status := voiceBody(r, voiceCreateLimit)
	if status != 0 {
		voiceErr(w, status, voiceBodyCode(status), "Invalid saved voice request")
		return
	}
	value, ok := voiceParse(raw, 0)
	if !ok || !voiceValidName(value, "VoiceHTTPSavedSessionRequest") {
		voiceErr(w, 422, "invalid_request", "Invalid saved voice request")
		return
	}
	request := value.(map[string]any)
	revision, err := strconv.Atoi(request["expected_revision"].(json.Number).String())
	if err != nil || revision < 1 || revision > 2147483647 {
		voiceErr(w, 422, "invalid_request", "Invalid saved voice request")
		return
	}
	ref := contract.ProductionContextRef{ContextId: request["context_id"].(string), WorkspaceId: p.Workspace, Revision: revision}
	guard, snapshotBytes, code := s.guardProduction(r.Context(), p, ref)
	if code != 0 {
		if code == 404 {
			voiceErr(w, 404, "production_context_unavailable", "Production context unavailable")
		} else if code == 409 {
			voiceErr(w, 409, "production_context_stale", "Production context revision changed")
		} else {
			voiceErr(w, 503, "production_context_unavailable", "Production context storage unavailable")
		}
		return
	}
	defer func() {
		if guard != nil {
			guard.rollback()
		}
	}()
	parsed, valid := voiceParse(snapshotBytes, 0)
	if !valid || !voiceValidName(parsed, "ProductionSnapshot") {
		guard.rollback()
		guard = nil
		voiceErr(w, 503, "production_context_unavailable", "Stored production context invalid")
		return
	}
	snapshot := parsed.(map[string]any)
	if snapshot["workspace_id"] != p.Workspace || snapshot["snapshot_id"] != "production-"+ref.ContextId+"-r"+strconv.Itoa(ref.Revision) || snapshot["plan_version"] != "production-plan-"+ref.ContextId+"-r"+strconv.Itoa(ref.Revision) {
		guard.rollback()
		guard = nil
		voiceErr(w, 503, "production_context_unavailable", "Stored production context invalid")
		return
	}
	response, record, status, codeName := s.voice.createRecord(r, p, snapshot, &ref, false)
	if status != 0 {
		guard.rollback()
		guard = nil
		voiceErr(w, status, codeName, "Voice session unavailable")
		return
	}
	if err := guard.close(); err != nil {
		s.voice.removeRecord(record)
		voiceErr(w, 503, "production_context_unavailable", "Production context storage unavailable")
		return
	}
	guard = nil
	s.voice.mu.Lock()
	if s.voice.sessions[record.id] != record || s.voice.closed || r.Context().Err() != nil {
		s.voice.mu.Unlock()
		s.voice.removeRecord(record)
		return
	}
	record.accepted = true
	s.voice.mu.Unlock()
	voiceWrite(w, 201, response)
	if r.Context().Err() != nil {
		s.voice.removeRecord(record)
	}
}

func (a *voiceHTTPAdapter) removeRecord(record *voiceRecord) {
	if record == nil {
		return
	}
	a.mu.Lock()
	if a.sessions[record.id] == record {
		delete(a.sessions, record.id)
		record.cancel()
		_ = a.manager.Close(record.owner, record.id)
	}
	a.mu.Unlock()
}

func (a *voiceHTTPAdapter) savedRef(owner voice.Owner, id string) (*contract.ProductionContextRef, *voiceRecord) {
	a.mu.Lock()
	record := a.currentLocked(owner, id)
	var ref *contract.ProductionContextRef
	if record != nil && record.productionRef != nil {
		copy := *record.productionRef
		ref = &copy
	}
	a.mu.Unlock()
	return ref, record
}

func (a *voiceHTTPAdapter) staleRecord(owner voice.Owner, id string, record *voiceRecord) {
	a.mu.Lock()
	if a.currentLocked(owner, id) == record {
		record.stale = true
		record.outcome = nil
		record.state = ""
		record.cancel()
		if record.lease != nil {
			_ = a.manager.Cancel(owner, record.lease)
		}
	}
	a.mu.Unlock()
}
