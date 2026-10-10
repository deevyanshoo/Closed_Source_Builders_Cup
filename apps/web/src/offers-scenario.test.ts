import { describe, expect, it } from 'vitest';
import raw from './offers-fixtures.json?raw';
import { createFixtureAdapter, offerReport } from './offers';
import type { Offer } from './offers';
import { cashTiming, compareTerms, decimalPlain, offerScenario, roundHalfEven, validityAt } from './offers-scenario';
import type { PaymentTerms, ScenarioInputs } from './offers-scenario';

const data = JSON.parse(raw) as { as_of: string; offers: Offer[]; scenario_examples: { inputs: ScenarioInputs & { offer_id: string }; result: unknown }[] };
const EVENTS = { shipment: '2026-11-05', bill_of_lading: '2026-11-06', documents: '2026-11-12', invoice: '2026-11-06', delivery: null, arrival: null };
const at = (day: string) => () => new Date(`${day}T12:00:00Z`);

describe('scenario rules match the Python service', () => {
  it('reproduces every example exported by procure.offers.scenario', () => {
    expect(data.scenario_examples.length).toBeGreaterThanOrEqual(4);
    for (const { inputs, result } of data.scenario_examples) {
      const offer = data.offers.find(o => o.offer_id === inputs.offer_id)!;
      const actual = offerScenario(offer, data.offers, { ...inputs, as_of: data.as_of });
      const exported = result as ReturnType<typeof offerScenario>;
      expect(actual.assumptions).toContain('An instalment due before an event is placed on its stated deadline, the latest it may be paid.');
      // The frozen export predates the corrected explanatory sentence; compare every calculated field unchanged.
      expect({ ...actual, assumptions: exported.assumptions }).toEqual(exported);
    }
  });
});

describe('exact decimal text', () => {
  it('keeps exact digits without trailing zeros', () => {
    expect(decimalPlain('705937.500000')).toBe('705937.5');
    expect(decimalPlain('-54.0')).toBe('-54');
    expect(decimalPlain('0.000')).toBe('0');
  });

  it('rounds half-even to 0.01 and keeps the sign of a value that rounds to zero', () => {
    expect(roundHalfEven(1n, 8n)).toBe('0.12');
    expect(roundHalfEven(5n, 1000n)).toBe('0.00');
    expect(roundHalfEven(15n, 1000n)).toBe('0.02');
    expect(roundHalfEven(-1n, 1000n)).toBe('-0.00');
    expect(roundHalfEven(-3925733n, 1000n)).toBe('-3925.73');
  });
});

describe('before-event payment deadlines', () => {
  const term = (timing: string): PaymentTerms => ({
    status: 'parsed', text: `100% TT 30 days ${timing === 'on_or_before' ? 'before' : 'after'} shipment`,
    unrecognised: [], evidence: [], reasons: [], source: 'rules',
    instalments: [{ percent: '100', percent_basis: 'stated', instrument: 'telegraphic_transfer', anchor: 'shipment',
      days: 30, timing, clause: `100% TT 30 days ${timing === 'on_or_before' ? 'before' : 'after'} shipment` }],
  });

  it('subtracts thirty days and values the hand-calculated sixty-day difference', () => {
    const amount = { amount: '36500', currency: 'USD' };
    const dates = { shipment: '2026-11-30' };
    const before = cashTiming(term('on_or_before'), amount, dates, '2026-10-10');
    const after = cashTiming(term('on'), amount, dates, '2026-10-10');
    expect(before.lines[0]).toMatchObject({ due_date: '2026-10-31', days_from_decision: 21 });
    expect(before.weighted_days).toBe('21');
    expect(after.lines[0].due_date).toBe('2026-12-30');
    expect(after.weighted_days).toBe('81');
    expect(compareTerms(before, after, amount, '10')).toMatchObject({
      weighted_days_change: '60', value_of_later_payment: { amount: '600.00', currency: 'USD' }, reason: null,
    });
  });

  it('leaves a missing shipment date unresolved', () => {
    const result = cashTiming(term('on_or_before'), null, { shipment: null }, '2026-10-10');
    expect(result.complete).toBe(false);
    expect(result.lines[0].due_date).toBeNull();
    expect(result.missing).toEqual(['date_not_entered:shipment']);
  });
});

