package main

import (
	"context"
	"net/http"
	"testing"
	"time"

	"closedsource/gateway/internal/voice"
)

type proposalSourceFunc func(context.Context, []byte) ([]byte, error)

func (f proposalSourceFunc) Propose(ctx context.Context, args []byte) ([]byte, error) {
	return f(ctx, args)
}

type proposalMapperFunc func(context.Context, []byte) ([]byte, error)

func (f proposalMapperFunc) Map(ctx context.Context, proposal []byte) ([]byte, error) {
	return f(ctx, proposal)
}

func TestProposalModeCannotUseClientSnapshotOrFakeSDKOutput(t *testing.T) {
	called := false
	source := proposalSourceFunc(func(context.Context, []byte) ([]byte, error) { called = true; return nil, nil })
	mapper := proposalMapperFunc(func(context.Context, []byte) ([]byte, error) { t.Fatal("mapper called"); return nil, nil })
	a, err := newVoiceHTTPAdapterWithProposal(voiceExecutorFunc(func(context.Context, []byte) ([]byte, error) { t.Fatal("executor called"); return nil, nil }), source, mapper, time.Now)
	if err != nil {
		t.Fatal(err)
	}
	defer a.Shutdown(context.Background())
	p := principal{ID: "p", Workspace: "W", Role: "viewer"}
	created := voiceCreated(t, a, p)
	body := voiceTurnBody(t, created["context_version"].(string))
	result := voiceDirect(t, a, p, http.MethodPost, voicePath+"/"+created["session_id"].(string)+"/turns", body)
	if result.Code != http.StatusServiceUnavailable {
		t.Fatalf("untrusted direct session status=%d body=%s", result.Code, result.Body.String())
	}
	if called {
		t.Fatal("source saw client-owned context")
	}
	if result := voiceDirect(t, a, p, http.MethodDelete, voicePath+"/"+created["session_id"].(string), nil); result.Code != 200 {
		t.Fatalf("close=%d", result.Code)
	}
}

var _ voice.ProposalSource = proposalSourceFunc(nil)
var _ voice.ProposalMapper = proposalMapperFunc(nil)
