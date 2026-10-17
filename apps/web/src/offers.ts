// Offers workspace data layer.
//
// This module is the explicit LOCAL FIXTURE ADAPTER; service data uses offers-service.ts.
// The fixtures are offers produced by the Python
// rules pipeline for the 18 synthetic quotes (scripts/export_offer_fixtures_for_web.py). An uploaded file is
// matched to a fixture by its SHA-256; any other file is reported unavailable, never invented. Corrections and
// saves happen in this browser tab only and are not persisted or evidence-checked here; the Python service does
// that through its /offers routes. Amounts stay decimal strings throughout.
import raw from './offers-fixtures.json?raw';
import { REPORT_NOTICE, toCsv } from './report';
import type { Report } from './report';
import { EVENTS, offerScenario, ScenarioInputError } from './offers-scenario';
import type { ScenarioInputs, ScenarioResult } from './offers-scenario';

export type FieldStatus = 'extracted' | 'missing' | 'conflict' | 'unverified' | 'corrected';
export type OfferStatus = 'extracted' | 'needs_review' | 'corrected' | 'saved' | 'used_in_plan' | 'rfq_drafted' | 'rejected' | 'expired';
export type Evidence = { locator: string; quote: string; text_sha256: string };
export type Field = {
  name: string; kind: string; status: FieldStatus; value: unknown; critical: boolean; source: string; reasons: string[];
  evidence: Evidence[]; candidates: { value: unknown; verified: boolean; problems: string[]; locators: string[] }[];
  evidence_status?: string;
};
export type BlockingReason = { code: string; field: string; message: string };
export type Segment = { locator: string; text: string; flags: string[] };
export type Offer = {
  fixture_id: string; split: string; offer_id: string; version: number; status: OfferStatus; synthetic: boolean;
  document: { document_id: string; filename: string; media_type: string; byte_size: number };
  extractor: string; as_of: string; fields: Record<string, Field>; items: Record<string, Field>[];
  review_flags: { flag: string; locator: string | null; text: string }[]; normalized: Record<string, unknown>;
  blocking_reasons: BlockingReason[]; segments: Segment[];
  revalidation_required?: string[];
};
type FixtureData = { synthetic: true; source: string; generated_from_revision: string; as_of: string; notice: string; materials: string[]; offers: Offer[] };

export type OffersErrorKind = 'unavailable' | 'invalid_value' | 'unknown_field' | 'save_blocked' | 'invalid_transition' | 'stale_version' | 'not_found';
export class OffersError extends Error {
  constructor(message: string, readonly kind: OffersErrorKind, readonly reasons: BlockingReason[] = []) { super(message); }
}

const data = JSON.parse(raw) as FixtureData;
const CRITICAL = new Set(['supplier_name', 'material', 'quantity', 'pricing', 'settlement_currency', 'incoterm', 'valid_until']);
const USABLE = new Set<FieldStatus>(['extracted', 'corrected']);
const UNITS = ['tonne', 'kg', 'lb'];
const AMOUNT = /^(0|[1-9]\d*)(\.\d+)?$/;
const SIGNED_AMOUNT = /^-?(0|[1-9]\d*)(\.\d+)?$/;
const INDEX_TERMS = ['fx_basis', 'fx_terms', 'fx_conversion', 'quotational_period'] as const;
const INCOTERMS = ['EXW', 'FCA', 'FAS', 'FOB', 'CFR', 'CIF', 'CPT', 'CIP', 'DAP', 'DPU', 'DDP'];
const EDITABLE: Record<OfferStatus, boolean> = { extracted: true, needs_review: true, corrected: true, saved: false, used_in_plan: false, rfq_drafted: false, rejected: false, expired: false };

export const fixtureInfo = { asOf: data.as_of, revision: data.generated_from_revision, notice: data.notice, materials: data.materials };

export function fieldEntries(offer: Offer): [string, Field][] {
  return [...Object.entries(offer.fields), ...offer.items.flatMap((item, i) => Object.entries(item).map(([k, f]) => [`items[${i}].${k}`, f] as [string, Field]))];
}

