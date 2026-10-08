package main

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"strconv"
	"strings"
	"testing"
	"time"
)

// This guard uses the real local authenticator, not a permission stub.
func TestShipmentRouteAuthorityBeforeAvailability(t *testing.T) {
	s := &stateServer{cfg: stateConfig{Mode: "local"}, identityReady: true,
		identities: []identity{{Token: "synthetic-local-only", PrincipalID: "p1", WorkspaceID: "W", Role: "viewer"}}}
	s.auth = s
	for _, action := range []string{"contexts", "comparisons"} {
		r := httptest.NewRequest("GET", "/v2/workspaces/FOREIGN/shipment-recovery/"+action, nil)
		r.RemoteAddr = "127.0.0.1:12345"
		r.Header.Set("Authorization", "Bearer synthetic-local-only")
		w := httptest.NewRecorder()
		s.ServeHTTP(w, r)
		if w.Code != 403 {
			t.Errorf("%s foreign workspace: got %d, want 403 before availability; body=%s", action, w.Code, w.Body.String())
		}
	}
}

func shipmentConnectedPython(t *testing.T) (string, string) {
	t.Helper()
	root, err := filepath.Abs("../..")
	if err != nil {
		t.Fatal(err)
	}
	python := filepath.Join(filepath.Dir(root), "Closed_Source_Builders_Cup", "procurement-engine", ".venv", "bin", "python")
	if _, err := os.Stat(python); err != nil {
		t.Fatalf("existing local acceptance interpreter unavailable: %v", err)
	}
	return python, filepath.Join(root, "procurement-engine", "src")
}

// The test-only wrapper executes the fixed module under a guard in the actual child.
// Its only diagnostic write is a zero-attempt count in this test's disposable directory.
func shipmentGuardedPython(t *testing.T, python string) (string, string) {
	t.Helper()
	dir := t.TempDir()
	proof := filepath.Join(dir, "guard-proof")
	guard := fmt.Sprintf(`import sys,runpy,atexit,builtins,os
attempts=[]
def deny(kind):
 attempts.append(kind)
 raise RuntimeError("offline operation rejected")
class Block:
 def find_spec(self,name,path=None,target=None):
  if name.split('.')[0] in ('google','dotenv','requests','httpx') or name in ('procure.settings','procure.service_app'): deny('provider_import')
sys.meta_path.insert(0,Block())
def audit(event,args):
 if event.startswith('socket.') or event=='subprocess.Popen': deny('network_or_subprocess')
 if event=='open' and isinstance(args[0],(str,bytes,os.PathLike)):
  p=os.fsdecode(args[0]); name=os.path.basename(p).lower()
  if name=='.env' or name.startswith('.env.') or 'credential' in name or name in ('adc.json','key.json','keys.json','database-url'): deny('sensitive_read')
sys.addaudithook(audit)
def finish():
 with open(%q,'a') as f: f.write(str(len(attempts))+'\n')
atexit.register(finish)
if sys.argv[1]=='-m':
 if sys.argv[2]!='procure.production.shipment_local_bridge': deny('unexpected_module')
 runpy.run_module(sys.argv[2],run_name='__main__')
elif sys.argv[1]=='-c': exec(sys.argv[2])
else: deny('unexpected_argv')
`, proof)
	// Single-quote shell escaping preserves the complete guard literally.
	quote := func(v string) string { return "'" + strings.ReplaceAll(v, "'", "'\"'\"'") + "'" }
	wrapper := filepath.Join(dir, "guarded-python")
	script := "#!/bin/sh\nexec " + quote(python) + " -c " + quote(guard) + " \"$@\"\n"
	if err := os.WriteFile(wrapper, []byte(script), 0700); err != nil {
		t.Fatal(err)
	}
	return wrapper, proof
}

