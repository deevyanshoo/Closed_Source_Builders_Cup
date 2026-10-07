package main

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"log"
	"math/big"
	"net/http"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"time"

	"closedsource/gateway/internal/contract"
)

const version = "0.1.0"
const maxBody = 64 << 10

var amountRE = regexp.MustCompile(`^(0|[1-9][0-9]*)(\.[0-9]+)?$`)
var moneyRE = regexp.MustCompile(`^-?(0|[1-9][0-9]*)(\.[0-9]+)?$`)
var unitRE = regexp.MustCompile(`^[a-zA-Z][a-zA-Z0-9_/-]{0,31}$`)
var materialRE = regexp.MustCompile(`^[a-zA-Z0-9_-]+$`)
var currencyRE = regexp.MustCompile(`^[A-Z]{3}$`)
var countryRE = regexp.MustCompile(`^[A-Z]{2}$`)
var numericRE = regexp.MustCompile(`^-?(0|[1-9][0-9]*)(\.[0-9]+)?([eE][+-]?[0-9]+)?$`)

func main() {
	cfg, addr, err := runtimeFromEnv(os.Getenv)
	if err != nil {
		log.Fatal("invalid gateway configuration")
	}
	var handler http.Handler
	if cfg.Mode == "private_machine" {
		handler, err = newPrivateStateHandler(cfg)
		if err != nil {
			log.Fatal("private gateway configuration unavailable")
		}
	} else {
		if cfg.AssetsDir != "" {
			if _, err := newAssetsHandler(cfg.AssetsDir); err != nil {
				log.Fatal("production assets unavailable")
			}
		}
		handler = newStateHandler(cfg)
	}
	log.Fatal(http.ListenAndServe(addr, handler))
}

func newHandler(upstream, examples string) http.Handler {
	return newHandlerWithClient(upstream, examples, &http.Client{Timeout: 8 * time.Second})
}

