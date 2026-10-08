package voice

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"testing"
)

func TestLocalProposalMapperPinnedSDKProjectionAndRejections(t *testing.T) {
	python := os.Getenv("VOICE_LOCAL_PYTHON")
	if python == "" {
		t.Skip("trusted offline Python path unset")
	}
	source, err := filepath.Abs("../../../../procurement-engine/src")
	if err != nil {
		t.Fatal(err)
	}
	mapper, err := NewLocalPythonProposalMapper(LocalPythonConfig{PythonPath: python, SourcePath: source})
	if err != nil {
		t.Fatal(err)
	}
	projection := map[string]any{"proposal_version": "1", "sdk_response": map[string]any{"candidates": []any{map[string]any{"finish_reason": "STOP", "content": map[string]any{"role": "model", "parts": []any{map[string]any{"function_call": map[string]any{"name": "compare_receipt_recovery", "args": map[string]any{"arrival_replacements": []any{}, "assignments": []any{}}}}}}}}}}
	raw, _ := json.Marshal(projection)
	call, err := mapper.Map(context.Background(), raw)
	if err != nil {
		t.Fatalf("valid SDK projection: %v", err)
	}
	if !bytes.Equal(call, []byte(`{"arguments":{"arrival_replacements":[],"assignments":[]},"name":"compare_receipt_recovery"}`)) {
		t.Fatalf("noncanonical call %s", call)
	}
	for _, tc := range []struct {
		name   string
		mutate func(map[string]any)
	}{
		{"multiple candidates", func(p map[string]any) {
			s := p["sdk_response"].(map[string]any)
			s["candidates"] = append(s["candidates"].([]any), s["candidates"].([]any)[0])
		}},
		{"partial", func(p map[string]any) {
			s := p["sdk_response"].(map[string]any)
			c := s["candidates"].([]any)[0].(map[string]any)
			part := c["content"].(map[string]any)["parts"].([]any)[0].(map[string]any)
			part["function_call"].(map[string]any)["will_continue"] = true
		}},
		{"authority", func(p map[string]any) {
			s := p["sdk_response"].(map[string]any)
			c := s["candidates"].([]any)[0].(map[string]any)
			part := c["content"].(map[string]any)["parts"].([]any)[0].(map[string]any)
			part["function_call"].(map[string]any)["args"].(map[string]any)["workspace_id"] = "other"
		}},
		{"numeric argument", func(p map[string]any) {
			s := p["sdk_response"].(map[string]any)
			c := s["candidates"].([]any)[0].(map[string]any)
			part := c["content"].(map[string]any)["parts"].([]any)[0].(map[string]any)
			part["function_call"].(map[string]any)["args"].(map[string]any)["assignments"] = []any{map[string]any{"order_id": "O", "period_id": 1}}
		}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			var altered map[string]any
			if err := json.Unmarshal(raw, &altered); err != nil {
				t.Fatal(err)
			}
			tc.mutate(altered)
			input, _ := json.Marshal(altered)
			if result, err := mapper.Map(context.Background(), input); !errors.Is(err, ErrProposalInvalid) || len(result) != 0 {
				t.Fatalf("hostile projection accepted: %s %v", result, err)
			}
		})
	}
}