func shipmentConnectedFixture(t *testing.T, kind string) HostSpec {
	t.Helper()
	b, err := os.ReadFile("../../contracts/examples/recovery_request.json")
	if err != nil {
		t.Fatal(err)
	}
	var base map[string]any
	if err = json.Unmarshal(b, &base); err != nil {
		t.Fatal(err)
	}
	production := base["snapshot"].(map[string]any)
	graphJSON := `{"workspace_id":"W","plan_version":"v7","snapshot_id":"graph-1","as_of":"2026-10-09T00:00:00Z","synthetic":true,"nodes":[{"id":"SH","kind":"shipment"},{"id":"PO","kind":"purchase_order"},{"id":"C","kind":"component"},{"id":"O-A","kind":"production_order"},{"id":"K","kind":"commitment"}],"edges":[{"from":"SH","to":"PO"},{"from":"PO","to":"C"},{"from":"C","to":"O-A"},{"from":"O-A","to":"K"}],"inventory":[{"component_id":"C","unit":"kg","starting_stock":"2"}],"arrival_lines":[{"line_id":"L1","shipment_id":"SH","purchase_order_id":"PO","component_id":"C","unit":"kg","quantity":"4","arrival_at":"2026-10-10T09:00:00Z"}],"commitments":[{"commitment_id":"K","production_order_id":"O-A","required_at":"2026-10-10T09:00:00Z","due_at":"2026-10-10T10:00:00Z","demands":[{"component_id":"C","unit":"kg","quantity":"6"}]}]}`
	rawJSON := `[{"event_id":"event-1","shipment_id":"SH","type":"eta_changed","occurred_at":"2026-10-08T01:00:00Z","observed_at":"2026-10-08T01:01:00Z","eta":"2026-10-10T10:00:00Z","synthetic":true,"lane":{"origin":"AEJEA","destination":"INMAA","mode":"ocean"},"source":{"provider_id":"synthetic-carrier","kind":"carrier_feed"},"evidence":[{"evidence_id":"e1","kind":"carrier_status","locator":"synthetic://status/1","content_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","retrieved_at":"2026-10-08T01:01:00Z","valid_until":null}]}]`
	var graph map[string]any
	var raw []any
	if err := json.Unmarshal([]byte(graphJSON), &graph); err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal([]byte(rawJSON), &raw); err != nil {
		t.Fatal(err)
	}
	assignments := []any{map[string]any{"order_id": "O-A", "period_id": "P2"}, map[string]any{"order_id": "O-B", "period_id": "P2"}, map[string]any{"order_id": "O-U", "period_id": "P1"}}
	switch kind {
	case "noop":
		raw[0].(map[string]any)["eta"] = "2026-10-10T09:00:00Z"
		assignments = production["baseline_assignments"].([]any)
	case "precision":
		graph["arrival_lines"].([]any)[0].(map[string]any)["quantity"] = "4.123456789123456789"
		production["receipts"].([]any)[0].(map[string]any)["quantity"] = "4.123456789123456789"
		graph["inventory"].([]any)[0].(map[string]any)["starting_stock"] = "2.123456789123456789"
		production["inventory"].([]any)[0].(map[string]any)["starting_stock"] = "2.123456789123456789"
	case "unsupported":
		huge := strings.Repeat("9", 1024)
		for _, i := range []int{0, 1} {
			production["orders"].([]any)[i].(map[string]any)["output_quantity"] = huge
		}
		production["routings"].([]any)[0].(map[string]any)["run_seconds_per_output_unit"] = huge
	case "noeligible":
		raw = []any{}
	case "badjoin":
	}
	joins := []any{map[string]any{"line_id": "L1", "receipt_id": "r-C"}}
	if kind == "badjoin" {
		joins = []any{}
	}
	request := map[string]any{"adapter_version": "1", "raw_records": raw, "propagation_snapshot": graph, "production_snapshot": production, "joins": joins, "recovery_assignments": assignments}
	data, err := shipmentMarshal(request)
	if err != nil {
		t.Fatal(err)
	}
	return HostSpec{WorkspaceID: "W", PlanVersion: "v7", PropagationSnapshotID: "graph-1", ProductionSnapshotID: production["snapshot_id"].(string), AsOf: "2026-10-09T00:00:00Z", Request: data}
}

