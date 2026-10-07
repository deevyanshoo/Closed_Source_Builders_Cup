package main

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"sync"
	"testing"
	"time"

	"closedsource/gateway/internal/contract"
	"github.com/jackc/pgx/v5"
)

func TestProductionContextStrictRevisionAndNoNumericQuantity(t *testing.T) {
	snapshot := map[string]any{}
	if err := json.Unmarshal(voiceFixture(t, "voice_http_recovery_session.json"), &snapshot); err != nil {
		t.Fatal(err)
	}
	body := map[string]any{"contract_version": stateVersion, "expected_revision": 1, "snapshot": snapshot["snapshot"]}
	raw, _ := json.Marshal(body)
	value, ok := voiceParse(raw, 0)
	if !ok || !voiceValidName(value, "ReplaceProductionContextRequest") {
		t.Fatal("valid integer revision and exact string quantities rejected")
	}
	for _, revision := range []string{"0", "2147483648", "1.0", "1e0", "-1"} {
		encoded := []byte(`{"contract_version":"0.2.0","expected_revision":` + revision + `,"snapshot":` + string(mustJSON(t, snapshot["snapshot"])) + `}`)
		parsed, valid := voiceParse(encoded, 0)
		if valid && voiceValidName(parsed, "ReplaceProductionContextRequest") {
			t.Fatalf("invalid revision %s accepted", revision)
		}
	}
}

func mustJSON(t *testing.T, value any) []byte {
	t.Helper()
	b, err := json.Marshal(value)
	if err != nil {
		t.Fatal(err)
	}
	return b
}

func TestProductionContextRoutesRequireHostIdentity(t *testing.T) {
	h := newStateHandler(stateConfig{})
	for _, path := range []string{"/v2/workspaces/W/production-contexts", "/v2/production-contexts/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "/v2/voice/saved-context-sessions"} {
		response := stateCallRaw(h, http.MethodGet, path, "", nil)
		if response.Code != http.StatusUnauthorized {
			t.Fatalf("%s status %d, want 401", path, response.Code)
		}
	}
}

func productionCall(h http.Handler, method, path, token string, body any) *httptest.ResponseRecorder {
	var raw []byte
	if body != nil {
		raw, _ = json.Marshal(body)
	}
	r := httptest.NewRequest(method, path, bytes.NewReader(raw))
	r.RemoteAddr = "127.0.0.1:12345"
	if token != "" {
		r.Header.Set("Authorization", "Bearer "+token)
	}
	if body != nil {
		r.Header.Set("Content-Type", "application/json")
	}
	w := httptest.NewRecorder()
	h.ServeHTTP(w, r)
	return w
}

func productionTestHandler(t *testing.T) (http.Handler, *pgx.Conn) {
	t.Helper()
	path := os.Getenv("PRODUCTION_CONTEXT_TEST_DATABASE_URL_FILE")
	if path == "" {
		t.Skip("task-owned production context database URL file unset")
	}
	url, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	conn, err := pgx.Connect(context.Background(), string(bytes.TrimSpace(url)))
	if err != nil {
		t.Fatal("task-owned database unavailable")
	}
	t.Cleanup(func() { _ = conn.Close(context.Background()) })
	identities := filepath.Join(t.TempDir(), "identities.json")
	data := []byte(`{"identities":[{"token":"editor","principal_id":"editor","workspace_id":"W","role":"editor"},{"token":"viewer","principal_id":"viewer","workspace_id":"W","role":"viewer"},{"token":"foreign","principal_id":"foreign","workspace_id":"Other","role":"editor"}]}`)
	if err := os.WriteFile(identities, data, 0600); err != nil {
		t.Fatal(err)
	}
	h := newStateHandler(stateConfig{DBURLFile: path, IdentitiesFile: identities})
	return h, conn
}