func newHandlerWithClient(upstream, examples string, client *http.Client) http.Handler {
	m := http.NewServeMux()
	m.HandleFunc("/health", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != "GET" {
			http.Error(w, "method not allowed", 405)
			return
		}
		write(w, 200, map[string]string{"status": "ok", "scope": "process_only"})
	})
	m.HandleFunc("/v2/actions", notImplemented)
	m.HandleFunc("/v2/actions/", notImplemented)
	m.HandleFunc("/v2/contracts/examples/", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != "GET" {
			http.Error(w, "method not allowed", 405)
			return
		}
		name := strings.TrimPrefix(r.URL.Path, "/v2/contracts/examples/")
		switch name {
		case "request", "success", "unsupported", "precision", "offer", "voice", "shipment":
		default:
			writeError(w, 404, "not_found", "Unknown example", "")
			return
		}
		b, e := os.ReadFile(filepath.Join(examples, name+".json"))
		if e != nil {
			writeError(w, 503, "example_unavailable", "Contract example unavailable", "")
			return
		}
		w.Header().Set("Content-Type", "application/json")
		w.Write(b)
	})
	m.HandleFunc("/v2/materials", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != "GET" {
			http.Error(w, "method not allowed", 405)
			return
		}
		body, status, e := fetch(r.Context(), client, upstream, "GET", "/materials", nil)
		if e != nil || status >= 500 {
			write(w, 200, contract.MaterialsResponse{ContractVersion: version, Availability: contract.Availability{Code: "provider_unavailable", Reason: "Material catalog service unavailable"}, Materials: []contract.Material{}})
			return
		}
		if status != 200 {
			writeError(w, 502, "invalid_upstream", "Invalid material catalog response", "")
			return
		}
		var rows []contract.Material
		if e = decodeUpstream(body, &rows); e != nil || rows == nil {
			writeError(w, 502, "invalid_upstream", "Invalid material catalog response", "")
			return
		}
		for _, v := range rows {
			if v.Key == "" || v.DisplayName == "" || v.SourceSeries == "" || v.SourceUnit == "" {
				writeError(w, 502, "invalid_upstream", "Invalid material catalog response", "")
				return
			}
		}
		code := "available"
		reason := "Legacy material catalog"
		if len(rows) == 0 {
			code = "empty_result"
			reason = "Material catalog is empty"
		}
		write(w, 200, contract.MaterialsResponse{ContractVersion: version, Availability: contract.Availability{Code: code, Reason: reason}, Materials: rows})
	})
	m.HandleFunc("/v2/plans/recommendations", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != "POST" {
			http.Error(w, "method not allowed", 405)
			return
		}
		recommend(w, r, client, upstream)
	})
	return m
}
func notImplemented(w http.ResponseWriter, r *http.Request) {
	writeError(w, 501, "not_implemented", "Actions are not implemented", "")
}
func write(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	json.NewEncoder(w).Encode(v)
}
func writeError(w http.ResponseWriter, status int, code, msg, field string) {
	x := contract.SemanticError{Code: code, Message: msg}
	if field != "" {
		x.Field = &field
	}
	write(w, status, contract.ErrorResponse{ContractVersion: version, Error: x})
}
func decode(b []byte, v any) error {
	d := json.NewDecoder(bytes.NewReader(b))
	d.DisallowUnknownFields()
	d.UseNumber()
	if e := d.Decode(v); e != nil {
		return e
	}
	var tail any
	if e := d.Decode(&tail); e != io.EOF {
		return errors.New("trailing JSON")
	}
	return nil
}
func decodeUpstream(b []byte, v any) error {
	d := json.NewDecoder(bytes.NewReader(b))
	d.UseNumber()
	if e := d.Decode(v); e != nil {
		return e
	}
	var tail any
	if e := d.Decode(&tail); e != io.EOF {
		return errors.New("trailing JSON")
	}
	return nil
}
func fetch(ctx context.Context, c *http.Client, base, method, path string, payload []byte) ([]byte, int, error) {
	req, e := http.NewRequestWithContext(ctx, method, base+path, bytes.NewReader(payload))
	if e != nil {
		return nil, 0, e
	}
	if payload != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	res, e := c.Do(req)
	if e != nil {
		return nil, 0, e
	}
	defer res.Body.Close()
	b, e := io.ReadAll(io.LimitReader(res.Body, 1<<20+1))
	if e != nil || len(b) > 1<<20 {
		return nil, res.StatusCode, errors.New("upstream body invalid")
	}
	return b, res.StatusCode, nil
}
func validAmount(s string, positive bool) bool {
	if len(s) > 120 || !amountRE.MatchString(s) {
		return false
	}
	if positive && strings.Trim(s, "0.") == "" {
		return false
	}
	return true
}
func precision(s string) bool {
	parts := strings.SplitN(s, ".", 2)
	if len(parts) == 2 && len(parts[1]) > 6 {
		return false
	}
	digits := strings.TrimLeft(strings.ReplaceAll(s, ".", ""), "0")
	return len(digits) <= 15
}
func required(raw map[string]json.RawMessage, keys ...string) bool {
	for _, k := range keys {
		v, ok := raw[k]
		if !ok || string(bytes.TrimSpace(v)) == "null" {
			return false
		}
	}
	return true
}
func validate(b []byte) (contract.PlanningRequest, string, string) {
	var q contract.PlanningRequest
	var raw map[string]json.RawMessage
	if e := decode(b, &raw); e != nil || raw == nil {
		return q, "invalid_request", "Invalid JSON object"
	}
	if !required(raw, "contract_version", "workspace_id", "buyer", "material", "quantity", "current_stock", "monthly_consumption", "need_by_month", "lead_time_months", "quote_currency", "settlement_currency", "reporting_currency", "risk_appetite") {
		return q, "invalid_request", "Missing required field"
	}
	if e := decode(b, &q); e != nil {
		return q, "invalid_request", "Invalid or unknown field"
	}
	for _, k := range []string{"buyer", "quantity", "current_stock", "monthly_consumption"} {
		var nested map[string]json.RawMessage
		if decode(raw[k], &nested) != nil {
			return q, "invalid_request", "Invalid nested object"
		}
		fields := []string{"amount", "unit"}
		if k == "buyer" {
			fields = []string{"id", "destination_country"}
		}
		if !required(nested, fields...) {
			return q, "invalid_request", "Missing required field"
		}
	}
	if q.Budget != nil {
		var nested map[string]json.RawMessage
		if decode(raw["budget"], &nested) != nil || !required(nested, "amount", "currency") {
			return q, "invalid_request", "Invalid budget"
		}
	} else if v, ok := raw["budget"]; ok && string(v) == "null" {
		return q, "invalid_request", "Invalid budget"
	}
	if q.ContractVersion != version || q.WorkspaceId == "" || q.Buyer.Id == "" || !countryRE.MatchString(q.Buyer.DestinationCountry) || q.Material == "" || len(q.Material) > 120 || !materialRE.MatchString(q.Material) || q.NeedByMonth < 1 || q.NeedByMonth > 6 || q.LeadTimeMonths < 0 || q.LeadTimeMonths > 3 || !currencyRE.MatchString(q.QuoteCurrency) || !currencyRE.MatchString(q.SettlementCurrency) || !currencyRE.MatchString(q.ReportingCurrency) || (q.RiskAppetite != "low" && q.RiskAppetite != "medium" && q.RiskAppetite != "high") {
		return q, "invalid_request", "Invalid planning request"
	}
	for _, a := range []struct {
		q        contract.Quantity
		positive bool
	}{{q.Quantity, true}, {q.CurrentStock, false}, {q.MonthlyConsumption, true}} {
		if !validAmount(a.q.Amount, a.positive) || !unitRE.MatchString(a.q.Unit) {
			return q, "invalid_request", "Invalid quantity"
		}
	}
	if q.Budget != nil && (len(q.Budget.Amount) > 120 || !moneyRE.MatchString(q.Budget.Amount) || strings.HasPrefix(q.Budget.Amount, "-") || strings.Trim(q.Budget.Amount, "0.") == "" || !currencyRE.MatchString(q.Budget.Currency)) {
		return q, "invalid_request", "Invalid budget"
	}
	if q.Quantity.Unit != q.CurrentStock.Unit || q.Quantity.Unit != q.MonthlyConsumption.Unit {
		return q, "semantic_validation", "Quantity units must match"
	}
	if q.Budget != nil && q.Budget.Currency != q.ReportingCurrency {
		return q, "semantic_validation", "Budget currency must match reporting currency"
	}
	if q.SettlementCurrency != q.ReportingCurrency {
		return q, "semantic_validation", "Settlement and reporting currencies must match in the legacy adapter"
	}
	for _, s := range []string{q.Quantity.Amount, q.CurrentStock.Amount, q.MonthlyConsumption.Amount} {
		if !precision(s) {
			return q, "legacy_precision_unsupported", "Legacy engine precision limit exceeded"
		}
	}
	if q.Budget != nil && !precision(q.Budget.Amount) {
		return q, "legacy_precision_unsupported", "Legacy engine precision limit exceeded"
	}
	return q, "", ""
}
func recommend(w http.ResponseWriter, r *http.Request, c *http.Client, upstream string) {
	b, e := io.ReadAll(io.LimitReader(r.Body, maxBody+1))
	if e != nil {
		writeError(w, 422, "invalid_request", "Cannot read request", "")
		return
	}
	if len(b) > maxBody {
		writeError(w, 413, "body_too_large", "Request body too large", "")
		return
	}
	q, code, msg := validate(b)
	if code != "" {
		writeError(w, 422, code, msg, "")
		return
	}
	unavailable := func(code, reason string) {
		write(w, 200, contract.PlanResponse{ContractVersion: version, Status: "unavailable", Availability: contract.Availability{Code: code, Reason: reason}, Request: q, Warnings: []string{}})
	}
	if q.Buyer.DestinationCountry != "IN" || q.QuoteCurrency != "USD" || q.SettlementCurrency != "INR" || q.ReportingCurrency != "INR" || q.Quantity.Unit != "kg" {
		unavailable("unsupported_context", "Legacy adapter supports only IN / USD / INR / kg")
		return
	}
	legacy := map[string]any{"material": q.Material, "quantity_kg": json.Number(q.Quantity.Amount), "current_stock_kg": json.Number(q.CurrentStock.Amount), "monthly_consumption_kg": json.Number(q.MonthlyConsumption.Amount), "need_by_month": q.NeedByMonth, "supplier_lead_time_months": q.LeadTimeMonths, "risk_appetite": q.RiskAppetite}
	if q.Budget != nil {
		legacy["max_budget_inr"] = json.Number(q.Budget.Amount)
	}
	payload, _ := json.Marshal(legacy)
	body, status, e := fetch(r.Context(), c, upstream, "POST", "/recommend", payload)
	if e != nil || status >= 500 {
		writeError(w, 503, "provider_unavailable", "Recommendation service unavailable", "")
		return
	}
	if status == 404 {
		var problem struct {
			Detail string `json:"detail"`
		}
		_ = decodeUpstream(body, &problem)
		detail := strings.Trim(problem.Detail, `"`)
		prefix := "Unknown material '" + q.Material + "'. Known: "
		if strings.HasPrefix(detail, prefix) && len(strings.TrimSpace(strings.TrimPrefix(detail, prefix))) > 0 {
			unavailable("unsupported_material", "Material is not supported by the legacy engine")
			return
		}
		writeError(w, 502, "invalid_upstream", "Invalid recommendation service response", "")
		return
	}
	if status != 200 {
		writeError(w, 502, "invalid_upstream", "Invalid recommendation service response", "")
		return
	}
	plan, e := adapt(body, q)
	if e != nil {
		writeError(w, 502, "invalid_upstream", "Invalid recommendation service response", "")
		return
	}
	var source legacyResponse
	_ = decodeUpstream(body, &source)
	warnings := append([]string{}, source.Warnings...)
	warnings = append(warnings, "Inventory feasibility is unverified. Review stock and arrival timing before relying on this schedule.", "Recommendation estimates have limited numerical precision and include rounded values.")
	if len(plan.Schedule) == 0 && len(plan.Comparisons) == 0 {
		write(w, 200, contract.PlanResponse{ContractVersion: version, Status: "empty", Availability: contract.Availability{Code: "empty_result", Reason: "Legacy computation returned no purchase schedule or comparisons"}, Request: q, Warnings: warnings})
		return
	}
	write(w, 200, contract.PlanResponse{ContractVersion: version, Status: "ready", Availability: contract.Availability{Code: "available", Reason: "Legacy computation completed"}, Request: q, Plan: &plan, Warnings: warnings})
}

