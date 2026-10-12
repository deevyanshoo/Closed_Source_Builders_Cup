package main

// Local dispatch connector (#77): read-only /v2/workspaces/{workspace_id}/dispatch/... routes proxied to the private
// Python service's synthetic dispatch read models. Default off; enabled only by -dispatch-local-upstream with a
// loopback origin in local mode. Same session, workspace and error handling as the offers connector. The Python
// answer must be JSON labelled synthetic; it is passed through unchanged (decimal strings intact) inside a wrapper
// that names the workspace and the source.

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"mime"
	"net/http"
	"net/url"
	"regexp"
	"strings"
	"time"
)

const dispatchResponseLimit = 4 << 20

var (
	errDispatchLocalUnavailable = errors.New("local dispatch connector unavailable")
	dispatchIDPattern           = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$`)
	dispatchLocationPattern     = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$`)
	dispatchReasonPattern       = regexp.MustCompile(`^[a-z0-9_]{1,64}$`)
)

type dispatchAdapter struct {
	upstream string
	client   *http.Client
}

type dispatchRoute struct {
	upstreamPath string
	params       map[string]bool // name -> repeatable
}

func withDispatchLocalFlags(cfg stateConfig, args []string) (stateConfig, []string, error) {
	remaining := make([]string, 0, len(args))
	for i := 0; i < len(args); i++ {
		name, value, inline := strings.Cut(args[i], "=")
		if name != "-dispatch-local-upstream" {
			remaining = append(remaining, args[i])
			continue
		}
		if cfg.DispatchLocalUpstream != "" {
			return cfg, nil, errDispatchLocalUnavailable
		}
		if !inline {
			i++
			if i >= len(args) {
				return cfg, nil, errDispatchLocalUnavailable
			}
			value = args[i]
		}
		if _, ok := quoteLocalOrigin(value); !ok {
			return cfg, nil, errDispatchLocalUnavailable
		}
		cfg.DispatchLocalUpstream = value
	}
	if cfg.DispatchLocalUpstream != "" && cfg.Mode != "" && cfg.Mode != "local" {
		return cfg, nil, errDispatchLocalUnavailable
	}
	return cfg, remaining, nil
}

func initDispatchHTTP(cfg stateConfig) *dispatchAdapter {
	if cfg.DispatchLocalUpstream == "" || (cfg.Mode != "" && cfg.Mode != "local") {
		return nil
	}
	if _, ok := quoteLocalOrigin(cfg.DispatchLocalUpstream); !ok {
		return nil
	}
	return &dispatchAdapter{upstream: cfg.DispatchLocalUpstream, client: &http.Client{
		Timeout:       10 * time.Second,
		CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse },
	}}
}

func dispatchRouteCandidate(path string) bool {
	if !strings.HasPrefix(path, "/v2/workspaces/") {
		return false
	}
	parts := strings.Split(strings.TrimPrefix(path, "/v2/workspaces/"), "/")
	return len(parts) >= 2 && parts[1] == "dispatch"
}

// dispatchRouteFor maps the gateway path to its workspace and the fixed Python read model.
func dispatchRouteFor(path string) (string, dispatchRoute, bool) {
	parts := strings.Split(strings.TrimPrefix(path, "/v2/workspaces/"), "/")
	if len(parts) < 3 || parts[1] != "dispatch" || !dispatchIDPattern.MatchString(parts[0]) {
		return "", dispatchRoute{}, false
	}
	workspace, rest := parts[0], parts[2:]
	cutoff := map[string]bool{"as_of": false, "now": false}
	located := map[string]bool{"as_of": false, "now": false, "location": true}
	switch {
	case len(rest) == 1 && rest[0] == "shipments":
		return workspace, dispatchRoute{"/dispatch/shipments", cutoff}, true
	case len(rest) == 2 && rest[0] == "shipments" && dispatchIDPattern.MatchString(rest[1]):
		return workspace, dispatchRoute{"/dispatch/shipments/" + rest[1], cutoff}, true
	case len(rest) == 3 && rest[0] == "events" && rest[2] == "affected" && dispatchIDPattern.MatchString(rest[1]):
		return workspace, dispatchRoute{"/dispatch/events/" + rest[1] + "/affected", cutoff}, true
	case len(rest) == 1 && rest[0] == "route-evidence":
		return workspace, dispatchRoute{"/dispatch/route-evidence", located}, true
	case len(rest) == 2 && rest[0] == "replay" && rest[1] == "samples":
		return workspace, dispatchRoute{"/dispatch/replay/samples", map[string]bool{}}, true
	case len(rest) == 1 && rest[0] == "replay":
		return workspace, dispatchRoute{"/dispatch/replay", map[string]bool{"sample": false, "as_of": false, "now": false}}, true
	case len(rest) == 1 && rest[0] == "hazards":
		return workspace, dispatchRoute{"/dispatch/hazards", located}, true
	}
	return "", dispatchRoute{}, false
}