func TestProductionContextPersistReplaceAndWorkspace(t *testing.T) {
	h, db := productionTestHandler(t)
	var fixture map[string]any
	if err := json.Unmarshal(voiceFixture(t, "voice_http_recovery_session.json"), &fixture); err != nil {
		t.Fatal(err)
	}
	request := map[string]any{"contract_version": stateVersion, "snapshot": fixture["snapshot"]}
	path := "/v2/workspaces/W/production-contexts"
	var foreignSnapshot map[string]any
	if err := json.Unmarshal(mustJSON(t, fixture["snapshot"]), &foreignSnapshot); err != nil {
		t.Fatal(err)
	}
	foreignSnapshot["workspace_id"] = "Other"
	if response := productionCall(h, "POST", path, "editor", map[string]any{"contract_version": stateVersion, "snapshot": foreignSnapshot}); response.Code != 403 {
		t.Fatalf("foreign snapshot write %d", response.Code)
	}
	if response := productionCall(h, "POST", path, "viewer", request); response.Code != 403 {
		t.Fatalf("viewer write %d", response.Code)
	}
	created := productionCall(h, "POST", path, "editor", request)
	if created.Code != 201 {
		t.Fatalf("create %d %s", created.Code, created.Body.String())
	}
	var stored contract.ProductionContextResponse
	if err := json.Unmarshal(created.Body.Bytes(), &stored); err != nil {
		t.Fatal(err)
	}
	id := stored.Context.ContextId
	t.Cleanup(func() {
		_, _ = db.Exec(context.Background(), `DELETE FROM cs_production_contexts.contexts WHERE context_id=$1`, id)
	})
	if stored.Context.Revision != 1 || stored.Context.Snapshot.SnapshotId != "production-"+id+"-r1" || stored.Context.Snapshot.PlanVersion != "production-plan-"+id+"-r1" || !stored.Context.Metadata.Synthetic || stored.Context.Metadata.NumericalAcceptance != "not_claimed" {
		t.Fatalf("server aliases/metadata not authoritative: %+v", stored.Context)
	}
	read := productionCall(h, "GET", "/v2/production-contexts/"+id, "viewer", nil)
	if read.Code != 200 {
		t.Fatalf("viewer read %d", read.Code)
	}
	if foreign := productionCall(h, "GET", "/v2/production-contexts/"+id, "foreign", nil); foreign.Code != 404 {
		t.Fatalf("foreign read %d", foreign.Code)
	}
	var snapshot map[string]any
	if err := json.Unmarshal(mustJSON(t, fixture["snapshot"]), &snapshot); err != nil {
		t.Fatal(err)
	}
	inventory := snapshot["inventory"].([]any)[0].(map[string]any)
	inventory["starting_stock"] = "123456789.123456789123456789"
	replacement := map[string]any{"contract_version": stateVersion, "expected_revision": 1, "snapshot": snapshot}
	updated := productionCall(h, "PUT", "/v2/production-contexts/"+id, "editor", replacement)
	if updated.Code != 200 {
		t.Fatalf("replace %d %s", updated.Code, updated.Body.String())
	}
	var after contract.ProductionContextResponse
	if err := json.Unmarshal(updated.Body.Bytes(), &after); err != nil {
		t.Fatal(err)
	}
	if after.Context.Revision != 2 || after.Context.Snapshot.Inventory[0].StartingStock != "123456789.123456789123456789" {
		t.Fatalf("revision/decimal drift: %+v", after.Context)
	}
	if stale := productionCall(h, "PUT", "/v2/production-contexts/"+id, "editor", replacement); stale.Code != 409 {
		t.Fatalf("stale replace %d", stale.Code)
	}
	if listed := productionCall(h, "GET", path, "viewer", nil); listed.Code != 200 || !bytes.Contains(listed.Body.Bytes(), []byte(id)) {
		t.Fatalf("list %d %s", listed.Code, listed.Body.String())
	}
	if reread := productionCall(newStateHandler(stateConfig{DBURLFile: os.Getenv("PRODUCTION_CONTEXT_TEST_DATABASE_URL_FILE"), IdentitiesFile: h.(*stateServer).cfg.IdentitiesFile}), "GET", "/v2/production-contexts/"+id, "viewer", nil); reread.Code != 200 {
		t.Fatalf("restart read %d", reread.Code)
	}
}

