import { recoveryExamples } from '../../../contracts/generated/recovery-examples';
import { recoverySchema } from '../../../contracts/generated/recovery-schema';
import type { RecoveryComparisonRequest, RecoveryComparisonResponse, RecoveryComparisonUnsupportedResponse } from '../../../contracts/generated/types';

export type RecoveryExample = 'recovery' | 'recovery_precision';
export type ComparisonResult = { kind: 'compared'; response: RecoveryComparisonResponse } | { kind: 'unsupported'; response: RecoveryComparisonUnsupportedResponse };
export class RecoveryError extends Error {
  constructor(message: string, readonly kind: 'validation' | 'unavailable' | 'invalid_response' | 'session') { super(message); }
}

type Schema = { $ref?: string; type?: string; const?: unknown; enum?: unknown[]; properties?: Record<string, Schema>; required?: string[]; additionalProperties?: boolean; items?: Schema; minItems?: number; maxItems?: number; uniqueItems?: boolean; minLength?: number; maxLength?: number; pattern?: string };
const generated = recoverySchema as { components: { schemas: Record<string, Schema> }; limits: { request_bytes: number; response_bytes: number; max_depth: number } };
const own = (v: object, k: string) => Object.prototype.hasOwnProperty.call(v, k);
const invalid = (): never => { throw new RecoveryError('The service returned an invalid comparison.', 'invalid_response'); };

function matches(value: unknown, schema: Schema, depth = 0): boolean {
  if (depth > generated.limits.max_depth) return false;
  if (schema.$ref) {
    const name = schema.$ref.split('/').at(-1) || '';
    const resolved = generated.components.schemas[name];
    return !!resolved && matches(value, resolved, depth + 1);
  }
  if (schema.const !== undefined && value !== schema.const) return false;
  if (schema.enum && !schema.enum.includes(value)) return false;
  if (schema.type === 'object') {
    if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
    const record = value as Record<string, unknown>;
    if (schema.required?.some(key => !own(record, key))) return false;
    return Object.entries(record).every(([key, item]) => {
      const child = schema.properties?.[key];
      return child ? matches(item, child, depth + 1) : schema.additionalProperties !== false;
    });
  }
  if (schema.type === 'array') {
    if (!Array.isArray(value) || (schema.minItems !== undefined && value.length < schema.minItems) ||
        (schema.maxItems !== undefined && value.length > schema.maxItems)) return false;
    if (schema.uniqueItems && new Set(value.map(item => JSON.stringify(item))).size !== value.length) return false;
    return !schema.items || value.every(item => matches(item, schema.items!, depth + 1));
  }
  if (schema.type === 'string') {
    return typeof value === 'string' &&
      (schema.minLength === undefined || value.length >= schema.minLength) &&
      (schema.maxLength === undefined || value.length <= schema.maxLength) &&
      (!schema.pattern || new RegExp(schema.pattern).test(value));
  }
  if (schema.type === 'boolean') return typeof value === 'boolean';
  return false;
}

const utc = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/;
function validUTC(value: string): boolean {
  if (!utc.test(value)) return false;
  const parsed = new Date(value);
  return !Number.isNaN(parsed.getTime()) && parsed.toISOString().replace('.000Z', 'Z') === value;
}

export function createExample(example: RecoveryExample, workspace: string): RecoveryComparisonRequest {
  if (!workspace) throw new RecoveryError('An authenticated workspace is required.', 'validation');
  const source = recoveryExamples[example];
  if (!source) throw new RecoveryError('Choose a provided example.', 'validation');
  const request = structuredClone(source);
  request.snapshot.workspace_id = workspace;
  request.scenario.workspace_id = workspace;
  request.recovery.workspace_id = workspace;
  return request;
}

