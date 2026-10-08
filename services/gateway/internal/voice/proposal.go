package voice

import (
	"context"
	"errors"
	"reflect"
)

const (
	ProposalLimit     = 256 << 10
	ProposalCallLimit = 128 << 10
)

var (
	ErrProposalUnavailable = errors.New("voice proposal unavailable")
	ErrProposalInvalid     = errors.New("voice proposal invalid")
	ErrProposalTimeout     = errors.New("voice proposal timed out")
)

// ProposalSource receives only the declared, owned tool arguments. It must
// honor cancellation; a noncooperative implementation retains its worker slot.
type ProposalSource interface {
	Propose(context.Context, []byte) ([]byte, error)
}

// ProposalMapper converts a private SDK projection into one canonical tool call.
type ProposalMapper interface {
	Map(context.Context, []byte) ([]byte, error)
}

func proposalNil(value any) bool {
	if value == nil {
		return true
	}
	v := reflect.ValueOf(value)
	switch v.Kind() {
	case reflect.Chan, reflect.Func, reflect.Interface, reflect.Map, reflect.Ptr, reflect.Slice:
		return v.IsNil()
	}
	return false
}

func proposalContextError(ctx context.Context) error {
	if errors.Is(ctx.Err(), context.DeadlineExceeded) {
		return ErrProposalTimeout
	}
	if ctx.Err() != nil {
		return ErrProposalUnavailable
	}
	return nil
}

// ConvertProposal never relays source or mapper error strings and never returns
// bytes after cancellation. No callback is detached from the calling worker.
func ConvertProposal(ctx context.Context, source ProposalSource, mapper ProposalMapper, arguments []byte) (call []byte, err error) {
	if ctx == nil || proposalNil(source) || proposalNil(mapper) {
		return nil, ErrProposalUnavailable
	}
	if len(arguments) == 0 || len(arguments) > ProposalCallLimit {
		return nil, ErrProposalInvalid
	}
	if err := proposalContextError(ctx); err != nil {
		return nil, err
	}
	defer func() {
		if recover() != nil {
			call = nil
			err = ErrProposalUnavailable
		}
	}()
	projection, sourceErr := source.Propose(ctx, append([]byte(nil), arguments...))
	if err := proposalContextError(ctx); err != nil {
		return nil, err
	}
	if sourceErr != nil {
		return nil, ErrProposalUnavailable
	}
	if len(projection) == 0 || len(projection) > ProposalLimit {
		return nil, ErrProposalInvalid
	}
	converted, mapErr := mapper.Map(ctx, append([]byte(nil), projection...))
	if err := proposalContextError(ctx); err != nil {
		return nil, err
	}
	if mapErr != nil || len(converted) == 0 || len(converted) > ProposalCallLimit {
		return nil, ErrProposalInvalid
	}
	return append([]byte(nil), converted...), nil
}
