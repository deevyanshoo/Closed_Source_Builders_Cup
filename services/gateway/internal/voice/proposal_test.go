package voice

import (
	"context"
	"errors"
	"strconv"
	"strings"
	"testing"
)

type sourceFunc func(context.Context, []byte) ([]byte, error)

func (f sourceFunc) Propose(ctx context.Context, args []byte) ([]byte, error) { return f(ctx, args) }

type mapperFunc func(context.Context, []byte) ([]byte, error)

func (f mapperFunc) Map(ctx context.Context, proposal []byte) ([]byte, error) {
	return f(ctx, proposal)
}

type canonicalTextSourceFunc func(context.Context, string, []byte) ([]byte, error)

func (f canonicalTextSourceFunc) Propose(ctx context.Context, utterance string, projection []byte) ([]byte, error) {
	return f(ctx, utterance, projection)
}

func TestCanonicalTextProposalValidatesOwnedReferencesWithoutExecuting(t *testing.T) {
	captured := &RecoveryContext{snapshot: map[string]any{
		"snapshot_id": "s1", "plan_version": "v1", "as_of": "2026-10-09T00:00:00Z",
		"receipts": []any{map[string]any{"receipt_id": "r1", "arrival_at": "2026-10-10T09:00:00Z"}},
		"orders":   []any{map[string]any{"order_id": "o1"}},
		"periods":  []any{map[string]any{"period_id": "p1"}},
	}}
	input, err := PrepareTextProposal(captured, "Move r1 and assign o1")
	if err != nil {
		t.Fatal(err)
	}
	valid := []byte(`{"name":"compare_receipt_recovery","arguments":{"arrival_replacements":[{"receipt_id":"r1","expected_arrival_at":"2026-10-10T09:00:00Z","arrival_at":"2026-10-10T10:00:00Z"}],"assignments":[{"order_id":"o1","period_id":"p1"}]}}`)
	called := 0
	source := canonicalTextSourceFunc(func(_ context.Context, utterance string, projection []byte) ([]byte, error) {
		called++
		if utterance != "Move r1 and assign o1" || !strings.Contains(string(projection), `"receipt_id":"r1"`) || strings.Contains(string(projection), "workspace_id") {
			t.Fatal("source saw wrong projection")
		}
		projection[0] = 'x'
		return valid, nil
	})
	call, err := ProposeCanonicalText(context.Background(), source, input, "Move r1 and assign o1")
	if err != nil || called != 1 || !strings.Contains(string(call), `"receipt_id":"r1"`) {
		t.Fatalf("valid proposal %v calls=%d", err, called)
	}
	for _, raw := range [][]byte{
		[]byte(`{"name":"compare_receipt_recovery","name":"compare_receipt_recovery","arguments":{}}`),
		[]byte(`{"name":"compare_receipt_recovery","arguments":{"arrival_replacements":[],"assignments":[]}}`),
		[]byte(`{"name":"compare_receipt_recovery","arguments":{"arrival_replacements":[{"receipt_id":"r1","expected_arrival_at":"2026-10-10T09:00:00Z","arrival_at":"2026-10-10T09:00:00Z"}],"assignments":[{"order_id":"o1","period_id":"p1"}]}}`),
		[]byte(`{"name":"compare_receipt_recovery","arguments":{"arrival_replacements":[{"receipt_id":"unknown","expected_arrival_at":"2026-10-10T09:00:00Z","arrival_at":"2026-10-10T10:00:00Z"}],"assignments":[{"order_id":"o1","period_id":"p1"}]}}`),
		[]byte(`{"name":"compare_receipt_recovery","arguments":{"arrival_replacements":[{"receipt_id":"r1","expected_arrival_at":"2026-10-10T09:00:00Z","arrival_at":"2026-10-10T10:00:00Z"}],"assignments":[]}}`),
	} {
		if _, err := validateCanonicalTextCall(input, raw); !errors.Is(err, ErrProposalInvalid) {
			t.Fatalf("invalid call accepted: %s %v", raw, err)
		}
	}
	if _, err := PrepareTextProposal(captured, strings.Repeat("a", 4097)); !errors.Is(err, ErrProposalInvalid) {
		t.Fatal("overlong utterance accepted")
	}
	if _, err := validateCanonicalTextCall(input, make([]byte, ProposalCallLimit+1)); !errors.Is(err, ErrProposalInvalid) {
		t.Fatal("overlong output accepted")
	}
	for _, tc := range []struct {
		name   string
		source canonicalTextSourceFunc
	}{
		{"private-source-error", canonicalTextSourceFunc(func(context.Context, string, []byte) ([]byte, error) { return nil, errors.New("private detail") })},
		{"source-panic", canonicalTextSourceFunc(func(context.Context, string, []byte) ([]byte, error) { panic("private detail") })},
	} {
		t.Run(tc.name, func(t *testing.T) {
			if _, err := ProposeCanonicalText(context.Background(), tc.source, input, "Move r1"); !errors.Is(err, ErrProposalUnavailable) || strings.Contains(err.Error(), "private") {
				t.Fatalf("source failure was not sanitized: %v", err)
			}
		})
	}
	ctx, cancel := context.WithCancel(context.Background())
	if _, err := ProposeCanonicalText(ctx, canonicalTextSourceFunc(func(context.Context, string, []byte) ([]byte, error) {
		cancel()
		return valid, nil
	}), input, "Move r1"); !errors.Is(err, ErrProposalUnavailable) {
		t.Fatalf("cancelled source published: %v", err)
	}
	captured.snapshot["receipts"].([]any)[0].(map[string]any)["arrival_at"] = "2026-10-10T09:00:00.1Z"
	if _, err := PrepareTextProposal(captured, "Move r1"); !errors.Is(err, ErrProposalInvalid) {
		t.Fatal("fractional source time accepted")
	}
}

