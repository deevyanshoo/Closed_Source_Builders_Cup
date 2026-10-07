package main

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"regexp"
	"strconv"
	"strings"
	"time"

	"closedsource/gateway/internal/contract"
	"github.com/jackc/pgx/v5"
)

const productionSchema = "cs_production_contexts"
const productionCollection = "production-contexts"

var productionID = regexp.MustCompile(`^[A-Za-z0-9_-]{32}$`)

func productionMetadata() contract.ProductionContextMetadata {
	return contract.ProductionContextMetadata{Synthetic: true, Source: "caller_supplied_synthetic", SyntacticValidation: "accepted", NumericalAcceptance: "not_claimed"}
}

func productionError(w http.ResponseWriter, status int, code, message string) {
	w.Header().Set("Cache-Control", "no-store")
	stateError(w, status, code, message)
}

func productionUnavailable(w http.ResponseWriter) {
	productionError(w, 503, "production_context_unavailable", "Production context storage unavailable")
}

func productionSnapshot(value any, workspace, id string, revision int) ([]byte, bool) {
	snapshot, ok := value.(map[string]any)
	if !ok || !voiceValidName(snapshot, "ProductionSnapshot") || snapshot["workspace_id"] != workspace || !voiceInstant(snapshot["as_of"].(string)) {
		return nil, false
	}
	snapshot["snapshot_id"] = "production-" + id + "-r" + strconv.Itoa(revision)
	snapshot["plan_version"] = "production-plan-" + id + "-r" + strconv.Itoa(revision)
	data, err := json.Marshal(snapshot)
	return data, err == nil && len(data) <= voiceSnapshotLimit
}

func scanProduction(row rowScanner) (contract.StoredProductionContext, error) {
	var result contract.StoredProductionContext
	var snapshot, metadata []byte
	var created, updated time.Time
	err := row.Scan(&result.ContextId, &result.WorkspaceId, &result.Revision, &snapshot, &metadata,
		&result.CreatedBy, &result.UpdatedBy, &created, &updated)
	if err != nil {
		return result, err
	}
	parsed, valid := voiceParse(snapshot, 0)
	if !valid || !voiceValidName(parsed, "ProductionSnapshot") || json.Unmarshal(snapshot, &result.Snapshot) != nil || json.Unmarshal(metadata, &result.Metadata) != nil || result.Metadata != productionMetadata() || !productionID.MatchString(result.ContextId) || result.Snapshot.WorkspaceId != result.WorkspaceId || result.Snapshot.SnapshotId != "production-"+result.ContextId+"-r"+strconv.Itoa(result.Revision) || result.Snapshot.PlanVersion != "production-plan-"+result.ContextId+"-r"+strconv.Itoa(result.Revision) || !voiceInstant(result.Snapshot.AsOf) {
		return result, errors.New("invalid stored production context")
	}
	result.SnapshotId = result.Snapshot.SnapshotId
	result.PlanVersion = result.Snapshot.PlanVersion
	result.AsOf = result.Snapshot.AsOf
	result.CreatedAt = created.UTC().Format(time.RFC3339Nano)
	result.UpdatedAt = updated.UTC().Format(time.RFC3339Nano)
	return result, nil
}

func productionSelect() string {
	return `SELECT context_id,workspace_id,revision,snapshot,metadata,created_by,updated_by,created_at,updated_at FROM ` + productionSchema + `.contexts`
}

func (s *stateServer) productionRoutes(w http.ResponseWriter, r *http.Request, ctx context.Context, c *pgx.Conn, p principal) {
	w.Header().Set("Cache-Control", "no-store")
	if !voiceHeaders(r) {
		productionError(w, 422, "invalid_request", "Invalid production context request")
		return
	}
	parts := strings.Split(strings.Trim(r.URL.Path, "/"), "/")
	if len(parts) == 4 && parts[0] == "v2" && parts[1] == "workspaces" && parts[3] == productionCollection {
		if parts[2] != p.Workspace {
			productionError(w, 403, "forbidden", "Workspace access denied")
			return
		}
		if r.Method == http.MethodGet {
			if !voiceNoBody(r) {
				productionError(w, 422, "invalid_request", "Invalid production context request")
				return
			}
			s.productionList(w, ctx, c, p)
			return
		}
		if r.Method == http.MethodPost {
			if !editor(w, p) {
				return
			}
			s.productionCreate(w, r, ctx, c, p)
			return
		}
		productionError(w, 405, "method_not_allowed", "Method not allowed")
		return
	}
	if len(parts) == 3 && parts[0] == "v2" && parts[1] == productionCollection && productionID.MatchString(parts[2]) {
		if r.Method == http.MethodGet {
			if !voiceNoBody(r) {
				productionError(w, 422, "invalid_request", "Invalid production context request")
				return
			}
			result, err := scanProduction(c.QueryRow(ctx, productionSelect()+` WHERE workspace_id=$1 AND context_id=$2`, p.Workspace, parts[2]))
			if errors.Is(err, pgx.ErrNoRows) {
				productionError(w, 404, "production_context_unavailable", "Production context unavailable")
				return
			}
			if err != nil {
				productionUnavailable(w)
				return
			}
			write(w, 200, contract.ProductionContextResponse{ContractVersion: stateVersion, Context: result})
			return
		}
		if r.Method == http.MethodPut {
			if !editor(w, p) {
				return
			}
			s.productionReplace(w, r, ctx, c, p, parts[2])
			return
		}
		productionError(w, 405, "method_not_allowed", "Method not allowed")
		return
	}
	productionError(w, 404, "production_context_unavailable", "Production context unavailable")
}