func shipmentStrictTestValue(t *testing.T, raw []byte) any {
	t.Helper()
	d := json.NewDecoder(bytes.NewReader(raw))
	d.UseNumber()
	var v any
	if err := d.Decode(&v); err != nil {
		t.Fatal(err)
	}
	if err := d.Decode(new(any)); err != io.EOF {
		t.Fatalf("trailing fixture JSON: %v", err)
	}
	return v
}

const shipmentIndependentExpected = `import json,sys,hashlib,copy
from procure.production.shipment_recovery import compare_shipment_production_recovery
from procure.production.recovery import compare_receipt_recovery
r=json.load(sys.stdin); original=copy.deepcopy(r)
def digest(v): return hashlib.sha256(json.dumps(v,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()).hexdigest()
g,p=r['propagation_snapshot'],r['production_snapshot']
b={'adapter_version':'1','synthetic':True,'workspace_id':g['workspace_id'],'plan_version':g['plan_version'],'propagation_snapshot_id':g['snapshot_id'],'production_snapshot_id':p['snapshot_id'],'as_of':g['as_of']}
for name,key in [('raw_sha256','raw_records'),('propagation_sha256','propagation_snapshot'),('production_sha256','production_snapshot'),('joins_sha256','joins'),('assignments_sha256','recovery_assignments')]: b[name]=digest(r[key])
out=compare_shipment_production_recovery(r,expected_binding=b)
public=[]
for row in out['comparisons']:
 expected=compare_receipt_recovery(p,row['receipt_scenario'],row['recovery'])
 assert row['result']==expected
 public.append(expected)
assert r==original
print(json.dumps({'outcome':out,'public_results':public},sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False))
`

func shipmentConnectedCall(t *testing.T, client *http.Client, base, method, path, token string, body []byte) (int, []byte) {
	t.Helper()
	req, err := http.NewRequest(method, base+path, bytes.NewReader(body))
	if err != nil {
		t.Fatal(err)
	}
	if token != "" {
		req.Header.Set("Authorization", "Bearer "+token)
	}
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	res, err := client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer res.Body.Close()
	data, err := io.ReadAll(io.LimitReader(res.Body, int64(shipmentWireLimit)+1))
	if err != nil || len(data) > shipmentWireLimit {
		t.Fatalf("HTTP bound/read: %v", err)
	}
	if res.Header.Get("Cache-Control") != "no-store" {
		t.Fatal("shipment response must not cache")
	}
	return res.StatusCode, data
}

