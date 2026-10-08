package main

import (
	"context"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestShipmentFlagsSplitOnlyOwnFlagsAndRejectPartialDuplicateUnsafe(t *testing.T) {
	cfg, rest, err := withShipmentLocalFlags(stateConfig{}, []string{
		"-shipment-local-contexts=/tmp/catalog.json", "-voice-local-python=/bin/python",
		"-shipment-local-python", "/bin/python3", "-shipment-local-source=/tmp/source", "-voice-local-source=/tmp/voice"})
	if err != nil || cfg.ShipmentContextsPath != "/tmp/catalog.json" || cfg.ShipmentPythonPath != "/bin/python3" || cfg.ShipmentSourcePath != "/tmp/source" ||
		strings.Join(rest, "|") != "-voice-local-python=/bin/python|-voice-local-source=/tmp/voice" {
		t.Fatalf("split cfg=%+v rest=%v err=%v", cfg, rest, err)
	}
	for _, args := range [][]string{
		{"-shipment-local-python=/bin/python3"},
		{"-shipment-local-python=/bin/python3", "-shipment-local-python=/bin/python3"},
		{"-shipment-local-contexts=relative", "-shipment-local-python=/bin/python3", "-shipment-local-source=/tmp/source"},
		{"-shipment-local-contexts=/tmp/a", "-shipment-local-python=/bin/python3", "-shipment-local-source=/tmp/source\x00x"},
	} {
		if _, _, err := withShipmentLocalFlags(stateConfig{}, args); err == nil {
			t.Fatalf("accepted %v", args)
		}
	}
	if _, _, err := withShipmentLocalFlags(stateConfig{Mode: "private_machine"}, []string{"-shipment-local-contexts=/tmp/a", "-shipment-local-python=/bin/python3", "-shipment-local-source=/tmp/source"}); err == nil {
		t.Fatal("private mode accepted")
	}
}

func shipmentScript(t *testing.T, content string) *shipmentLocalExecutor {
	t.Helper()
	dir := t.TempDir()
	path := filepath.Join(dir, "python")
	if err := os.WriteFile(path, []byte("#!/bin/sh\n"+content+"\n"), 0700); err != nil {
		t.Fatal(err)
	}
	executor, err := newShipmentLocalExecutor(path, dir)
	if err != nil {
		t.Fatal(err)
	}
	return executor
}

func TestShipmentChildNonzeroOverflowAndDeadlineAreReaped(t *testing.T) {
	for _, tc := range []struct {
		name, script string
		timeout      time.Duration
		want         error
	}{
		{"nonzero", "exit 7", time.Second, errShipmentUnavailable},
		{"stdout_overflow", "exec /bin/cat /dev/zero", 3 * time.Second, nil},
		{"deadline", "exec /bin/sleep 10", 30 * time.Millisecond, context.DeadlineExceeded},
	} {
		t.Run(tc.name, func(t *testing.T) {
			executor := shipmentScript(t, tc.script)
			ctx, cancel := context.WithTimeout(context.Background(), tc.timeout)
			defer cancel()
			_, err := executor.Execute(ctx, []byte(`{"bridge_version":"1"}`))
			if tc.want != nil && !errors.Is(err, tc.want) {
				t.Fatalf("err=%v want=%v", err, tc.want)
			}
			if tc.want == nil && !errors.Is(err, errShipmentInvalidOutput) {
				t.Fatalf("overflow err=%v", err)
			}
		})
	}
}

func TestShipmentChildInheritedPipeCannotHangReap(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "forking-python")
	marker := filepath.Join(dir, "parent-ready")
	script := fmt.Sprintf("#!/usr/bin/python3\nimport os, time\nif os.fork() == 0:\n    time.sleep(3)\n    os._exit(0)\nwith open(%q, 'w') as ready:\n    ready.write('ready')\nos._exit(0)\n", marker)
	if err := os.WriteFile(path, []byte(script), 0700); err != nil {
		t.Fatal(err)
	}
	executor, err := newShipmentLocalExecutor(path, dir)
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	done := make(chan error, 1)
	go func() { _, runErr := executor.Execute(ctx, []byte(`{}`)); done <- runErr }()
	startup := time.NewTimer(7 * time.Second)
	defer startup.Stop()
	ticker := time.NewTicker(5 * time.Millisecond)
	defer ticker.Stop()
	var markedAt time.Time
	for markedAt.IsZero() {
		select {
		case runErr := <-done:
			t.Fatalf("child returned before direct parent marker: %v", runErr)
		case <-startup.C:
			t.Fatal("direct child never reached parent-exit marker")
		case <-ticker.C:
			if _, statErr := os.Stat(marker); statErr == nil {
				markedAt = time.Now()
			}
		}
	}
	err = <-done
	if err == nil {
		t.Fatal("inherited stdout accepted as completed")
	}
	if time.Since(markedAt) > 1800*time.Millisecond {
		t.Fatalf("pipe hold delayed reap after parent marker: %v", time.Since(markedAt))
	}
}

func TestShipmentCatalogAtomicInvalidAndEmptyAvailable(t *testing.T) {
	dir := t.TempDir()
	source := t.TempDir()
	python := filepath.Join(dir, "python")
	if err := os.WriteFile(python, []byte("#!/bin/sh\nexit 0\n"), 0700); err != nil {
		t.Fatal(err)
	}
	catalog := filepath.Join(dir, "contexts.json")
	cfg := stateConfig{ShipmentContextsPath: catalog, ShipmentPythonPath: python, ShipmentSourcePath: source}
	if err := os.WriteFile(catalog, []byte(`{"catalog_version":"1","contexts":[]}`), 0600); err != nil {
		t.Fatal(err)
	}
	if a := initShipmentHTTP(cfg); a == nil || !a.ready || len(a.list("W")) != 0 {
		t.Fatal("empty catalog unavailable")
	}
	spec := shipmentTestSpec(t)
	good, err := shipmentMarshal(spec)
	if err != nil {
		t.Fatal(err)
	}
	bad := []byte(`{"workspace_id":"foreign"}`)
	data := []byte(`{"catalog_version":"1","contexts":[` + string(good) + `,` + string(bad) + `]}`)
	if err := os.WriteFile(catalog, data, 0600); err != nil {
		t.Fatal(err)
	}
	if a := initShipmentHTTP(cfg); a != nil && len(a.list("W")) > 0 {
		t.Fatal("partial context exposed from invalid catalog")
	}
}
