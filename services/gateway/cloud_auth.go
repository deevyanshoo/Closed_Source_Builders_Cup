package main

import (
	"context"
	"errors"
	"net/http"
	"strings"
	"time"
)

type verifiedIdentity struct {
	Issuer, Audience, Subject, Email string
	EmailVerified                    bool
	IssuedAt, Expires                int64
}

// machineVerifier is only implemented in production by the Google SDK adapter.
// Tests inject a deterministic verifier to exercise mapping policy without
// issuing provider tokens.
type machineVerifier interface {
	Verify(context.Context, string, string) (verifiedIdentity, error)
}

type machineEntry struct {
	Subject     string `json:"subject"`
	Email       string `json:"email"`
	PrincipalID string `json:"principal_id"`
	WorkspaceID string `json:"workspace_id"`
	Role        string `json:"role"`
}

type machineFile struct {
	Principals []machineEntry `json:"principals"`
}

type machineAuthenticator struct {
	audience   string
	verifier   machineVerifier
	principals map[string]principal
}

func machineKey(subject, email string) string { return subject + "\x00" + email }

func serviceAccountEmail(email string) bool {
	if !strings.HasSuffix(email, ".gserviceaccount.com") || strings.ContainsAny(email, " \t\r\n") {
		return false
	}
	local := strings.TrimSuffix(email, ".gserviceaccount.com")
	return strings.Count(local, "@") == 1 && len(strings.SplitN(local, "@", 2)[0]) > 0 &&
		len(strings.SplitN(local, "@", 2)[1]) > 0
}

func newMachineAuthenticator(cfg stateConfig) (*machineAuthenticator, error) {
	if cfg.Verifier == nil || cfg.GatewayAudience == "" {
		return nil, errors.New("machine verifier unavailable")
	}
	b, err := restrictedFile(cfg.MachineIdentitiesFile)
	if err != nil {
		return nil, err
	}
	var f machineFile
	if err = decode(b, &f); err != nil || len(f.Principals) == 0 {
		return nil, errors.New("machine identities unavailable")
	}
	m := &machineAuthenticator{audience: cfg.GatewayAudience, verifier: cfg.Verifier, principals: map[string]principal{}}
	subjects := map[string]bool{}
	emails := map[string]bool{}
	principalIDs := map[string]bool{}
	for _, entry := range f.Principals {
		if entry.Subject == "" || !serviceAccountEmail(entry.Email) ||
			entry.PrincipalID == "" || entry.WorkspaceID == "" ||
			(entry.Role != "editor" && entry.Role != "viewer") ||
			subjects[entry.Subject] || emails[entry.Email] || principalIDs[entry.PrincipalID] {
			return nil, errors.New("invalid machine identities")
		}
		subjects[entry.Subject] = true
		emails[entry.Email] = true
		principalIDs[entry.PrincipalID] = true
		m.principals[machineKey(entry.Subject, entry.Email)] = principal{
			ID: entry.PrincipalID, Workspace: entry.WorkspaceID, Role: entry.Role,
		}
	}
	return m, nil
}

func (m *machineAuthenticator) Authenticate(w http.ResponseWriter, r *http.Request) (principal, bool) {
	for _, name := range []string{"X-Actor", "X-Role", "X-Principal-Id", "X-Workspace-Id"} {
		if len(r.Header.Values(name)) > 0 {
			stateError(w, 422, "invalid_request", "Client identity assertions are forbidden")
			return principal{}, false
		}
	}
	for _, name := range []string{"actor", "role", "principal_id", "workspace_id"} {
		if r.URL.Query().Has(name) {
			stateError(w, 422, "invalid_request", "Client identity assertions are forbidden")
			return principal{}, false
		}
	}
	if len(r.Header.Values("Origin")) > 0 {
		stateError(w, 403, "forbidden", "Browser origin is not allowed")
		return principal{}, false
	}
	values := r.Header.Values("Authorization")
	if len(values) != 1 || !strings.HasPrefix(values[0], "Bearer ") ||
		strings.TrimSpace(values[0]) != values[0] ||
		strings.ContainsAny(strings.TrimPrefix(values[0], "Bearer "), " \t\r\n") {
		stateError(w, 401, "unauthorized", "Valid machine identity required")
		return principal{}, false
	}
	token := strings.TrimPrefix(values[0], "Bearer ")
	if token == "" || len(token) > 16<<10 {
		stateError(w, 401, "unauthorized", "Valid machine identity required")
		return principal{}, false
	}
	ctx, cancel := context.WithTimeout(r.Context(), 4*time.Second)
	defer cancel()
	v, err := m.verifier.Verify(ctx, token, m.audience)
	now := time.Now().Unix()
	if err != nil || (v.Issuer != "https://accounts.google.com" && v.Issuer != "accounts.google.com") ||
		v.Audience != m.audience || v.Subject == "" || !serviceAccountEmail(v.Email) ||
		!v.EmailVerified || v.IssuedAt <= 0 || v.IssuedAt > now+30 ||
		v.Expires <= now || v.Expires <= v.IssuedAt {
		stateError(w, 401, "unauthorized", "Valid machine identity required")
		return principal{}, false
	}
	p, ok := m.principals[machineKey(v.Subject, v.Email)]
	if !ok {
		stateError(w, 401, "unauthorized", "Valid machine identity required")
		return principal{}, false
	}
	return p, true
}
