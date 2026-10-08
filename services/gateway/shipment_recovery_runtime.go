package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"
)

const shipmentModule = "procure.production.shipment_local_bridge"

type shipmentLocalExecutor struct{ python, source string }

type shipmentLimitWriter struct {
	buffer   bytes.Buffer
	limit    int
	overflow bool
	kill     func()
}

func (w *shipmentLimitWriter) Write(p []byte) (int, error) {
	if len(p) > w.limit-w.buffer.Len() {
		w.overflow = true
		if w.kill != nil {
			w.kill()
		}
		return 0, errors.New("shipment child output overflow")
	}
	return w.buffer.Write(p)
}

func newShipmentLocalExecutor(python, source string) (*shipmentLocalExecutor, error) {
	if !filepath.IsAbs(python) || !filepath.IsAbs(source) || strings.ContainsRune(python, 0) || strings.ContainsRune(source, 0) {
		return nil, errShipmentUnavailable
	}
	file, err := os.Stat(python)
	if err != nil || !file.Mode().IsRegular() || file.Mode()&0111 == 0 {
		return nil, errShipmentUnavailable
	}
	dir, err := os.Stat(source)
	if err != nil || !dir.IsDir() {
		return nil, errShipmentUnavailable
	}
	return &shipmentLocalExecutor{python: python, source: source}, nil
}

func (e *shipmentLocalExecutor) Execute(ctx context.Context, input []byte) ([]byte, error) {
	if e == nil || len(input) > (4<<20)+(32<<10) {
		return nil, errShipmentUnavailable
	}
	cmd := exec.CommandContext(ctx, e.python, "-m", shipmentModule)
	cmd.Dir = e.source
	cmd.Env = []string{"PATH=/usr/bin:/bin", "PYTHONPATH=" + e.source, "PYTHONDONTWRITEBYTECODE=1", "PYTHONNOUSERSITE=1"}
	cmd.Stdin = bytes.NewReader(input)
	cmd.WaitDelay = time.Second
	out := &shipmentLimitWriter{limit: shipmentWireLimit}
	stderr := &shipmentLimitWriter{limit: 64 << 10}
	kill := func() {
		if cmd.Process != nil {
			_ = cmd.Process.Kill()
		}
	}
	out.kill, stderr.kill = kill, kill
	cmd.Stdout, cmd.Stderr = out, stderr
	if err := cmd.Start(); err != nil {
		return nil, err
	}
	waitErr := cmd.Wait()
	if ctx.Err() != nil {
		return nil, ctx.Err()
	}
	if out.overflow || stderr.overflow {
		return nil, errShipmentInvalidOutput
	}
	if waitErr != nil {
		return nil, errShipmentUnavailable
	}
	return append([]byte(nil), out.buffer.Bytes()...), nil
}

func withShipmentLocalFlags(cfg stateConfig, args []string) (stateConfig, []string, error) {
	remaining := make([]string, 0, len(args))
	seen := map[string]bool{}
	for i := 0; i < len(args); i++ {
		arg := args[i]
		name, value, inline := strings.Cut(arg, "=")
		switch name {
		case "-shipment-local-contexts", "-shipment-local-python", "-shipment-local-source":
			if seen[name] {
				return cfg, nil, errors.New("duplicate shipment local flag")
			}
			seen[name] = true
			if !inline {
				i++
				if i >= len(args) {
					return cfg, nil, errors.New("missing shipment local path")
				}
				value = args[i]
			}
			if value == "" || strings.HasPrefix(value, "-") || !filepath.IsAbs(value) || strings.ContainsRune(value, 0) {
				return cfg, nil, errors.New("unsafe shipment local path")
			}
			switch name {
			case "-shipment-local-contexts":
				cfg.ShipmentContextsPath = value
			case "-shipment-local-python":
				cfg.ShipmentPythonPath = value
			case "-shipment-local-source":
				cfg.ShipmentSourcePath = value
			}
		default:
			remaining = append(remaining, arg)
		}
	}
	if cfg.ShipmentContextsPath != "" || cfg.ShipmentPythonPath != "" || cfg.ShipmentSourcePath != "" {
		if cfg.Mode == "private_machine" || cfg.ShipmentContextsPath == "" || cfg.ShipmentPythonPath == "" || cfg.ShipmentSourcePath == "" {
			return cfg, nil, errors.New("incomplete shipment local configuration")
		}
		for _, p := range []string{cfg.ShipmentContextsPath, cfg.ShipmentPythonPath, cfg.ShipmentSourcePath} {
			if !filepath.IsAbs(p) || strings.ContainsRune(p, 0) {
				return cfg, nil, errors.New("unsafe shipment local configuration")
			}
		}
	}
	return cfg, remaining, nil
}

func initShipmentHTTP(cfg stateConfig) *shipmentAdapter {
	if cfg.Mode == "private_machine" || cfg.ShipmentContextsPath == "" || cfg.ShipmentPythonPath == "" || cfg.ShipmentSourcePath == "" {
		return nil
	}
	executor, err := newShipmentLocalExecutor(cfg.ShipmentPythonPath, cfg.ShipmentSourcePath)
	if err != nil {
		return nil
	}
	adapter := newShipmentAdapter(executor)
	file, err := os.Open(cfg.ShipmentContextsPath)
	if err != nil {
		return nil
	}
	defer file.Close()
	stat, err := file.Stat()
	if err != nil || !stat.Mode().IsRegular() || stat.Size() > shipmentCatalogLimit {
		return nil
	}
	data, err := io.ReadAll(io.LimitReader(file, shipmentCatalogLimit+1))
	if err != nil || len(data) > shipmentCatalogLimit {
		return nil
	}
	parsed, err := shipmentParse(data, shipmentJSONLimits{shipmentCatalogLimit, 19, 256, 65536 + 64, 128 << 10, false})
	if err != nil {
		return nil
	}
	root, ok := shipmentObject(parsed, "catalog_version", "contexts")
	if !ok || shipmentString(root, "catalog_version") != "1" {
		return nil
	}
	contexts, ok := root["contexts"].([]any)
	if !ok || len(contexts) > 4 {
		return nil
	}
	var raw struct {
		Contexts []HostSpec `json:"contexts"`
	}
	if json.Unmarshal(data, &raw) != nil || len(raw.Contexts) != len(contexts) {
		return nil
	}
	for i, value := range contexts {
		if _, ok := shipmentObject(value, "workspace_id", "plan_version", "propagation_snapshot_id", "production_snapshot_id", "as_of", "request"); !ok {
			return nil
		}
		if _, err := adapter.InstallHostContext(raw.Contexts[i]); err != nil {
			return nil
		}
	}
	return adapter
}
