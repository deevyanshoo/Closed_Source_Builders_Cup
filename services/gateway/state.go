package main

import (
	"bytes"
	"context"
	"crypto/rand"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"time"

	"closedsource/gateway/internal/contract"
	"github.com/jackc/pgx/v5"
)

const stateVersion = "0.2.0"

type stateConfig struct {
	ShipmentContextsPath, ShipmentPythonPath, ShipmentSourcePath            string
	VoicePythonPath, VoiceSourcePath                                        string
	QuoteLocalKeyFile, QuoteLocalUpstream                                   string
	DBURLFile, IdentitiesFile, AllowedOrigin, Upstream, Examples, Schema    string
	Mode, GatewayAudience, PythonAudience, MachineIdentitiesFile, AssetsDir string
	Verifier                                                                machineVerifier
	OutboundTokenSource                                                     outboundTokenSource
}
type principal struct{ ID, Workspace, Role string }

// Authenticator is the server-owned identity seam for persistent routes.
type Authenticator interface {
	Authenticate(http.ResponseWriter, *http.Request) (principal, bool)
}
type identity struct {
	Token       string `json:"token"`
	PrincipalID string `json:"principal_id"`
	WorkspaceID string `json:"workspace_id"`
	Role        string `json:"role"`
}
type identityFile struct {
	Identities []identity `json:"identities"`
}
type stateServer struct {
	shipment         *shipmentAdapter
	voice            *voiceHTTPAdapter
	quote            *quoteAdapter
	cfg              stateConfig
	db               *pgx.ConnConfig
	identities       []identity
	identityReady    bool
	legacy           http.Handler
	auth             Authenticator
	outbound         *http.Client
	assets           http.Handler
	quoteShipmentNow func() time.Time // test clock; nil uses the current server UTC time
}

func newStateHandler(cfg stateConfig) http.Handler {
	if cfg.Mode == "private_machine" {
		h, err := newPrivateStateHandler(cfg)
		if err != nil {
			return http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
				stateError(w, 503, "service_unavailable", "Gateway configuration unavailable")
			})
		}
		return h
	}
	if cfg.Mode != "" && cfg.Mode != "local" {
		return http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
			stateError(w, 503, "service_unavailable", "Gateway configuration unavailable")
		})
	}
	s := newLocalStateServer(cfg)
	if cfg.AssetsDir != "" {
		root, err := filepath.Abs(cfg.AssetsDir)
		if err == nil {
			s.assets, err = newAssetsHandler(root)
		}
		if err != nil {
			_ = s.Shutdown(context.Background())
			return http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
				stateError(w, 503, "service_unavailable", "Gateway configuration unavailable")
			})
		}
	}
	return s
}

func newLocalStateServer(cfg stateConfig) *stateServer {
	if cfg.Schema == "" {
		cfg.Schema = "cs_plan_actions"
	}
	if cfg.Upstream == "" {
		cfg.Upstream = "http://127.0.0.1:8016"
	}
	if cfg.Examples == "" {
		cfg.Examples = "../../contracts/examples"
	}
	client := &http.Client{Timeout: 8 * time.Second}
	s := &stateServer{cfg: cfg, outbound: client, legacy: newHandlerWithClient(cfg.Upstream, cfg.Examples, client)}
	s.auth = s
	var dbURL []byte
	var dbErr error
	if cfg.Mode == "private_machine" {
		dbURL, dbErr = restrictedFile(cfg.DBURLFile)
	} else {
		dbURL, dbErr = os.ReadFile(cfg.DBURLFile)
	}
	if b, e := dbURL, dbErr; e == nil && len(bytes.TrimSpace(b)) > 0 {
		if parsed, e := pgx.ParseConfig(string(bytes.TrimSpace(b))); e == nil {
			s.db = parsed
		}
	}
	if b, e := os.ReadFile(cfg.IdentitiesFile); e == nil {
		var f identityFile
		if decode(b, &f) == nil && len(f.Identities) > 0 {
			valid := true
			seen := map[string]bool{}
			for _, id := range f.Identities {
				if id.Token == "" || id.PrincipalID == "" || id.WorkspaceID == "" || (id.Role != "editor" && id.Role != "viewer") || seen[id.Token] {
					valid = false
					break
				}
				seen[id.Token] = true
			}
			if valid {
				s.identities = f.Identities
				s.identityReady = true
			}
		}
	}
	if !regexp.MustCompile(`^[a-z][a-z0-9_]*$`).MatchString(cfg.Schema) {
		s.db = nil
	}
	s.shipment = initShipmentHTTP(cfg)
	s.quote = initQuoteHTTP(cfg)
	s.voice = initVoiceHTTP(cfg)
	if s.voice != nil {
		s.voice.production = s
	}
	return s
}