type legacyLine struct {
	Month         *int        `json:"month"`
	CalendarMonth string      `json:"calendar_month"`
	Kg            json.Number `json:"kg"`
	Percent       json.Number `json:"percent"`
	Expected      json.Number `json:"expected_inr_per_kg"`
}
type legacyComparison struct {
	Name     string      `json:"name"`
	Schedule string      `json:"schedule"`
	Expected json.Number `json:"expected_cost_inr"`
	P90      json.Number `json:"p90_cost_inr"`
	Penalty  json.Number `json:"stockout_penalty_inr"`
}
type legacySource struct {
	Used      string  `json:"used"`
	Status    string  `json:"status"`
	FetchedAt *string `json:"fetched_at"`
}
type legacyResponse struct {
	ID         *string                    `json:"id"`
	Material   string                     `json:"material"`
	Action     string                     `json:"action"`
	Summary    string                     `json:"summary"`
	Current    json.Number                `json:"current_inr_per_kg"`
	Latest     string                     `json:"latest_data_month"`
	Schedule   []legacyLine               `json:"schedule"`
	Comparison []legacyComparison         `json:"comparison"`
	Warnings   []string                   `json:"warnings"`
	Provenance map[string]json.RawMessage `json:"provenance"`
}

func decimal(n json.Number) (string, error) {
	s := n.String()
	if !numericRE.MatchString(s) {
		return "", errors.New("invalid number")
	}
	parts := strings.Split(strings.ToLower(s), "e")
	mant := parts[0]
	exp := 0
	if len(parts) > 1 {
		var e error
		exp, e = strconv.Atoi(parts[1])
		if e != nil || exp > 100 || exp < -100 {
			return "", errors.New("number exponent unsupported")
		}
	}
	neg := strings.HasPrefix(mant, "-")
	mant = strings.TrimPrefix(mant, "-")
	dot := strings.Index(mant, ".")
	if dot < 0 {
		dot = len(mant)
	}
	digits := strings.ReplaceAll(mant, ".", "")
	pos := dot + exp
	if pos <= 0 {
		digits = "0." + strings.Repeat("0", -pos) + digits
	} else if pos >= len(digits) {
		digits += strings.Repeat("0", pos-len(digits))
	} else {
		digits = digits[:pos] + "." + digits[pos:]
	}
	if strings.Contains(digits, ".") {
		parts := strings.SplitN(digits, ".", 2)
		head := strings.TrimLeft(parts[0], "0")
		if head == "" {
			head = "0"
		}
		digits = head + "." + parts[1]
	} else {
		digits = strings.TrimLeft(digits, "0")
		if digits == "" {
			digits = "0"
		}
	}
	if neg {
		digits = "-" + digits
	}
	return digits, nil
}
func adapt(b []byte, q contract.PlanningRequest) (contract.LegacyPlan, error) {
	var src legacyResponse
	var plan contract.LegacyPlan
	var fields map[string]json.RawMessage
	if e := decodeUpstream(b, &fields); e != nil || !required(fields, "material", "action", "summary", "current_inr_per_kg", "latest_data_month", "schedule", "comparison", "warnings", "provenance") {
		return plan, errors.New("missing or invalid upstream fields")
	}
	if e := decodeUpstream(b, &src); e != nil {
		return plan, e
	}
	if src.Material != q.Material || src.Summary == "" || src.Latest == "" || src.Schedule == nil || src.Comparison == nil || src.Provenance == nil || (src.Action != "BUY NOW" && src.Action != "SPLIT BUY" && src.Action != "WAIT") {
		return plan, errors.New("missing or invalid upstream fields")
	}
	month, e := time.Parse("Jan 2006", src.Latest)
	if e != nil {
		return plan, e
	}
	current, e := decimal(src.Current)
	if e != nil {
		return plan, e
	}
	currentRat, ok := new(big.Rat).SetString(current)
	if !ok || currentRat.Sign() < 0 {
		return plan, errors.New("invalid current price")
	}
	if (len(src.Schedule) == 0) != (len(src.Comparison) == 0) {
		return plan, errors.New("incomplete upstream plan")
	}
	plan = contract.LegacyPlan{ComputationOnly: true, Action: src.Action, Summary: src.Summary, CurrentPrice: contract.UnitPrice{Money: contract.Money{Amount: current, Currency: "INR"}, PerUnit: "kg"}, Schedule: []contract.PurchaseLine{}, Comparisons: []contract.Comparison{}, Evidence: []contract.SourceEvidence{}, LegacyRecommendationId: src.ID, Precision: "legacy_float_rounded"}
	quantityTotal := new(big.Rat)
	percentTotal := new(big.Rat)
	for _, row := range src.Schedule {
		if row.Month == nil || *row.Month < 0 || *row.Month > 6 || row.CalendarMonth == "" {
			return plan, errors.New("invalid schedule")
		}
		if _, e := time.Parse("Jan 2006", row.CalendarMonth); e != nil {
			return plan, e
		}
		qty, e := decimal(row.Kg)
		if e != nil {
			return plan, e
		}
		pct, e := decimal(row.Percent)
		if e != nil {
			return plan, e
		}
		price, e := decimal(row.Expected)
		if e != nil {
			return plan, e
		}
		qrat, pok := new(big.Rat).SetString(qty)
		prat, ppok := new(big.Rat).SetString(pct)
		if !pok || !ppok || qrat.Sign() < 0 || prat.Sign() < 0 || prat.Cmp(big.NewRat(100, 1)) > 0 || strings.HasPrefix(price, "-") {
			return plan, errors.New("invalid schedule values")
		}
		quantityTotal.Add(quantityTotal, qrat)
		percentTotal.Add(percentTotal, prat)
		plan.Schedule = append(plan.Schedule, contract.PurchaseLine{PurchaseMonth: *row.Month, CalendarMonth: row.CalendarMonth, Quantity: contract.Quantity{Amount: qty, Unit: "kg"}, Percent: pct, ExpectedUnitPrice: contract.UnitPrice{Money: contract.Money{Amount: price, Currency: "INR"}, PerUnit: "kg"}})
	}
	if len(src.Schedule) > 0 {
		requested, ok := new(big.Rat).SetString(q.Quantity.Amount)
		if !ok {
			return plan, errors.New("invalid request quantity")
		}
		// Legacy rows round kg and percent to one decimal, so each row may differ by at most 0.05.
		tolerance := big.NewRat(int64(len(src.Schedule)), 20)
		quantityDelta := new(big.Rat).Sub(quantityTotal, requested)
		quantityDelta.Abs(quantityDelta)
		percentDelta := new(big.Rat).Sub(percentTotal, big.NewRat(100, 1))
		percentDelta.Abs(percentDelta)
		if quantityDelta.Cmp(tolerance) > 0 || percentDelta.Cmp(tolerance) > 0 {
			return plan, errors.New("incomplete schedule totals")
		}
	}
	for _, row := range src.Comparison {
		if row.Name == "" || row.Schedule == "" {
			return plan, errors.New("invalid comparison")
		}
		a, e := decimal(row.Expected)
		if e != nil {
			return plan, e
		}
		p, e := decimal(row.P90)
		if e != nil {
			return plan, e
		}
		s, e := decimal(row.Penalty)
		if e != nil {
			return plan, e
		}
		if strings.HasPrefix(a, "-") || strings.HasPrefix(p, "-") || strings.HasPrefix(s, "-") {
			return plan, errors.New("invalid comparison values")
		}
		plan.Comparisons = append(plan.Comparisons, contract.Comparison{Name: row.Name, Schedule: row.Schedule, ExpectedCost: contract.Money{Amount: a, Currency: "INR"}, P90Cost: contract.Money{Amount: p, Currency: "INR"}, StockoutPenalty: contract.Money{Amount: s, Currency: "INR"}})
	}
	keys := make([]string, 0, len(src.Provenance))
	for key := range src.Provenance {
		if key != "any_synthetic" && key != "any_sample" {
			keys = append(keys, key)
		}
	}
	sort.Strings(keys)
	for _, key := range keys {
		raw := src.Provenance[key]
		var source legacySource
		if e := json.Unmarshal(raw, &source); e != nil || source.Used == "" || source.Status == "" {
			return plan, errors.New("invalid provenance")
		}
		switch source.Used {
		case "live", "cache", "synthetic", "sample", "unknown":
		default:
			source.Used = "unknown"
		}
		var observation *string
		if source.FetchedAt != nil {
			if _, e := time.Parse(time.RFC3339Nano, *source.FetchedAt); e != nil {
				return plan, errors.New("invalid provenance date")
			}
		}
		if key == "prices" {
			v := month.Format("2006-01-02")
			observation = &v
		}
		basis := "Reported source provenance; observation period only where known"
		if key == "prices" {
			basis = "Monthly price period; ISO day 1 is a period marker, not a source publication day"
		}
		plan.Evidence = append(plan.Evidence, contract.SourceEvidence{Source: key, Used: source.Used, Status: source.Status, RetrievedAt: source.FetchedAt, ObservationEnd: observation, Basis: basis})
	}
	if len(plan.Evidence) == 0 {
		return plan, errors.New("missing provenance")
	}
	content := plan
	content.LegacyRecommendationId = nil
	snapshot, _ := json.Marshal(struct {
		Request contract.PlanningRequest `json:"request"`
		Plan    contract.LegacyPlan      `json:"plan"`
	}{q, content})
	sum := sha256.Sum256(snapshot)
	id := hex.EncodeToString(sum[:])
	plan.PlanId = id[:24]
	plan.PlanVersion = id
	return plan, nil
}