export function buildComparisonRequest(base: RecoveryComparisonRequest, arrivals: Record<string, string>, assignments: Record<string, string>): RecoveryComparisonRequest {
  const request = structuredClone(base);
  const receipts = new Map(request.snapshot.receipts.map(receipt => [receipt.receipt_id, receipt]));
  const periods = new Set(request.snapshot.periods.map(period => period.period_id));
  const orders = new Set(request.snapshot.orders.map(order => order.order_id));
  for (const key of Object.keys(arrivals)) if (!receipts.has(key)) throw new RecoveryError('Choose a listed receipt.', 'validation');
  for (const key of Object.keys(assignments)) if (!orders.has(key)) throw new RecoveryError('Choose a listed order.', 'validation');
  request.scenario.arrival_replacements = request.snapshot.receipts.flatMap(receipt => {
    const next = arrivals[receipt.receipt_id] ?? receipt.arrival_at;
    if (!validUTC(next)) throw new RecoveryError('Enter a complete UTC arrival, for example 2026-10-10T10:00:00Z.', 'validation');
    return next === receipt.arrival_at ? [] : [{ receipt_id: receipt.receipt_id, expected_arrival_at: receipt.arrival_at, arrival_at: next }];
  });
  request.recovery.assignments = request.snapshot.orders.map(order => {
    const period = assignments[order.order_id] ?? request.recovery.assignments.find(item => item.order_id === order.order_id)?.period_id;
    if (!period || !periods.has(period)) throw new RecoveryError('Choose a listed production period for every order.', 'validation');
    return { order_id: order.order_id, period_id: period };
  });
  if (!matches(request, generated.components.schemas.RecoveryComparisonRequest)) throw new RecoveryError('The comparison inputs are incomplete.', 'validation');
  return request;
}

export function validateComparisonResponse(value: unknown, request: RecoveryComparisonRequest): RecoveryComparisonResponse {
  if (!matches(value, generated.components.schemas.RecoveryComparisonResponse)) return invalid();
  const result = value as RecoveryComparisonResponse;
  const comparison = result.comparison;
  if (comparison.workspace_id !== request.snapshot.workspace_id || comparison.plan_version !== request.snapshot.plan_version ||
      comparison.snapshot_id !== request.snapshot.snapshot_id || comparison.scenario_id !== request.scenario.scenario_id ||
      !comparison.synthetic || comparison.status !== 'compared') return invalid();
  const orderIds = request.snapshot.orders.map(item => item.order_id).sort();
  for (const schedule of [comparison.original_baseline, comparison.disrupted_baseline, comparison.recovery]) {
    const received = schedule.orders.map(item => item.order_id).sort();
    if (received.length !== orderIds.length || received.some((id, index) => id !== orderIds[index])) return invalid();
  }
  const submittedOrders = new Set(orderIds);
  for (const ids of [comparison.potentially_exposed_order_ids, comparison.disruption_changed_order_ids, comparison.recovery_changed_order_ids]) {
    if (ids.some(id => !submittedOrders.has(id))) return invalid();
  }
  const submittedReceipts = new Set(request.snapshot.receipts.map(item => item.receipt_id));
  const changedReceipts = new Set<string>();
  for (const change of comparison.receipt_changes) {
    if (!submittedReceipts.has(change.receipt_id) || changedReceipts.has(change.receipt_id)) return invalid();
    changedReceipts.add(change.receipt_id);
  }
  return result;
}

async function jsonBody(response: Response, limit: number): Promise<unknown> {
  if (!/^application\/json(?:\s*;|$)/i.test(response.headers.get('Content-Type') || '')) return invalid();
  const body = await response.text();
  if (new TextEncoder().encode(body).length > limit) return invalid();
  try { return JSON.parse(body); } catch { return invalid(); }
}

export async function requestComparison(request: RecoveryComparisonRequest, signal: AbortSignal): Promise<ComparisonResult> {
  if (!matches(request, generated.components.schemas.RecoveryComparisonRequest)) throw new RecoveryError('The comparison inputs are incomplete.', 'validation');
  const body = JSON.stringify(request);
  if (new TextEncoder().encode(body).length > generated.limits.request_bytes) throw new RecoveryError('This example exceeds the comparison limit.', 'validation');
  let response: Response;
  try {
    response = await fetch('/v2/production/receipt-recovery/comparisons', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body, signal,
    });
  } catch (error) {
    if (signal.aborted) throw error;
    throw new RecoveryError('The comparison service is unavailable. Retry when it is available.', 'unavailable');
  }
  if (response.status === 422) {
    const value = await jsonBody(response, generated.limits.response_bytes);
    if (!matches(value, generated.components.schemas.RecoveryComparisonUnsupportedResponse)) return invalid();
    return { kind: 'unsupported', response: value as RecoveryComparisonUnsupportedResponse };
  }
  if (response.status === 401 || response.status === 403) throw new RecoveryError('The session changed. Check the current workspace and compare again.', 'session');
  if (!response.ok) throw new RecoveryError(response.status === 503 ? 'The comparison service is unavailable. Retry when it is available.' : 'The comparison could not be completed.', 'unavailable');
  const value = await jsonBody(response, generated.limits.response_bytes);
  return { kind: 'compared', response: validateComparisonResponse(value, request) };
}