func TestShipmentConnectedFourWholePublicEqualities(t *testing.T) {
	python, source := shipmentConnectedPython(t)
	guarded, proof := shipmentGuardedPython(t, python)
	for _, kind := range []string{"delayed", "noop", "precision", "unsupported"} {
		t.Run(kind, func(t *testing.T) {
			spec := shipmentConnectedFixture(t, kind)
			original := append([]byte(nil), spec.Request...)
			catalog, err := shipmentMarshal(map[string]any{"catalog_version": "1", "contexts": []HostSpec{spec}})
			if err != nil {
				t.Fatal(err)
			}
			file := filepath.Join(t.TempDir(), "synthetic-catalog.json")
			if err = os.WriteFile(file, catalog, 0600); err != nil {
				t.Fatal(err)
			}
			cfg := stateConfig{Mode: "local", ShipmentContextsPath: file, ShipmentPythonPath: guarded, ShipmentSourcePath: source}
			s := &stateServer{cfg: cfg, shipment: initShipmentHTTP(cfg), identityReady: true, identities: []identity{{Token: "synthetic-viewer", PrincipalID: "viewer", WorkspaceID: "W", Role: "viewer"}, {Token: "synthetic-editor", PrincipalID: "editor", WorkspaceID: "W", Role: "editor"}, {Token: "synthetic-foreign", PrincipalID: "other", WorkspaceID: "FOREIGN", Role: "viewer"}}}
			s.auth = s
			if s.shipment == nil {
				t.Fatal("real catalog/fixed child initialization unavailable")
			}
			server := httptest.NewServer(s)
			defer server.Close()
			defer s.Shutdown(context.Background())
			prefix := "/v2/workspaces/W/shipment-recovery/"
			code, list := shipmentConnectedCall(t, server.Client(), server.URL, "GET", prefix+"contexts", "synthetic-viewer", nil)
			if code != 200 {
				t.Fatalf("list: %d %s", code, list)
			}
			var listed struct {
				ContractVersion string            `json:"contract_version"`
				Contexts        []ContextMetadata `json:"contexts"`
			}
			if err = json.Unmarshal(list, &listed); err != nil || len(listed.Contexts) != 1 {
				t.Fatalf("list metadata: %v %s", err, list)
			}
			metadata := listed.Contexts[0]
			digest := sha256.Sum256(original)
			if metadata.RequestSHA256 != hex.EncodeToString(digest[:]) || metadata.SourceAuthenticity != "not_verified" || metadata.Persistence != "ephemeral" {
				t.Fatal("claimed/different host provenance")
			}
			body, _ := shipmentMarshal(map[string]any{"contract_version": "0.2.0", "context_id": metadata.ContextID, "expected_revision": metadata.Revision})
			code, response := shipmentConnectedCall(t, server.Client(), server.URL, "POST", prefix+"comparisons", "synthetic-viewer", body)
			if code != 200 {
				t.Fatalf("connected %s: %d %s", kind, code, response)
			}
			shipmentContractCheck(t, "ShipmentRecoveryResponse", response)
			var envelope struct {
				ContractVersion string          `json:"contract_version"`
				Context         ContextMetadata `json:"context"`
				OutcomeJSON     string          `json:"outcome_json"`
			}
			if err = json.Unmarshal(response, &envelope); err != nil || envelope.Context != metadata {
				t.Fatalf("response metadata: %v", err)
			}
			expectedCmd := exec.Command(guarded, "-c", shipmentIndependentExpected)
			expectedCmd.Dir = source
			expectedCmd.Env = []string{"PATH=/usr/bin:/bin", "PYTHONPATH=" + source, "PYTHONDONTWRITEBYTECODE=1", "PYTHONNOUSERSITE=1"}
			expectedCmd.Stdin = bytes.NewReader(original)
			expectedBytes, err := expectedCmd.Output()
			if err != nil {
				t.Fatalf("independent local public math: %v", err)
			}
			expected := shipmentStrictTestValue(t, expectedBytes).(map[string]any)
			actual := shipmentStrictTestValue(t, []byte(envelope.OutcomeJSON)).(map[string]any)
			if !reflect.DeepEqual(actual, expected["outcome"]) {
				t.Fatalf("%s complete outcome/evidence mismatch", kind)
			}
			rows := actual["comparisons"].([]any)
			results := expected["public_results"].([]any)
			if len(rows) != 1 || len(results) != 1 || !reflect.DeepEqual(rows[0].(map[string]any)["result"], results[0]) {
				t.Fatal("whole public result mismatch")
			}
			result := results[0].(map[string]any)
			if kind == "unsupported" && result["status"] != "unsupported" {
				t.Fatal("missing unsupported state")
			}
			if kind != "unsupported" && result["status"] != "compared" {
				t.Fatal("missing compared state")
			}
			if kind == "precision" && !strings.Contains(envelope.OutcomeJSON, "3.876543210876543211") {
				t.Fatal("lost exact decimal")
			}
			if !bytes.Equal(spec.Request, original) {
				t.Fatal("input mutation")
			}
			// Real HTTP membership remains required even when a foreign client knows this reference.
			if code, _ := shipmentConnectedCall(t, server.Client(), server.URL, "POST", prefix+"comparisons", "synthetic-foreign", body); code != 403 {
				t.Fatalf("foreign HTTP identity got%d", code)
			}
			for _, token := range []string{"synthetic-viewer", "synthetic-editor"} {
				stale, _ := shipmentMarshal(map[string]any{"contract_version": "0.2.0", "context_id": metadata.ContextID, "expected_revision": 2})
				if code, _ := shipmentConnectedCall(t, server.Client(), server.URL, "POST", prefix+"comparisons", token, stale); code != 409 {
					t.Fatalf("stale %s got%d", token, code)
				}
			}
			t.Logf("shipment_connected kind=%s whole_public_equal=true complete_evidence_equal=true decimal_strings=true actual_auth=true fixed_module=%s", kind, shipmentModule)
		})
	}
	b, err := os.ReadFile(proof)
	if err != nil {
		t.Fatal(err)
	}
	lines := strings.Fields(string(b))
	if len(lines) != 8 {
		t.Fatalf("expected8 guarded actual/independent children, got%d: %q", len(lines), b)
	}
	for _, n := range lines {
		if n != "0" {
			t.Fatal("offline guard rejected prohibited operation")
		}
	}
	t.Log("offline_guard executions=8 prohibited_attempts=0")
}