func (s *stateServer) productionCreate(w http.ResponseWriter, r *http.Request, ctx context.Context, c *pgx.Conn, p principal) {
	raw, status := voiceBody(r, voiceCreateLimit)
	if status != 0 {
		productionError(w, status, voiceBodyCode(status), "Invalid production context request")
		return
	}
	value, ok := voiceParse(raw, 0)
	if !ok || !voiceValidName(value, "CreateProductionContextRequest") {
		productionError(w, 422, "invalid_request", "Invalid production context request")
		return
	}
	if value.(map[string]any)["snapshot"].(map[string]any)["workspace_id"] != p.Workspace {
		productionError(w, 403, "forbidden", "Workspace access denied")
		return
	}
	id := newID()
	snapshot, ok := productionSnapshot(value.(map[string]any)["snapshot"], p.Workspace, id, 1)
	if !ok {
		productionError(w, 422, "invalid_request", "Invalid production snapshot")
		return
	}
	result, err := scanProduction(c.QueryRow(ctx, `INSERT INTO `+productionSchema+`.contexts(context_id,workspace_id,revision,snapshot,created_by,updated_by) VALUES($1,$2,1,$3,$4,$4) RETURNING context_id,workspace_id,revision,snapshot,metadata,created_by,updated_by,created_at,updated_at`, id, p.Workspace, snapshot, p.ID))
	if err != nil {
		productionUnavailable(w)
		return
	}
	write(w, 201, contract.ProductionContextResponse{ContractVersion: stateVersion, Context: result})
}

func (s *stateServer) productionReplace(w http.ResponseWriter, r *http.Request, ctx context.Context, c *pgx.Conn, p principal, id string) {
	raw, status := voiceBody(r, voiceCreateLimit)
	if status != 0 {
		productionError(w, status, voiceBodyCode(status), "Invalid production context request")
		return
	}
	value, ok := voiceParse(raw, 0)
	if !ok || !voiceValidName(value, "ReplaceProductionContextRequest") {
		productionError(w, 422, "invalid_request", "Invalid production context request")
		return
	}
	request := value.(map[string]any)
	if request["snapshot"].(map[string]any)["workspace_id"] != p.Workspace {
		productionError(w, 403, "forbidden", "Workspace access denied")
		return
	}
	revision, err := strconv.Atoi(request["expected_revision"].(json.Number).String())
	if err != nil || revision < 1 || revision >= 2147483647 {
		productionError(w, 422, "invalid_request", "Invalid expected revision")
		return
	}
	snapshot, ok := productionSnapshot(request["snapshot"], p.Workspace, id, revision+1)
	if !ok {
		productionError(w, 422, "invalid_request", "Invalid production snapshot")
		return
	}
	result, err := scanProduction(c.QueryRow(ctx, `UPDATE `+productionSchema+`.contexts SET revision=revision+1,snapshot=$3,updated_by=$4,updated_at=now() WHERE context_id=$1 AND workspace_id=$2 AND revision=$5 AND revision<2147483647 RETURNING context_id,workspace_id,revision,snapshot,metadata,created_by,updated_by,created_at,updated_at`, id, p.Workspace, snapshot, p.ID, revision))
	if errors.Is(err, pgx.ErrNoRows) {
		var exists bool
		if c.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM `+productionSchema+`.contexts WHERE context_id=$1 AND workspace_id=$2)`, id, p.Workspace).Scan(&exists) != nil {
			productionUnavailable(w)
			return
		}
		if !exists {
			productionError(w, 404, "production_context_unavailable", "Production context unavailable")
			return
		}
		productionError(w, 409, "production_context_stale", "Production context revision changed")
		return
	}
	if err != nil {
		productionUnavailable(w)
		return
	}
	write(w, 200, contract.ProductionContextResponse{ContractVersion: stateVersion, Context: result})
}

func (s *stateServer) productionList(w http.ResponseWriter, ctx context.Context, c *pgx.Conn, p principal) {
	rows, err := c.Query(ctx, productionSelect()+` WHERE workspace_id=$1 ORDER BY updated_at DESC,context_id LIMIT 101`, p.Workspace)
	if err != nil {
		productionUnavailable(w)
		return
	}
	defer rows.Close()
	contexts := make([]contract.ProductionContextSummary, 0)
	more := false
	for rows.Next() {
		stored, err := scanProduction(rows)
		if err != nil {
			productionUnavailable(w)
			return
		}
		if len(contexts) == 100 {
			more = true
			break
		}
		contexts = append(contexts, contract.ProductionContextSummary{ContextId: stored.ContextId, WorkspaceId: stored.WorkspaceId, Revision: stored.Revision, SnapshotId: stored.SnapshotId, PlanVersion: stored.PlanVersion, AsOf: stored.AsOf, Metadata: stored.Metadata, CreatedBy: stored.CreatedBy, UpdatedBy: stored.UpdatedBy, CreatedAt: stored.CreatedAt, UpdatedAt: stored.UpdatedAt})
	}
	if rows.Err() != nil {
		productionUnavailable(w)
		return
	}
	write(w, 200, contract.ProductionContextsResponse{ContractVersion: stateVersion, Contexts: contexts, HasMore: more})
}