func TestCanonicalTextProposalRejectsUnownedMalformedAndOversizedInputs(t *testing.T) {
	captured := &RecoveryContext{snapshot: map[string]any{
		"receipts": []any{map[string]any{"receipt_id": "r1", "arrival_at": "2026-10-10T09:00:00Z"}},
		"orders":   []any{map[string]any{"order_id": "o1"}},
		"periods":  []any{map[string]any{"period_id": "p1"}},
	}}
	input, err := PrepareTextProposal(captured, strings.Repeat("a", 4096))
	if err != nil {
		t.Fatalf("4096-byte utterance rejected: %v", err)
	}
	valid := `{"name":"compare_receipt_recovery","arguments":{"arrival_replacements":[{"receipt_id":"r1","expected_arrival_at":"2026-10-10T09:00:00Z","arrival_at":"2026-10-10T10:00:00Z"}],"assignments":[{"order_id":"o1","period_id":"p1"}]}}`
	if _, err := validateCanonicalTextCall(input, []byte(valid)); err != nil {
		t.Fatalf("valid call rejected: %v", err)
	}
	for _, tc := range []struct{ name, raw string }{
		{"extra-top", strings.Replace(valid, `"name":`, `"extra":true,"name":`, 1)},
		{"duplicate-top", strings.Replace(valid, `"name":`, `"name":"compare_receipt_recovery","name":`, 1)},
		{"null-argument", strings.Replace(valid, `"assignments":[`, `"assignments":null,"unused":[`, 1)},
		{"unknown-receipt", strings.Replace(valid, `"receipt_id":"r1"`, `"receipt_id":"other"`, 1)},
		{"wrong-expected", strings.Replace(valid, `"expected_arrival_at":"2026-10-10T09:00:00Z"`, `"expected_arrival_at":"2026-10-10T08:00:00Z"`, 1)},
		{"unchanged", strings.Replace(valid, `"arrival_at":"2026-10-10T10:00:00Z"`, `"arrival_at":"2026-10-10T09:00:00Z"`, 1)},
		{"fractional", strings.Replace(valid, `"arrival_at":"2026-10-10T10:00:00Z"`, `"arrival_at":"2026-10-10T10:00:00.1Z"`, 1)},
		{"offset", strings.Replace(valid, `"arrival_at":"2026-10-10T10:00:00Z"`, `"arrival_at":"2026-10-10T10:00:00+00:00"`, 1)},
		{"unknown-order", strings.Replace(valid, `"order_id":"o1"`, `"order_id":"other"`, 1)},
		{"unknown-period", strings.Replace(valid, `"period_id":"p1"`, `"period_id":"other"`, 1)},
		{"missing-assignment", strings.Replace(valid, `[{"order_id":"o1","period_id":"p1"}]`, `[]`, 1)},
		{"no-op", strings.Replace(valid, `[{"receipt_id":"r1","expected_arrival_at":"2026-10-10T09:00:00Z","arrival_at":"2026-10-10T10:00:00Z"}]`, `[]`, 1)},
	} {
		t.Run(tc.name, func(t *testing.T) {
			if _, err := validateCanonicalTextCall(input, []byte(tc.raw)); !errors.Is(err, ErrProposalInvalid) {
				t.Fatalf("accepted invalid %s: %v", tc.name, err)
			}
		})
	}
	if _, err := PrepareTextProposal(captured, strings.Repeat("é", 2049)); !errors.Is(err, ErrProposalInvalid) {
		t.Fatal("UTF-8 byte overflow accepted")
	}
	large := &RecoveryContext{snapshot: map[string]any{"receipts": []any{}, "orders": []any{}, "periods": []any{}}}
	for i := 0; i < 64; i++ {
		id := strconv.Itoa(i) + strings.Repeat("x", 128-len(strconv.Itoa(i)))
		large.snapshot["receipts"] = append(large.snapshot["receipts"].([]any), map[string]any{"receipt_id": id, "arrival_at": "2026-10-10T09:00:00Z"})
		large.snapshot["orders"] = append(large.snapshot["orders"].([]any), map[string]any{"order_id": id})
		large.snapshot["periods"] = append(large.snapshot["periods"].([]any), map[string]any{"period_id": id})
	}
	if _, err := PrepareTextProposal(large, strings.Repeat("a", 4096)); !errors.Is(err, ErrProposalInvalid) {
		t.Fatal("complete fake request over 32768 bytes accepted")
	}
	large.snapshot["orders"] = append(large.snapshot["orders"].([]any), map[string]any{"order_id": "overflow"})
	if _, err := PrepareTextProposal(large, "one"); !errors.Is(err, ErrProposalInvalid) {
		t.Fatal("65th order accepted")
	}
	duplicate := &RecoveryContext{snapshot: map[string]any{"receipts": []any{map[string]any{"receipt_id": "r1", "arrival_at": "2026-10-10T09:00:00Z"}, map[string]any{"receipt_id": "r1", "arrival_at": "2026-10-10T10:00:00Z"}}, "orders": []any{map[string]any{"order_id": "o1"}}, "periods": []any{map[string]any{"period_id": "p1"}}}}
	if _, err := PrepareTextProposal(duplicate, "one"); !errors.Is(err, ErrProposalInvalid) {
		t.Fatal("duplicate receipt id accepted")
	}
}

