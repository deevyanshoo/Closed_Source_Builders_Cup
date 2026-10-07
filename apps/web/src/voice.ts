import { recoveryExamples } from '../../../contracts/generated/recovery-examples';
import { voiceHTTPSchema } from '../../../contracts/generated/voice-http-schema';
import type { ProductionContextRef, ProductionContextResponse, ProductionContextSummary, ProductionContextsResponse, ProductionSnapshot, StoredProductionContext, ReceiptRecoveryComparison, RecoveryComparisonRequest, SessionResponse, VoiceHTTPOutcome, VoiceHTTPCancelResponse, VoiceHTTPCloseResponse, VoiceHTTPResultResponse, VoiceHTTPSessionResponse, VoiceHTTPTurnResponse } from '../../../contracts/generated/types';
import noopRaw from '../../../contracts/examples/recovery_noop_request.json?raw';
import unsupportedRaw from '../../../contracts/examples/recovery_unsupported_request.json?raw';
import { buildComparisonRequest, validateComparisonResponse } from './recovery';
import { fetchSession } from './state';
import { contextRef, contextRefMatches, parseSnapshot, savedScenario } from './production-context';

export type VoiceExample = 'recovery' | 'recovery_noop' | 'recovery_precision' | 'recovery_unsupported';
export type VoiceKind = 'checking' | 'signed_out' | 'idle' | 'closing' | 'creating' | 'created' | 'starting' | 'pending' | 'compared' | 'unsupported' | 'clarification' | 'rejected' | 'failed' | 'expired' | 'capacity' | 'unavailable' | 'cancelled' | 'loading_contexts' | 'stale';
export type VoiceView = { kind: VoiceKind; example: VoiceExample; identity?: SessionResponse; base?: RecoveryComparisonRequest;
  arrivals: Record<string, string>; assignments: Record<string, string>; session?: VoiceHTTPSessionResponse;
  turn?: VoiceHTTPTurnResponse; outcome?: VoiceHTTPOutcome; comparison?: ReceiptRecoveryComparison; message?: string;
  source: 'inline' | 'saved'; contexts: ProductionContextSummary[]; hasMore: boolean; selected?: StoredProductionContext; snapshotText: string };
type Schema = { $ref?: string; oneOf?: Schema[]; anyOf?: Schema[]; type?: string; const?: unknown; enum?: unknown[];
  properties?: Record<string, Schema>; required?: string[]; additionalProperties?: boolean; items?: Schema;
  minItems?: number; maxItems?: number; uniqueItems?: boolean; minLength?: number; maxLength?: number; pattern?: string; minimum?: number; maximum?: number };
const generated = voiceHTTPSchema as { components: { schemas: Record<string, Schema> }; limits: { session_request_bytes: number; turn_request_bytes: number; response_bytes: number; max_depth: number; max_array_items: number; total_array_items: number } };
const own = (value: object, key: string) => Object.prototype.hasOwnProperty.call(value, key);
const root = '/v2/voice/sessions';
const byteLength = (value: string) => new TextEncoder().encode(value).length;

