package voice

import (
	"bufio"
	"context"
	"errors"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"testing"
	"time"
)

func TestFixedLocalChildCancellationWaitsAndReapsTrustedSyntheticShim(t *testing.T) {
	python := os.Getenv("VOICE_LOCAL_PYTHON")
	if python == "" {
		t.Fatal("VOICE_LOCAL_PYTHON must name the trusted offline test Python")
	}
	source := t.TempDir()
	packageDir := filepath.Join(source, "procure", "agents")
	if err := os.MkdirAll(packageDir, 0700); err != nil {
		t.Fatal(err)
	}
	for _, path := range []string{filepath.Join(source, "procure", "__init__.py"),
		filepath.Join(packageDir, "__init__.py")} {
		if err := os.WriteFile(path, nil, 0600); err != nil {
			t.Fatal(err)
		}
	}
	marker := filepath.Join(source, "child-ready.fifo")
	if err := syscall.Mkfifo(marker, 0600); err != nil {
		t.Fatal(err)
	}
	// This fixture is deliberately noncooperative and is separate from the four
	// actual numerical integration cases. It only proves local process reaping.
	shim := "import os,signal,sys\nsys.stdin.buffer.read()\n" +
		"with open(" + strconv.Quote(marker) + ", 'w') as f: f.write(str(os.getpid()) + '\\n')\n" +
		"signal.pause()\n"
	if err := os.WriteFile(filepath.Join(packageDir, "voice_local_bridge.py"), []byte(shim), 0600); err != nil {
		t.Fatal(err)
	}
	executor, err := NewLocalPythonExecutor(LocalPythonConfig{PythonPath: python, SourcePath: source})
	if err != nil {
		t.Fatal(err)
	}
	parent, cancel := context.WithCancel(context.Background())
	defer cancel()
	done := make(chan error, 1)
	go func() {
		_, err := executor.Execute(parent, []byte(`{"bridge_version":"1"}`))
		done <- err
	}()
	ready := make(chan string, 1)
	go func() {
		file, err := os.Open(marker)
		if err != nil {
			ready <- ""
			return
		}
		defer file.Close()
		line, _ := bufio.NewReader(file).ReadString('\n')
		ready <- strings.TrimSpace(line)
	}()
	var pidText string
	select {
	case pidText = <-ready:
	case <-time.After(5 * time.Second):
		t.Fatal("trusted synthetic child did not start")
	}
	pid, err := strconv.Atoi(pidText)
	if err != nil || pid <= 0 {
		t.Fatalf("invalid synthetic child marker")
	}
	cancel()
	select {
	case err := <-done:
		if !errors.Is(err, ErrExecution) {
			t.Fatalf("cancelled child error: %v", err)
		}
	case <-time.After(5 * time.Second):
		t.Fatal("cancelled local child not reaped")
	}
	if err := syscall.Kill(pid, 0); !errors.Is(err, syscall.ESRCH) {
		t.Fatalf("trusted child PID remains after Wait: %v", err)
	}
}
