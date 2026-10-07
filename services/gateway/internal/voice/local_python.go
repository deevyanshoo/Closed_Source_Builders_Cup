package voice

import (
	"bytes"
	"context"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"
)

type LocalPythonConfig struct {
	PythonPath string
	SourcePath string
}

type LocalPythonExecutor struct {
	pythonPath string
	sourcePath string
}

func NewLocalPythonExecutor(config LocalPythonConfig) (*LocalPythonExecutor, error) {
	if !filepath.IsAbs(config.PythonPath) || !filepath.IsAbs(config.SourcePath) ||
		strings.ContainsRune(config.PythonPath, 0) || strings.ContainsRune(config.SourcePath, 0) {
		return nil, ErrInvalid
	}
	python, err := os.Stat(config.PythonPath)
	if err != nil || !python.Mode().IsRegular() || python.Mode().Perm()&0111 == 0 {
		return nil, ErrInvalid
	}
	source, err := os.Stat(config.SourcePath)
	if err != nil || !source.IsDir() {
		return nil, ErrInvalid
	}
	return &LocalPythonExecutor{pythonPath: config.PythonPath, sourcePath: config.SourcePath}, nil
}

type boundedWriter struct {
	buffer bytes.Buffer
	max    int
	cancel context.CancelFunc
}

func (writer *boundedWriter) Write(data []byte) (int, error) {
	if writer.buffer.Len()+len(data) > writer.max {
		writer.cancel()
		return 0, ErrExecution
	}
	return writer.buffer.Write(data)
}

func (executor *LocalPythonExecutor) Execute(parent context.Context, input []byte) ([]byte, error) {
	if executor == nil || parent == nil || len(input) == 0 || len(input) > localRequestLimit {
		return nil, ErrExecution
	}
	ctx, cancel := context.WithTimeout(parent, 30*time.Second)
	defer cancel()
	cmd := exec.CommandContext(ctx, executor.pythonPath, "-m", "procure.agents.voice_local_bridge")
	cmd.Dir = executor.sourcePath
	cmd.Env = []string{
		"PATH=/usr/bin:/bin", "PYTHONPATH=" + executor.sourcePath,
		"PYTHONDONTWRITEBYTECODE=1", "PYTHONNOUSERSITE=1",
		"PYTHONUNBUFFERED=1", "LANG=C.UTF-8",
	}
	cmd.Stdin = bytes.NewReader(input)
	cmd.WaitDelay = time.Second
	stdout := &boundedWriter{max: localResponseLimit, cancel: cancel}
	stderr := &boundedWriter{max: 64 << 10, cancel: cancel}
	cmd.Stdout, cmd.Stderr = stdout, stderr
	if err := cmd.Run(); err != nil || ctx.Err() != nil {
		return nil, ErrExecution
	}
	return append([]byte(nil), stdout.buffer.Bytes()...), nil
}
