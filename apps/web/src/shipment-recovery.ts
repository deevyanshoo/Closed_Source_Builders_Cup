import type { ShipmentRecoveryContext, ShipmentRecoveryContextsResponse, ShipmentRecoveryResponse } from '../../../contracts/generated/types';
import { recoverySchema } from '../../../contracts/generated/recovery-schema';
import { voiceHTTPSchema } from '../../../contracts/generated/voice-http-schema';
import shipmentSchema from '../../../services/gateway/internal/contract/shipment_recovery_schema.json';

export type ShipmentEvidence = { scenarios: unknown[]; excluded: unknown[]; normalization_rejected: unknown[]; normalization_duplicates: unknown[] };
export type ShipmentComparison = { shipment_id: string; source_scenario_id: string; receipt_scenario: Record<string, unknown>; recovery: Record<string, unknown>; projection: Record<string, unknown>; result: Record<string, unknown> };
export type ShipmentOutcome = { adapter_version: '1'; synthetic: true; status: 'evaluated' | 'no_eligible_scenario' | 'rejected'; binding?: Record<string, unknown>; stage?: string; reason?: string; details?: unknown[]; evidence: ShipmentEvidence | null; comparisons: ShipmentComparison[] };
export type ShipmentViewOutcome = { kind: ShipmentOutcome['status']; context: ShipmentRecoveryContext; raw: string; outcome: ShipmentOutcome };
export type ShipmentErrorKind = 'session' | 'stale' | 'missing_context' | 'busy' | 'timeout' | 'unavailable' | 'invalid_response';
export class ShipmentRecoveryError extends Error {
  constructor(message: string, readonly kind: ShipmentErrorKind) { super(message); }
}

const fail = (): never => { throw new ShipmentRecoveryError('The shipment comparison returned an invalid response.', 'invalid_response'); };
const obj = (v: unknown): v is Record<string, unknown> => !!v && typeof v === 'object' && !Array.isArray(v);
const str = (v: unknown): v is string => typeof v === 'string' && v.length > 0;
const hash = (v: unknown) => typeof v === 'string' && /^[0-9a-f]{64}$/.test(v);
const keys = (v: Record<string, unknown>, required: string[]) => Object.keys(v).length === required.length && required.every(k => Object.prototype.hasOwnProperty.call(v, k));
const bytes = (s: string) => new TextEncoder().encode(s).byteLength;
const outcomeLimit = 4 << 20;
type Schema = { $ref?: string; type?: string; const?: unknown; enum?: unknown[]; properties?: Record<string, Schema>; required?: string[]; additionalProperties?: boolean; items?: Schema; maxItems?: number; minItems?: number; minLength?: number; maxLength?: number; pattern?: string; minimum?: number; maximum?: number; uniqueItems?: boolean };
const shipmentSchemas = shipmentSchema.components.schemas as Record<string, Schema>;
const recoverySchemas = recoverySchema.components.schemas as Record<string, Schema>;
const stateSchemas = voiceHTTPSchema.components.schemas as Record<string, Schema>;
function matches(v: unknown, s: Schema | undefined, definitions: Record<string, Schema>, depth = 0): boolean {
  if (!s || depth > 40) return false;
  if (s.$ref) return matches(v, definitions[s.$ref.split('/').at(-1) || ''], definitions, depth + 1);
  if (s.const !== undefined && v !== s.const) return false;
  if (s.enum && !s.enum.includes(v)) return false;
  if (s.type === 'object') {
    if (!obj(v) || s.required?.some(k => !(k in v))) return false;
    return Object.entries(v).every(([k, item]) => s.properties?.[k] ? matches(item, s.properties[k], definitions, depth + 1) : s.additionalProperties !== false);
  }
  if (s.type === 'array') return Array.isArray(v) && (s.maxItems === undefined || v.length <= s.maxItems) && (s.minItems === undefined || v.length >= s.minItems) &&
    (!s.uniqueItems || new Set(v.map(item => JSON.stringify(item))).size === v.length) && (!s.items || v.every(item => matches(item, s.items, definitions, depth + 1)));
  if (s.type === 'string') return typeof v === 'string' && (s.minLength === undefined || v.length >= s.minLength) && (s.maxLength === undefined || v.length <= s.maxLength) && (!s.pattern || new RegExp(s.pattern).test(v));
  if (s.type === 'integer') return typeof v === 'number' && Number.isSafeInteger(v) && (s.minimum === undefined || v >= s.minimum) && (s.maximum === undefined || v <= s.maximum);
  return s.type === 'boolean' ? typeof v === 'boolean' : false;
}