func TestProposalConversionOwnsBytesAndBounds(t *testing.T) {
	input := []byte(`{"assignments":[]}`)
	projection := []byte(`{"proposal_version":"1","sdk_response":{}}`)
	call := []byte(`{"name":"compare_receipt_recovery","arguments":{}}`)
	var got []byte
	source := sourceFunc(func(_ context.Context, args []byte) ([]byte, error) {
		got = append([]byte(nil), args...)
		args[0] = 'x'
		return projection, nil
	})
	mapper := mapperFunc(func(_ context.Context, raw []byte) ([]byte, error) {
		if string(raw) != string(projection) {
			t.Fatal("proposal changed")
		}
		raw[0] = 'x'
		return call, nil
	})
	result, err := ConvertProposal(context.Background(), source, mapper, input)
	if err != nil {
		t.Fatal(err)
	}
	if string(got) != `{"assignments":[]}` || string(input) != string(got) || string(projection) != `{"proposal_version":"1","sdk_response":{}}` || string(result) != string(call) {
		t.Fatal("ownership boundary failed")
	}
	for _, tc := range []struct {
		name   string
		source ProposalSource
		mapper ProposalMapper
		want   error
	}{
		{"nil source", nil, mapper, ErrProposalUnavailable},
		{"nil mapper", source, nil, ErrProposalUnavailable},
		{"source error", sourceFunc(func(context.Context, []byte) ([]byte, error) { return nil, errors.New("private") }), mapper, ErrProposalUnavailable},
		{"empty source", sourceFunc(func(context.Context, []byte) ([]byte, error) { return nil, nil }), mapper, ErrProposalInvalid},
		{"oversize source", sourceFunc(func(context.Context, []byte) ([]byte, error) { return make([]byte, 256<<10+1), nil }), mapper, ErrProposalInvalid},
		{"mapper error", source, mapperFunc(func(context.Context, []byte) ([]byte, error) { return nil, errors.New("private") }), ErrProposalInvalid},
		{"oversize call", source, mapperFunc(func(context.Context, []byte) ([]byte, error) { return make([]byte, 128<<10+1), nil }), ErrProposalInvalid},
	} {
		t.Run(tc.name, func(t *testing.T) {
			_, err := ConvertProposal(context.Background(), tc.source, tc.mapper, input)
			if !errors.Is(err, tc.want) {
				t.Fatalf("got %v want %v", err, tc.want)
			}
		})
	}
}
