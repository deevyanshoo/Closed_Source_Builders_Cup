package voice

import (
	"bytes"
	"context"
	"os/exec"
	"time"
)

type LocalPythonProposalMapper struct{ pythonPath, sourcePath string }

func NewLocalPythonProposalMapper(config LocalPythonConfig) (*LocalPythonProposalMapper, error) {
	executor, err := NewLocalPythonExecutor(config)
	if err != nil {
		return nil, err
	}
	return &LocalPythonProposalMapper{pythonPath: executor.pythonPath, sourcePath: executor.sourcePath}, nil
}

func (mapper *LocalPythonProposalMapper) Map(parent context.Context, projection []byte) ([]byte, error) {
	if mapper == nil || parent == nil || len(projection) == 0 || len(projection) > ProposalLimit {
		return nil, ErrProposalInvalid
	}
	if err := proposalContextError(parent); err != nil {
		return nil, err
	}
	cmd := exec.CommandContext(parent, mapper.pythonPath, "-m", "procure.agents.voice_proposal_adapter")
	cmd.Dir = mapper.sourcePath
	cmd.Env = []string{
		"PATH=/usr/bin:/bin", "PYTHONPATH=" + mapper.sourcePath,
		"PYTHONDONTWRITEBYTECODE=1", "PYTHONNOUSERSITE=1",
		"PYTHONUNBUFFERED=1", "LANG=C.UTF-8",
	}
	cmd.Stdin = bytes.NewReader(projection)
	cmd.WaitDelay = time.Second
	stdout := &boundedWriter{max: ProposalCallLimit, cancel: func() { _ = cmd.Process.Kill() }}
	stderr := &boundedWriter{max: 64 << 10, cancel: func() { _ = cmd.Process.Kill() }}
	cmd.Stdout, cmd.Stderr = stdout, stderr
	if err := cmd.Run(); err != nil {
		if mapped := proposalContextError(parent); mapped != nil {
			return nil, mapped
		}
		return nil, ErrProposalInvalid
	}
	if err := proposalContextError(parent); err != nil {
		return nil, err
	}
	if stdout.buffer.Len() == 0 {
		return nil, ErrProposalInvalid
	}
	return append([]byte(nil), stdout.buffer.Bytes()...), nil
}
