package voice

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"testing"
)

// fakeChild writes an executable stand-in for the Python interpreter. It records stdin and
// exits with the given status after printing the given stdout.
func fakeChild(t *testing.T, stdout string, status int) (LocalPythonConfig, string) {
	t.Helper()
	dir := t.TempDir()
	seen := filepath.Join(dir, "stdin.json")
	script := "#!/bin/sh\ncat > '" + seen + "'\nprintf '%s' '" + stdout + "'\nexit " + string(rune('0'+status)) + "\n"
	path := filepath.Join(dir, "python")
	if err := os.WriteFile(path, []byte(script), 0o700); err != nil {
		t.Fatal(err)
	}
	return LocalPythonConfig{PythonPath: path, SourcePath: dir}, seen
}

func TestOfflineTextSourceReturnsTheChildCall(t *testing.T) {
	call := `{"name":"compare_receipt_recovery","arguments":{}}`
	config, seen := fakeChild(t, call, 0)
	source, err := NewLocalPythonTextProposalSource(config)
	if err != nil {
		t.Fatal(err)
	}
	got, err := source.Propose(context.Background(), "receipt r-C arrives 1 hour late", []byte(`{"receipts":[]}`))
	if err != nil || string(got) != call {
		t.Fatalf("got %q %v", got, err)
	}
	var envelope map[string]json.RawMessage
	raw, _ := os.ReadFile(seen)
	if err := json.Unmarshal(raw, &envelope); err != nil || len(envelope) != 2 ||
		string(envelope["utterance"]) != `"receipt r-C arrives 1 hour late"` || string(envelope["projection"]) != `{"receipts":[]}` {
		t.Fatalf("stdin envelope %s", raw)
	}
}

func TestOfflineTextSourceChildFailureIsUnavailable(t *testing.T) {
	for _, status := range []int{2, 3} {
		config, _ := fakeChild(t, "", status)
		source, err := NewLocalPythonTextProposalSource(config)
		if err != nil {
			t.Fatal(err)
		}
		if got, err := source.Propose(context.Background(), "no changes", []byte(`{}`)); got != nil || !errors.Is(err, ErrProposalUnavailable) {
			t.Fatalf("status %d: got %q %v", status, got, err)
		}
	}
	config, _ := fakeChild(t, "", 0)
	source, _ := NewLocalPythonTextProposalSource(config)
	if got, err := source.Propose(context.Background(), "x", []byte(`{}`)); got != nil || !errors.Is(err, ErrProposalUnavailable) {
		t.Fatalf("empty output: got %q %v", got, err)
	}
}

func TestOfflineTextSourceRefusesBadConfigurationAndInput(t *testing.T) {
	if _, err := NewLocalPythonTextProposalSource(LocalPythonConfig{PythonPath: "python", SourcePath: "/tmp"}); err == nil {
		t.Fatal("relative interpreter accepted")
	}
	config, _ := fakeChild(t, "{}", 0)
	source, _ := NewLocalPythonTextProposalSource(config)
	if _, err := source.Propose(context.Background(), "", []byte(`{}`)); !errors.Is(err, ErrProposalUnavailable) {
		t.Fatal("empty utterance accepted")
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if _, err := source.Propose(ctx, "x", []byte(`{}`)); err == nil {
		t.Fatal("cancelled context accepted")
	}
}