func shipmentExpandedSchema(value any, definitions map[string]any) any {
	switch v := value.(type) {
	case map[string]any:
		if name, ok := v["$ref"].(string); ok {
			return shipmentExpandedSchema(definitions[strings.TrimPrefix(name, "#/components/schemas/")], definitions)
		}
		copy := map[string]any{}
		for k, c := range v {
			copy[k] = shipmentExpandedSchema(c, definitions)
		}
		return copy
	case []any:
		copy := make([]any, len(v))
		for i, c := range v {
			copy[i] = shipmentExpandedSchema(c, definitions)
		}
		return copy
	default:
		return v
	}
}

// Envelope-only checker adds integer revision handling without broadening receipt validation.
func shipmentEnvelopeValid(value any, schema map[string]any) bool {
	switch schema["type"] {
	case "integer":
		n, ok := value.(json.Number)
		if !ok || strings.ContainsAny(string(n), ".eE") {
			return false
		}
		v, err := strconv.ParseInt(string(n), 10, 64)
		if err != nil {
			return false
		}
		if min, ok := schema["minimum"].(float64); ok && v < int64(min) {
			return false
		}
		if max, ok := schema["maximum"].(float64); ok && v > int64(max) {
			return false
		}
		return true
	case "object":
		object, ok := value.(map[string]any)
		if !ok {
			return false
		}
		properties, ok := schema["properties"].(map[string]any)
		if !ok {
			return false
		}
		for _, name := range schema["required"].([]any) {
			if _, ok := object[name.(string)]; !ok {
				return false
			}
		}
		for name, v := range object {
			def, ok := properties[name]
			if !ok {
				if schema["additionalProperties"] == false {
					return false
				}
				continue
			}
			if !shipmentEnvelopeValid(v, def.(map[string]any)) {
				return false
			}
		}
		return true
	case "array":
		rows, ok := value.([]any)
		if !ok {
			return false
		}
		if max, ok := schema["maxItems"].(float64); ok && len(rows) > int(max) {
			return false
		}
		if min, ok := schema["minItems"].(float64); ok && len(rows) < int(min) {
			return false
		}
		for _, v := range rows {
			if !shipmentEnvelopeValid(v, schema["items"].(map[string]any)) {
				return false
			}
		}
		return true
	default:
		if max, ok := schema["x-max-utf8-bytes"].(float64); ok {
			text, ok := value.(string)
			if !ok || len(text) > int(max) {
				return false
			}
		}
		return recoveryValid(value, schema, 0)
	}
}

func shipmentContractCheck(t *testing.T, name string, raw []byte) {
	t.Helper()
	b, err := os.ReadFile("internal/contract/shipment_recovery_schema.json")
	if err != nil {
		t.Fatal(err)
	}
	var doc map[string]any
	if err = json.Unmarshal(b, &doc); err != nil {
		t.Fatal(err)
	}
	defs := doc["components"].(map[string]any)["schemas"].(map[string]any)
	schema := shipmentExpandedSchema(defs[name], defs).(map[string]any)
	if !shipmentEnvelopeValid(shipmentStrictTestValue(t, raw), schema) {
		t.Fatalf("%s violates generated authoritative envelope schema", name)
	}
}