function matches(value: unknown, schema: Schema, depth = 0, arrays = { count: 0 }): boolean {
  if (!schema || depth > generated.limits.max_depth) return false;
  if (schema.$ref) {
    const name = schema.$ref.split('/').at(-1) || '';
    return !!generated.components.schemas[name] && matches(value, generated.components.schemas[name], depth, arrays);
  }
  for (const branches of [schema.oneOf, schema.anyOf]) {
    if (!branches) continue;
    const successes = branches.map(branch => { const count = { count: arrays.count }; return matches(value, branch, depth, count) ? count : null; }).filter((item): item is { count: number } => item !== null);
    if (schema.oneOf && successes.length !== 1 || schema.anyOf && successes.length === 0) return false;
    arrays.count = successes[0].count;
  }
  if (schema.const !== undefined && value !== schema.const) return false;
  if (schema.enum && !schema.enum.includes(value)) return false;
  if (schema.type === 'object') {
    if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
    const object = value as Record<string, unknown>;
    if (schema.required?.some(key => !own(object, key))) return false;
    return Object.entries(object).every(([key, item]) => { const child = schema.properties?.[key]; return child ? matches(item, child, depth + 1, arrays) : schema.additionalProperties !== false; });
  }
  if (schema.type === 'array') {
    if (!Array.isArray(value) || value.length > generated.limits.max_array_items || schema.minItems !== undefined && value.length < schema.minItems || schema.maxItems !== undefined && value.length > schema.maxItems) return false;
    arrays.count += value.length;
    if (arrays.count > generated.limits.total_array_items) return false;
    if (schema.uniqueItems && new Set(value.map(item => JSON.stringify(item))).size !== value.length) return false;
    return !schema.items || value.every(item => matches(item, schema.items!, depth + 1, arrays));
  }
  if (schema.type === 'string') {
    if (typeof value !== 'string') return false;
    const length = Array.from(value).length;
    return (schema.minLength === undefined || length >= schema.minLength) && (schema.maxLength === undefined || length <= schema.maxLength) && (!schema.pattern || new RegExp(schema.pattern).test(value));
  }
  if (schema.type === 'integer') return typeof value === 'number' && Number.isSafeInteger(value) && (schema.minimum === undefined || value >= schema.minimum) && (schema.maximum === undefined || value <= schema.maximum);
  return schema.type === 'boolean' ? typeof value === 'boolean' : !!(schema.oneOf || schema.anyOf);
}
export function voiceMatches(value: unknown, name: string): boolean { return !!generated.components.schemas[name] && matches(value, generated.components.schemas[name]); }
export class VoiceError extends Error {
  constructor(message: string, readonly kind: 'validation' | 'session' | 'expired' | 'capacity' | 'unavailable' | 'invalid_response' | 'cleanup') { super(message); }
}
const invalid = (): never => { throw new VoiceError('The voice comparison service returned an invalid response.', 'invalid_response'); };
const identityKey = (value: SessionResponse) => [value.principal_id, value.workspace_id, value.role, value.identity_mode ?? 'development'].join('\u0000');
const validIdentity = (value: SessionResponse) => value?.contract_version === '0.2.0'
  && typeof value.principal_id === 'string' && value.principal_id.length > 0 && value.principal_id.length <= 256
  && typeof value.workspace_id === 'string' && value.workspace_id.length > 0 && value.workspace_id.length <= 256
  && (value.role === 'viewer' || value.role === 'editor')
  && ((value.development_only === true && (value.identity_mode === undefined || value.identity_mode === 'development'))
    || (value.development_only === false && value.identity_mode === 'private_machine'));
async function readJSON(response: Response, limit: number): Promise<unknown> {
  if (!/^application\/json(?:\s*;|$)/i.test(response.headers.get('Content-Type') || '')) return invalid();
  const declared = response.headers.get('Content-Length');
  if (declared && Number(declared) > limit) return invalid();
  const reader = response.body?.getReader();
  if (!reader) return invalid();
  const parts: Uint8Array[] = []; let size = 0;
  for (;;) { const { value, done } = await reader.read(); if (done) break; size += value.byteLength; if (size > limit) { await reader.cancel(); return invalid(); } parts.push(value); }
  const body = new Uint8Array(size); let offset = 0;
  for (const part of parts) { body.set(part, offset); offset += part.byteLength; }
  try { return JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(body)); } catch { return invalid(); }
}
type Options = { transport?: typeof fetch; loadSession?: (signal?: AbortSignal) => Promise<SessionResponse>; pause?: (ms: number, signal: AbortSignal) => Promise<void>; now?: () => number; requestTimeoutMs?: number };
type Run = { generation: number; identity: SessionResponse; input: RecoveryComparisonRequest; ref?: ProductionContextRef; session?: VoiceHTTPSessionResponse; turn?: VoiceHTTPTurnResponse; revoked: boolean; readAbort: AbortController; done: Promise<void> };
const defaultPause = (ms: number, signal: AbortSignal) => new Promise<void>((resolve, reject) => {
  if (signal.aborted) { reject(signal.reason); return; }
  const onAbort = () => { clearTimeout(timer); reject(signal.reason); };
  const timer = setTimeout(() => { signal.removeEventListener('abort', onAbort); resolve(); }, ms);
  signal.addEventListener('abort', onAbort, { once: true });
});
export function voiceExample(example: VoiceExample, workspace: string): RecoveryComparisonRequest {
  const source = example === 'recovery' || example === 'recovery_precision' ? recoveryExamples[example] : JSON.parse(example === 'recovery_noop' ? noopRaw : unsupportedRaw) as RecoveryComparisonRequest;
  const request = structuredClone(source) as RecoveryComparisonRequest;
  request.snapshot.workspace_id = workspace; request.scenario.workspace_id = workspace; request.recovery.workspace_id = workspace;
  return request;
}