// JSON.parse silently accepts duplicate keys and lone surrogates. Scan first; output arrays have no input-only cap.
function strictJSON(raw: string, maxBytes: number, numericKeys: ReadonlySet<string>): unknown {
  if (bytes(raw) > maxBytes) return fail();
  let i = 0;
  const space = () => { while (i < raw.length && /\s/.test(raw[i])) i++; };
  const string = (): string => {
    const start = i++;
    for (;;) {
      if (i >= raw.length) return fail();
      if (raw[i] === '"') { i++; break; }
      if (raw[i] === '\\') {
        i++; if (i >= raw.length) return fail();
        if (raw[i] === 'u') { if (!/^[0-9a-fA-F]{4}$/.test(raw.slice(i + 1, i + 5))) return fail(); i += 5; }
        else if ('"\\/bfnrt'.includes(raw[i])) i++;
        else return fail();
      } else { if (raw.charCodeAt(i) < 32) return fail(); i++; }
    }
    let value: string;
    try { value = JSON.parse(raw.slice(start, i)) as string; } catch { return fail(); }
    for (let p = 0; p < value.length; p++) {
      const n = value.charCodeAt(p);
      if (n >= 0xd800 && n <= 0xdbff) { if (p + 1 >= value.length || value.charCodeAt(++p) < 0xdc00 || value.charCodeAt(p) > 0xdfff) return fail(); }
      else if (n >= 0xdc00 && n <= 0xdfff) return fail();
    }
    return value;
  };
  const parse = (depth: number, key = ''): unknown => {
    if (depth > 32) return fail();
    space();
    if (raw[i] === '"') return string();
    if (raw[i] === '{') {
      i++; const result: Record<string, unknown> = Object.create(null); const seen = new Set<string>(); space();
      if (raw[i] === '}') { i++; return result; }
      for (;;) {
        if (raw[i] !== '"') return fail();
        const name = string(); if (seen.has(name)) return fail(); seen.add(name); space();
        if (raw[i++] !== ':') return fail();
        result[name] = parse(depth + 1, name); space();
        if (raw[i] === '}') { i++; return result; }
        if (raw[i++] !== ',') return fail(); space();
      }
    }
    if (raw[i] === '[') {
      i++; const result: unknown[] = []; space();
      if (raw[i] === ']') { i++; return result; }
      for (;;) {
        result.push(parse(depth + 1, key)); space();
        if (raw[i] === ']') { i++; return result; }
        if (raw[i++] !== ',') return fail();
      }
    }
    for (const [literal, value] of [['true', true], ['false', false], ['null', null]] as const) if (raw.startsWith(literal, i)) { i += literal.length; return value; }
    const number = raw.slice(i).match(/^-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?/);
    if (!number || !numericKeys.has(key) || !/^(?:0|[1-9]\d*)$/.test(number[0])) return fail();
    i += number[0].length; const n = Number(number[0]); if (!Number.isSafeInteger(n)) return fail(); return n;
  };
  const result = parse(0); space(); if (i !== raw.length) return fail(); return result;
}
export const parseStrictOutcome = (raw: string): unknown => strictJSON(raw, outcomeLimit, new Set(['record_index']));

