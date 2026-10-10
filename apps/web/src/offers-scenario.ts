// Expiry and payment-timing scenario for the /offers local adapter.
//
// Mirrors procure.offers.scenario in the Python service; offers-scenario.test.ts checks this file against the
// examples the export script computes in Python. Payment terms are parsed in Python and read from
// offer.normalized.payment_terms; they are not re-parsed here. Amounts are exact decimal text, computed with
// BigInt fractions. Only the rate-based value is rounded (half-even to 0.01). No rate, FX or date is assumed.
import type { Field, Offer } from './offers';

export const EVENTS = ['shipment', 'bill_of_lading', 'documents', 'invoice', 'delivery', 'arrival'] as const;
export type EventName = typeof EVENTS[number];
export type ScenarioInputs = {
  decision_date: string; dates: Partial<Record<EventName, string | null>>; rate_percent: string | null;
  alternative_offer_id: string | null;
};
type Evidence = { locator: string; quote: string };
type Money = { amount: string; currency: string };
export type Validity = { state: 'valid' | 'expired' | 'not_stated' | 'unavailable'; valid_until: string | null; days_left: number | null; evidence: Evidence[] };
export type Instalment = { percent: string | null; percent_basis: string; instrument: string; anchor: string; days: number | null; timing: string; clause: string };
export type PaymentTerms = {
  status: string; text: string | null; instalments: Instalment[]; unrecognised: string[]; evidence: Evidence[];
  reasons: string[]; source: string | null;
};
export type ScheduleLine = Instalment & { due_date: string | null; days_from_decision: number | null; amount: Money | null; reason: string | null };
export type Schedule = { decision_date: string; lines: ScheduleLine[]; complete: boolean; weighted_days: string | null; total_percent: string; missing: string[] };
export type Comparison = { date: string; included: string[]; excluded: { offer_id: string; reason: string; valid_until: string | null }[] };
export type TermsComparison = {
  weighted_days_change: string | null; value_of_later_payment: Money | null; rate_percent: string | null;
  day_count: number; rounding: string; reason: string | null;
};
export type ScenarioResult = {
  synthetic: true; assumptions: string[]; as_of: string; decision_date: string; event_dates: Record<EventName, string | null>;
  rate_percent: string | null; validity: { as_of: Validity; decision: Validity };
  comparison: { as_of: Comparison; decision: Comparison; newly_expired: { offer_id: string; valid_until: string | null }[] };
  payment_terms: PaymentTerms; amount: { amount: string | null; currency: string | null; reason: string | null };
  schedule: Schedule; alternative: { offer_id: string; payment_terms: PaymentTerms; schedule: Schedule } | null;
  terms_comparison: TermsComparison | null;
};

export class ScenarioInputError extends Error {}

export const ASSUMPTIONS = [
  'Synthetic scenario on synthetic quotes. Nothing is ordered, paid or sent.',
  'A quote is open through its valid-until date. A missing date is not stated and is never treated as open.',
  'The order is placed on the decision date, so advance payments fall on it.',
  'Shipment, bill of lading, documents, invoice, delivery and arrival dates are user inputs, not forecasts.',
  'An instalment due before an event is placed on its stated deadline, the latest it may be paid.',
  'Amounts are quantity times unit price in the quote currency, with no FX conversion, tax or freight added.',
  'The value of paying later uses the annual rate you enter, simple interest on actual days over 365, rounded half-even to 0.01. No rate is assumed.',
];
const USABLE = new Set(['extracted', 'corrected']);
const DAY_COUNT = 365;
const RATE = /^(0|[1-9]\d{0,2})(\.\d{1,6})?$/;
const KG_PER_UNIT: Record<string, string> = { tonne: '1000', kg: '1', lb: '0.45359237' };

