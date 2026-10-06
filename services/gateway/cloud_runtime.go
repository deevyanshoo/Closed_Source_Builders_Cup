package main

import (
	"errors"
	"net"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"strconv"
)

func serviceOrigin(raw string, scheme string) bool {
	u, err := url.Parse(raw)
	if err != nil || u.Scheme != scheme || u.Hostname() == "" || u.User != nil ||
		u.Path != "" || u.RawQuery != "" || u.Fragment != "" || u.Opaque != "" {
		return false
	}
	return u.String() == raw
}

func localOrigin(raw string) bool {
	if !serviceOrigin(raw, "http") {
		return false
	}
	u, _ := url.Parse(raw)
	ip := net.ParseIP(u.Hostname())
	return ip != nil && ip.IsLoopback()
}

// runtimeFromEnv only parses configuration; no credential lookup or provider call.
func runtimeFromEnv(getenv func(string) string) (stateConfig, string, error) {
	mode := getenv("GATEWAY_MODE")
	if mode == "" {
		mode = "local"
	}
	cfg := stateConfig{
		Mode: mode, DBURLFile: getenv("DATABASE_URL_FILE"),
		IdentitiesFile: getenv("DEV_IDENTITIES_FILE"), AllowedOrigin: getenv("DEV_ALLOWED_ORIGIN"),
		Upstream: getenv("PYTHON_SERVICE_URL"), Examples: getenv("CONTRACT_EXAMPLES_DIR"),
		GatewayAudience:       getenv("GATEWAY_AUDIENCE"),
		PythonAudience:        getenv("PYTHON_SERVICE_AUDIENCE"),
		MachineIdentitiesFile: getenv("MACHINE_IDENTITIES_FILE"),
		AssetsDir:             getenv("WEB_ASSETS_DIR"),
	}
	if cfg.Examples == "" {
		cfg.Examples = "../../contracts/examples"
	}
	if mode == "local" {
		if cfg.Upstream == "" {
			cfg.Upstream = "http://127.0.0.1:8016"
		}
		addr := getenv("GATEWAY_ADDR")
		if addr == "" {
			addr = "127.0.0.1:8816"
		}
		host, port, err := net.SplitHostPort(addr)
		n, parseErr := strconv.Atoi(port)
		if err != nil || parseErr != nil || n < 1 || n > 65535 ||
			net.ParseIP(host) == nil || !net.ParseIP(host).IsLoopback() ||
			!localOrigin(cfg.Upstream) {
			return stateConfig{}, "", errors.New("invalid local gateway configuration")
		}
		return cfg, addr, nil
	}
	if mode != "private_machine" || getenv("DEV_IDENTITIES_FILE") != "" ||
		getenv("DEV_ALLOWED_ORIGIN") != "" || getenv("GATEWAY_ADDR") != "" {
		return stateConfig{}, "", errors.New("invalid private gateway configuration")
	}
	port, err := strconv.Atoi(getenv("PORT"))
	if err != nil || port < 1 || port > 65535 ||
		!serviceOrigin(cfg.GatewayAudience, "https") ||
		!serviceOrigin(cfg.Upstream, "https") ||
		cfg.PythonAudience != cfg.Upstream ||
		cfg.DBURLFile == "" || cfg.MachineIdentitiesFile == "" || cfg.AssetsDir == "" {
		return stateConfig{}, "", errors.New("incomplete private gateway configuration")
	}
	return cfg, net.JoinHostPort("0.0.0.0", strconv.Itoa(port)), nil
}

// restrictedFile prevents a private-mode secret path from resolving through a
// symlink or being readable by group/other users.
func restrictedFile(path string) ([]byte, error) {
	if path == "" {
		return nil, errors.New("private file unavailable")
	}
	fi, err := os.Lstat(path)
	if err != nil || !fi.Mode().IsRegular() || fi.Mode().Perm()&0077 != 0 ||
		fi.Size() <= 0 || fi.Size() > 1<<20 {
		return nil, errors.New("private file unavailable")
	}
	return os.ReadFile(path)
}

func newPrivateStateHandler(cfg stateConfig) (http.Handler, error) {
	if cfg.Mode != "private_machine" || !serviceOrigin(cfg.GatewayAudience, "https") ||
		!serviceOrigin(cfg.Upstream, "https") || cfg.PythonAudience != cfg.Upstream ||
		cfg.IdentitiesFile != "" || cfg.AllowedOrigin != "" {
		return nil, errors.New("invalid private gateway configuration")
	}
	if _, err := restrictedFile(cfg.DBURLFile); err != nil {
		return nil, err
	}
	if cfg.Verifier == nil {
		var err error
		cfg.Verifier, err = newGoogleVerifier()
		if err != nil {
			return nil, errors.New("machine verifier unavailable")
		}
	}
	if cfg.OutboundTokenSource == nil {
		var err error
		cfg.OutboundTokenSource, err = newGoogleTokenSource(cfg.PythonAudience)
		if err != nil {
			return nil, errors.New("outbound identity unavailable")
		}
	}
	auth, err := newMachineAuthenticator(cfg)
	if err != nil {
		return nil, err
	}
	client, err := newPrivateOutboundClient(cfg.Upstream, cfg.OutboundTokenSource)
	if err != nil {
		return nil, err
	}
	assetsRoot, err := filepath.Abs(cfg.AssetsDir)
	if err != nil {
		return nil, errors.New("assets unavailable")
	}
	assets, err := newAssetsHandler(assetsRoot)
	if err != nil {
		return nil, err
	}
	s := newLocalStateServer(cfg)
	if s.db == nil {
		return nil, errors.New("database unavailable")
	}
	s.auth = auth
	s.identityReady = true
	s.outbound = client
	s.legacy = newHandlerWithClient(cfg.Upstream, cfg.Examples, client)
	s.assets = assets
	return s, nil
}
