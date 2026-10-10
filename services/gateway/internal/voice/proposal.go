package voice

import (
	"context"
	"encoding/json"
	"errors"
	"reflect"
	"strings"
	"time"
	"unicode"
	"unicode/utf8"
)

const (
	ProposalLimit      = 256 << 10
	ProposalCallLimit  = 128 << 10
	textEnvelopeLimit  = 32 << 10
	textUtteranceLimit = 4096
	textIDLimit        = 128
	textRowLimit       = 64
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

// CanonicalTextProposalSource is injected for synthetic, proposal-only text turns.
// Its output is untrusted canonical call JSON, not an SDK projection.
type CanonicalTextProposalSource interface {
	Propose(context.Context, string, []byte) ([]byte, error)
}

type TextProposalInput struct {
	projection []byte
	arrivals   map[string]string
	orders     map[string]bool
	periods    map[string]bool
}

func textID(value any) (string, bool) {
	s, ok := value.(string)
	if !ok || s == "" || len(s) > textIDLimit || !utf8.ValidString(s) || strings.TrimSpace(s) != s {
		return "", false
	}
	for _, r := range s {
		if unicode.IsControl(r) {
			return "", false
		}
	}
	return s, true
}

func textInstant(value any) (string, bool) {
	s, ok := value.(string)
	if !ok || len(s) != len("2006-01-02T15:04:05Z") {
		return "", false
	}
	t, err := time.Parse("2006-01-02T15:04:05Z", s)
	return s, err == nil && t.Format("2006-01-02T15:04:05Z") == s
}

// PrepareTextProposal rejects an unrepresentable complete owned snapshot before
// making a detached minimal projection or invoking a source.
func PrepareTextProposal(captured *RecoveryContext, utterance string) (*TextProposalInput, error) {
	if captured == nil || len(utterance) == 0 || len(utterance) > textUtteranceLimit ||
		!utf8.ValidString(utterance) || strings.TrimSpace(utterance) == "" {
		return nil, ErrProposalInvalid
	}
	snapshot := captured.snapshot
	if snapshot == nil {
		return nil, ErrProposalInvalid
	}
	type receipt struct {
		ReceiptID string `json:"receipt_id"`
		ArrivalAt string `json:"arrival_at"`
	}
	type order struct {
		OrderID string `json:"order_id"`
	}
	type period struct {
		PeriodID string `json:"period_id"`
	}
	projection := struct {
		Receipts    []receipt `json:"receipts"`
		Orders      []order   `json:"orders"`
		Periods     []period  `json:"periods"`
		SnapshotID  string    `json:"snapshot_id,omitempty"`
		PlanVersion string    `json:"plan_version,omitempty"`
		AsOf        string    `json:"as_of,omitempty"`
	}{}
	input := &TextProposalInput{arrivals: make(map[string]string), orders: make(map[string]bool), periods: make(map[string]bool)}
	for _, name := range []string{"receipts", "orders", "periods"} {
		rows, ok := snapshot[name].([]any)
		if !ok || len(rows) < 1 || len(rows) > textRowLimit {
			return nil, ErrProposalInvalid
		}
		for _, item := range rows {
			row, ok := item.(map[string]any)
			if !ok {
				return nil, ErrProposalInvalid
			}
			switch name {
			case "receipts":
				id, valid := textID(row["receipt_id"])
				arrival, instant := textInstant(row["arrival_at"])
				if !valid || !instant || input.arrivals[id] != "" {
					return nil, ErrProposalInvalid
				}
				input.arrivals[id] = arrival
				projection.Receipts = append(projection.Receipts, receipt{id, arrival})
			case "orders":
				id, valid := textID(row["order_id"])
				if !valid || input.orders[id] {
					return nil, ErrProposalInvalid
				}
				input.orders[id] = true
				projection.Orders = append(projection.Orders, order{id})
			case "periods":
				id, valid := textID(row["period_id"])
				if !valid || input.periods[id] {
					return nil, ErrProposalInvalid
				}
				input.periods[id] = true
				projection.Periods = append(projection.Periods, period{id})
			}
		}
	}
	for _, name := range []string{"snapshot_id", "plan_version"} {
		if value, present := snapshot[name]; present {
			id, ok := textID(value)
			if !ok {
				return nil, ErrProposalInvalid
			}
			if name == "snapshot_id" {
				projection.SnapshotID = id
			} else {
				projection.PlanVersion = id
			}
		}
	}
	if value, present := snapshot["as_of"]; present {
		asOf, ok := textInstant(value)
		if !ok {
			return nil, ErrProposalInvalid
		}
		projection.AsOf = asOf
	}
	encoded, err := json.Marshal(projection)
	if err != nil {
		return nil, ErrProposalInvalid
	}
	envelope, err := json.Marshal(struct {
		Utterance  string          `json:"utterance"`
		Projection json.RawMessage `json:"projection"`
	}{utterance, encoded})
	if err != nil || len(envelope) > textEnvelopeLimit {
		return nil, ErrProposalInvalid
	}
	input.projection = encoded
	return input, nil
}

func validateCanonicalTextCall(input *TextProposalInput, raw []byte) ([]byte, error) {
	if input == nil || len(raw) == 0 || len(raw) > ProposalCallLimit {
		return nil, ErrProposalInvalid
	}
	total := 0
	value, err := strictParse(raw, ProposalCallLimit, &total)
	if err != nil {
		return nil, ErrProposalInvalid
	}
	call, ok := value.(map[string]any)
	if !ok || !exactKeys(call, "name", "arguments") || call["name"] != localToolName {
		return nil, ErrProposalInvalid
	}
	args, ok := call["arguments"].(map[string]any)
	if !ok || !exactKeys(args, "arrival_replacements", "assignments") {
		return nil, ErrProposalInvalid
	}
	replacements, ok := args["arrival_replacements"].([]any)
	if !ok || len(replacements) < 1 || len(replacements) > textRowLimit {
		return nil, ErrProposalInvalid
	}
	seenReceipts := make(map[string]bool)
	for _, item := range replacements {
		row, ok := item.(map[string]any)
		if !ok || !exactKeys(row, "receipt_id", "expected_arrival_at", "arrival_at") {
			return nil, ErrProposalInvalid
		}
		id, ok := textID(row["receipt_id"])
		if !ok || input.arrivals[id] == "" || seenReceipts[id] || row["expected_arrival_at"] != input.arrivals[id] {
			return nil, ErrProposalInvalid
		}
		arrival, valid := textInstant(row["arrival_at"])
		if !valid || arrival == input.arrivals[id] {
			return nil, ErrProposalInvalid
		}
		seenReceipts[id] = true
	}
	assignments, ok := args["assignments"].([]any)
	if !ok || len(assignments) != len(input.orders) {
		return nil, ErrProposalInvalid
	}
	seenOrders := make(map[string]bool)
	for _, item := range assignments {
		row, ok := item.(map[string]any)
		if !ok || !exactKeys(row, "order_id", "period_id") {
			return nil, ErrProposalInvalid
		}
		orderID, validOrder := textID(row["order_id"])
		periodID, validPeriod := textID(row["period_id"])
		if !validOrder || !validPeriod || !input.orders[orderID] || !input.periods[periodID] || seenOrders[orderID] {
			return nil, ErrProposalInvalid
		}
		seenOrders[orderID] = true
	}
	canonical, err := json.Marshal(call)
	if err != nil || len(canonical) > ProposalCallLimit {
		return nil, ErrProposalInvalid
	}
	return canonical, nil
}

// ProposeCanonicalText makes exactly one injected call, checks cancellation,
// and returns only owned, structurally validated bytes with sanitized errors.
func ProposeCanonicalText(ctx context.Context, source CanonicalTextProposalSource, input *TextProposalInput, utterance string) (call []byte, err error) {
	if ctx == nil || proposalNil(source) || input == nil {
		return nil, ErrProposalUnavailable
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
	raw, sourceErr := source.Propose(ctx, utterance, append([]byte(nil), input.projection...))
	if err := proposalContextError(ctx); err != nil {
		return nil, err
	}
	if sourceErr != nil {
		return nil, ErrProposalUnavailable
	}
	return validateCanonicalTextCall(input, raw)
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