func (s *stateServer) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	var machine principal
	if s.cfg.Mode == "private_machine" && r.URL.Path != "/health" {
		p, ok := s.auth.Authenticate(w, r)
		if !ok {
			return
		}
		machine = p
		r = r.WithContext(context.WithValue(r.Context(), machinePrincipalKey{}, p))
	}
	if quoteRouteCandidate(r.URL.Path) {
		s.quoteHTTPRoute(w, r)
		return
	}
	if quoteShipmentRouteCandidate(r.URL.Path) {
		s.quoteShipmentHTTPRoute(w, r)
		return
	}
	if shipmentRouteCandidate(r.URL.Path) {
		s.shipmentHTTPRoute(w, r)
		return
	}
	if r.URL.Path == "/v2/voice/sessions" || strings.HasPrefix(r.URL.Path, "/v2/voice/sessions/") || r.URL.Path == "/v2/voice/saved-context-sessions" {
		s.voiceHTTPRoute(w, r)
		return
	}
	if r.URL.Path == "/ready" {
		s.ready(w, r)
		return
	}
	if r.URL.Path == recoveryPath {
		s.recoveryComparison(w, r)
		return
	}
	if r.URL.Path == "/v2/plans/recommendations" {
		if s.cfg.Mode == "private_machine" && r.Method == "POST" {
			body, err := io.ReadAll(io.LimitReader(r.Body, maxBody+1))
			if err != nil {
				stateError(w, 422, "invalid_request", "Cannot read request")
				return
			}
			r.Body = io.NopCloser(bytes.NewReader(body))
			if q, code, _ := validate(body); code == "" && q.WorkspaceId != machine.Workspace {
				stateError(w, 403, "forbidden", "Workspace access denied")
				return
			}
		}
		s.legacy.ServeHTTP(w, r)
		return
	}
	if r.URL.Path == "/v2/session" || strings.HasPrefix(r.URL.Path, "/v2/workspaces/") || strings.HasPrefix(r.URL.Path, "/v2/production-contexts/") || strings.HasPrefix(r.URL.Path, "/v2/plans/") || r.URL.Path == "/v2/actions/prepare_rfq" {
		s.stateRoute(w, r)
		return
	}
	if s.assets != nil && !strings.HasPrefix(r.URL.Path, "/v2/") && r.URL.Path != "/health" {
		s.assets.ServeHTTP(w, r)
		return
	}
	s.legacy.ServeHTTP(w, r)
}

type machinePrincipalKey struct{}