func TestProductionContextVoiceReferenceRevokesStaleTurnButAllowsCleanup(t *testing.T) {
	h, db := productionTestHandler(t)
	server := h.(*stateServer)
	adapter := voiceTestAdapter(t, voiceExecutorFunc(func(context.Context, []byte) ([]byte, error) { return nil, nil }))
	server.voice = adapter
	adapter.production = server
	var fixture map[string]any
	if err := json.Unmarshal(voiceFixture(t, "voice_http_recovery_session.json"), &fixture); err != nil {
		t.Fatal(err)
	}
	created := productionCall(h, "POST", "/v2/workspaces/W/production-contexts", "editor", map[string]any{"contract_version": stateVersion, "snapshot": fixture["snapshot"]})
	if created.Code != 201 {
		t.Fatalf("context create %d %s", created.Code, created.Body.String())
	}
	var stored contract.ProductionContextResponse
	if err := json.Unmarshal(created.Body.Bytes(), &stored); err != nil {
		t.Fatal(err)
	}
	id := stored.Context.ContextId
	t.Cleanup(func() {
		_, _ = db.Exec(context.Background(), `DELETE FROM cs_production_contexts.contexts WHERE context_id=$1`, id)
	})
	request := map[string]any{"contract_version": stateVersion, "context_id": id, "expected_revision": 1}
	if forbidden := productionCall(h, "POST", savedVoicePath, "foreign", request); forbidden.Code != 404 {
		t.Fatalf("foreign ref %d", forbidden.Code)
	}
	withSnapshot := map[string]any{"contract_version": stateVersion, "context_id": id, "expected_revision": 1, "snapshot": fixture["snapshot"]}
	if override := productionCall(h, "POST", savedVoicePath, "viewer", withSnapshot); override.Code != 422 {
		t.Fatalf("snapshot override %d", override.Code)
	}
	first := productionCall(h, "POST", savedVoicePath, "viewer", request)
	if first.Code != 201 {
		t.Fatalf("saved session %d %s", first.Code, first.Body.String())
	}
	var session map[string]any
	if err := json.Unmarshal(first.Body.Bytes(), &session); err != nil {
		t.Fatal(err)
	}
	if session["production_context_ref"] == nil {
		t.Fatal("missing saved ref")
	}
	second := productionCall(h, "POST", savedVoicePath, "viewer", request)
	if second.Code != 201 {
		t.Fatalf("second saved session %d", second.Code)
	}
	var unstarted map[string]any
	if err := json.Unmarshal(second.Body.Bytes(), &unstarted); err != nil {
		t.Fatal(err)
	}
	sessionID := session["session_id"].(string)
	turn := productionCall(h, "POST", voicePath+"/"+sessionID+"/turns", "viewer", json.RawMessage(voiceTurnBody(t, session["context_version"].(string))))
	if turn.Code != 202 {
		t.Fatalf("turn %d %s", turn.Code, turn.Body.String())
	}
	var accepted map[string]any
	if err := json.Unmarshal(turn.Body.Bytes(), &accepted); err != nil {
		t.Fatal(err)
	}
	if accepted["production_context_ref"] == nil {
		t.Fatal("turn omitted saved ref")
	}
	replaced := productionCall(h, "PUT", "/v2/production-contexts/"+id, "editor", map[string]any{"contract_version": stateVersion, "expected_revision": 1, "snapshot": fixture["snapshot"]})
	if replaced.Code != 200 {
		t.Fatalf("replace %d %s", replaced.Code, replaced.Body.String())
	}
	resultPath := voicePath + "/" + sessionID + "/turns/" + accepted["turn_id"].(string) + "/result"
	if stale := productionCall(h, "GET", resultPath, "viewer", nil); stale.Code != 409 || bytes.Contains(stale.Body.Bytes(), []byte(`"outcome"`)) {
		t.Fatalf("stale result %d %s", stale.Code, stale.Body.String())
	}
	if stale := productionCall(h, "GET", resultPath, "viewer", nil); stale.Code != 409 || bytes.Contains(stale.Body.Bytes(), []byte(`"outcome"`)) {
		t.Fatalf("repeated stale result %d %s", stale.Code, stale.Body.String())
	}
	otherID := unstarted["session_id"].(string)
	if stale := productionCall(h, "POST", voicePath+"/"+otherID+"/turns", "viewer", json.RawMessage(voiceTurnBody(t, unstarted["context_version"].(string)))); stale.Code != 409 {
		t.Fatalf("stale start %d %s", stale.Code, stale.Body.String())
	}
	if cancel := productionCall(h, "POST", voicePath+"/"+sessionID+"/turns/"+accepted["turn_id"].(string)+"/cancel", "viewer", nil); cancel.Code != 200 {
		t.Fatalf("stale cancel %d %s", cancel.Code, cancel.Body.String())
	}
	for _, sid := range []string{sessionID, otherID} {
		if closed := productionCall(h, "DELETE", voicePath+"/"+sid, "viewer", nil); closed.Code != 200 {
			t.Fatalf("stale close %d %s", closed.Code, closed.Body.String())
		}
	}
}

