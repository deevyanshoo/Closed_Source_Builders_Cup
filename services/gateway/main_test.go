package main

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"closedsource/gateway/internal/contract"
)

const legacy = `{"id":"legacy-1","material":"copper","action":"SPLIT BUY","summary":"Buy now and later","current_inr_per_kg":8.00e2,"latest_data_month":"Jan 2006","schedule":[{"month":0,"calendar_month":"Oct 2026","kg":30000.0,"percent":60,"expected_inr_per_kg":8.4612e2},{"month":2,"calendar_month":"Dec 2026","kg":20000.0,"percent":40,"expected_inr_per_kg":900}],"comparison":[{"name":"Recommended","schedule":"Now and later","expected_cost_inr":42123456.123456789012345678,"p90_cost_inr":45000000,"stockout_penalty_inr":0}],"warnings":[],"provenance":{"prices":{"used":"synthetic","status":"ok","fetched_at":"2026-10-01T00:00:00Z"},"fx":{"used":"cache","status":"ok"}}}`

func fixture(t *testing.T, name string) []byte {
	t.Helper()
	b, e := os.ReadFile(filepath.Join("..", "..", "contracts", "examples", name))
	if e != nil {
		t.Fatal(e)
	}
	return b
}
func call(h http.Handler, method, path string, body []byte) *httptest.ResponseRecorder {
	r := httptest.NewRequest(method, path, bytes.NewReader(body))
	w := httptest.NewRecorder()
	h.ServeHTTP(w, r)
	return w
}
func TestRecommendationExactAndAdapted(t *testing.T) {
	calls := 0
	up := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls++
		if r.URL.Path != "/recommend" || r.Method != "POST" {
			t.Errorf("unexpected upstream %s %s", r.Method, r.URL.Path)
		}
		var v map[string]any
		d := json.NewDecoder(r.Body)
		d.UseNumber()
		if e := d.Decode(&v); e != nil {
			t.Error(e)
		}
		if v["quantity_kg"].(json.Number).String() != "50000" {
			t.Errorf("quantity %#v", v["quantity_kg"])
		}
		w.Write([]byte(legacy))
	}))
	defer up.Close()
	h := newHandler(up.URL, "../../contracts/examples")
	w := call(h, "POST", "/v2/plans/recommendations", fixture(t, "request.json"))
	if w.Code != 200 {
		t.Fatalf("%d %s", w.Code, w.Body.String())
	}
	var v map[string]any
	json.Unmarshal(w.Body.Bytes(), &v)
	p := v["plan"].(map[string]any)
	if p["current_price"].(map[string]any)["money"].(map[string]any)["amount"] != "800" {
		t.Error(w.Body.String())
	}
	if p["comparisons"].([]any)[0].(map[string]any)["expected_cost"].(map[string]any)["amount"] != "42123456.123456789012345678" {
		t.Error(w.Body.String())
	}
	if v["request"].(map[string]any)["quantity"].(map[string]any)["amount"] != "50000" || calls != 1 {
		t.Error(w.Body.String())
	}
}
func TestInvalidRequestsDoNotCallUpstream(t *testing.T) {
	calls := 0
	up := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { calls++ }))
	defer up.Close()
	h := newHandler(up.URL, "../../contracts/examples")
	base := fixture(t, "request.json")
	cases := []struct {
		name   string
		body   []byte
		status int
		code   string
	}{
		{"precision", fixture(t, "precision.json"), 422, "legacy_precision_unsupported"},
		{"unit mismatch", bytes.Replace(base, []byte(`"unit": "kg"`), []byte(`"unit": "tonne"`), 1), 422, "semantic_validation"},
		{"budget mismatch", bytes.Replace(base, []byte(`"risk_appetite": "medium"`), []byte(`"risk_appetite": "medium", "budget":{"amount":"10","currency":"USD"}`), 1), 422, "semantic_validation"},
		{"unknown field", bytes.Replace(base, []byte(`"material": "copper"`), []byte(`"material": "copper", "other":1`), 1), 422, "invalid_request"},
		{"missing schedule", bytes.Replace(base, []byte(`"need_by_month": 3,`), nil, 1), 422, "invalid_request"},
		{"oversize", []byte(`{"x":"` + strings.Repeat("x", 1<<20) + `"}`), 413, "body_too_large"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			w := call(h, "POST", "/v2/plans/recommendations", tc.body)
			if w.Code != tc.status || !strings.Contains(w.Body.String(), tc.code) {
				t.Fatalf("%d %s", w.Code, w.Body.String())
			}
		})
	}
	if calls != 0 {
		t.Fatalf("upstream called %d times", calls)
	}
}
func TestUnavailableAndMalformed(t *testing.T) {
	base := fixture(t, "request.json")
	for _, tc := range []struct {
		name, reply string
		code        int
		want        int
	}{{"malformed", `{"schedule":[]}`, 200, 502}, {"route missing", `{"detail":"Not Found"}`, 404, 502}} {
		t.Run(tc.name, func(t *testing.T) {
			up := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { w.WriteHeader(tc.code); w.Write([]byte(tc.reply)) }))
			defer up.Close()
			w := call(newHandler(up.URL, "../../contracts/examples"), "POST", "/v2/plans/recommendations", base)
			if w.Code != tc.want {
				t.Fatalf("%d %s", w.Code, w.Body.String())
			}
		})
	}
	unknown := bytes.Replace(base, []byte(`"material": "copper"`), []byte(`"material": "unobtainium"`), 1)
	up := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(404)
		w.Write([]byte(`{"detail":"\"Unknown material 'unobtainium'. Known: copper, aluminium, iron_ore\""}`))
	}))
	defer up.Close()
	w := call(newHandler(up.URL, "../../contracts/examples"), "POST", "/v2/plans/recommendations", unknown)
	if w.Code != 200 || !strings.Contains(w.Body.String(), `"code":"unsupported_material"`) {
		t.Fatalf("material 404 %d %s", w.Code, w.Body.String())
	}
	w = call(newHandler("http://127.0.0.1:1", "../../contracts/examples"), "POST", "/v2/plans/recommendations", base)
	if w.Code != 503 {
		t.Fatalf("%d %s", w.Code, w.Body.String())
	}
}
func TestFullLegacyResponseAndSemanticStates(t *testing.T) {
	calls := 0
	up := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls++
		if r.URL.Path == "/materials" {
			w.Write([]byte(`[{"key":"copper","display_name":"Copper","source_series":"Copper","source_unit":"USD per metric tonne","keywords":[],"landed_cost":{"freight":1}}]`))
			return
		}
		w.Write([]byte(strings.TrimSuffix(legacy, "}") + `,"forecast":{"unused":true},"confidence":{"level":"LOW"}}`))
	}))
	defer up.Close()
	h := newHandler(up.URL, "../../contracts/examples")
	w := call(h, "GET", "/v2/materials", nil)
	if w.Code != 200 || !strings.Contains(w.Body.String(), `"key":"copper"`) {
		t.Fatalf("catalog %d %s", w.Code, w.Body.String())
	}
	w = call(h, "POST", "/v2/plans/recommendations", fixture(t, "request.json"))
	if w.Code != 200 || !strings.Contains(w.Body.String(), `"status":"ready"`) {
		t.Fatalf("full response %d %s", w.Code, w.Body.String())
	}
	var first contract.PlanResponse
	if e := json.Unmarshal(w.Body.Bytes(), &first); e != nil {
		t.Fatal(e)
	}
	w = call(h, "POST", "/v2/plans/recommendations", fixture(t, "request.json"))
	var second contract.PlanResponse
	if e := json.Unmarshal(w.Body.Bytes(), &second); e != nil {
		t.Fatal(e)
	}
	if first.Plan.PlanVersion != second.Plan.PlanVersion {
		t.Fatal("content hash changed for identical response")
	}
	unsupported := bytes.Replace(fixture(t, "request.json"), []byte(`"destination_country": "IN"`), []byte(`"destination_country": "DE"`), 1)
	w = call(h, "POST", "/v2/plans/recommendations", unsupported)
	if w.Code != 200 || !strings.Contains(w.Body.String(), `"code":"unsupported_context"`) {
		t.Fatalf("unsupported %d %s", w.Code, w.Body.String())
	}
	if calls != 3 {
		t.Fatalf("unsupported context called upstream: %d", calls)
	}
	w = call(h, "PATCH", "/v2/actions/prepare_rfq", nil)
	if w.Code != 501 || !strings.Contains(w.Body.String(), "not_implemented") {
		t.Fatalf("actions %d %s", w.Code, w.Body.String())
	}
}
func TestDecimalAndEmptyResult(t *testing.T) {
	for in, want := range map[string]string{"0e2": "0", "0e-2": "0.00", "1.20e2": "120", "1e-2": "0.01", "-0e2": "-0"} {
		got, e := decimal(json.Number(in))
		if e != nil || got != want {
			t.Errorf("%s -> %q, %v", in, got, e)
		}
	}
	var emptyObject map[string]json.RawMessage
	if e := json.Unmarshal([]byte(legacy), &emptyObject); e != nil {
		t.Fatal(e)
	}
	emptyObject["schedule"] = json.RawMessage(`[]`)
	emptyObject["comparison"] = json.RawMessage(`[]`)
	emptyBytes, _ := json.Marshal(emptyObject)
	empty := string(emptyBytes)
	up := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { w.Write([]byte(empty)) }))
	defer up.Close()
	w := call(newHandler(up.URL, "../../contracts/examples"), "POST", "/v2/plans/recommendations", fixture(t, "request.json"))
	if w.Code != 200 || !strings.Contains(w.Body.String(), `"status":"empty"`) || !strings.Contains(w.Body.String(), `"plan":null`) {
		t.Fatalf("empty %d %s", w.Code, w.Body.String())
	}
}
func TestFixtureHTTPRead(t *testing.T) {
	w := call(newHandler("http://127.0.0.1:1", "../../contracts/examples"), "GET", "/v2/contracts/examples/precision", nil)
	if w.Code != 200 || !bytes.Equal(w.Body.Bytes(), fixture(t, "precision.json")) {
		t.Fatalf("fixture %d %s", w.Code, w.Body.String())
	}
}
func TestRejectInvalidUpstreamRows(t *testing.T) {
	q := contract.PlanningRequest{Material: "copper", Quantity: contract.Quantity{Amount: "50000", Unit: "kg"}}
	for _, tc := range []struct{ name, old, new string }{
		{"negative current price", `"current_inr_per_kg":8.00e2`, `"current_inr_per_kg":-1`},
		{"truncated schedule", `,{"month":2,"calendar_month":"Dec 2026","kg":20000.0,"percent":40,"expected_inr_per_kg":900}`, ``},
		{"percentage mismatch", `"percent":40`, `"percent":30`},
		{"partial empty comparison", `"comparison":[{"name":"Recommended","schedule":"Now and later","expected_cost_inr":42123456.123456789012345678,"p90_cost_inr":45000000,"stockout_penalty_inr":0}]`, `"comparison":[]`},
		{"partial empty schedule", `"schedule":[{"month":0,"calendar_month":"Oct 2026","kg":30000.0,"percent":60,"expected_inr_per_kg":8.4612e2},{"month":2,"calendar_month":"Dec 2026","kg":20000.0,"percent":40,"expected_inr_per_kg":900}]`, `"schedule":[]`},
		{"negative quantity", `"kg":30000.0`, `"kg":-1`},
		{"invalid month", `"calendar_month":"Oct 2026"`, `"calendar_month":"not a month"`},
		{"out of range purchase month", `"month":0`, `"month":-1`},
		{"missing schedule", `"schedule":[{"month":0,"calendar_month":"Oct 2026","kg":30000.0,"percent":60,"expected_inr_per_kg":8.4612e2},{"month":2,"calendar_month":"Dec 2026","kg":20000.0,"percent":40,"expected_inr_per_kg":900}],`, ``},
		{"invalid provenance date", `"fetched_at":"2026-10-01T00:00:00Z"`, `"fetched_at":"yesterday"`},
	} {
		t.Run(tc.name, func(t *testing.T) {
			b := strings.Replace(legacy, tc.old, tc.new, 1)
			if b == legacy {
				t.Fatal("regression mutation did not match fixture")
			}
			if _, e := adapt([]byte(b), q); e == nil {
				t.Fatal("accepted invalid upstream response")
			}
		})
	}
}
func TestLegacyRowRoundingTolerance(t *testing.T) {
	q := contract.PlanningRequest{Material: "copper", Quantity: contract.Quantity{Amount: "50000", Unit: "kg"}}
	b := strings.Replace(legacy, `"kg":20000.0,"percent":40`, `"kg":19999.9,"percent":39.9`, 1)
	if b == legacy {
		t.Fatal("fixture mutation did not match")
	}
	if _, e := adapt([]byte(b), q); e != nil {
		t.Fatalf("rejected two rows within 0.05 per row rounding allowance: %v", e)
	}
}