export class VoiceController {
  state: VoiceView = { kind: 'checking', example: 'recovery', arrivals: {}, assignments: {}, source: 'inline', contexts: [], hasMore: false, snapshotText: '' };
  private readonly transport: typeof fetch;
  private readonly loadSession: (signal?: AbortSignal) => Promise<SessionResponse>;
  private readonly pause: (ms: number, signal: AbortSignal) => Promise<void>;
  private readonly now: () => number;
  private readonly requestTimeoutMs: number;
  private readonly listeners = new Set<(view: VoiceView) => void>();
  private generation = 0;
  private current?: Run;
  private identityAbort?: AbortController;
  private cleanupBarrier: Promise<void> = Promise.resolve();
  private readonly retired: Run[] = [];
  private cleanupFailed = false;
  private cleanupFailure?: unknown;
  private disposed = false;
  private modified = false;
  constructor(options: Options = {}) { this.transport = options.transport ?? ((input, init) => globalThis.fetch(input, init)); this.loadSession = options.loadSession ?? fetchSession; this.pause = options.pause ?? defaultPause; this.now = options.now ?? Date.now; this.requestTimeoutMs = options.requestTimeoutMs ?? 12000; }
  subscribe(listener: (view: VoiceView) => void): () => void { this.listeners.add(listener); listener(this.state); return () => { this.listeners.delete(listener); }; }
  private emit(update: Partial<VoiceView>) { if (this.disposed) return; this.state = { ...this.state, ...update }; for (const listener of this.listeners) listener(this.state); }
  private isCurrent(run: Run) { return !this.disposed && !run.revoked && this.current === run && this.generation === run.generation; }
  private validRunRef(actual: ProductionContextRef | undefined, expected: ProductionContextRef | undefined) {
    return expected ? contextRefMatches(actual, expected) : actual === undefined;
  }
  private clear(kind: VoiceKind) { this.emit({ kind, session: undefined, turn: undefined, outcome: undefined, comparison: undefined, message: undefined }); }
  private signedOut(message = 'The authenticated local session is unavailable. Check the session again.') {
    this.clear('signed_out'); this.emit({ identity: undefined, base: undefined, selected: undefined, contexts: [], hasMore: false, arrivals: {}, assignments: {}, snapshotText: '', message });
  }
  private async request<T>(method: string, path: string, schema: string, body?: unknown, signal?: AbortSignal): Promise<T> {
    const encoded = body === undefined ? undefined : JSON.stringify(body);
    const limit = schema === 'VoiceHTTPTurnResponse' ? generated.limits.turn_request_bytes : generated.limits.session_request_bytes;
    if (encoded && byteLength(encoded) > limit) throw new VoiceError('The supplied scenario exceeds the local request limit.', 'validation');
    const timeout = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    const deadline = new Promise<never>((_, reject) => { timer = setTimeout(() => { timeout.abort(); reject(new VoiceError('The local request timed out. A response to a create request may have been lost; its unknown session expires on the server.', 'unavailable')); }, this.requestTimeoutMs); });
    let response: Response; let value: unknown;
    try {
      ({ response, value } = await Promise.race([
        this.transport(path, { method, cache: 'no-store', headers: encoded === undefined ? undefined : { 'Content-Type': 'application/json' }, body: encoded,
          signal: signal ? AbortSignal.any([signal, timeout.signal]) : timeout.signal }).then(async response => ({ response, value: await readJSON(response, generated.limits.response_bytes) })),
        deadline,
      ]));
    } catch (error) {
      if (signal?.aborted) throw error;
      if (error instanceof VoiceError) throw error;
      throw new VoiceError('The local voice comparison service is unavailable.', 'unavailable');
    } finally { if (timer) clearTimeout(timer); }
    if (!response.ok) {
      if (!voiceMatches(value, 'StateErrorResponse')) return invalid();
      const problem = (value as { error: { code: string; message: string } }).error;
      const kind = response.status === 401 || response.status === 403 ? 'session' : response.status === 409 ? 'validation' : response.status === 404 ? 'expired' : response.status === 429 ? 'capacity' : response.status === 422 || response.status === 413 ? 'validation' : 'unavailable';
      const message = response.status === 409 ? 'The saved context changed. Refresh and select its current revision before comparing.' : kind === 'session' ? 'The authenticated local session changed. Check the session again.' : kind === 'expired' ? 'This local session or turn is no longer available.' : kind === 'capacity' ? 'Local voice comparison capacity is full.' : kind === 'validation' ? 'The supplied comparison was rejected. Review the inputs.' : 'The local voice comparison service is unavailable.';
      void problem;
      throw new VoiceError(message, kind);
    }
    if (!voiceMatches(value, schema)) return invalid();
    return value as T;
  }
  private async checkIdentity(): Promise<SessionResponse> {
    const controller = new AbortController(); this.identityAbort = controller;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const deadline = new Promise<never>((_, reject) => { timer = setTimeout(() => { controller.abort(); reject(new VoiceError('Checking the local session timed out.', 'session')); }, this.requestTimeoutMs); });
    try {
      const identity = await Promise.race([this.loadSession(controller.signal), deadline]);
      if (!validIdentity(identity)) throw new VoiceError('The local session response is invalid.', 'session');
      return identity;
    } catch { throw new VoiceError('The authenticated local session is unavailable. Check the session again.', 'session'); }
    finally { if (timer) clearTimeout(timer); if (this.identityAbort === controller) this.identityAbort = undefined; }
  }
  private async cleanup(run: Run): Promise<void> {
    const session = run.session;
    if (!session) return;
    let failure: unknown;
    if (run.turn) {
      try {
        const cancelled = await this.request<VoiceHTTPCancelResponse>('POST', `${root}/${encodeURIComponent(session.session_id)}/turns/${encodeURIComponent(run.turn.turn_id)}/cancel`, 'VoiceHTTPCancelResponse');
        if (cancelled.session_id !== session.session_id || cancelled.turn_id !== run.turn.turn_id || cancelled.context_version !== session.context_version || cancelled.context_fingerprint !== session.context_fingerprint) return invalid();
      } catch (error) { if (!(error instanceof VoiceError && error.kind === 'expired')) failure = error; }
    }
    try {
      const closed = await this.request<VoiceHTTPCloseResponse>('DELETE', `${root}/${encodeURIComponent(session.session_id)}`, 'VoiceHTTPCloseResponse');
      if (closed.session_id !== session.session_id) return invalid();
    } catch (error) { if (!(error instanceof VoiceError && error.kind === 'expired')) failure = error; }
    if (failure) throw new VoiceError('Could not confirm cleanup of the previous local session. Check the session before continuing.', 'cleanup');
    run.session = undefined; run.turn = undefined;
  }
  private async drainRetired(explicitRetry = false): Promise<void> {
    if (this.cleanupFailed && !explicitRetry) throw this.cleanupFailure ?? new VoiceError('Could not confirm cleanup of the previous local session.', 'cleanup');
    try {
      while (this.retired.length) {
        const run = this.retired[0];
        await run.done;
        await this.cleanup(run);
        this.retired.shift();
      }
      this.cleanupFailed = false;
      this.cleanupFailure = undefined;
    } catch (error) { this.cleanupFailed = true; this.cleanupFailure = error; throw error; }
  }
  private retire(kind: VoiceKind): Promise<void> {
    ++this.generation; this.identityAbort?.abort();
    const old = this.current; this.current = undefined;
    if (old) { old.revoked = true; old.readAbort.abort(); this.retired.push(old); }
    this.clear(kind);
    this.cleanupBarrier = this.cleanupBarrier.then(() => this.drainRetired());
    return this.cleanupBarrier;
  }
  private contextPath(workspace: string) { return `/v2/workspaces/${encodeURIComponent(workspace)}/production-contexts`; }
  private async readContext(id: string, ref?: ProductionContextRef): Promise<StoredProductionContext> {
    const response = await this.request<ProductionContextResponse>('GET', `/v2/production-contexts/${encodeURIComponent(id)}`, 'ProductionContextResponse');
    const context = response.context;
    if (!Number.isSafeInteger(context.revision) || context.snapshot.workspace_id !== context.workspace_id || context.snapshot.snapshot_id !== context.snapshot_id || context.snapshot.plan_version !== context.plan_version || context.metadata.numerical_acceptance !== 'not_claimed' || ref && !contextRefMatches(contextRef(context), ref)) {
      throw new VoiceError('The saved context changed. Refresh and select its current revision before comparing.', 'validation');
    }
    return context;
  }
  private setBase(base: RecoveryComparisonRequest) {
    this.modified = false;
    this.emit({ base, arrivals: Object.fromEntries(base.snapshot.receipts.map(item => [item.receipt_id, item.arrival_at])),
      assignments: Object.fromEntries(base.recovery.assignments.map(item => [item.order_id, item.period_id])) });
  }
  selectSource(source: 'inline' | 'saved'): void {
    if (source === this.state.source) return;
    void this.retire('idle').catch(error => this.emit({ kind: 'failed', message: error instanceof Error ? error.message : 'Cleanup failed.' }));
    const identity = this.state.identity;
    this.emit({ source, selected: undefined, snapshotText: '', base: undefined, arrivals: {}, assignments: {} });
    if (source === 'inline' && identity) this.setBase(voiceExample(this.state.example, identity.workspace_id));
    if (source === 'saved' && identity) void this.refreshContexts();
  }
  editSnapshot(value: string): void {
    void this.retire('idle').catch(error => this.emit({ kind: 'failed', message: error instanceof Error ? error.message : 'Cleanup failed.' }));
    this.emit({ snapshotText: value, base: undefined, arrivals: {}, assignments: {} });
  }
  useCompleteExample(): void {
    const identity = this.state.identity;
    if (!identity || identity.role !== 'editor') return;
    this.editSnapshot(JSON.stringify(voiceExample(this.state.example, identity.workspace_id).snapshot, null, 2));
  }
  async refreshContexts(): Promise<void> {
    const cleanup = this.retire('loading_contexts'); const generation = this.generation;
    this.emit({ selected: undefined, base: undefined, contexts: [], hasMore: false, snapshotText: '', arrivals: {}, assignments: {} });
    try {
      await cleanup;
      if (generation !== this.generation || this.disposed) return;
      const identity = this.state.identity;
      if (!identity) { this.signedOut(); return; }
      const latest = await this.checkIdentity();
      if (generation !== this.generation || this.disposed) return;
      if (identityKey(latest) !== identityKey(identity)) { this.signedOut(); return; }
      const list = await this.request<ProductionContextsResponse>('GET', this.contextPath(identity.workspace_id), 'ProductionContextsResponse');
      if (generation !== this.generation || this.disposed) return;
      if (list.contexts.some(item => item.workspace_id !== identity.workspace_id || !Number.isSafeInteger(item.revision))) return invalid();
      this.emit({ kind: 'idle', contexts: list.contexts, hasMore: list.has_more });
    } catch (error) {
      if (generation !== this.generation || this.disposed) return;
      if (error instanceof VoiceError && error.kind === 'session') this.signedOut(error.message);
      else this.emit({ kind: 'failed', message: error instanceof Error ? error.message : 'Could not load saved contexts.' });
    }
  }
  async selectSaved(id: string): Promise<void> {
    if (!this.state.contexts.some(item => item.context_id === id)) return;
    const cleanup = this.retire('loading_contexts'); const generation = this.generation;
    this.emit({ selected: undefined, base: undefined, snapshotText: '', arrivals: {}, assignments: {} });
    try {
      await cleanup;
      if (generation !== this.generation || this.disposed) return;
      const identity = this.state.identity;
      if (!identity) { this.signedOut(); return; }
      const latest = await this.checkIdentity();
      if (generation !== this.generation || this.disposed) return;
      if (identityKey(latest) !== identityKey(identity)) { this.signedOut(); return; }
      const selected = await this.readContext(id);
      if (generation !== this.generation || this.disposed) return;
      if (selected.workspace_id !== identity.workspace_id) return invalid();
      this.emit({ kind: 'idle', selected, snapshotText: JSON.stringify(selected.snapshot, null, 2) });
      this.setBase(savedScenario(selected.snapshot));
    } catch (error) {
      if (generation !== this.generation || this.disposed) return;
      this.emit({ kind: 'failed', message: error instanceof Error ? error.message : 'Could not read the saved context.' });
    }
  }
  async saveContext(replace: boolean): Promise<void> {
    const cleanup = this.retire('closing'); const generation = this.generation;
    const selected = this.state.selected;
    try {
      await cleanup;
      if (generation !== this.generation || this.disposed) return;
      const identity = this.state.identity;
      if (!identity) { this.signedOut(); return; }
      if (identity.role !== 'editor') throw new VoiceError('Only an editor can save or replace a synthetic context.', 'validation');
      const snapshot = parseSnapshot(this.state.snapshotText, identity.workspace_id);
      const latest = await this.checkIdentity();
      if (generation !== this.generation || this.disposed) return;
      if (identityKey(latest) !== identityKey(identity)) { this.signedOut(); return; }
      if (replace && !selected) throw new VoiceError('Select a saved context before replacing it.', 'validation');
      const response = replace && selected
        ? await this.request<ProductionContextResponse>('PUT', `/v2/production-contexts/${encodeURIComponent(selected.context_id)}`, 'ProductionContextResponse', { contract_version: '0.2.0', expected_revision: selected.revision, snapshot })
        : await this.request<ProductionContextResponse>('POST', this.contextPath(identity.workspace_id), 'ProductionContextResponse', { contract_version: '0.2.0', snapshot });
      if (generation !== this.generation || this.disposed) return;
      const context = response.context;
      if (context.workspace_id !== identity.workspace_id || !Number.isSafeInteger(context.revision) || replace && selected && (context.context_id !== selected.context_id || context.revision !== selected.revision + 1)) return invalid();
      this.emit({ kind: 'idle', source: 'saved', selected: context, snapshotText: JSON.stringify(context.snapshot, null, 2), message: `Saved revision ${context.revision}. Storage validation is syntactic; numerical acceptance is not claimed.` });
      this.setBase(savedScenario(context.snapshot));
      try {
        const list = await this.request<ProductionContextsResponse>('GET', this.contextPath(identity.workspace_id), 'ProductionContextsResponse');
        if (generation === this.generation && !this.disposed && list.contexts.every(item => item.workspace_id === identity.workspace_id)) this.emit({ contexts: list.contexts, hasMore: list.has_more });
      } catch { /* The successful write remains selected; the list can be refreshed explicitly. */ }
    } catch (error) {
      if (generation !== this.generation || this.disposed) return;
      if (error instanceof VoiceError && error.kind === 'session') this.signedOut(error.message);
      else {
        const stale = error instanceof VoiceError && error.message.includes('changed');
        this.emit({ kind: stale ? 'stale' : 'failed', selected: stale ? undefined : this.state.selected, base: stale ? undefined : this.state.base, message: error instanceof Error ? error.message : 'Could not save the context.' });
      }
    }
  }
  async refreshIdentity(): Promise<void> {
    const retryFailedCleanup = this.cleanupFailed;
    const cleanup = this.retire('checking'); const generation = this.generation;
    this.emit({ identity: undefined, base: undefined, selected: undefined, contexts: [], hasMore: false, snapshotText: '', arrivals: {}, assignments: {} });
    try {
      if (retryFailedCleanup) {
        this.cleanupBarrier = cleanup.catch(() => {}).then(() => this.drainRetired(true));
        await this.cleanupBarrier;
      } else await cleanup;
      if (generation !== this.generation || this.disposed) return;
      const identity = await this.checkIdentity();
      if (generation !== this.generation || this.disposed) return;
      const base = this.state.source === 'inline' ? voiceExample(this.state.example, identity.workspace_id) : undefined;
      this.emit({ kind: this.state.source === 'saved' ? 'loading_contexts' : 'idle', identity });
      if (base) this.setBase(base);
      if (this.state.source === 'saved') {
        const list = await this.request<ProductionContextsResponse>('GET', this.contextPath(identity.workspace_id), 'ProductionContextsResponse');
        if (generation !== this.generation || this.disposed) return;
        if (list.contexts.some(item => item.workspace_id !== identity.workspace_id || !Number.isSafeInteger(item.revision))) return invalid();
        this.emit({ kind: 'idle', contexts: list.contexts, hasMore: list.has_more });
      }
    } catch (error) {
      if (generation !== this.generation || this.disposed) return;
      if (error instanceof VoiceError && error.kind !== 'session') this.emit({ kind: 'failed', message: error.message });
      else this.signedOut(error instanceof VoiceError ? error.message : 'Session unavailable.');
    }
  }
  selectExample(example: VoiceExample): void {
    if (!['recovery', 'recovery_noop', 'recovery_precision', 'recovery_unsupported'].includes(example)) return;
    void this.retire('idle').catch(error => this.emit({ kind: 'failed', message: error instanceof Error ? error.message : 'Cleanup failed.' }));
    const identity = this.state.identity; const base = identity && this.state.source === 'inline' ? voiceExample(example, identity.workspace_id) : undefined;
    this.modified = false;
    this.emit({ example, base, arrivals: base ? Object.fromEntries(base.snapshot.receipts.map(item => [item.receipt_id, base.scenario.arrival_replacements.find(change => change.receipt_id === item.receipt_id)?.arrival_at ?? item.arrival_at])) : this.state.arrivals,
      assignments: base ? Object.fromEntries(base.recovery.assignments.map(item => [item.order_id, item.period_id])) : this.state.assignments });
  }
  editArrival(id: string, value: string): void { this.modified = true; void this.retire('idle').catch(error => this.emit({ kind: 'failed', message: error instanceof Error ? error.message : 'Cleanup failed.' })); this.emit({ arrivals: { ...this.state.arrivals, [id]: value } }); }
  editAssignment(id: string, value: string): void { this.modified = true; void this.retire('idle').catch(error => this.emit({ kind: 'failed', message: error instanceof Error ? error.message : 'Cleanup failed.' })); this.emit({ assignments: { ...this.state.assignments, [id]: value } }); }
  async cancel(): Promise<void> {
    const cleanup = this.retire('closing'); const generation = this.generation;
    try { await cleanup; if (generation === this.generation && !this.disposed) this.clear('cancelled'); }
    catch (error) { if (generation === this.generation) this.emit({ kind: 'failed', message: error instanceof Error ? error.message : 'Cleanup failed.' }); }
  }
  private async execute(run: Run): Promise<void> {
    try {
      if (run.ref) await this.readContext(run.ref.context_id, run.ref);
      if (!this.isCurrent(run)) return;
      const created = run.ref
        ? await this.request<VoiceHTTPSessionResponse>('POST', '/v2/voice/saved-context-sessions', 'VoiceHTTPSessionResponse', { contract_version: '0.2.0', context_id: run.ref.context_id, expected_revision: run.ref.revision })
        : await this.request<VoiceHTTPSessionResponse>('POST', root, 'VoiceHTTPSessionResponse', { contract_version: '0.2.0', snapshot: run.input.snapshot });
      run.session = created;
      if (created.snapshot_id !== run.input.snapshot.snapshot_id || created.plan_version !== run.input.snapshot.plan_version || !this.validRunRef(created.production_context_ref, run.ref)) return invalid();
      if (!this.isCurrent(run)) return;
      this.emit({ kind: 'created', session: created });
      const beforeStart = await this.checkIdentity();
      if (!this.isCurrent(run)) return;
      if (identityKey(beforeStart) !== identityKey(run.identity)) throw new VoiceError('The authenticated identity changed.', 'session');
      if (run.ref) await this.readContext(run.ref.context_id, run.ref);
      if (!this.isCurrent(run)) return;
      run.input.scenario.scenario_id = created.scenario_id; run.input.recovery.scenario_id = created.scenario_id;
      this.emit({ kind: 'starting' });
      const turn = await this.request<VoiceHTTPTurnResponse>('POST', `${root}/${encodeURIComponent(created.session_id)}/turns`, 'VoiceHTTPTurnResponse', {
        contract_version: '0.2.0', context_version: created.context_version,
        call: { name: 'compare_receipt_recovery', arguments: { arrival_replacements: run.input.scenario.arrival_replacements, assignments: run.input.recovery.assignments } },
      });
      if (turn.session_id !== created.session_id || turn.context_version !== created.context_version || turn.context_fingerprint !== created.context_fingerprint || !this.validRunRef(turn.production_context_ref, run.ref)) return invalid();
      run.turn = turn;
      if (!this.isCurrent(run)) return;
      this.emit({ kind: 'pending', turn });
      const deadline = this.now() + 45000; let delay = 120;
      while (this.isCurrent(run)) {
        if (this.now() >= deadline) throw new VoiceError('The local turn timed out. Start a fresh comparison.', 'expired');
        const result = await this.request<VoiceHTTPResultResponse>('GET', `${root}/${encodeURIComponent(created.session_id)}/turns/${encodeURIComponent(turn.turn_id)}/result`, 'VoiceHTTPResultResponse', undefined, run.readAbort.signal);
        if (!this.isCurrent(run)) return;
        if (result.session_id !== created.session_id || result.turn_id !== turn.turn_id || result.context_version !== created.context_version || result.context_fingerprint !== created.context_fingerprint || result.read_only !== true || result.synthetic !== true || !this.validRunRef(result.production_context_ref, run.ref)) return invalid();
        if (result.state === 'failed') throw new VoiceError('The local comparison failed. Review the supplied scenario or try again.', 'unavailable');
        if (result.state === 'ready') {
          if (result.outcome.ownership.session_id !== created.session_id || result.outcome.ownership.turn_id !== turn.turn_id || result.outcome.read_only !== true || result.outcome.tool_name !== 'compare_receipt_recovery') return invalid();
          this.clear('checking');
          const latest = await this.checkIdentity();
          if (!this.isCurrent(run)) return;
          if (identityKey(latest) !== identityKey(run.identity)) throw new VoiceError('The authenticated identity changed.', 'session');
          if (run.ref) await this.readContext(run.ref.context_id, run.ref);
          if (!this.isCurrent(run)) return;
          if (result.outcome.status === 'compared') {
            const comparison = validateComparisonResponse({ contract_version: '0.2.0', comparison: result.outcome.result }, run.input).comparison;
            this.emit({ kind: 'compared', session: created, turn, outcome: result.outcome, comparison });
          } else {
            const kind = result.outcome.status === 'unsupported' ? 'unsupported' : result.outcome.status === 'clarification_required' ? 'clarification' : result.outcome.status === 'rejected' ? 'rejected' : 'unavailable';
            this.emit({ kind, session: created, turn, outcome: result.outcome });
          }
          return;
        }
        await this.pause(delay, run.readAbort.signal); delay = Math.min(1000, delay * 2);
      }
    } catch (error) {
      if (!this.isCurrent(run)) return;
      const kind = error instanceof VoiceError ? error.kind : 'unavailable';
      if (kind === 'session') this.signedOut(error instanceof VoiceError ? error.message : undefined);
      else { const stale = kind === 'validation' && error instanceof VoiceError && error.message.includes('changed'); this.clear(kind === 'capacity' ? 'capacity' : kind === 'expired' ? 'expired' : stale ? 'stale' : kind === 'validation' ? 'failed' : 'unavailable'); this.emit({ selected: stale ? undefined : this.state.selected, base: stale ? undefined : this.state.base, message: error instanceof VoiceError ? error.message : 'Local comparison unavailable.' }); }
      try { await this.cleanup(run); } catch (cleanupError) {
        this.cleanupFailed = true; this.cleanupFailure = cleanupError;
        this.emit({ kind: 'failed', message: cleanupError instanceof Error ? cleanupError.message : 'Cleanup failed.' });
      }
    }
  }
  async run(): Promise<void> {
    const cleanup = this.retire('closing'); const generation = this.generation;
    try { await cleanup; } catch (error) { if (generation === this.generation) this.emit({ kind: 'failed', message: error instanceof Error ? error.message : 'Cleanup failed.' }); return; }
    if (generation !== this.generation || this.disposed) return;
    const identity = this.state.identity; const base = this.state.base; const selected = this.state.selected;
    if (!identity || !base) { this.signedOut('Check the local session before comparing.'); return; }
    this.clear('checking');
    let latest: SessionResponse;
    try { latest = await this.checkIdentity(); } catch (error) { if (generation === this.generation) this.signedOut(error instanceof VoiceError ? error.message : undefined); return; }
    if (generation !== this.generation || this.disposed) return;
    if (identityKey(latest) !== identityKey(identity)) { this.signedOut('The authenticated identity changed. Check the session again.'); return; }
    let input: RecoveryComparisonRequest;
    try { input = this.state.source === 'inline' && this.state.example === 'recovery_unsupported' && !this.modified ? structuredClone(base) : buildComparisonRequest(base, this.state.arrivals, this.state.assignments); } catch (error) { this.emit({ kind: 'failed', message: error instanceof Error ? error.message : 'Review the supplied inputs.' }); return; }
    const ref = this.state.source === 'saved' && selected ? contextRef(selected) : undefined;
    if (this.state.source === 'saved' && !ref) { this.emit({ kind: 'failed', message: 'Select a saved context first.' }); return; }
    const run: Run = { generation, identity, input, ref, revoked: false, readAbort: new AbortController(), done: Promise.resolve() };
    this.current = run; this.emit({ kind: 'creating' });
    run.done = this.execute(run); await run.done;
  }
  dispose(): void { if (this.disposed) return; void this.retire('cancelled').catch(() => {}); this.disposed = true; this.listeners.clear(); }
}
