package voice

import (
	"context"
	"errors"
	"testing"
)

type sourceFunc func(context.Context, []byte) ([]byte, error)

func (f sourceFunc) Propose(ctx context.Context, args []byte) ([]byte, error) { return f(ctx, args) }

type mapperFunc func(context.Context, []byte) ([]byte, error)

func (f mapperFunc) Map(ctx context.Context, proposal []byte) ([]byte, error) {
	return f(ctx, proposal)
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