func TestProductionContextConcurrentReplaceSingleWinner(t *testing.T) {
	h, db := productionTestHandler(t)
	var fixture map[string]any
	if err := json.Unmarshal(voiceFixture(t, "voice_http_recovery_session.json"), &fixture); err != nil {
		t.Fatal(err)
	}
	created := productionCall(h, "POST", "/v2/workspaces/W/production-contexts", "editor", map[string]any{"contract_version": stateVersion, "snapshot": fixture["snapshot"]})
	if created.Code != 201 {
		t.Fatalf("create %d", created.Code)
	}
	var stored contract.ProductionContextResponse
	if err := json.Unmarshal(created.Body.Bytes(), &stored); err != nil {
		t.Fatal(err)
	}
	id := stored.Context.ContextId
	t.Cleanup(func() {
		_, _ = db.Exec(context.Background(), `DELETE FROM cs_production_contexts.contexts WHERE context_id=$1`, id)
	})
	request := map[string]any{"contract_version": stateVersion, "expected_revision": 1, "snapshot": fixture["snapshot"]}
	start := make(chan struct{})
	statuses := make(chan int, 2)
	var wg sync.WaitGroup
	for range 2 {
		wg.Add(1)
		go func() {
			defer wg.Done()
			<-start
			statuses <- productionCall(h, "PUT", "/v2/production-contexts/"+id, "editor", request).Code
		}()
	}
	close(start)
	wg.Wait()
	close(statuses)
	winners := 0
	conflicts := 0
	for status := range statuses {
		if status == 200 {
			winners++
		} else if status == 409 {
			conflicts++
		} else {
			t.Fatalf("unexpected replacement status %d", status)
		}
	}
	if winners != 1 || conflicts != 1 {
		t.Fatalf("winners %d conflicts %d", winners, conflicts)
	}
	read := productionCall(h, "GET", "/v2/production-contexts/"+id, "viewer", nil)
	if read.Code != 200 {
		t.Fatalf("read %d", read.Code)
	}
	var after contract.ProductionContextResponse
	if err := json.Unmarshal(read.Body.Bytes(), &after); err != nil || after.Context.Revision != 2 {
		t.Fatalf("final revision %d err %v", after.Context.Revision, err)
	}
}

func TestProductionContextGuardDoesNotBlockVoiceCleanup(t *testing.T) {
	h, db := productionTestHandler(t)
	server := h.(*stateServer)
	adapter := voiceTestAdapter(t, voiceExecutorFunc(func(context.Context, []byte) ([]byte, error) { return nil, nil }))
	server.voice = adapter
	adapter.production = server
	var fixture map[string]any
	if err := json.Unmarshal(voiceFixture(t, "voice_http_recovery_session.json"), &fixture); err != nil {
		t.Fatal(err)
	}
	created := productionCall(h, "POST", "/v2/workspaces/W/production-contexts", "editor", map[string]any{"contract_version": stateVersion, "snapshot": fixture["snapshot"]})
	if created.Code != 201 {
		t.Fatalf("create %d", created.Code)
	}
	var stored contract.ProductionContextResponse
	if err := json.Unmarshal(created.Body.Bytes(), &stored); err != nil {
		t.Fatal(err)
	}
	id := stored.Context.ContextId
	t.Cleanup(func() {
		_, _ = db.Exec(context.Background(), `DELETE FROM cs_production_contexts.contexts WHERE context_id=$1`, id)
	})
	createdSession := productionCall(h, "POST", savedVoicePath, "viewer", map[string]any{"contract_version": stateVersion, "context_id": id, "expected_revision": 1})
	if createdSession.Code != 201 {
		t.Fatalf("session %d", createdSession.Code)
	}
	var session map[string]any
	if err := json.Unmarshal(createdSession.Body.Bytes(), &session); err != nil {
		t.Fatal(err)
	}
	ref := contract.ProductionContextRef{ContextId: id, WorkspaceId: "W", Revision: 1}
	guard, _, status := server.guardProduction(context.Background(), principal{ID: "viewer", Workspace: "W", Role: "viewer"}, ref)
	if status != 0 {
		t.Fatalf("guard status %d", status)
	}
	defer guard.rollback()
	closed := make(chan int, 1)
	go func() {
		closed <- productionCall(h, "DELETE", voicePath+"/"+session["session_id"].(string), "viewer", nil).Code
	}()
	select {
	case code := <-closed:
		if code != 200 {
			t.Fatalf("close under guard %d", code)
		}
	case <-time.After(time.Second):
		t.Fatal("voice cleanup blocked by production row guard")
	}
}

