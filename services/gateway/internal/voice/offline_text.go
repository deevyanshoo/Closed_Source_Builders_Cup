package voice

import (
	"bytes"
	"context"
	"encoding/json"
	"os/exec"
	"time"
	"unicode/utf8"
)

// LocalPythonTextProposalSource is the offline rule-based text proposal source (#96). It runs the fixed module
// procure.agents.offline_text_proposal as one bounded child per request, like LocalPythonProposalMapper. There is
// no model or network call. The child's output is untrusted: ProposeCanonicalText validates it against the owned
// projection, and the browser still requires confirmation before a comparison runs.
type LocalPythonTextProposalSource struct{ pythonPath, sourcePath string }

func NewLocalPythonTextProposalSource(config LocalPythonConfig) (*LocalPythonTextProposalSource, error) {
	executor, err := NewLocalPythonExecutor(config)
	if err != nil {
		return nil, err
	}
	return &LocalPythonTextProposalSource{pythonPath: executor.pythonPath, sourcePath: executor.sourcePath}, nil
}

func (source *LocalPythonTextProposalSource) Propose(parent context.Context, utterance string, projection []byte) ([]byte, error) {
	if source == nil || parent == nil || utterance == "" || len(utterance) > textUtteranceLimit || !utf8.ValidString(utterance) ||
		len(projection) == 0 || len(projection) > textEnvelopeLimit {
		return nil, ErrProposalUnavailable
	}
	if err := proposalContextError(parent); err != nil {
		return nil, err
	}
	envelope, err := json.Marshal(struct {
		Utterance  string          `json:"utterance"`
		Projection json.RawMessage `json:"projection"`
	}{utterance, projection})
	if err != nil {
		return nil, ErrProposalUnavailable
	}
	cmd := exec.CommandContext(parent, source.pythonPath, "-m", "procure.agents.offline_text_proposal")
	cmd.Dir = source.sourcePath
	cmd.Env = []string{
		"PATH=/usr/bin:/bin", "PYTHONPATH=" + source.sourcePath,
		"PYTHONDONTWRITEBYTECODE=1", "PYTHONNOUSERSITE=1",
		"PYTHONUNBUFFERED=1", "LANG=C.UTF-8",
	}
	cmd.Stdin = bytes.NewReader(envelope)
	cmd.WaitDelay = time.Second
	stdout := &boundedWriter{max: ProposalCallLimit, cancel: func() { _ = cmd.Process.Kill() }}
	stderr := &boundedWriter{max: 64 << 10, cancel: func() { _ = cmd.Process.Kill() }}
	cmd.Stdout, cmd.Stderr = stdout, stderr
	if err := cmd.Run(); err != nil {
		if mapped := proposalContextError(parent); mapped != nil {
			return nil, mapped
		}
		return nil, ErrProposalUnavailable
	}
	if err := proposalContextError(parent); err != nil {
		return nil, err
	}
	if stdout.buffer.Len() == 0 {
		return nil, ErrProposalUnavailable
	}
	return append([]byte(nil), stdout.buffer.Bytes()...), nil
}