// Exact fractions
type Q = { n: bigint; d: bigint };
const abs = (x: bigint) => (x < 0n ? -x : x);
const gcd = (a: bigint, b: bigint): bigint => { a = abs(a); b = abs(b); while (b) [a, b] = [b, a % b]; return a || 1n; };
const reduce = ({ n, d }: Q): Q => { const g = gcd(n, d); return d < 0n ? { n: -n / g, d: -d / g } : { n: n / g, d: d / g }; };
function q(text: string): Q {
  const m = /^(-)?(\d+)(?:\.(\d+))?$/.exec(text);
  if (!m) throw new ScenarioInputError(`${text} is not decimal text`);
  const digits = BigInt(m[2] + (m[3] ?? ''));
  return reduce({ n: m[1] ? -digits : digits, d: 10n ** BigInt((m[3] ?? '').length) });
}
const add = (a: Q, b: Q) => reduce({ n: a.n * b.d + b.n * a.d, d: a.d * b.d });
const sub = (a: Q, b: Q) => add(a, { n: -b.n, d: b.d });
const mul = (a: Q, b: Q) => reduce({ n: a.n * b.n, d: a.d * b.d });
const div = (a: Q, b: Q) => reduce({ n: a.n * b.d, d: a.d * b.n });
const ZERO: Q = { n: 0n, d: 1n };

/** Exact decimal text of a fraction, or null if it does not terminate. */
function plain(x: Q): string | null {
  const r = reduce(x);
  let d = r.d, twos = 0, fives = 0;
  while (d % 2n === 0n) { d /= 2n; twos++; }
  while (d % 5n === 0n) { d /= 5n; fives++; }
  if (d !== 1n) return null;
  const places = Math.max(twos, fives);
  const scaled = abs(r.n) * (10n ** BigInt(places) / r.d);
  let digits = scaled.toString().padStart(places + 1, '0');
  if (places) digits = `${digits.slice(0, -places)}.${digits.slice(-places)}`.replace(/\.?0+$/, '');
  return (r.n < 0n && digits !== '0' ? '-' : '') + digits;
}

export const decimalPlain = (text: string): string => plain(q(text))!;

/** n/d rounded half-even to 0.01, keeping the sign of a negative value that rounds to zero. */
export function roundHalfEven(n: bigint, d: bigint): string {
  const negative = (n < 0n) !== (d < 0n) && n !== 0n;
  const a = abs(n) * 100n, b = abs(d);
  let whole = a / b;
  const twice = (a % b) * 2n;
  if (twice > b || (twice === b && whole % 2n === 1n)) whole += 1n;
  const text = whole.toString().padStart(3, '0');
  return `${negative ? '-' : ''}${text.slice(0, -2)}.${text.slice(-2)}`;
}

// Dates
function day(text: string | null | undefined, name: string): number | null {
  if (text === null || text === undefined || text === '') return null;
  const ms = /^\d{4}-\d{2}-\d{2}$/.test(text) ? Date.parse(`${text}T00:00:00Z`) : NaN;
  if (Number.isNaN(ms) || new Date(ms).toISOString().slice(0, 10) !== text)
    throw new ScenarioInputError(`${name.replaceAll('_', ' ')} must be a real calendar date in YYYY-MM-DD.`);
  return ms / 86_400_000;
}
const isoDay = (n: number) => new Date(n * 86_400_000).toISOString().slice(0, 10);
const evidenceOf = (field: Field): Evidence[] => field.evidence.map(e => ({ locator: e.locator, quote: e.quote }));

export function validityAt(field: Field, on: string): Validity {
  if (field.status === 'missing' || (USABLE.has(field.status) && !field.value))
    return { state: 'not_stated', valid_until: null, days_left: null, evidence: [] };
  if (!USABLE.has(field.status)) return { state: 'unavailable', valid_until: null, days_left: null, evidence: [] };
  const left = day(field.value as string, 'valid until')! - day(on, 'date')!;
  return { state: left >= 0 ? 'valid' : 'expired', valid_until: field.value as string, days_left: left >= 0 ? left : null, evidence: evidenceOf(field) };
}