func TestProductionContextBlockedGuardDoesNotHoldVoiceMutex(t *testing.T) {
	h, db := productionTestHandler(t)
	server := h.(*stateServer)
	adapter := voiceTestAdapter(t, voiceExecutorFunc(func(context.Context, []byte) ([]byte, error) { return nil, nil }))
	server.voice = adapter
	adapter.production = server
	var fixture map[string]any
	if err := json.Unmarshal(voiceFixture(t, "voice_http_recovery_session.json"), &fixture); err != nil {
		t.Fatal(err)
	}
	created := productionCall(h, "POST", "/v2/workspaces/W/production-contexts", "editor", map[string]any{"contract_version": stateVersion, "snapshot": fixture["snapshot"]})
	if created.Code != 201 {
		t.Fatalf("create %d", created.Code)
	}
	var stored contract.ProductionContextResponse
	if err := json.Unmarshal(created.Body.Bytes(), &stored); err != nil {
		t.Fatal(err)
	}
	id := stored.Context.ContextId
	t.Cleanup(func() {
		_, _ = db.Exec(context.Background(), `DELETE FROM cs_production_contexts.contexts WHERE context_id=$1`, id)
	})
	createdSession := productionCall(h, "POST", savedVoicePath, "viewer", map[string]any{"contract_version": stateVersion, "context_id": id, "expected_revision": 1})
	if createdSession.Code != 201 {
		t.Fatalf("session %d", createdSession.Code)
	}
	var session map[string]any
	if err := json.Unmarshal(createdSession.Body.Bytes(), &session); err != nil {
		t.Fatal(err)
	}
	lock, err := db.Begin(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	defer lock.Rollback(context.Background())
	if _, err := lock.Exec(context.Background(), `UPDATE cs_production_contexts.contexts SET updated_at=updated_at WHERE context_id=$1`, id); err != nil {
		t.Fatal(err)
	}
	turnBody := json.RawMessage(voiceTurnBody(t, session["context_version"].(string)))
	turn := make(chan int, 1)
	go func() {
		turn <- productionCall(h, "POST", voicePath+"/"+session["session_id"].(string)+"/turns", "viewer", turnBody).Code
	}()
	select {
	case code := <-turn:
		t.Fatalf("start unexpectedly passed blocked guard: %d", code)
	case <-time.After(100 * time.Millisecond):
	}
	closed := make(chan int, 1)
	go func() {
		closed <- productionCall(h, "DELETE", voicePath+"/"+session["session_id"].(string), "viewer", nil).Code
	}()
	select {
	case code := <-closed:
		if code != 200 {
			t.Fatalf("cleanup while guard blocked %d", code)
		}
	case <-time.After(time.Second):
		t.Fatal("blocked database guard held voice mutex")
	}
	if err := lock.Commit(context.Background()); err != nil {
		t.Fatal(err)
	}
	select {
	case code := <-turn:
		if code != 404 {
			t.Fatalf("closed session start %d", code)
		}
	case <-time.After(time.Second):
		t.Fatal("start did not finish after row lock release")
	}
}

func TestProductionContextCorruptStoredGraphFailsClosed(t *testing.T) {
	h, db := productionTestHandler(t)
	server := h.(*stateServer)
	adapter := voiceTestAdapter(t, voiceExecutorFunc(func(context.Context, []byte) ([]byte, error) { return nil, nil }))
	server.voice = adapter
	adapter.production = server
	var fixture map[string]any
	if err := json.Unmarshal(voiceFixture(t, "voice_http_recovery_session.json"), &fixture); err != nil {
		t.Fatal(err)
	}
	created := productionCall(h, "POST", "/v2/workspaces/W/production-contexts", "editor", map[string]any{"contract_version": stateVersion, "snapshot": fixture["snapshot"]})
	if created.Code != 201 {
		t.Fatalf("create %d", created.Code)
	}
	var stored contract.ProductionContextResponse
	if err := json.Unmarshal(created.Body.Bytes(), &stored); err != nil {
		t.Fatal(err)
	}
	id := stored.Context.ContextId
	t.Cleanup(func() {
		_, _ = db.Exec(context.Background(), `DELETE FROM cs_production_contexts.contexts WHERE context_id=$1`, id)
	})
	if _, err := db.Exec(context.Background(), `UPDATE cs_production_contexts.contexts SET snapshot=snapshot || '{"unexpected_input":true}'::jsonb WHERE context_id=$1`, id); err != nil {
		t.Fatal(err)
	}
	if read := productionCall(h, "GET", "/v2/production-contexts/"+id, "viewer", nil); read.Code != 503 {
		t.Fatalf("corrupt read %d %s", read.Code, read.Body.String())
	}
	if list := productionCall(h, "GET", "/v2/workspaces/W/production-contexts", "viewer", nil); list.Code != 503 {
		t.Fatalf("corrupt list %d %s", list.Code, list.Body.String())
	}
	if session := productionCall(h, "POST", savedVoicePath, "viewer", map[string]any{"contract_version": stateVersion, "context_id": id, "expected_revision": 1}); session.Code != 503 {
		t.Fatalf("corrupt saved session %d %s", session.Code, session.Body.String())
	}
}