func dispatchQuery(raw string, route dispatchRoute) (string, bool) {
	values, err := url.ParseQuery(raw)
	if err != nil || len(raw) > 2048 {
		return "", false
	}
	for name, list := range values {
		repeatable, allowed := route.params[name]
		if !allowed || len(list) == 0 || (!repeatable && len(list) != 1) || len(list) > 16 {
			return "", false
		}
		for _, value := range list {
			switch {
			case name == "location" && !dispatchLocationPattern.MatchString(value):
				return "", false
			case name == "sample" && !dispatchIDPattern.MatchString(value):
				return "", false
			case (name == "as_of" || name == "now") && (value == "" || len(value) > 64 || strings.ContainsAny(value, "\x00\r\n")):
				return "", false
			}
		}
	}
	return values.Encode(), true
}

func dispatchUpstreamError(w http.ResponseWriter, status int, body map[string]any) {
	reasons := []string{}
	if list, ok := body["reasons"].([]any); ok {
		for _, item := range list {
			if text, ok := item.(string); ok && dispatchReasonPattern.MatchString(text) && len(reasons) < 8 {
				reasons = append(reasons, text)
			}
		}
	}
	detail := strings.Join(reasons, ", ")
	switch status {
	case 404:
		quoteError(w, 404, "not_found", strings.TrimSuffix("Dispatch resource not found: "+detail, ": "))
	case 400, 422:
		quoteError(w, 422, "invalid_request", strings.TrimSuffix("Invalid dispatch request: "+detail, ": "))
	case 503:
		quoteError(w, 503, "service_unavailable", "Synthetic dispatch data unavailable")
	default:
		quoteError(w, 502, "upstream_invalid_response", "Invalid dispatch response")
	}
}

func (s *stateServer) dispatchHTTPRoute(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Cache-Control", "no-store")
	if s.cfg.Mode == "private_machine" || s.dispatch == nil || s.dispatch.client == nil {
		quoteError(w, 503, "service_unavailable", "Dispatch workspace unavailable")
		return
	}
	if r.URL.EscapedPath() != r.URL.Path || strings.ContainsAny(r.URL.Path, "%?#") || strings.Contains(r.URL.Path, "..") {
		quoteError(w, 422, "invalid_request", "Invalid dispatch request")
		return
	}
	workspace, route, ok := dispatchRouteFor(r.URL.Path)
	if !ok {
		quoteError(w, 404, "not_found", "Resource not found")
		return
	}
	if r.Method != http.MethodGet {
		quoteError(w, 405, "method_not_allowed", "Method not allowed")
		return
	}
	query, ok := dispatchQuery(r.URL.RawQuery, route)
	if !ok || r.ContentLength > 0 || len(r.TransferEncoding) > 0 {
		quoteError(w, 422, "invalid_request", "Invalid dispatch request")
		return
	}
	if s.auth == nil {
		quoteError(w, 401, "unauthorized", "Valid identity required")
		return
	}
	p, ok := s.auth.Authenticate(w, r)
	if !ok {
		return
	}
	if p.ID == "" || p.Workspace == "" || (p.Role != "editor" && p.Role != "viewer") {
		quoteError(w, 403, "forbidden", "Dispatch workspace role unavailable")
		return
	}
	if p.Workspace != workspace {
		quoteError(w, 404, "not_found", "Resource not found")
		return
	}
	target := s.dispatch.upstream + route.upstreamPath
	if query != "" {
		target += "?" + query
	}
	ctx, cancel := context.WithTimeout(r.Context(), 10*time.Second)
	defer cancel()
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, target, nil)
	if err != nil {
		quoteError(w, 503, "service_unavailable", "Dispatch service unavailable")
		return
	}
	req.Header.Set("Accept", "application/json")
	res, err := s.dispatch.client.Do(req)
	if err != nil {
		quoteError(w, 503, "service_unavailable", "Dispatch service unavailable")
		return
	}
	defer res.Body.Close()
	media, _, typeErr := mime.ParseMediaType(res.Header.Get("Content-Type"))
	if res.StatusCode >= 300 && res.StatusCode < 400 || typeErr != nil || media != "application/json" {
		quoteError(w, 502, "upstream_invalid_response", "Invalid dispatch response")
		return
	}
	raw, err := io.ReadAll(io.LimitReader(res.Body, dispatchResponseLimit+1))
	if err != nil || len(raw) > dispatchResponseLimit {
		quoteError(w, 502, "upstream_invalid_response", "Invalid dispatch response")
		return
	}
	var body map[string]any
	if json.Unmarshal(raw, &body) != nil || body["synthetic"] != true {
		quoteError(w, 502, "upstream_invalid_response", "Invalid dispatch response")
		return
	}
	if res.StatusCode != http.StatusOK {
		dispatchUpstreamError(w, res.StatusCode, body)
		return
	}
	out, err := json.Marshal(struct {
		ContractVersion string          `json:"contract_version"`
		WorkspaceID     string          `json:"workspace_id"`
		Synthetic       bool            `json:"synthetic"`
		Source          string          `json:"source"`
		Data            json.RawMessage `json:"data"`
	}{stateVersion, workspace, true, "local_python_service", json.RawMessage(raw)})
	if err != nil {
		quoteError(w, 502, "upstream_invalid_response", "Invalid dispatch response")
		return
	}
	w.Header().Set("Content-Type", "application/json")
	_, _ = w.Write(out)
}