export function comparisonSet(offers: Offer[], on: string): Comparison {
  const included: string[] = [], excluded: Comparison['excluded'] = [];
  for (const offer of offers) {
    const state = validityAt(offer.fields.valid_until, on).state;
    const reason = offer.status === 'rejected' ? 'rejected'
      : ({ expired: 'expired', not_stated: 'validity_not_stated', unavailable: 'validity_unavailable' } as Record<string, string>)[state];
    if (reason) excluded.push({ offer_id: offer.offer_id, reason, valid_until: (offer.fields.valid_until.value as string | null) ?? null });
    else included.push(offer.offer_id);
  }
  return { date: on, included, excluded };
}

export function expiryScenario(offers: Offer[], asOf: string, decision: string): ScenarioResult['comparison'] {
  const before = comparisonSet(offers, asOf), after = comparisonSet(offers, decision);
  const dropped = after.excluded.filter(e => e.reason === 'expired' && before.included.includes(e.offer_id));
  return { as_of: before, decision: after, newly_expired: dropped.map(e => ({ offer_id: e.offer_id, valid_until: e.valid_until })) };
}

type NormalizedItem = { quantity?: { status?: string; original?: { amount: string; unit: string } } | null; pricing?: { status?: string; type?: string; original?: { unit_price: Money; per_unit: string } } | null };
const unavailable = (reason: string) => ({ amount: null, currency: null, reason });

export function offerAmount(offer: Offer): ScenarioResult['amount'] {
  const items = (offer.normalized.items ?? []) as NormalizedItem[];
  if (!items.length) return unavailable('no_items');
  let total = ZERO;
  const currencies = new Set<string>();
  for (const item of items) {
    const quantity = item.quantity ?? {}, pricing = item.pricing ?? {};
    if (quantity.status !== 'ok' || pricing.status !== 'ok') return unavailable('price_or_quantity_unavailable');
    if (pricing.type !== 'fixed') return unavailable('price_not_fixed');
    const units = div(mul(q(quantity.original!.amount), q(KG_PER_UNIT[quantity.original!.unit])), q(KG_PER_UNIT[pricing.original!.per_unit]));
    if (plain(units) === null) return unavailable('unit_conversion_not_exact');
    total = add(total, mul(units, q(pricing.original!.unit_price.amount)));
    currencies.add(pricing.original!.unit_price.currency);
  }
  if (currencies.size !== 1) return unavailable('mixed_currencies');
  return { amount: plain(total), currency: [...currencies][0], reason: null };
}

export function cashTiming(terms: PaymentTerms, amount: Money | null, dates: ScenarioInputs['dates'], decision: string): Schedule {
  const events: Record<string, number | null> = { order: day(decision, 'decision date') };
  for (const e of EVENTS) events[e] = day(dates[e], e);
  const missing = terms.status === 'parsed' ? [] : [`terms_${terms.status}`];
  const lines: ScheduleLine[] = terms.instalments.map(inst => {
    let due: number | null = null, reason: string | null = null;
    if (inst.anchor === 'not_stated') reason = 'anchor_not_stated';
    else if (events[inst.anchor] === null || events[inst.anchor] === undefined) reason = `date_not_entered:${inst.anchor}`;
    else due = events[inst.anchor]! + (inst.timing === 'on_or_before' ? -(inst.days ?? 0) : (inst.days ?? 0));
    if (inst.percent === null) reason ??= 'percent_not_stated';
    const share = amount && inst.percent !== null
      ? { amount: plain(div(mul(q(amount.amount), q(inst.percent)), { n: 100n, d: 1n }))!, currency: amount.currency } : null;
    if (reason && !missing.includes(reason)) missing.push(reason);
    return { percent: inst.percent, percent_basis: inst.percent_basis, instrument: inst.instrument, anchor: inst.anchor, days: inst.days,
      timing: inst.timing, clause: inst.clause, due_date: due === null ? null : isoDay(due),
      days_from_decision: due === null ? null : due - events.order!, amount: share, reason };
  });
  const total = lines.reduce((sum, l) => (l.percent === null ? sum : add(sum, q(l.percent))), ZERO);
  const complete = lines.length > 0 && missing.length === 0;
  const weighted = complete
    ? plain(div(lines.reduce((sum, l) => add(sum, mul(q(l.percent!), { n: BigInt(l.days_from_decision!), d: 1n })), ZERO), { n: 100n, d: 1n }))
    : null;
  return { decision_date: decision, lines, complete, weighted_days: weighted, total_percent: plain(total)!, missing };
}