func TestShipmentGeneratedContractExamplesAndPrecision(t *testing.T) {
	for file, name := range map[string]string{"shipment_recovery_request.json": "ShipmentRecoveryRequest", "shipment_recovery_response.json": "ShipmentRecoveryResponse"} {
		b, err := os.ReadFile("../../contracts/examples/" + file)
		if err != nil {
			t.Fatal(err)
		}
		shipmentContractCheck(t, name, b)
	}
	// The inner payload stays a string through ordinary Go envelope transport.
	exact := `{"amount":"9007199254740993.123456789123456789","record_index":0,"valid_until":null}`
	v := map[string]any{"contract_version": "0.2.0", "context": map[string]any{"context_id": strings.Repeat("a", 32), "revision": 1, "workspace_id": "W", "plan_version": "v7", "propagation_snapshot_id": "graph", "production_snapshot_id": "prod", "as_of": "2026-10-09T00:00:00Z", "synthetic": true, "source": "host_configured_synthetic", "persistence": "ephemeral", "source_authenticity": "not_verified", "request_sha256": strings.Repeat("b", 64)}, "outcome_json": exact}
	b, err := shipmentMarshal(v)
	if err != nil {
		t.Fatal(err)
	}
	shipmentContractCheck(t, "ShipmentRecoveryResponse", b)
	if got := shipmentStrictTestValue(t, b).(map[string]any)["outcome_json"]; got != exact {
		t.Fatal("envelope changed exact inner text")
	}
}

func TestShipmentConnectedSourceJoinAndProjectionAvailability(t *testing.T) {
	python, source := shipmentConnectedPython(t)
	guarded, proof := shipmentGuardedPython(t, python)
	for _, kind := range []string{"noeligible", "badjoin", "expired", "future", "partial", "hold", "projection"} {
		t.Run(kind, func(t *testing.T) {
			spec := shipmentConnectedFixture(t, kind)
			if kind != "noeligible" && kind != "badjoin" {
				r := shipmentStrictTestValue(t, spec.Request).(map[string]any)
				raw := r["raw_records"].([]any)[0].(map[string]any)
				switch kind {
				case "expired":
					raw["evidence"].([]any)[0].(map[string]any)["valid_until"] = "2026-10-08T02:00:00Z"
				case "future":
					raw["observed_at"] = "2026-10-09T01:00:00Z"
				case "partial":
					raw["type"] = "partially_delivered"
					raw["quantity"] = map[string]any{"amount": "1", "unit": "kg"}
				case "hold":
					raw["type"] = "port_hold"
					delete(raw, "eta")
				case "projection":
					g := r["propagation_snapshot"].(map[string]any)
					g["edges"] = append(g["edges"].([]any), map[string]any{"from": "K", "to": "PO"})
				}
				spec.Request, _ = shipmentMarshal(r)
			}
			catalog, _ := shipmentMarshal(map[string]any{"catalog_version": "1", "contexts": []HostSpec{spec}})
			path := filepath.Join(t.TempDir(), "synthetic-catalog.json")
			if err := os.WriteFile(path, catalog, 0600); err != nil {
				t.Fatal(err)
			}
			cfg := stateConfig{Mode: "local", ShipmentContextsPath: path, ShipmentPythonPath: guarded, ShipmentSourcePath: source}
			s := &stateServer{cfg: cfg, shipment: initShipmentHTTP(cfg), identityReady: true, identities: []identity{{Token: "synthetic-member", PrincipalID: "p", WorkspaceID: "W", Role: "viewer"}}}
			s.auth = s
			if s.shipment == nil {
				t.Fatal("synthetic host context refused before fixed child")
			}
			server := httptest.NewServer(s)
			defer server.Close()
			defer s.Shutdown(context.Background())
			prefix := "/v2/workspaces/W/shipment-recovery/"
			code, list := shipmentConnectedCall(t, server.Client(), server.URL, "GET", prefix+"contexts", "synthetic-member", nil)
			if code != 200 {
				t.Fatalf("list%d", code)
			}
			shipmentContractCheck(t, "ShipmentRecoveryContextsResponse", list)
			listed := shipmentStrictTestValue(t, list).(map[string]any)["contexts"].([]any)[0].(map[string]any)
			body, _ := shipmentMarshal(map[string]any{"contract_version": "0.2.0", "context_id": listed["context_id"], "expected_revision": 1})
			code, response := shipmentConnectedCall(t, server.Client(), server.URL, "POST", prefix+"comparisons", "synthetic-member", body)
			want := 200
			if kind == "badjoin" || kind == "projection" {
				want = 422
			}
			if code != want {
				t.Fatalf("%s got%d want%d body%s", kind, code, want, response)
			}
			shipmentContractCheck(t, "ShipmentRecoveryResponse", response)
			envelope := shipmentStrictTestValue(t, response).(map[string]any)
			outcome := shipmentStrictTestValue(t, []byte(envelope["outcome_json"].(string))).(map[string]any)
			if len(outcome["comparisons"].([]any)) != 0 {
				t.Fatal("source/projection/join failure leaked numerical comparisons")
			}
			expected := exec.Command(guarded, "-c", shipmentIndependentExpected)
			expected.Dir = source
			expected.Env = []string{"PATH=/usr/bin:/bin", "PYTHONPATH=" + source, "PYTHONDONTWRITEBYTECODE=1", "PYTHONNOUSERSITE=1"}
			expected.Stdin = bytes.NewReader(spec.Request)
			b, err := expected.Output()
			if err != nil {
				t.Fatal(err)
			}
			direct := shipmentStrictTestValue(t, b).(map[string]any)["outcome"]
			if !reflect.DeepEqual(outcome, direct) {
				t.Fatal("availability altered complete direct evidence/result")
			}
			t.Logf("shipment_connected availability=%s http=%d status=%s full_evidence_equal=true partial_comparisons=0", kind, code, outcome["status"])
		})
	}
	b, err := os.ReadFile(proof)
	if err != nil {
		t.Fatal(err)
	}
	lines := strings.Fields(string(b))
	if len(lines) != 14 {
		t.Fatalf("guarded subprocess count%d want14", len(lines))
	}
	for _, n := range lines {
		if n != "0" {
			t.Fatal("offline guard rejected operation")
		}
	}
	t.Log("offline_guard availability_executions=14 prohibited_attempts=0")
}

