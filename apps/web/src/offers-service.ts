import type { SessionResponse } from '../../../contracts/generated/types';
import { fetchSession } from './state';
import type { BlockingReason, FieldStatus, Segment } from './offers';

const object = (v: unknown): v is Record<string, unknown> => v !== null && typeof v === 'object' && !Array.isArray(v);
const nonempty = (v: unknown): v is string => typeof v === 'string' && v.length > 0 && v.length <= 256;
const decimal = (v: unknown): v is string => typeof v === 'string' && /^-?(0|[1-9]\d*)(\.\d+)?$/.test(v) && v.length <= 64;
const id = (v: unknown): v is string => typeof v === 'string' && /^[A-Za-z0-9_-]{1,128}$/.test(v);
export type QuoteContext = { principal_id: string; workspace_id: string; role: 'editor' | 'viewer'; identity_mode: 'development' };
export type QuoteSession = QuoteContext & { development_only: true };
export type ServiceEvidence = { document_id: string; locator: string; quote: string; text_sha256: string; segment_text?: string };
export type SourcingEvidence = { field: string; document_id: string; locator: string; text_sha256: string };
export type ServiceCandidate = { value: unknown; verified: boolean; problems: string[]; evidence: ServiceEvidence[]; cited?: { locator: string; quote: string }[] };
export type ServiceField = { name: string; kind: string; status: FieldStatus; value: unknown; critical: boolean; source: string;
  reasons: string[]; evidence: ServiceEvidence[]; candidates: ServiceCandidate[]; evidence_status?: string; corrected_by?: string | null };
export type ServiceSegment = Segment & { text_sha256: string };
export type ServiceOffer = {
  offer_id: string; version: number; status: string; synthetic: boolean; extraction_id: string;
  document: { document_id: string; filename: string; media_type: string; byte_size: number };
  extractor?: string; fields: Record<string, ServiceField>; items: Record<string, ServiceField>[];
  blocking_reasons: BlockingReason[]; review_flags: { flag: string; locator: string | null; text: string }[];
  normalized: Record<string, unknown>; allowed_actions: string[]; segments?: ServiceSegment[];
};
export type ServiceOfferSummary = { offer_id: string; version: number; status: string; synthetic: boolean;
  filename: string; supplier_name: string | null; materials: string[]; blocking_reasons: number; updated_at: string };
export type ServiceExtraction = { extraction_id: string; extraction: { document: ServiceOffer['document']; synthetic: boolean; extractor: string; as_of: string }; segments: ServiceSegment[] };
export type SourcingCandidate = { candidate_id: string; offer_id: string; offer_version: number; item_index: number; document_id: string;
  material: string; quantity: { amount: string; unit: string; kg: string }; price: { type: 'fixed' | 'index_linked'; per_kg?: { amount: string; currency: string } | null;
  premium_per_kg?: { amount: string; currency: string } | null }; landed_price: { status: string; per_kg: { amount: string; currency: string } | null };
  payment_terms?: { source?: string }; evidence?: SourcingEvidence[]; field_sources?: Record<string, { status: string; source: string }> };
export type SourcingExclusion = { offer_id: string; offer_version: number; reason: string; item_index: number | null; detail?: string | null };
export type Sourcing = { as_of: string; candidates: SourcingCandidate[]; excluded: SourcingExclusion[]; synthetic: boolean; notes?: string[] };
export type ServiceUpload = { offer: ServiceOffer; extraction_id: string; deduplicated?: boolean };
export type QuoteErrorKind = 'session' | 'identity_changed' | 'invalid_response' | 'conflict' | 'forbidden' | 'unavailable' | 'invalid_request';
export class QuoteServiceError extends Error { constructor(readonly kind: QuoteErrorKind, message: string, readonly status = 0) { super(message); } }
const fail = (kind: QuoteErrorKind, message: string, status = 0): never => { throw new QuoteServiceError(kind, message, status); };

