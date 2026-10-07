package main

import (
	"bytes"
	"context"
	_ "embed"
	"encoding/json"
	"errors"
	"io"
	"mime"
	"net/http"
	"reflect"
	"regexp"
	"strings"
	"time"
)

//go:embed internal/contract/recovery_schema.json
var recoverySchemaBytes []byte

const recoveryPath = "/v2/production/receipt-recovery/comparisons"
const recoveryUpstreamPath = "/production/receipt-recovery/comparisons"

// Keep limits literal at the boundary too: malformed generated data never
// relaxes size or nesting limits at runtime.
const recoveryRequestLimit = 1 << 20
const recoveryResponseLimit = 2 << 20
const recoveryDepthLimit = 16

var recoveryDefinitions = func() map[string]map[string]any {
	var document struct {
		Components struct {
			Schemas map[string]map[string]any `json:"schemas"`
		} `json:"components"`
	}
	if json.Unmarshal(recoverySchemaBytes, &document) != nil {
		return nil
	}
	return document.Components.Schemas
}()

func recoveryValue(d *json.Decoder, depth int) (any, error) {
	if depth > recoveryDepthLimit {
		return nil, errors.New("depth")
	}
	token, err := d.Token()
	if err != nil {
		return nil, err
	}
	switch token {
	case json.Delim('{'):
		object := make(map[string]any)
		for d.More() {
			key, err := d.Token()
			if err != nil {
				return nil, err
			}
			name, ok := key.(string)
			if !ok {
				return nil, errors.New("object key")
			}
			if _, exists := object[name]; exists {
				return nil, errors.New("duplicate key")
			}
			value, err := recoveryValue(d, depth+1)
			if err != nil {
				return nil, err
			}
			object[name] = value
		}
		_, err = d.Token()
		return object, err
	case json.Delim('['):
		items := []any{}
		for d.More() {
			value, err := recoveryValue(d, depth+1)
			if err != nil {
				return nil, err
			}
			items = append(items, value)
		}
		_, err = d.Token()
		return items, err
	case json.Delim('}'), json.Delim(']'):
		return nil, errors.New("unexpected delimiter")
	default:
		return token, nil
	}
}

func recoveryParse(body []byte) (any, error) {
	d := json.NewDecoder(bytes.NewReader(body))
	d.UseNumber()
	value, err := recoveryValue(d, 0)
	if err != nil {
		return nil, err
	}
	if _, err = d.Token(); err != io.EOF {
		return nil, errors.New("trailing JSON")
	}
	return value, nil
}

func recoveryValid(value any, schema map[string]any, depth int) bool {
	if depth > recoveryDepthLimit || schema == nil {
		return false
	}
	if ref, ok := schema["$ref"].(string); ok {
		const prefix = "#/components/schemas/"
		if !strings.HasPrefix(ref, prefix) {
			return false
		}
		return recoveryValid(value, recoveryDefinitions[strings.TrimPrefix(ref, prefix)], depth+1)
	}
	switch schema["type"] {
	case "object":
		object, ok := value.(map[string]any)
		if !ok {
			return false
		}
		properties, ok := schema["properties"].(map[string]any)
		if !ok {
			return false
		}
		if required, ok := schema["required"].([]any); ok {
			for _, item := range required {
				key, ok := item.(string)
				if !ok {
					return false
				}
				if _, present := object[key]; !present {
					return false
				}
			}
		}
		for key, item := range object {
			property, present := properties[key]
			if !present {
				if schema["additionalProperties"] == false {
					return false
				}
				continue
			}
			definition, ok := property.(map[string]any)
			if !ok || !recoveryValid(item, definition, depth+1) {
				return false
			}
		}
	case "array":
		array, ok := value.([]any)
		if !ok {
			return false
		}
		if min, ok := schema["minItems"].(float64); ok && len(array) < int(min) {
			return false
		}
		if max, ok := schema["maxItems"].(float64); ok && len(array) > int(max) {
			return false
		}
		items, ok := schema["items"].(map[string]any)
		if !ok {
			return false
		}
		for _, item := range array {
			if !recoveryValid(item, items, depth+1) {
				return false
			}
		}
		if schema["uniqueItems"] == true {
			seen := make(map[string]bool, len(array))
			for _, item := range array {
				encoded, err := json.Marshal(item)
				if err != nil || seen[string(encoded)] {
					return false
				}
				seen[string(encoded)] = true
			}
		}
	case "string":
		text, ok := value.(string)
		if !ok {
			return false
		}
		if min, ok := schema["minLength"].(float64); ok && len([]rune(text)) < int(min) {
			return false
		}
		if max, ok := schema["maxLength"].(float64); ok && len([]rune(text)) > int(max) {
			return false
		}
		if pattern, ok := schema["pattern"].(string); ok {
			matched, err := regexp.MatchString(pattern, text)
			if err != nil || !matched {
				return false
			}
		}
	case "boolean":
		if _, ok := value.(bool); !ok {
			return false
		}
	default:
		return false
	}
	if constant, ok := schema["const"]; ok && !reflect.DeepEqual(value, constant) {
		return false
	}
	if enumeration, ok := schema["enum"].([]any); ok {
		matched := false
		for _, candidate := range enumeration {
			if reflect.DeepEqual(value, candidate) {
				matched = true
				break
			}
		}
		if !matched {
			return false
		}
	}
	return true
}