describe('validity', () => {
  const q01 = data.offers.find(o => o.fixture_id === 'Q01')!;
  const q17 = data.offers.find(o => o.fixture_id === 'Q17')!;

  it('is open through the valid-until day', () => {
    expect(validityAt(q01.fields.valid_until, '2026-10-31')).toMatchObject({ state: 'valid', days_left: 0 });
    expect(validityAt(q01.fields.valid_until, '2026-11-01')).toMatchObject({ state: 'expired', days_left: null });
  });

  it('keeps a missing date not stated, never open', () => {
    expect(validityAt(q17.fields.valid_until, '2026-10-07')).toEqual({ state: 'not_stated', valid_until: null, days_left: null, evidence: [] });
  });
});

describe('scenario through the local adapter', () => {
  const inputs = { decision_date: '2026-10-21', dates: EVENTS, rate_percent: '8', alternative_offer_id: 'fixture-q03' };

  it('shows an expired offer as expired at the decision date and keeps it listed', async () => {
    const adapter = createFixtureAdapter(at('2026-10-10'));
    const result = await adapter.scenario('fixture-q05', inputs);
    expect(result.as_of).toBe('2026-10-10');
    expect(result.validity.decision.state).toBe('expired');
    expect(result.comparison.decision.excluded).toContainEqual({ offer_id: 'fixture-q05', reason: 'expired', valid_until: '2026-10-18' });
    expect(result.comparison.newly_expired.map(e => e.offer_id)).toEqual(['fixture-q03', 'fixture-q05', 'fixture-q10']);
    expect(adapter.list().map(o => o.offer_id)).toContain('fixture-q05');
  });

  it('refuses bad dates, rates and unknown alternatives', async () => {
    const adapter = createFixtureAdapter(at('2026-10-10'));
    await expect(adapter.scenario('fixture-q01', { ...inputs, decision_date: '2026-02-30' })).rejects.toMatchObject({ kind: 'invalid_value' });
    await expect(adapter.scenario('fixture-q01', { ...inputs, rate_percent: '8%' })).rejects.toMatchObject({ kind: 'invalid_value' });
    await expect(adapter.scenario('fixture-q01', { ...inputs, rate_percent: '101' })).rejects.toMatchObject({ kind: 'invalid_value' });
    await expect(adapter.scenario('fixture-q01', { ...inputs, alternative_offer_id: 'nope' })).rejects.toMatchObject({ kind: 'invalid_value' });
    await expect(adapter.scenario('nope', inputs)).rejects.toMatchObject({ kind: 'not_found' });
  });

  it('marks payment terms corrected in the tab as needing service revalidation', async () => {
    const adapter = createFixtureAdapter(at('2026-10-10'));
    const q01 = adapter.get('fixture-q01');
    await adapter.correct('fixture-q01', 'payment_terms', 'Net 30 days from invoice date', q01.version);
    const result = await adapter.scenario('fixture-q01', inputs);
    expect(result.payment_terms).toMatchObject({ status: 'unavailable', reasons: ['revalidation_required'], instalments: [] });
    expect(result.schedule.complete).toBe(false);
    expect(result.terms_comparison?.reason).toBe('schedule_incomplete');
  });
});

describe('export', () => {
  it('adds the scenario to the JSON and CSV, labelled synthetic on every row', async () => {
    const adapter = createFixtureAdapter(at('2026-10-10'));
    const offer = adapter.get('fixture-q01');
    const scenario = await adapter.scenario('fixture-q01', { decision_date: '2026-10-10', dates: EVENTS, rate_percent: '8', alternative_offer_id: 'fixture-q03' });
    const report = offerReport(offer, new Date('2026-10-10T12:00:00Z'), scenario);
    const json = JSON.parse(report.json);
    expect(json.scenario.terms_comparison.value_of_later_payment).toEqual({ amount: '50595.41', currency: 'USD' });
    expect(json.scenario.schedule.lines[0].amount).toEqual({ amount: '705937.5', currency: 'USD' });
    const rows = report.csv.trim().split('\r\n');
    expect(rows.some(r => r.startsWith('payment_instalment,1,'))).toBe(true);
    expect(rows.some(r => r.startsWith('terms_comparison,fixture-q03,') && r.includes('50595.41 USD'))).toBe(true);
    expect(rows.some(r => r.startsWith('scenario_assumption,'))).toBe(true);
    expect(rows.slice(1).every(r => r.endsWith(',true'))).toBe(true);
  });

  it('leaves the scenario out until one is run', () => {
    const offer = createFixtureAdapter(at('2026-10-10')).get('fixture-q01');
    expect(JSON.parse(offerReport(offer, new Date('2026-10-10T12:00:00Z')).json).scenario).toBeNull();
  });
});