async function read(response: Response, limit: number): Promise<unknown> {
  if (!/^application\/json(?:\s*;|$)/i.test(response.headers.get('Content-Type') || '')) return fail();
  const length = response.headers.get('Content-Length'); if (length && (+length > limit || !Number.isSafeInteger(+length))) return fail();
  const reader = response.body?.getReader(); if (!reader) return fail();
  const chunks: Uint8Array[] = []; let lengthRead = 0;
  for (;;) { const { value, done } = await reader.read(); if (done) break; lengthRead += value.byteLength; if (lengthRead > limit) { await reader.cancel(); return fail(); } chunks.push(value); }
  const out = new Uint8Array(lengthRead); let offset = 0;
  for (const part of chunks) { out.set(part, offset); offset += part.length; }
  try { return strictJSON(new TextDecoder('utf-8', { fatal: true }).decode(out), limit, new Set(['revision', 'record_index'])); }
  catch { return fail(); }
}
const root = (workspace: string) => `/v2/workspaces/${encodeURIComponent(workspace)}/shipment-recovery`;
async function call(path: string, signal: AbortSignal, init?: RequestInit): Promise<{ response: Response; value: unknown }> {
  let response: Response;
  try { response = await globalThis.fetch(path, { ...init, signal }); }
  catch (error) { if (signal.aborted) throw error; throw new ShipmentRecoveryError('The local shipment service is unavailable.', 'unavailable'); }
  const value = await read(response, init?.method === 'POST' ? (8 << 20) + (32 << 10) : 64 << 10);
  return { response, value };
}
function stateError(status: number, value: unknown): never {
  if (!matches(value, stateSchemas.StateErrorResponse, stateSchemas)) return fail();
  const kind: ShipmentErrorKind = status === 401 || status === 403 ? 'session' : status === 409 ? 'stale' : status === 404 ? 'missing_context' : status === 429 ? 'busy' : status === 504 ? 'timeout' : 'unavailable';
  const messages: Record<ShipmentErrorKind, string> = { session: 'The local session changed. Refresh the session.', stale: 'The selected context changed. Reload the list.', missing_context: 'The context is no longer available. Reload the list.', busy: 'The local comparison is busy. Retry shortly.', timeout: 'The comparison timed out. Retry.', unavailable: 'The local shipment service is unavailable.', invalid_response: 'The shipment comparison returned an invalid response.' };
  throw new ShipmentRecoveryError(messages[kind], kind);
}
function validContext(value: unknown, workspace: string): value is ShipmentRecoveryContext {
  return matches(value, shipmentSchemas.ShipmentRecoveryContext, shipmentSchemas) && (value as ShipmentRecoveryContext).workspace_id === workspace && Number.isSafeInteger((value as ShipmentRecoveryContext).revision);
}
export async function listShipmentContexts(workspaceId: string, signal: AbortSignal): Promise<ShipmentRecoveryContext[]> {
  if (!workspaceId) return fail();
  const { response, value } = await call(`${root(workspaceId)}/contexts`, signal);
  if (!response.ok) return stateError(response.status, value);
  if (!matches(value, shipmentSchemas.ShipmentRecoveryContextsResponse, shipmentSchemas)) return fail();
  const data = value as ShipmentRecoveryContextsResponse;
  if (!data.contexts.every(row => validContext(row, workspaceId))) return fail();
  return data.contexts;
}
function sameContext(a: ShipmentRecoveryContext, b: ShipmentRecoveryContext) {
  return (Object.keys(a) as (keyof ShipmentRecoveryContext)[]).every(k => a[k] === b[k]);
}
function validEvidence(value: unknown): value is ShipmentEvidence {
  return obj(value) && keys(value, ['scenarios', 'excluded', 'normalization_rejected', 'normalization_duplicates']) &&
    Array.isArray(value.scenarios) && value.scenarios.every(validSource) &&
    Array.isArray(value.excluded) && value.excluded.every(item => exact(item, ['event_id', 'shipment_id', 'reason']) && id(item.event_id) && id(item.shipment_id) && id(item.reason)) &&
    Array.isArray(value.normalization_rejected) && value.normalization_rejected.every(item => exact(item, ['record_index', 'event_id', 'reason', 'details']) && index(item.record_index) && (item.event_id === null || id(item.event_id)) && id(item.reason) && list(item.details, id)) &&
    Array.isArray(value.normalization_duplicates) && value.normalization_duplicates.every(item => exact(item, ['event_id', 'record_indexes', 'kept_record_index']) && id(item.event_id) && list(item.record_indexes, index, 2) && index(item.kept_record_index));
}
const exact = (v: unknown, names: string[]): v is Record<string, unknown> => obj(v) && keys(v, names);
const id = (v: unknown): v is string => str(v) && v.length <= 256 && v.trim() === v;
const index = (v: unknown): v is number => typeof v === 'number' && Number.isSafeInteger(v) && v >= 0;
const list = (v: unknown, check: (item: unknown) => boolean, minimum = 0): v is unknown[] => Array.isArray(v) && v.length >= minimum && v.every(check);
const amount = (v: unknown): v is string => typeof v === 'string' && v.length <= 2050 && /^(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$/.test(v);
// The Python adapter accepts six fractional digits and compares aware UTC instants.
// Date.parse rounds to milliseconds and also normalizes impossible calendar dates.
function parseInstant(v: unknown): bigint | null {
  if (typeof v !== 'string' || v.length > 40) return null;
  const parts = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,6}))?(Z|[+-]\d{2}:\d{2})$/.exec(v);
  if (!parts) return null;
  const year = Number(parts[1]), month = Number(parts[2]), day = Number(parts[3]);
  const hour = Number(parts[4]), minute = Number(parts[5]), second = Number(parts[6]);
  const leap = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
  const lengths = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  if (year < 1 || month < 1 || month > 12 || day < 1 || day > lengths[month - 1] ||
      hour > 23 || minute > 59 || second > 59) return null;
  const zone = parts[8];
  const offsetHours = zone === 'Z' ? 0 : Number(zone.slice(1, 3));
  const offsetMinutes = zone === 'Z' ? 0 : Number(zone.slice(4, 6));
  if (offsetHours > 23 || offsetMinutes > 59) return null;
  const signedOffset = zone === 'Z' ? 0 : (zone[0] === '+' ? 1 : -1) * (offsetHours * 60 + offsetMinutes);
  const adjustedYear = year - (month <= 2 ? 1 : 0);
  const era = Math.floor(adjustedYear / 400);
  const yearOfEra = adjustedYear - era * 400;
  const shiftedMonth = month + (month > 2 ? -3 : 9);
  const dayOfYear = Math.floor((153 * shiftedMonth + 2) / 5) + day - 1;
  const dayOfEra = yearOfEra * 365 + Math.floor(yearOfEra / 4) - Math.floor(yearOfEra / 100) + dayOfYear;
  const daysSinceEpoch = era * 146097 + dayOfEra - 719468;
  const microsOfDay = BigInt(hour * 3600 + minute * 60 + second) * 1000000n + BigInt((parts[7] ?? '').padEnd(6, '0') || '0');
  const utcMicros = BigInt(daysSinceEpoch) * 86400000000n + microsOfDay - BigInt(signedOffset) * 60000000n;
  // Python datetime.astimezone rejects UTC instants outside years 1..9999.
  if (utcMicros < -62135596800000000n || utcMicros > 253402300799999999n) return null;
  return utcMicros;
}
const instant = (v: unknown): v is string => parseInstant(v) !== null;
const sameInstant = (a: unknown, b: unknown) => {
  const first = parseInstant(a), second = parseInstant(b);
  return first !== null && second !== null && first === second;
};
const sourceRef = (v: unknown) => exact(v, ['provider_id', 'kind', 'reference']) && id(v.provider_id) && id(v.kind) && (v.reference === null || typeof v.reference === 'string');
const evidenceRef = (v: unknown) => exact(v, ['evidence_id', 'kind', 'locator', 'content_sha256', 'retrieved_at', 'valid_until', 'status']) &&
  id(v.evidence_id) && id(v.kind) && id(v.locator) && hash(v.content_sha256) && instant(v.retrieved_at) && (v.valid_until === null || instant(v.valid_until)) &&
  ['no_stated_expiry', 'expired', 'current'].includes(String(v.status));