function rate(text: string | null): Q | null {
  if (text === null) return null;
  if (!RATE.test(text) || q(text).n > 100n * q(text).d) throw new ScenarioInputError('Enter the annual rate as a decimal from 0 to 100, such as 8 or 7.25.');
  return q(text);
}

export function compareTerms(base: Schedule, alternative: Schedule, amount: Money | null, ratePercent: string | null): TermsComparison {
  const r = rate(ratePercent);
  const out: TermsComparison = { weighted_days_change: null, value_of_later_payment: null, rate_percent: ratePercent, day_count: DAY_COUNT,
    rounding: 'half_even_0.01', reason: null };
  if (!base.complete || !alternative.complete) return { ...out, reason: 'schedule_incomplete' };
  const change = sub(q(alternative.weighted_days!), q(base.weighted_days!));
  out.weighted_days_change = plain(change);
  if (r === null) return { ...out, reason: 'no_rate_entered' };
  if (!amount) return { ...out, reason: 'amount_unavailable' };
  const value = mul(mul(q(amount.amount), r), change);
  return { ...out, value_of_later_payment: { amount: roundHalfEven(value.n, value.d * BigInt(100 * DAY_COUNT)), currency: amount.currency } };
}

function termsOf(offer: Offer): PaymentTerms {
  const parsed = offer.normalized.payment_terms as PaymentTerms | null | undefined;
  return parsed ?? { status: 'unavailable', text: null, instalments: [], unrecognised: [], evidence: [], reasons: ['revalidation_required'],
    source: offer.fields.payment_terms?.source ?? null };
}

export function offerScenario(offer: Offer, offers: Offer[], inputs: ScenarioInputs & { as_of: string }): ScenarioResult {
  const terms = termsOf(offer);
  const amount = offerAmount(offer);
  const usable = amount.amount !== null ? { amount: amount.amount, currency: amount.currency! } : null;
  const schedule = cashTiming(terms, usable, inputs.dates, inputs.decision_date);
  let alternative: ScenarioResult['alternative'] = null, comparison: TermsComparison | null = null;
  if (inputs.alternative_offer_id) {
    const other = offers.find(o => o.offer_id === inputs.alternative_offer_id);
    if (!other) throw new ScenarioInputError(`Unknown alternative offer ${inputs.alternative_offer_id}.`);
    const altTerms = termsOf(other);
    const altSchedule = cashTiming(altTerms, usable, inputs.dates, inputs.decision_date);
    alternative = { offer_id: other.offer_id, payment_terms: altTerms, schedule: altSchedule };
    comparison = compareTerms(schedule, altSchedule, usable, inputs.rate_percent);
  } else rate(inputs.rate_percent);
  return {
    synthetic: true, assumptions: [...ASSUMPTIONS], as_of: inputs.as_of, decision_date: inputs.decision_date,
    event_dates: Object.fromEntries(EVENTS.map(e => [e, inputs.dates[e] || null])) as Record<EventName, string | null>,
    rate_percent: inputs.rate_percent,
    validity: { as_of: validityAt(offer.fields.valid_until, inputs.as_of), decision: validityAt(offer.fields.valid_until, inputs.decision_date) },
    comparison: expiryScenario(offers, inputs.as_of, inputs.decision_date),
    payment_terms: terms, amount, schedule, alternative, terms_comparison: comparison,
  };
}