export function getField(offer: Offer, name: string): Field {
  const item = /^items\[(\d+)\]\.(\w+)$/.exec(name);
  const field = item ? offer.items[Number(item[1])]?.[item[2]] : offer.fields[name];
  if (!field) throw new OffersError(`${name} is not a field of this offer.`, 'unknown_field');
  return field;
}

const utcDay = (clock: () => Date) => clock().toISOString().slice(0, 10);

export function blockingReasons(offer: Offer, reviewDay = utcDay(() => new Date())): BlockingReason[] {
  const reasons = fieldEntries(offer)
    .filter(([name, f]) => CRITICAL.has(name.split('.').at(-1)!) && !USABLE.has(f.status))
    .map(([name, f]) => ({ code: `field_${f.status}`, field: name, message: `${name} is ${f.status}` }));
  const valid = offer.fields.valid_until;
  if (USABLE.has(valid.status) && typeof valid.value === 'string' && valid.value < reviewDay)
    reasons.unshift({ code: 'expired', field: 'valid_until', message: `the quote expired on ${valid.value}` });
  for (const name of offer.revalidation_required ?? [])
    reasons.push({ code: 'revalidation_required', field: name, message: `${name} needs authoritative service revalidation` });
  const normalizedItems = Array.isArray(offer.normalized.items) ? offer.normalized.items as Record<string, unknown>[] : [];
  offer.items.forEach((item, i) => {
    const normalized = normalizedItems[i];
    for (const name of ['quantity', 'pricing'] as const) {
      if (!USABLE.has(item[name]?.status)) continue;
      const derived = normalized?.[name] as Record<string, unknown> | undefined;
      const reporting = derived?.reporting as Record<string, unknown> | undefined;
      if (derived?.status !== 'ok' || (name === 'pricing' && !['ok', 'not_needed'].includes(String(reporting?.status))))
        reasons.push({ code: 'normalization_unavailable', field: `items[${i}].${name}`, message: `${name} normalization is unavailable` });
    }
  });
  return reasons;
}

const decimalText = (value: unknown, signed = false): boolean => {
  if (typeof value !== 'string' || value.length > 64 || !(signed ? SIGNED_AMOUNT : AMOUNT).test(value)) return false;
  const unsigned = value.startsWith('-') ? value.slice(1) : value;
  const [whole, fraction = ''] = unsigned.split('.');
  return whole.length + fraction.length <= 30 && fraction.length <= 10
    && (signed || !/^0(?:\.0+)?$/.test(unsigned));
};

const money = (m: unknown, signed = false) => {
  const v = m as { amount?: unknown; currency?: unknown } | null;
  return !!v && typeof v === 'object' && !Array.isArray(v) && Object.keys(v).sort().join(',') === 'amount,currency'
    && decimalText(v.amount, signed) && typeof v.currency === 'string' && /^[A-Z]{3}$/.test(v.currency);
};

const exactDate = (value: unknown): boolean => typeof value === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(value)
  && value.slice(0, 4) !== '0000'
  && !Number.isNaN(Date.parse(`${value}T00:00:00Z`))
  && new Date(`${value}T00:00:00Z`).toISOString().slice(0, 10) === value;

const boundedTerm = (value: unknown): boolean => typeof value === 'string' && value.trim().length > 0 && value.length <= 500;