func recoveryObject(value any) map[string]any { object, _ := value.(map[string]any); return object }
func recoveryString(object map[string]any, key string) string {
	value, _ := object[key].(string)
	return value
}

func recoveryOrderSet(schedule any) (map[string]bool, bool) {
	rows, ok := recoveryObject(schedule)["orders"].([]any)
	if !ok {
		return nil, false
	}
	set := make(map[string]bool, len(rows))
	for _, item := range rows {
		id := recoveryString(recoveryObject(item), "order_id")
		if id == "" || set[id] {
			return nil, false
		}
		set[id] = true
	}
	return set, true
}

func recoveryEqualOrders(expected map[string]bool, schedule any) bool {
	got, ok := recoveryOrderSet(schedule)
	if !ok || len(got) != len(expected) {
		return false
	}
	for id := range expected {
		if !got[id] {
			return false
		}
	}
	return true
}

func (s *stateServer) recoveryComparison(w http.ResponseWriter, r *http.Request) {
	if r.URL.Path != recoveryPath {
		stateError(w, 404, "not_found", "Resource not found")
		return
	}
	if r.Method != http.MethodPost {
		stateError(w, 405, "method_not_allowed", "Method not allowed")
		return
	}
	if len(r.Header.Values("Authorization")) > 1 || len(r.Header.Values("Origin")) > 1 {
		stateError(w, 422, "invalid_request", "Invalid request headers")
		return
	}
	for _, header := range []string{"X-Actor", "X-Role", "X-Principal-Id", "X-Workspace-Id", "X-Principal", "X-Workspace"} {
		if len(r.Header.Values(header)) > 0 {
			stateError(w, 422, "invalid_request", "Client identity assertions are forbidden")
			return
		}
	}
	for _, query := range []string{"actor", "role", "principal_id", "workspace_id"} {
		if r.URL.Query().Has(query) {
			stateError(w, 422, "invalid_request", "Client identity assertions are forbidden")
			return
		}
	}
	p, ok := r.Context().Value(machinePrincipalKey{}).(principal)
	if !ok {
		if s.auth == nil {
			stateError(w, 401, "unauthorized", "Valid identity required")
			return
		}
		p, ok = s.auth.Authenticate(w, r)
		if !ok {
			return
		}
	}
	if p.Workspace == "" {
		stateError(w, 401, "unauthorized", "Valid identity required")
		return
	}
	media, params, err := mime.ParseMediaType(r.Header.Get("Content-Type"))
	if err != nil || media != "application/json" || (params["charset"] != "" && !strings.EqualFold(params["charset"], "utf-8")) || len(r.Header.Values("Content-Type")) != 1 {
		stateError(w, 422, "invalid_request", "Invalid content type")
		return
	}
	body, err := io.ReadAll(io.LimitReader(r.Body, recoveryRequestLimit+1))
	if err != nil {
		stateError(w, 422, "invalid_request", "Invalid request body")
		return
	}
	if len(body) > recoveryRequestLimit {
		stateError(w, 413, "request_too_large", "Request too large")
		return
	}
	value, err := recoveryParse(body)
	if err != nil || !recoveryValid(value, recoveryDefinitions["RecoveryComparisonRequest"], 0) {
		stateError(w, 422, "invalid_request", "Invalid recovery comparison request")
		return
	}
	request := recoveryObject(value)
	snapshot, scenario, alternative := recoveryObject(request["snapshot"]), recoveryObject(request["scenario"]), recoveryObject(request["recovery"])
	if recoveryString(snapshot, "workspace_id") != p.Workspace || recoveryString(scenario, "workspace_id") != p.Workspace || recoveryString(alternative, "workspace_id") != p.Workspace {
		stateError(w, 403, "forbidden", "Workspace access denied")
		return
	}
	ctx, cancel := context.WithTimeout(r.Context(), 6*time.Second)
	defer cancel()
	upstream, err := http.NewRequestWithContext(ctx, http.MethodPost, s.cfg.Upstream+recoveryUpstreamPath, bytes.NewReader(body))
	if err != nil || s.outbound == nil {
		stateError(w, 503, "service_unavailable", "Recovery comparison service unavailable")
		return
	}
	upstream.Header.Set("Content-Type", "application/json")
	client := *s.outbound
	client.CheckRedirect = func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }
	response, err := client.Do(upstream)
	if err != nil {
		stateError(w, 503, "service_unavailable", "Recovery comparison service unavailable")
		return
	}
	defer response.Body.Close()
	if response.StatusCode >= 500 {
		stateError(w, 503, "service_unavailable", "Recovery comparison service unavailable")
		return
	}
	if response.StatusCode != 200 && response.StatusCode != 422 {
		stateError(w, 502, "upstream_invalid_response", "Invalid recovery comparison response")
		return
	}
	contentType, _, err := mime.ParseMediaType(response.Header.Get("Content-Type"))
	if err != nil || contentType != "application/json" || len(response.Header.Values("Content-Type")) != 1 {
		stateError(w, 502, "upstream_invalid_response", "Invalid recovery comparison response")
		return
	}
	upstreamBody, err := io.ReadAll(io.LimitReader(response.Body, recoveryResponseLimit+1))
	if ctx.Err() != nil {
		stateError(w, 503, "service_unavailable", "Recovery comparison service unavailable")
		return
	}
	if err != nil || len(upstreamBody) > recoveryResponseLimit {
		stateError(w, 502, "upstream_invalid_response", "Invalid recovery comparison response")
		return
	}
	upstreamValue, err := recoveryParse(upstreamBody)
	if err != nil {
		stateError(w, 502, "upstream_invalid_response", "Invalid recovery comparison response")
		return
	}
	name := "RecoveryComparisonResponse"
	if response.StatusCode == 422 {
		name = "RecoveryComparisonUnsupportedResponse"
	}
	if !recoveryValid(upstreamValue, recoveryDefinitions[name], 0) {
		stateError(w, 502, "upstream_invalid_response", "Invalid recovery comparison response")
		return
	}
	if response.StatusCode == 200 {
		comparison := recoveryObject(recoveryObject(upstreamValue)["comparison"])
		requestAsOf, requestErr := time.Parse(time.RFC3339Nano, recoveryString(snapshot, "as_of"))
		responseAsOf, responseErr := time.Parse(time.RFC3339Nano, recoveryString(comparison, "as_of"))
		if recoveryString(comparison, "workspace_id") != p.Workspace ||
			recoveryString(comparison, "plan_version") != recoveryString(snapshot, "plan_version") ||
			recoveryString(comparison, "snapshot_id") != recoveryString(snapshot, "snapshot_id") ||
			recoveryString(comparison, "scenario_id") != recoveryString(scenario, "scenario_id") ||
			requestErr != nil || responseErr != nil || !requestAsOf.Equal(responseAsOf) {
			stateError(w, 502, "upstream_invalid_response", "Invalid recovery comparison response")
			return
		}
		expected := map[string]bool{}
		for _, item := range snapshot["orders"].([]any) {
			expected[recoveryString(recoveryObject(item), "order_id")] = true
		}
		for _, key := range []string{"original_baseline", "disrupted_baseline", "recovery"} {
			if !recoveryEqualOrders(expected, comparison[key]) {
				stateError(w, 502, "upstream_invalid_response", "Invalid recovery comparison response")
				return
			}
		}
	}
	if ctx.Err() != nil {
		stateError(w, 503, "service_unavailable", "Recovery comparison service unavailable")
		return
	}
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(response.StatusCode)
	_, _ = w.Write(upstreamBody)
}
