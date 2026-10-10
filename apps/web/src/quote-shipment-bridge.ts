import type { QuoteShipmentBridgeResponse, ShipmentRecoveryContext } from '../../../contracts/generated/types';
import type { QuoteSession, SourcingCandidate } from './offers-service';

const object = (v: unknown): v is Record<string, unknown> => v !== null && typeof v === 'object' && !Array.isArray(v);
const exact = (v: Record<string, unknown>, names: string[]) => Object.keys(v).length === names.length && names.every(k => Object.hasOwn(v, k));
const text = (v: unknown, max = 256): v is string => typeof v === 'string' && v.length > 0 && v.length <= max;
const hex32 = (v: unknown): v is string => typeof v === 'string' && /^[0-9a-f]{32}$/.test(v);
const hex64 = (v: unknown): v is string => typeof v === 'string' && /^[0-9a-f]{64}$/.test(v);
const documentId = (v: unknown): v is string => typeof v === 'string' && /^sha256:[0-9a-f]{64}$/.test(v);
const positive = (v: unknown): v is number => Number.isSafeInteger(v) && (v as number) > 0;
const version = (v: unknown): v is number => positive(v) && v <= 2147483647;
const index = (v: unknown): v is number => Number.isSafeInteger(v) && (v as number) >= 0 && (v as number) <= 2147483647;
const day = (v: unknown): v is string => typeof v === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(v) &&
  !Number.isNaN(Date.parse(`${v}T00:00:00Z`)) && new Date(`${v}T00:00:00Z`).toISOString().slice(0, 10) === v;
const instant = (v: unknown): v is string => typeof v === 'string' && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?Z$/.test(v) &&
  !Number.isNaN(Date.parse(v)) && new Date(`${v.slice(0, 19)}Z`).toISOString().slice(0, 19) === v.slice(0, 19);

export type BridgeCandidate = Pick<SourcingCandidate, 'candidate_id' | 'offer_id' | 'offer_version' | 'item_index' | 'document_id'>;
export type ContextTriple = { context_id: string; revision: number; request_sha256: string };
export type QuoteShipmentHandoff = { token: string; session: Pick<QuoteSession, 'principal_id' | 'workspace_id' | 'role' | 'identity_mode'>; triple: ContextTriple;
  source_as_of: string; source_checked_at: string; context_as_of: string;
  host_binding: 'host_configured_synthetic'; source_authenticity: 'not_verified' };
let pending: QuoteShipmentHandoff | null = null;

export class QuoteShipmentBridgeError extends Error {
  constructor() { super('The local quote-to-shipment check is unavailable. Refresh the quote workspace.'); }
}
const fail = (): never => { throw new QuoteShipmentBridgeError(); };
export const quoteSessionKey = (s: Pick<QuoteSession, 'principal_id' | 'workspace_id' | 'role' | 'identity_mode'>) =>
  JSON.stringify([s.principal_id, s.workspace_id, s.role, s.identity_mode]);