function validSource(v: unknown): boolean {
  if (!exact(v, ['event_id', 'shipment_id', 'basis', 'arrival_at', 'assumptions', 'source_receipt', 'scenario']) ||
      !id(v.event_id) || !id(v.shipment_id) || !['eta', 'actual_arrival'].includes(String(v.basis)) || !instant(v.arrival_at) ||
      !list(v.assumptions, id, 1) || !exact(v.scenario, ['workspace_id', 'expected_plan_version', 'source_snapshot_id', 'event_id', 'scenario_id', 'synthetic', 'changes']) ||
      !id(v.scenario.workspace_id) || !id(v.scenario.expected_plan_version) || !id(v.scenario.source_snapshot_id) || v.scenario.event_id !== v.event_id ||
      !id(v.scenario.scenario_id) || v.scenario.synthetic !== true || !list(v.scenario.changes, change => exact(change, ['line_id', 'arrival_at']) && id(change.line_id) && sameInstant(change.arrival_at, v.arrival_at), 1)) return false;
  const receipt = v.source_receipt;
  if (!exact(receipt, ['as_of', 'workspace_id', 'plan_version', 'snapshot_id', 'raw_sha256', 'snapshot_sha256', 'normalized_sha256', 'binding_sha256', 'event']) ||
      !instant(receipt.as_of) || !id(receipt.workspace_id) || !id(receipt.plan_version) || !id(receipt.snapshot_id) ||
      ![receipt.raw_sha256, receipt.snapshot_sha256, receipt.normalized_sha256, receipt.binding_sha256].every(hash) ||
      !exact(receipt.event, ['event_id', 'shipment_id', 'event_type', 'occurred_at', 'observed_at', 'record_index', 'synthetic', 'source', 'evidence'])) return false;
  const event = receipt.event;
  return event.event_id === v.event_id && event.shipment_id === v.shipment_id && id(event.event_type) && instant(event.occurred_at) &&
    instant(event.observed_at) && index(event.record_index) && event.synthetic === true && sourceRef(event.source) && list(event.evidence, evidenceRef, 1);
}
const stockTriple = (v: unknown) => exact(v, ['used', 'shortfall', 'remaining_stock']) && amount(v.used) && amount(v.shortfall) && amount(v.remaining_stock);
const balance = (v: unknown) => exact(v, ['component_id', 'unit', 'starting_stock', 'baseline_ending_stock', 'changed_ending_stock']) &&
  id(v.component_id) && id(v.unit) && amount(v.starting_stock) && amount(v.baseline_ending_stock) && amount(v.changed_ending_stock);