export function valueProblem(kind: string, value: unknown): string | null {
  const v = value as Record<string, unknown>;
  switch (kind) {
    case 'text': return typeof value === 'string' && value.trim() && value.length <= 500 ? null : 'Enter 1 to 500 characters.';
    case 'date': return exactDate(value) ? null : 'Use a real calendar date in YYYY-MM-DD.';
    case 'integer': return Number.isInteger(value) && (value as number) >= 0 && (value as number) <= 3650 ? null : 'Enter whole days from 0 to 3650.';
    case 'currency': return typeof value === 'string' && /^[A-Z]{3}$/.test(value) ? null : 'Use a three-letter ISO code such as USD.';
    case 'quantity': return v && decimalText(v.amount) && UNITS.includes(v.unit as string) ? null : 'Enter a positive decimal amount (up to 30 digits and 10 places) and a unit (tonne, kg or lb).';
    case 'incoterm': return v && INCOTERMS.includes(v.code as string) ? null : `Use an Incoterms 2020 code: ${INCOTERMS.join(', ')}.`;
    case 'material': return typeof value === 'string' && data.materials.includes(value) ? null : `Choose one of ${data.materials.join(', ')}.`;
    case 'pricing':
      if (!v || !UNITS.includes(v.per_unit as string)) return 'Choose a price unit (tonne, kg or lb).';
      if (v.type === 'fixed') return Object.keys(v).sort().join(',') === 'per_unit,type,unit_price' && money(v.unit_price) ? null : 'Enter a bounded positive decimal price and an ISO currency.';
      if (v.type === 'index_linked') {
        const keys = Object.keys(v);
        const required = ['type', 'index', 'averaging_period', 'premium', 'per_unit'];
        return required.every(k => keys.includes(k)) && keys.every(k => required.includes(k) || INDEX_TERMS.includes(k as typeof INDEX_TERMS[number]))
          && boundedTerm(v.index) && boundedTerm(v.averaging_period) && money(v.premium, true)
          && INDEX_TERMS.every(k => !(k in v) || boundedTerm(v[k])) ? null
          : 'Enter a complete index, averaging period, bounded signed premium, and any FX/quotational terms.';
      }
      return 'Pricing is fixed or index-linked.';
    default: return 'This field cannot be edited here.';
  }
}

async function sha256(file: Blob): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', await file.arrayBuffer());
  return 'sha256:' + [...new Uint8Array(digest)].map(b => b.toString(16).padStart(2, '0')).join('');
}

export type OffersAdapter = ReturnType<typeof createFixtureAdapter>;

export function createFixtureAdapter(clock: () => Date = () => new Date()) {
  const offers = new Map<string, Offer>(data.offers.map(o => [o.offer_id, structuredClone(o)]));
  const fresh = (id: string) => {
    const offer = offers.get(id);
    if (!offer) throw new OffersError('Offer not found.', 'not_found');
    return offer;
  };
  const check = (offer: Offer, version: number) => {
    if (version !== offer.version) throw new OffersError(`This offer changed (now version ${offer.version}). Reload it before saving.`, 'stale_version');
  };
  return {
    source: 'local_fixture_adapter' as const,
    synthetic: true as const,
    list: () => [...offers.values()].map(o => ({ ...structuredClone(o), blocking_reasons: blockingReasons(o, utcDay(clock)) })),
    get: (id: string) => ({ ...structuredClone(fresh(id)), blocking_reasons: blockingReasons(fresh(id), utcDay(clock)) }),
    async upload(file: File): Promise<Offer> {
      const id = await sha256(file);
      const offer = [...offers.values()].find(o => o.document.document_id === id);
      if (!offer) throw new OffersError('This local adapter accepts only the 18 listed sample quotes. Extracting other files needs the Python /offers service or the gateway, which is not connected yet.', 'unavailable');
      return { ...structuredClone(offer), blocking_reasons: blockingReasons(offer, utcDay(clock)) };
    },
    async correct(id: string, name: string, value: unknown, version: number): Promise<Offer> {
      const offer = fresh(id);
      const field = getField(offer, name);
      const problem = valueProblem(field.kind, value);
      if (problem) throw new OffersError(`${name}: ${problem}`, 'invalid_value');
      if (!EDITABLE[offer.status]) throw new OffersError(`A ${offer.status.replace('_', ' ')} offer cannot be edited.`, 'invalid_transition');
      check(offer, version);
      Object.assign(field, { status: 'corrected', value: structuredClone(value), source: 'user', evidence: [], candidates: [],
        evidence_status: 'user_entered_not_checked_by_local_adapter', reasons: [] });
      const normalizedItems = offer.normalized.items as Record<string, unknown>[] | undefined;
      const itemMatch = /^items\[(\d+)\]\.(quantity|pricing)$/.exec(name);
      if (itemMatch && normalizedItems?.[Number(itemMatch[1])])
        normalizedItems[Number(itemMatch[1])][itemMatch[2]] = null;
      if (name === 'settlement_currency' || name === 'incoterm')
        normalizedItems?.forEach(item => { item.pricing = null; });
      if (name === 'valid_until') offer.normalized.expiry = null;
      if (name === 'payment_terms') offer.normalized.payment_terms = null;
      if (name === 'origin_country') offer.normalized.origin_country_code = null;
      if (name === 'min_order') offer.normalized.min_order = null;
      if (name === 'capacity') offer.normalized.capacity = null;
      if (itemMatch || ['settlement_currency', 'incoterm', 'valid_until', 'origin_country', 'min_order', 'capacity'].includes(name))
        offer.revalidation_required = [...new Set([...(offer.revalidation_required ?? []), name])];
      offer.version += 1;
      offer.status = 'corrected';
      offer.blocking_reasons = blockingReasons(offer, utcDay(clock));
      return structuredClone(offer);
    },
    async save(id: string, version: number): Promise<Offer> {
      const offer = fresh(id);
      if (!['extracted', 'needs_review', 'corrected'].includes(offer.status))
        throw new OffersError(`A ${offer.status.replace('_', ' ')} offer cannot be saved.`, 'invalid_transition', blockingReasons(offer, utcDay(clock)));
      check(offer, version);
      const reasons = blockingReasons(offer, utcDay(clock));
      if (reasons.length) throw new OffersError('Resolve these before saving.', 'save_blocked', reasons);
      offer.version += 1;
      offer.status = 'saved';
      offer.blocking_reasons = [];
      return structuredClone(offer);
    },
    /** Expiry and payment-timing scenario, computed by the mirror of procure.offers.scenario. Nothing is saved. */
    async scenario(id: string, inputs: ScenarioInputs): Promise<ScenarioResult> {
      const offer = fresh(id);
      try {
        return offerScenario(structuredClone(offer), [...offers.values()].map(o => structuredClone(o)), { ...inputs, as_of: utcDay(clock) });
      } catch (error) {
        if (error instanceof ScenarioInputError) throw new OffersError(error.message, 'invalid_value');
        throw error;
      }
    },
  };
}