export const candidateIdentity = (c: BridgeCandidate) => `${c.offer_id}@v${c.offer_version}#item${c.item_index}`;
export function validBridgeCandidate(c: BridgeCandidate): boolean {
  return text(c.offer_id, 128) && /^[A-Za-z0-9_-]+$/.test(c.offer_id) && version(c.offer_version) && index(c.item_index) &&
    documentId(c.document_id) && text(c.candidate_id, 160) && c.candidate_id === candidateIdentity(c);
}
function matchesResponse(v: unknown, session: QuoteSession, selected: BridgeCandidate): v is QuoteShipmentBridgeResponse {
  if (!object(v) || !exact(v, ['contract_version', 'context', 'bridge']) || v.contract_version !== '0.2.0' ||
      !object(v.context) || !exact(v.context, ['principal_id', 'workspace_id', 'role', 'identity_mode']) ||
      v.context.principal_id !== session.principal_id || v.context.workspace_id !== session.workspace_id ||
      v.context.role !== session.role || v.context.identity_mode !== session.identity_mode || !object(v.bridge) ||
      !exact(v.bridge, ['candidate', 'shipment_context', 'host_binding', 'source_authenticity', 'context_as_of', 'source_as_of', 'source_checked_at', 'evidence_refs'])) return false;
  const b = v.bridge;
  if (!object(b.candidate) || !exact(b.candidate, ['candidate_id', 'offer_id', 'offer_version', 'item_index', 'document_id']) ||
      !validBridgeCandidate(b.candidate as BridgeCandidate) ||
      (['candidate_id', 'offer_id', 'offer_version', 'item_index', 'document_id'] as const).some(k => (b.candidate as Record<string, unknown>)[k] !== selected[k]) ||
      !object(b.shipment_context) || !exact(b.shipment_context, ['context_id', 'revision', 'request_sha256', 'plan_version']) ||
      !hex32(b.shipment_context.context_id) || !positive(b.shipment_context.revision) ||
      !hex64(b.shipment_context.request_sha256) || !text(b.shipment_context.plan_version) ||
      b.host_binding !== 'host_configured_synthetic' || b.source_authenticity !== 'not_verified' ||
      !instant(b.context_as_of) || !day(b.source_as_of) || !instant(b.source_checked_at) ||
      !Array.isArray(b.evidence_refs) || b.evidence_refs.length > 64) return false;
  return b.evidence_refs.every(e => object(e) && exact(e, ['field', 'locator', 'text_sha256', 'document_id']) &&
    text(e.field) && text(e.locator) && hex64(e.text_sha256) && e.document_id === selected.document_id);
}
async function boundedJson(response: Response): Promise<unknown> {
  if (!response.headers.get('content-type')?.toLowerCase().includes('application/json')) return fail();
  const length = response.headers.get('content-length');
  if (length !== null && (!/^\d+$/.test(length) || Number(length) > 65536)) return fail();
  if (!response.body) return fail();
  const reader = response.body.getReader();
  const chunks: Uint8Array[] = []; let total = 0;
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      total += value.byteLength;
      if (total > 65536) return fail();
      chunks.push(value);
    }
  } finally { reader.releaseLock(); }
  const bytes = new Uint8Array(total); let offset = 0;
  for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
  try { return JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes)); }
  catch { return fail(); }
}
export async function getQuoteShipmentBridge(session: QuoteSession, selected: BridgeCandidate,
  isCurrent: () => boolean, signal: AbortSignal, fetcher: typeof fetch = fetch): Promise<QuoteShipmentBridgeResponse> {
  const requested = { candidate_id: selected.candidate_id, offer_id: selected.offer_id,
    offer_version: selected.offer_version, item_index: selected.item_index, document_id: selected.document_id };
  if (!isCurrent() || signal.aborted || session.identity_mode !== 'development' || !validBridgeCandidate(requested)) return fail();
  const query = new URLSearchParams({ candidate_id: requested.candidate_id, offer_id: requested.offer_id,
    offer_version: String(requested.offer_version), item_index: String(requested.item_index), document_id: requested.document_id });
  let response: Response;
  try { response = await fetcher(`/v2/workspaces/${encodeURIComponent(session.workspace_id)}/shipment-recovery/quote-bridge?${query}`, { method: 'GET', signal, cache: 'no-store' }); }
  catch { return fail(); }
  if (!isCurrent() || signal.aborted || !response.ok) return fail();
  let body: unknown;
  try { body = await boundedJson(response); } catch { return fail(); }
  if (!isCurrent() || signal.aborted || !matchesResponse(body, session, requested)) return fail();
  return body;
}
export function handoffUrl(t: ContextTriple): string {
  if (!hex32(t.context_id) || !positive(t.revision) || !hex64(t.request_sha256)) return fail();
  return `/shipment-recovery?${new URLSearchParams({ context_id: t.context_id, revision: String(t.revision), request_sha256: t.request_sha256 })}`;
}
export function parseHandoff(search: string): ContextTriple | null {
  const p = new URLSearchParams(search);
  if ([...p.keys()].length !== 3 || ['context_id', 'revision', 'request_sha256'].some(k => p.getAll(k).length !== 1)) return null;
  const context_id = p.get('context_id'), revisionText = p.get('revision'), request_sha256 = p.get('request_sha256');
  if (!hex32(context_id) || !revisionText || !/^[1-9]\d*$/.test(revisionText) || !hex64(request_sha256)) return null;
  const revision = Number(revisionText);
  const triple = { context_id, revision, request_sha256 };
  return positive(revision) && String(revision) === revisionText &&
    search === handoffUrl(triple).replace('/shipment-recovery', '') ? triple : null;
}
export function contextMatchesTriple(c: ShipmentRecoveryContext, t: ContextTriple, workspace: string): boolean {
  return c.workspace_id === workspace && c.context_id === t.context_id && c.revision === t.revision && c.request_sha256 === t.request_sha256;
}
export function selectHandoffContext(list: ShipmentRecoveryContext[], handoff: QuoteShipmentHandoff | null,
  triple: ContextTriple | null, session: Pick<QuoteSession, 'principal_id' | 'workspace_id' | 'role' | 'identity_mode'>): ShipmentRecoveryContext | null {
  if (!handoff || !triple || quoteSessionKey(handoff.session) !== quoteSessionKey(session)) return null;
  if (handoff.triple.context_id !== triple.context_id || handoff.triple.revision !== triple.revision ||
      handoff.triple.request_sha256 !== triple.request_sha256) return null;
  const matches = list.filter(c => contextMatchesTriple(c, triple, session.workspace_id) &&
    c.as_of === handoff.context_as_of && c.source === 'host_configured_synthetic' &&
    c.source_authenticity === 'not_verified');
  return matches.length === 1 ? matches[0] : null;
}
export function stageHandoff(response: QuoteShipmentBridgeResponse, session: QuoteSession): QuoteShipmentHandoff {
  const b = response.bridge;
  pending = { token: crypto.randomUUID(), session: { principal_id: session.principal_id, workspace_id: session.workspace_id,
    role: session.role, identity_mode: session.identity_mode }, triple: {
    context_id: b.shipment_context.context_id, revision: b.shipment_context.revision,
    request_sha256: b.shipment_context.request_sha256 },
    source_as_of: b.source_as_of, source_checked_at: b.source_checked_at, context_as_of: b.context_as_of,
    host_binding: b.host_binding, source_authenticity: b.source_authenticity };
  return pending;
}
export function claimHandoff(state: unknown, search: string): QuoteShipmentHandoff | null {
  const triple = parseHandoff(search);
  const current = pending;
  pending = null;
  if (!triple || !current || !object(state) || state.token !== current.token ||
      JSON.stringify(state) !== JSON.stringify(current) ||
      triple.context_id !== current.triple.context_id || triple.revision !== current.triple.revision ||
      triple.request_sha256 !== current.triple.request_sha256) return null;
  return current;
}