const demand = (v: unknown) => exact(v, ['component_id', 'unit', 'quantity', 'baseline', 'changed', 'comparison']) &&
  id(v.component_id) && id(v.unit) && amount(v.quantity) && stockTriple(v.baseline) && stockTriple(v.changed) &&
  ['unchanged_shortage', 'new_shortage', 'increased_shortage', 'reduced_shortage'].includes(String(v.comparison));
function validProjection(v: unknown, source: Record<string, unknown>, context: ShipmentRecoveryContext): boolean {
  if (!exact(v, ['status', 'synthetic', 'workspace_id', 'plan_version', 'snapshot_id', 'event_id', 'scenario_id', 'as_of', 'arrival_changes', 'affected_commitments', 'component_balances', 'limitations']) ||
      v.status !== 'screened' || v.synthetic !== true || v.workspace_id !== context.workspace_id || v.plan_version !== context.plan_version ||
      v.snapshot_id !== context.propagation_snapshot_id || v.event_id !== source.event_id || !sameInstant(v.as_of, context.as_of) ||
      !list(v.arrival_changes, change => exact(change, ['line_id', 'component_id', 'quantity', 'unit', 'baseline_at', 'changed_at']) &&
        id(change.line_id) && id(change.component_id) && amount(change.quantity) && id(change.unit) && instant(change.baseline_at) && instant(change.changed_at), 1) ||
      !list(v.affected_commitments, item => exact(item, ['commitment_id', 'production_order_id', 'required_at', 'due_at', 'material_at_risk', 'demands']) &&
        id(item.commitment_id) && id(item.production_order_id) && instant(item.required_at) && instant(item.due_at) &&
        typeof item.material_at_risk === 'boolean' && list(item.demands, demand, 1), 1) ||
      !list(v.component_balances, balance, 1) || !list(v.limitations, id, 1)) return false;
  const scenario = source.scenario as Record<string, unknown>;
  if (v.scenario_id !== scenario.scenario_id) return false;
  const changes = scenario.changes as Record<string, unknown>[];
  const projected = v.arrival_changes as Record<string, unknown>[];
  if (projected.length !== changes.length) return false;
  if (new Set(changes.map(item => item.line_id)).size !== changes.length) return false;
  const byLine = new Map(projected.map(item => [item.line_id, item]));
  if (byLine.size !== projected.length || changes.some(item => !byLine.has(item.line_id) || !sameInstant(byLine.get(item.line_id)?.changed_at, item.arrival_at))) return false;
  const balances = v.component_balances as Record<string, unknown>[];
  const components = new Set(balances.map(item => item.component_id));
  if (components.size !== balances.length || projected.some(item => !components.has(item.component_id))) return false;
  const commitments = v.affected_commitments as Record<string, unknown>[];
  if (new Set(commitments.map(item => item.commitment_id)).size !== commitments.length ||
      commitments.some(item => (item.demands as Record<string, unknown>[]).some(d => !components.has(d.component_id)))) return false;
  return true;
}
function outcome(raw: string, context: ShipmentRecoveryContext): ShipmentOutcome {
  const value = parseStrictOutcome(raw); if (!obj(value) || value.adapter_version !== '1' || value.synthetic !== true || !validEvidence(value.evidence) && value.evidence !== null) return fail();
  if (value.status === 'rejected') {
    if (!keys(value, ['adapter_version', 'status', 'synthetic', 'stage', 'reason', 'details', 'evidence', 'comparisons']) || !str(value.stage) || !str(value.reason) || !Array.isArray(value.details) || value.details.length || !Array.isArray(value.comparisons) || value.comparisons.length) return fail();
    return value as ShipmentOutcome;
  }
  if (value.status !== 'evaluated' && value.status !== 'no_eligible_scenario') return fail();
  if (!keys(value, ['adapter_version', 'status', 'synthetic', 'binding', 'evidence', 'comparisons']) || !validEvidence(value.evidence) || !obj(value.binding) || !Array.isArray(value.comparisons)) return fail();
  const binding = value.binding;
  if (!keys(binding, ['adapter_version', 'synthetic', 'workspace_id', 'plan_version', 'propagation_snapshot_id', 'production_snapshot_id', 'as_of', 'raw_sha256', 'propagation_sha256', 'production_sha256', 'joins_sha256', 'assignments_sha256', 'binding_sha256']) || binding.adapter_version !== '1' || binding.synthetic !== true ||
      binding.workspace_id !== context.workspace_id || binding.plan_version !== context.plan_version || binding.propagation_snapshot_id !== context.propagation_snapshot_id || binding.production_snapshot_id !== context.production_snapshot_id || binding.as_of !== context.as_of ||
      !['raw_sha256', 'propagation_sha256', 'production_sha256', 'joins_sha256', 'assignments_sha256', 'binding_sha256'].every(k => hash(binding[k]))) return fail();
  if (value.status === 'no_eligible_scenario' && value.comparisons.length || value.status === 'evaluated' && (value.comparisons.length < 1 || value.comparisons.length > 16 || value.comparisons.length !== value.evidence.scenarios.length)) return fail();
  const sources = new Map<string, Record<string, unknown>>();
  for (const source of value.evidence.scenarios) {
    if (!obj(source) || !str(source.shipment_id) || !str(source.event_id) || !obj(source.scenario) || !str(source.scenario.scenario_id)) return fail();
    const receipt = source.source_receipt as Record<string, unknown>;
    if (source.scenario.workspace_id !== context.workspace_id || source.scenario.expected_plan_version !== context.plan_version ||
        source.scenario.source_snapshot_id !== context.propagation_snapshot_id ||
        receipt.workspace_id !== context.workspace_id || receipt.plan_version !== context.plan_version ||
        receipt.snapshot_id !== context.propagation_snapshot_id || !sameInstant(receipt.as_of, context.as_of) ||
        receipt.raw_sha256 !== binding.raw_sha256) return fail();
    if (sources.has(source.scenario.scenario_id)) return fail(); sources.set(source.scenario.scenario_id, source);
  }
  const rowIds = new Set<string>();
  for (const row of value.comparisons) {
    if (!obj(row) || !keys(row, ['shipment_id', 'source_scenario_id', 'receipt_scenario', 'recovery', 'projection', 'result']) || !str(row.shipment_id) || !str(row.source_scenario_id) || !sources.has(row.source_scenario_id) || rowIds.has(row.source_scenario_id) || !obj(row.projection) || !obj(row.result) || !obj(row.receipt_scenario) || !obj(row.recovery)) return fail();
    rowIds.add(row.source_scenario_id);
    const source = sources.get(row.source_scenario_id)!;
    if (row.shipment_id !== source.shipment_id || !validProjection(row.projection, source, context) || row.projection.scenario_id !== row.source_scenario_id) return fail();
    for (const scope of [row.receipt_scenario, row.recovery]) if (scope.workspace_id !== context.workspace_id || scope.expected_plan_version !== context.plan_version || scope.source_snapshot_id !== context.production_snapshot_id || scope.synthetic !== true) return fail();
    if (!str(row.receipt_scenario.scenario_id) || row.receipt_scenario.scenario_id !== row.recovery.scenario_id) return fail();
    if (!matches(row.receipt_scenario, recoverySchemas.ReceiptArrivalScenario, recoverySchemas) ||
        !matches(row.recovery, recoverySchemas.RecoveryAssignment, recoverySchemas)) return fail();
    if (row.result.status === 'compared') {
      if (!matches(row.result, recoverySchemas.ReceiptRecoveryComparison, recoverySchemas) || row.result.workspace_id !== context.workspace_id || row.result.plan_version !== context.plan_version || row.result.snapshot_id !== context.production_snapshot_id || row.result.scenario_id !== row.receipt_scenario.scenario_id) return fail();
    } else if (row.result.status === 'unsupported') {
      if (!matches({ contract_version: '0.2.0', ...row.result }, recoverySchemas.RecoveryComparisonUnsupportedResponse, recoverySchemas)) return fail();
    } else return fail();
  }
  return value as ShipmentOutcome;
}
export async function compareShipmentContext(workspaceId: string, selected: ShipmentRecoveryContext, signal: AbortSignal): Promise<ShipmentViewOutcome> {
  if (!validContext(selected, workspaceId)) return fail();
  const body = JSON.stringify({ contract_version: '0.2.0', context_id: selected.context_id, expected_revision: selected.revision });
  const { response, value } = await call(`${root(workspaceId)}/comparisons`, signal, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body });
  if (!matches(value, shipmentSchemas.ShipmentRecoveryResponse, shipmentSchemas)) return stateError(response.status, value);
  const data = value as ShipmentRecoveryResponse;
  if (!validContext(data.context, workspaceId) || !sameContext(selected, data.context) || bytes(data.outcome_json) > outcomeLimit) return fail();
  const parsed = outcome(data.outcome_json, selected);
  if ((response.status === 200 && parsed.status !== 'evaluated' && parsed.status !== 'no_eligible_scenario') || ([422, 502, 503].includes(response.status) && parsed.status !== 'rejected') || ![200, 422, 502, 503].includes(response.status)) return fail();
  return { kind: parsed.status, context: data.context, raw: data.outcome_json, outcome: parsed };
}