func TestShipmentShutdownPreservesConcurrentLegacyCancellation(t *testing.T) {
	a := newShipmentAdapter(shipmentExecutorFunc(shipmentFakeChild))
	metadata, err := a.InstallHostContext(shipmentConnectedFixture(t, "delayed"))
	if err != nil {
		t.Fatal(err)
	}
	lease, leaseContext, err := a.acquire(context.Background(), "synthetic-p", "W", metadata.ContextID, 1)
	if err != nil {
		t.Fatal(err)
	}
	entered, canceled := make(chan struct{}), make(chan struct{})
	v := voiceTestAdapter(t, voiceExecutorFunc(func(ctx context.Context, input []byte) ([]byte, error) {
		close(entered)
		<-ctx.Done()
		close(canceled)
		return nil, ctx.Err()
	}))
	p := principal{ID: "synthetic-legacy-p", Workspace: "W", Role: "viewer"}
	created := voiceCreated(t, v, p)
	id := created["session_id"].(string)
	started := voiceDirect(t, v, p, "POST", voicePath+"/"+id+"/turns", voiceTurnBody(t, created["context_version"].(string)))
	if started.Code != 202 {
		t.Fatalf("legacy compatible typed setup: %d", started.Code)
	}
	select {
	case <-entered:
	case <-time.After(time.Second):
		a.release(lease)
		t.Fatal("legacy test executor did not enter")
	}
	s := &stateServer{shipment: a, voice: v}
	grace, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	done := make(chan error, 1)
	go func() { done <- s.Shutdown(grace) }()
	select {
	case <-leaseContext.Done():
	case <-time.After(time.Second):
		a.release(lease)
		t.Fatal("shipment lease not canceled")
	}
	// Until the legacy cancellation fires we deliberately cannot release this lease.
	// Serial shutdown therefore fails by a causal wait, not a scheduler ordering assumption.
	select {
	case <-canceled:
	case <-time.After(200 * time.Millisecond):
		t.Error("legacy cancellation blocked behind unreaped shipment lease")
	}
	a.release(lease)
	select {
	case err := <-done:
		if err != nil {
			t.Fatalf("shutdown error:%v", err)
		}
	case <-time.After(time.Second):
		t.Fatal("shutdown did not drain")
	}
}
