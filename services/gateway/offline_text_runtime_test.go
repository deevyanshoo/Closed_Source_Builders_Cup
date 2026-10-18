package main

import (
	"os"
	"path/filepath"
	"testing"
)

func offlineTextPaths(t *testing.T) (string, string) {
	t.Helper()
	dir := t.TempDir()
	python := filepath.Join(dir, "python")
	if err := os.WriteFile(python, []byte("#!/bin/sh\nexit 3\n"), 0o700); err != nil {
		t.Fatal(err)
	}
	return python, dir
}

func TestOfflineTextProposalFlagIsOffByDefault(t *testing.T) {
	python, source := offlineTextPaths(t)
	cfg, err := withVoiceLocalFlags(stateConfig{}, []string{"-voice-local-python=" + python, "-voice-local-source=" + source})
	if err != nil || cfg.VoiceOfflineTextProposals {
		t.Fatalf("default: %+v %v", cfg, err)
	}
	adapter := initVoiceHTTP(cfg)
	if adapter == nil || adapter.textSource != nil {
		t.Fatal("text proposal source set without the flag")
	}
}

func TestOfflineTextProposalFlagOnSetsTheSource(t *testing.T) {
	python, source := offlineTextPaths(t)
	cfg, err := withVoiceLocalFlags(stateConfig{}, []string{"-voice-local-python=" + python, "-voice-local-source=" + source,
		"-voice-offline-text-proposals=on"})
	if err != nil || !cfg.VoiceOfflineTextProposals {
		t.Fatalf("on: %+v %v", cfg, err)
	}
	adapter := initVoiceHTTP(cfg)
	if adapter == nil || adapter.textSource == nil {
		t.Fatal("flag on did not set the text proposal source")
	}
}

func TestOfflineTextProposalFlagRefusesUnsafeUse(t *testing.T) {
	python, source := offlineTextPaths(t)
	for _, args := range [][]string{
		{"-voice-offline-text-proposals=on"},
		{"-voice-local-python=" + python, "-voice-local-source=" + source, "-voice-offline-text-proposals=yes"},
		{"-voice-local-python=" + python, "-voice-local-source=" + source, "-voice-offline-text-proposals=on", "-voice-offline-text-proposals=on"},
	} {
		if _, err := withVoiceLocalFlags(stateConfig{}, args); err == nil {
			t.Fatalf("accepted %v", args)
		}
	}
	if _, err := withVoiceLocalFlags(stateConfig{Mode: "private_machine"}, []string{"-voice-local-python=" + python,
		"-voice-local-source=" + source, "-voice-offline-text-proposals=on"}); err == nil {
		t.Fatal("private machine mode accepted")
	}
}