export function normalizeQuoteSession(value: SessionResponse | Record<string, unknown>): QuoteSession {
  if (!object(value) || value.contract_version !== '0.2.0' || !nonempty(value.principal_id) || !nonempty(value.workspace_id) ||
      (value.role !== 'editor' && value.role !== 'viewer') || value.development_only !== true ||
      (value.identity_mode !== undefined && value.identity_mode !== 'development'))
    return fail('session', 'A local development session is required.');
  return { principal_id: value.principal_id, workspace_id: value.workspace_id, role: value.role,
    identity_mode: 'development', development_only: true };
}
export async function loadQuoteSession(signal?: AbortSignal): Promise<QuoteSession> {
  try { return normalizeQuoteSession(await fetchSession(signal)); }
  catch { return fail('session', 'The local development session is unavailable.'); }
}
function sameContext(value: unknown, session: QuoteSession): boolean {
  return object(value) && Object.keys(value).sort().join(',') === 'identity_mode,principal_id,role,workspace_id' &&
    value.principal_id === session.principal_id && value.workspace_id === session.workspace_id &&
    value.role === session.role && value.identity_mode === session.identity_mode;
}
const hash64 = (value: unknown): value is string => typeof value === 'string' && /^[0-9a-f]{64}$/.test(value);
function validEvidence(value: unknown): boolean {
  return object(value) && nonempty(value.locator) && typeof value.quote === 'string' && hash64(value.text_sha256) &&
    nonempty(value.document_id) && (value.segment_text === undefined || typeof value.segment_text === 'string');
}
const supportedKinds = new Set(['text', 'date', 'integer', 'currency', 'quantity', 'incoterm', 'pricing', 'shipments', 'material']);
const money = (v: unknown) => object(v) && decimal(v.amount) && typeof v.currency === 'string' && /^[A-Z]{3}$/.test(v.currency);
function validValue(kind: string, v: unknown): boolean {
  if (v === null) return true;
  if (['text', 'date', 'currency', 'material'].includes(kind)) return typeof v === 'string' && v.length <= 500;
  if (kind === 'integer') return Number.isSafeInteger(v) && (v as number) >= 0;
  if (kind === 'quantity') return object(v) && decimal(v.amount) && nonempty(v.unit) &&
    (v.period === undefined || nonempty(v.period));
  if (kind === 'incoterm') return object(v) && nonempty(v.code) && (v.place === null || v.place === undefined || typeof v.place === 'string');
  if (kind === 'pricing') return object(v) && nonempty(v.per_unit) &&
    (v.type === 'fixed' ? money(v.unit_price) : v.type === 'index_linked' && nonempty(v.index) &&
      nonempty(v.averaging_period) && money(v.premium));
  if (kind === 'shipments') return Array.isArray(v) && v.length <= 100 && v.every(lot =>
    object(lot) && object(lot.quantity) && decimal(lot.quantity.amount) && nonempty(lot.quantity.unit) &&
    nonempty(lot.window));
  return false;
}
function validField(value: unknown): value is ServiceField {
  if (!object(value) || !nonempty(value.name) || !supportedKinds.has(String(value.kind)) ||
      !['extracted','missing','conflict','unverified','corrected'].includes(String(value.status)) ||
      typeof value.critical !== 'boolean' || !nonempty(value.source) || !Array.isArray(value.reasons) ||
      !value.reasons.every(v => typeof v === 'string') || !Array.isArray(value.evidence) || !value.evidence.every(validEvidence) ||
      ((value.source === 'user' || value.status === 'corrected') && value.evidence.length > 0) ||
      !Array.isArray(value.candidates) || !value.candidates.every(v => object(v) && typeof v.verified === 'boolean' &&
        Array.isArray(v.problems) && v.problems.every(p => typeof p === 'string') &&
        Array.isArray(v.evidence) && v.evidence.every(validEvidence) && validValue(value.kind as string, v.value))) return false;
  return validValue(value.kind as string, value.value);
}
function validOffer(value: unknown): value is ServiceOffer {
  return object(value) && id(value.offer_id) && Number.isSafeInteger(value.version) && (value.version as number) > 0 &&
    nonempty(value.status) && typeof value.synthetic === 'boolean' && nonempty(value.extraction_id) &&
    object(value.document) && nonempty(value.document.document_id) && nonempty(value.document.filename) &&
    nonempty(value.document.media_type) && Number.isSafeInteger(value.document.byte_size) &&
    object(value.fields) && Object.values(value.fields).every(validField) && Array.isArray(value.items) &&
    value.items.every(v => object(v) && Object.values(v).every(validField)) &&
    [...Object.values(value.fields), ...value.items.flatMap(v => Object.values(v))].every(v =>
      object(v) && Array.isArray(v.evidence) && v.evidence.every(e => object(e) && e.document_id === (value.document as Record<string, unknown>).document_id) &&
      Array.isArray(v.candidates) && v.candidates.every(c => object(c) && Array.isArray(c.evidence) &&
        c.evidence.every(e => object(e) && e.document_id === (value.document as Record<string, unknown>).document_id))) &&
    Array.isArray(value.review_flags) && value.review_flags.every(v => object(v) && nonempty(v.flag) &&
      (v.locator === null || typeof v.locator === 'string') && typeof v.text === 'string') &&
    Array.isArray(value.blocking_reasons) && value.blocking_reasons.every(v => object(v) &&
      nonempty(v.code) && nonempty(v.field) && typeof v.message === 'string') && object(value.normalized) &&
    Array.isArray(value.allowed_actions) && value.allowed_actions.every(v => typeof v === 'string');
}
function validSummary(value: unknown): value is ServiceOfferSummary {
  return object(value) && id(value.offer_id) && Number.isSafeInteger(value.version) && (value.version as number) > 0 &&
    nonempty(value.status) && typeof value.synthetic === 'boolean' && nonempty(value.filename) &&
    (value.supplier_name === null || typeof value.supplier_name === 'string') && Array.isArray(value.materials) &&
    value.materials.every(v => typeof v === 'string') && Number.isSafeInteger(value.blocking_reasons) &&
    (value.blocking_reasons as number) >= 0 && nonempty(value.updated_at);
}
function validSegment(v: unknown): v is Segment {
  return object(v) && nonempty(v.locator) && typeof v.text === 'string' && hash64(v.text_sha256) &&
    Array.isArray(v.flags) && v.flags.every(f => typeof f === 'string');
}
function validSourcing(v: unknown): v is Sourcing {
  if (!object(v) || typeof v.as_of !== 'string' || !Array.isArray(v.candidates) || !Array.isArray(v.excluded) || typeof v.synthetic !== 'boolean') return false;
  for (const candidate of v.candidates) {
    if (!object(candidate) || !nonempty(candidate.candidate_id) || !id(candidate.offer_id) ||
        !Number.isSafeInteger(candidate.offer_version) || (candidate.offer_version as number) < 1 ||
        !Number.isSafeInteger(candidate.item_index) || (candidate.item_index as number) < 0 ||
        !nonempty(candidate.document_id) || typeof candidate.material !== 'string' ||
        !object(candidate.quantity) || !decimal(candidate.quantity.amount) ||
        !nonempty(candidate.quantity.unit) || !decimal(candidate.quantity.kg) ||
        !object(candidate.price) || !object(candidate.landed_price)) return false;
    if (candidate.candidate_id !== `${candidate.offer_id}@v${candidate.offer_version}#item${candidate.item_index}`) return false;
    const p = candidate.price, l = candidate.landed_price;
    if (p.type !== 'fixed' && p.type !== 'index_linked') return false;
    for (const money of [p.unit_price, p.premium, p.per_kg, p.premium_per_kg, l.per_kg]) {
      if (money !== null && money !== undefined && (!object(money) || !decimal(money.amount) || !/^[A-Z]{3}$/.test(String(money.currency)))) return false;
    }
    if (p.type === 'fixed' && !money(p.per_kg)) return false;
    if (p.type === 'index_linked' && (!money(p.premium_per_kg) || l.per_kg !== null ||
        !nonempty(p.index) || !nonempty(p.averaging_period))) return false;
    if (!nonempty(l.status) || (candidate.evidence !== undefined && (!Array.isArray(candidate.evidence) ||
        !candidate.evidence.every(e => object(e) && nonempty(e.field) && nonempty(e.locator) &&
          hash64(e.text_sha256) && e.document_id === candidate.document_id)))) return false;
    if (candidate.field_sources !== undefined && (!object(candidate.field_sources) ||
        !Object.values(candidate.field_sources).every(f => object(f) && nonempty(f.status) && nonempty(f.source)))) return false;
  }
  return v.excluded.every(e => object(e) && id(e.offer_id) && Number.isSafeInteger(e.offer_version) &&
    (e.offer_version as number) > 0 && (e.item_index === null || Number.isSafeInteger(e.item_index) && (e.item_index as number) >= 0) &&
    nonempty(e.reason));
}
function validExtraction(v: unknown): v is ServiceExtraction {
  if (!object(v) || !id(v.extraction_id) || !object(v.extraction) || !object(v.extraction.document) ||
      !nonempty(v.extraction.document.document_id) || !nonempty(v.extraction.document.filename) ||
      !nonempty(v.extraction.document.media_type) || !Number.isSafeInteger(v.extraction.document.byte_size) ||
      !nonempty(v.extraction.as_of) || !nonempty(v.extraction.extractor) || typeof v.extraction.synthetic !== 'boolean' ||
      !Array.isArray(v.segments) || v.segments.length > 10000 || !v.segments.every(validSegment)) return false;
  return new Set(v.segments.map(s => s.locator)).size === v.segments.length;
}
const shapes = {
  offers: (v: unknown) => Array.isArray(v) && v.every(validSummary), offer: validOffer,
  upload: (v: unknown) => object(v) && validOffer(v.offer) && id(v.extraction_id) && v.offer.extraction_id === v.extraction_id,
  extraction: validExtraction,
  sourcing: validSourcing,
};
type Shape = keyof typeof shapes;
type ShapeData = { offers: ServiceOfferSummary[]; offer: ServiceOffer; upload: ServiceUpload; extraction: ServiceExtraction; sourcing: Sourcing };
export async function bindOfferExtraction(offer: ServiceOffer, extraction: ServiceExtraction, isCurrent: () => boolean): Promise<ServiceOffer> {
  if (!isCurrent() || offer.extraction_id !== extraction.extraction_id ||
      offer.document.document_id !== extraction.extraction.document.document_id)
    return fail('invalid_response', 'Offer and extraction identity do not match.');
  const segments = new Map<string, ServiceSegment>();
  for (const segment of extraction.segments) {
    if (!isCurrent()) return fail('identity_changed', 'The session changed during evidence verification.');
    const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(segment.text));
    if (!isCurrent()) return fail('identity_changed', 'The session changed during evidence verification.');
    const hash = Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('');
    if (hash !== segment.text_sha256 || segments.has(segment.locator))
      return fail('invalid_response', 'The extraction text and evidence hash do not match.');
    segments.set(segment.locator, segment);
  }
  const fields = [...Object.values(offer.fields), ...offer.items.flatMap(item => Object.values(item))];
  for (const field of fields) for (const evidence of [...field.evidence, ...field.candidates.flatMap(c => c.evidence)]) {
    const segment = segments.get(evidence.locator);
    if (evidence.document_id !== offer.document.document_id || !segment ||
        evidence.text_sha256 !== segment.text_sha256 ||
        (evidence.segment_text !== undefined && evidence.segment_text !== segment.text) ||
        typeof evidence.quote !== 'string' || evidence.quote.length === 0 || !segment.text.includes(evidence.quote))
      return fail('invalid_response', 'Field evidence is not bound to the extracted segment.');
  }
  if (!isCurrent()) return fail('identity_changed', 'The session changed during evidence verification.');
  return { ...offer, segments: extraction.segments };
}
export function createQuoteService(session: QuoteSession, isCurrent: () => boolean, fetcher: typeof fetch = fetch, signal?: AbortSignal) {
  async function request<K extends Shape>(path: string, shape: K, init: RequestInit = {}): Promise<ShapeData[K]> {
    if (!isCurrent() || signal?.aborted) return fail('identity_changed', 'The session changed. Refresh it before continuing.');
    let response: Response;
    try { response = await fetcher(path, { ...init, signal, cache: 'no-store' }); }
    catch { return fail(signal?.aborted ? 'identity_changed' : 'unavailable', 'The local quote service is unavailable.'); }
    if (!isCurrent() || signal?.aborted) return fail('identity_changed', 'The session changed. Refresh it before continuing.');
    let body: unknown;
    try { body = await response.json(); }
    catch { return fail('invalid_response', 'The quote service returned an unreadable response.', response.status); }
    if (!response.ok) {
      const kind = response.status === 409 ? 'conflict' : response.status === 401 || response.status === 403 ? 'forbidden' : 'unavailable';
      return fail(kind, response.status === 409 ? 'This offer changed. Refresh the session and offer before trying again.' : 'The quote request could not be completed.', response.status);
    }
    if (!object(body) || body.contract_version !== '0.2.0' || !sameContext(body.context, session))
      return fail('identity_changed', 'The quote response does not match the current session.', response.status);
    if (!shapes[shape](body[shape])) return fail('invalid_response', 'The quote service returned invalid data.', response.status);
    if (!isCurrent() || signal?.aborted) return fail('identity_changed', 'The session changed. Refresh it before continuing.');
    return body[shape] as ShapeData[K];
  }
  const route = (offerId: string) => id(offerId) ? `/v2/offers/${encodeURIComponent(offerId)}` : fail('invalid_request', 'Invalid offer identifier.');
  const write = async (path: string, value: unknown, key?: string) => {
    if (session.role !== 'editor') return fail('forbidden', 'Viewer access cannot change offers.');
    if (key !== undefined && (!/^[A-Za-z0-9_-]{8,128}$/.test(key))) return fail('invalid_request', 'Invalid operation key.');
    return request(path, path.endsWith('/extractions') ? 'upload' : 'offer', {
      method: 'POST', headers: { 'Content-Type': 'application/json', ...(key ? { 'Idempotency-Key': key } : {}) }, body: JSON.stringify(value),
    });
  };
  return {
    list: () => request('/v2/offers', 'offers'),
    detail: async (offerId: string) => {
      const found = await request(`${route(offerId)}?include_events=true`, 'offer');
      if (found.offer_id !== offerId) return fail('invalid_response', 'The offer identity does not match the request.');
      return found;
    },
    extraction: async (extractionId: string) => {
      const found = await request(`/v2/offers/extractions/${id(extractionId) ? encodeURIComponent(extractionId) : fail('invalid_request', 'Invalid extraction identifier.')}`, 'extraction');
      if (found.extraction_id !== extractionId) return fail('invalid_response', 'The extraction identity does not match the request.');
      return found;
    },
    sourcing: () => request('/v2/offers/sourcing-candidates', 'sourcing'),
    upload: (filename: string, content_base64: string) => {
      if (!filename || filename.length > 255 || !/^[A-Za-z0-9+/]*={0,2}$/.test(content_base64) || content_base64.length > 11_184_812)
        return Promise.reject(new QuoteServiceError('invalid_request', 'Choose a PDF, XLSX or CSV file of at most 8 MiB.'));
      return write('/v2/offers/extractions', { filename, content_base64, mode: 'rules', synthetic: true }) as Promise<ServiceUpload>;
    },
    correct: (offerId: string, field: string, value: unknown, expected_version: number, key: string) => {
      if (!nonempty(field) || !Number.isSafeInteger(expected_version) || expected_version < 1) return Promise.reject(new QuoteServiceError('invalid_request', 'Invalid correction.'));
      return (write(`${route(offerId)}/corrections`, { field, value, expected_version }, key) as Promise<ServiceOffer>)
        .then(found => found.offer_id === offerId ? found : fail('invalid_response', 'The offer identity does not match the correction.'));
    },
    save: (offerId: string, expected_version: number, key: string) => {
      if (!Number.isSafeInteger(expected_version) || expected_version < 1) return Promise.reject(new QuoteServiceError('invalid_request', 'Invalid offer version.'));
      return (write(`${route(offerId)}/save`, { expected_version }, key) as Promise<ServiceOffer>)
        .then(found => found.offer_id === offerId ? found : fail('invalid_response', 'The offer identity does not match the save.'));
    },
  };
}