func stateError(w http.ResponseWriter, status int, code, msg string) {
	write(w, status, contract.StateErrorResponse{ContractVersion: stateVersion, Error: contract.SemanticError{Code: code, Message: msg}})
}
func (s *stateServer) Authenticate(w http.ResponseWriter, r *http.Request) (principal, bool) {
	var p principal
	host, _, err := net.SplitHostPort(r.RemoteAddr)
	if err != nil || net.ParseIP(host) == nil || !net.ParseIP(host).IsLoopback() {
		stateError(w, 403, "forbidden", "Local development access only")
		return p, false
	}
	if origin := r.Header.Get("Origin"); origin != "" && (s.cfg.AllowedOrigin == "" || origin != s.cfg.AllowedOrigin) {
		stateError(w, 403, "forbidden", "Origin is not allowed")
		return p, false
	}
	for _, h := range []string{"X-Actor", "X-Role", "X-Principal-Id", "X-Workspace-Id"} {
		if r.Header.Get(h) != "" {
			stateError(w, 422, "invalid_request", "Client identity assertions are forbidden")
			return p, false
		}
	}
	a := r.Header.Get("Authorization")
	if !strings.HasPrefix(a, "Bearer ") || !s.identityReady {
		stateError(w, 401, "unauthorized", "Valid development identity required")
		return p, false
	}
	token := strings.TrimPrefix(a, "Bearer ")
	for _, id := range s.identities {
		if subtle.ConstantTimeCompare([]byte(token), []byte(id.Token)) == 1 {
			return principal{ID: id.PrincipalID, Workspace: id.WorkspaceID, Role: id.Role}, true
		}
	}
	stateError(w, 401, "unauthorized", "Valid development identity required")
	return p, false
}
func (s *stateServer) stateRoute(w http.ResponseWriter, r *http.Request) {
	production := strings.Contains(r.URL.Path, "/production-contexts")
	if production {
		w.Header().Set("Cache-Control", "no-store")
	}
	p, ok := r.Context().Value(machinePrincipalKey{}).(principal)
	if !ok {
		p, ok = s.auth.Authenticate(w, r)
		if !ok {
			return
		}
	}
	if r.URL.Path == "/v2/session" {
		if r.Method != "GET" {
			stateError(w, 405, "method_not_allowed", "Method not allowed")
			return
		}
		session := contract.SessionResponse{ContractVersion: stateVersion, PrincipalId: p.ID, WorkspaceId: p.Workspace, Role: p.Role, DevelopmentOnly: s.cfg.Mode != "private_machine"}
		if s.cfg.Mode == "private_machine" {
			mode := "private_machine"
			session.IdentityMode = &mode
		}
		write(w, 200, session)
		return
	}
	if strings.HasPrefix(r.URL.Path, "/v2/workspaces/") {
		parts := strings.Split(strings.Trim(r.URL.Path, "/"), "/")
		if len(parts) == 4 && parts[0] == "v2" && parts[1] == "workspaces" && parts[3] == "plans" && parts[2] != p.Workspace {
			stateError(w, 403, "forbidden", "Workspace access denied")
			return
		}
	}
	if s.db == nil {
		if production {
			productionUnavailable(w)
			return
		}
		stateError(w, 503, "database_unavailable", "Database is not configured")
		return
	}
	ctx, cancel := context.WithTimeout(r.Context(), 6*time.Second)
	defer cancel()
	conn, err := pgx.ConnectConfig(ctx, s.db)
	if err != nil {
		if production {
			productionUnavailable(w)
			return
		}
		stateError(w, 503, "database_unavailable", "Database unavailable")
		return
	}
	defer conn.Close(context.Background())
	if production {
		s.productionRoutes(w, r, ctx, conn, p)
		return
	}
	if r.URL.Path == "/v2/actions/prepare_rfq" {
		s.execute(w, r, ctx, conn, p)
		return
	}
	if strings.HasPrefix(r.URL.Path, "/v2/workspaces/") {
		parts := strings.Split(strings.Trim(r.URL.Path, "/"), "/")
		if len(parts) != 4 || parts[0] != "v2" || parts[1] != "workspaces" || parts[3] != "plans" {
			stateError(w, 404, "not_found", "Resource not found")
			return
		}
		if parts[2] != p.Workspace {
			stateError(w, 403, "forbidden", "Workspace access denied")
			return
		}
		s.plans(w, r, ctx, conn, p)
		return
	}
	parts := strings.Split(strings.Trim(r.URL.Path, "/"), "/")
	if len(parts) < 3 || len(parts) > 4 || parts[0] != "v2" || parts[1] != "plans" || parts[2] == "" {
		stateError(w, 404, "not_found", "Resource not found")
		return
	}
	id := parts[2]
	if len(parts) == 3 {
		s.plan(w, r, ctx, conn, p, id)
		return
	}
	switch parts[3] {
	case "approvals":
		s.approvals(w, r, ctx, conn, p, id)
	case "receipts":
		s.receipts(w, r, ctx, conn, p, id)
	case "audit":
		s.audit(w, r, ctx, conn, p, id)
	default:
		stateError(w, 404, "not_found", "Resource not found")
	}
}
func (s *stateServer) ready(w http.ResponseWriter, r *http.Request) {
	if r.Method != "GET" {
		stateError(w, 405, "method_not_allowed", "Method not allowed")
		return
	}
	database := "not_configured"
	if s.db != nil {
		database = "unavailable"
		ctx, cancel := context.WithTimeout(r.Context(), 2*time.Second)
		defer cancel()
		if conn, e := pgx.ConnectConfig(ctx, s.db); e == nil {
			var version int
			if conn.QueryRow(ctx, "SELECT version FROM "+s.cfg.Schema+".schema_migrations WHERE version=1").Scan(&version) == nil {
				database = "ready"
			}
			conn.Close(context.Background())
		}
	}
	identity := "unavailable"
	if s.identityReady {
		identity = "development"
		if s.cfg.Mode == "private_machine" {
			identity = "private_machine"
		}
	}
	status := 200
	if database != "ready" || identity == "unavailable" {
		status = 503
	}
	write(w, status, contract.ReadinessResponse{ContractVersion: stateVersion, Process: "ready", Database: database, Identity: identity, Provider: "not_verified"})
}
func stateBody(r *http.Request, v any) error {
	b, e := io.ReadAll(io.LimitReader(r.Body, maxBody+1))
	if e != nil {
		return e
	}
	if len(b) > maxBody {
		return errors.New("body too large")
	}
	// Preserve required and null semantics before JSON zero-values can turn a
	// missing lead time into a valid-looking immediate-arrival request.
	switch v.(type) {
	case *contract.CreateSavedPlanRequest, *contract.ReviseSavedPlanRequest:
		var envelope map[string]json.RawMessage
		if e := decode(b, &envelope); e != nil || !required(envelope, "contract_version", "planning_request") {
			return errors.New("invalid plan envelope")
		}
		if _, code, msg := validate(envelope["planning_request"]); code != "" {
			return errors.New(msg)
		}
	}
	return decode(b, v)
}
func newID() string {
	var b [16]byte
	if _, e := rand.Read(b[:]); e != nil {
		panic(e)
	}
	return hex.EncodeToString(b[:])
}
func now() string          { return time.Now().UTC().Format(time.RFC3339Nano) }
func marshal(v any) []byte { b, _ := json.Marshal(v); return b }
func (s *stateServer) computedSnapshot(ctx context.Context, q contract.PlanningRequest) (contract.PlanResponse, int, string) {
	b := marshal(q)
	w := httptest.NewRecorder()
	r := httptest.NewRequest("POST", "/v2/plans/recommendations", bytes.NewReader(b)).WithContext(ctx)
	recommend(w, r, s.outbound, s.cfg.Upstream)
	if w.Code != 200 {
		if w.Code == 422 {
			return contract.PlanResponse{}, 422, "Invalid planning request"
		}
		return contract.PlanResponse{}, 503, "Recommendation unavailable"
	}
	var snapshot contract.PlanResponse
	if decode(w.Body.Bytes(), &snapshot) != nil || snapshot.Status != "ready" || snapshot.Plan == nil || snapshot.Plan.PlanVersion == "" || snapshot.Request.WorkspaceId != q.WorkspaceId {
		return contract.PlanResponse{}, 422, "Only a ready computed recommendation can be saved"
	}
	return snapshot, 0, ""
}
func validPlanning(q contract.PlanningRequest, workspace string) bool {
	return q.WorkspaceId == workspace && q.ContractVersion == version && q.Buyer.Id != ""
}
func validArguments(a contract.RfqDraftArguments, q contract.PlanningRequest) bool {
	return a.BuyerId == q.Buyer.Id && a.Material == q.Material && a.Quantity == q.Quantity && a.DestinationCountry == q.Buyer.DestinationCountry && a.QuoteCurrency == q.QuoteCurrency && a.SettlementCurrency == q.SettlementCurrency && a.ReportingCurrency == q.ReportingCurrency
}
func editor(w http.ResponseWriter, p principal) bool {
	if p.Role != "editor" {
		stateError(w, 403, "forbidden", "Editor role required")
		return false
	}
	return true
}
func dbFailure(w http.ResponseWriter) {
	stateError(w, 503, "database_unavailable", "Database operation failed")
}
func notFound(w http.ResponseWriter)             { stateError(w, 404, "not_found", "Resource not found") }
func conflict(w http.ResponseWriter, msg string) { stateError(w, 409, "conflict", msg) }
func invalid(w http.ResponseWriter, msg string)  { stateError(w, 422, "invalid_request", msg) }
func hashRequest(v any) string                   { h := sha256.Sum256(marshal(v)); return hex.EncodeToString(h[:]) }