export function formatValue(kind: string, value: unknown): string {
  if (value === null || value === undefined) return 'Not stated';
  const v = value as Record<string, any>;
  switch (kind) {
    case 'quantity': return `${v.amount} ${v.unit ?? '(no unit)'}${v.period ? ` per ${v.period}` : ''}`;
    case 'incoterm': return [v.code, v.place].filter(Boolean).join(' ');
    case 'pricing':
      if (v.type === 'fixed') return `${v.unit_price.currency} ${v.unit_price.amount} per ${v.per_unit ?? '(no unit)'}`;
      return `${v.index}${v.averaging_period ? ` (${v.averaging_period})` : ''} + ${v.premium.currency} ${v.premium.amount} per ${v.per_unit ?? '(no unit)'}`
        + INDEX_TERMS.filter(k => v[k]).map(k => ` · ${k.replaceAll('_', ' ')}: ${v[k]}`).join('');
    case 'shipments': return (value as { quantity: { amount: string; unit: string }; window: string }[])
      .map(lot => `${lot.quantity.amount} ${lot.quantity.unit} in ${lot.window}`).join('; ');
    default: return String(value);
  }
}

export function dataAge(asOf: string, now: Date): { days: number; old: boolean } {
  const days = Math.max(0, Math.floor((now.getTime() - Date.parse(`${asOf}T00:00:00Z`)) / 86_400_000));
  return { days, old: days > 7 };
}

/** A reviewed offer with its evidence as downloadable JSON and CSV. Values keep their exact decimal strings. */
export function offerReport(offer: Offer, generatedAt: Date, scenario: ScenarioResult | null = null): Report {
  const fields = fieldEntries(offer).map(([name, field]) => ({
    name, kind: field.kind, status: field.status, critical: field.critical, source: field.source, value: field.value,
    display: formatValue(field.kind, field.value), reasons: field.reasons,
    evidence: field.evidence.map(e => ({ locator: e.locator, quote: e.quote, text_sha256: e.text_sha256 })),
    candidates: field.candidates.map(c => ({ value: c.value, verified: c.verified, problems: c.problems, locators: c.locators })),
  }));
  const json = {
    report: 'offer_review', synthetic: true, notice: REPORT_NOTICE, source: 'local_fixture_adapter',
    generated_at: generatedAt.toISOString(), fixture_revision: fixtureInfo.revision, data_as_of: fixtureInfo.asOf,
    offer: { offer_id: offer.offer_id, version: offer.version, status: offer.status, extractor: offer.extractor, as_of: offer.as_of,
      document: offer.document },
    fields, blocking_reasons: offer.blocking_reasons, review_flags: offer.review_flags,
    revalidation_required: offer.revalidation_required ?? [],
    scenario,
  };
  const rows: (string | null)[][] = [
    ['notice', null, null, null, null, null, REPORT_NOTICE],
    ['offer', offer.offer_id, offer.status, `version ${offer.version}`, offer.extractor, null, offer.document.filename],
  ];
  for (const f of fields) {
    rows.push(['field', f.name, f.status, f.display, f.source, null, f.reasons.join('; ') || null]);
    for (const e of f.evidence) rows.push(['evidence', f.name, null, null, 'document', e.locator, e.quote]);
    for (const c of f.candidates) rows.push(['candidate', f.name, c.verified ? 'verified' : 'unverified',
      formatValue(f.kind, c.value), 'document', c.locators.join('; '), c.problems.join('; ') || null]);
  }
  for (const r of offer.blocking_reasons) rows.push(['blocking_reason', r.field, r.code, null, null, null, r.message]);
  for (const r of offer.review_flags) rows.push(['review_flag', null, r.flag, null, null, r.locator, r.text]);
  if (scenario) rows.push(...scenarioRows(scenario));
  return {
    basename: `synthetic-offer-${offer.offer_id}-v${offer.version}`,
    json: JSON.stringify(json, null, 2),
    csv: toCsv(['record_type', 'name', 'status', 'value', 'source', 'locator', 'detail', 'synthetic'], rows.map(r => [...r, 'true'])),
  };
}

const moneyText = (m: { amount: string | null; currency: string | null } | null) => (m?.amount ? `${m.amount} ${m.currency}` : null);

/** CSV rows for a scenario: validity, comparison exclusions, payment instalments, the terms comparison and assumptions. */
function scenarioRows(s: ScenarioResult): (string | null)[][] {
  const v = s.validity.decision;
  const rows: (string | null)[][] = [
    ['scenario', null, v.state, `decision ${s.decision_date}`, 'user input', null,
      `as of ${s.as_of}; rate ${s.rate_percent ?? 'not entered'}; events ${EVENTS.map(e => `${e} ${s.event_dates[e] ?? 'not entered'}`).join(', ')}`],
    ...s.comparison.decision.excluded.map(e => ['comparison_excluded', e.offer_id, e.reason, e.valid_until, null, null, null]),
    ['payment_terms', null, s.payment_terms.status, s.payment_terms.text, s.payment_terms.source,
      s.payment_terms.evidence.map(e => e.locator).join('; ') || null, s.payment_terms.reasons.join('; ') || null],
    ...s.payment_terms.unrecognised.map(text => ['payment_unrecognised', null, 'unrecognised', null, null, null, text]),
    ['offer_amount', null, s.amount.reason ?? 'computed', moneyText(s.amount), 'quantity x unit price', null, null],
  ];
  s.schedule.lines.forEach((l, i) => rows.push(['payment_instalment', String(i + 1), l.reason ?? 'scheduled', moneyText(l.amount),
    s.payment_terms.source === 'user' ? 'reviewer entry' : 'document', s.payment_terms.evidence.map(e => e.locator).join('; ') || null,
    `${l.percent ?? 'not stated'}% ${l.instrument.replaceAll('_', ' ')}, ${l.timing.replaceAll('_', ' ')} ${l.anchor.replaceAll('_', ' ')} + ${l.days ?? 'not stated'} days, due ${l.due_date ?? 'unknown'}`]));
  if (s.terms_comparison && s.alternative) {
    const c = s.terms_comparison;
    rows.push(['terms_comparison', s.alternative.offer_id, c.reason ?? 'computed', moneyText(c.value_of_later_payment), 'user input', null,
      `weighted payment days change ${c.weighted_days_change ?? 'not available'}; rate ${c.rate_percent ?? 'not entered'}; day count ${c.day_count}; rounded ${c.rounding.replaceAll('_', ' ')}`]);
  }
  for (const a of s.assumptions) rows.push(['scenario_assumption', null, null, null, null, null, a]);
  return rows;
}
