import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { createFixtureAdapter, dataAge, formatValue, OffersError, valueProblem } from './offers';

const fixtureFile = (name: string) => {
  const url = new URL(`../../../procurement-engine/tests/fixtures/offers/${name}`, import.meta.url);
  return new File([readFileSync(url)], name.split('/').at(-1)!);
};
const Q01 = 'train/SYN-Q01_atacama_copper_cif.pdf';

describe('local fixture adapter', () => {
  it('labels itself as synthetic and local', () => {
    const adapter = createFixtureAdapter();
    expect(adapter.source).toBe('local_fixture_adapter');
    expect(adapter.synthetic).toBe(true);
    expect(adapter.list()).toHaveLength(18);
  });

  it('recognises an uploaded fixture by its SHA-256 and keeps decimal strings', async () => {
    const offer = await createFixtureAdapter().upload(fixtureFile(Q01));
    expect(offer.fixture_id).toBe('Q01');
    expect(offer.status).toBe('needs_review');
    const pricing = offer.items[0].pricing;
    expect(pricing.value).toEqual({ type: 'fixed', unit_price: { amount: '9412.50', currency: 'USD' }, per_unit: 'tonne' });
    expect(pricing.evidence[0].locator).toBe('p1:l10');
  });

  it('refuses files it cannot extract instead of inventing an offer', async () => {
    const other = new File(['not a fixture'], 'quote.pdf');
    await expect(createFixtureAdapter().upload(other)).rejects.toMatchObject({ kind: 'unavailable' });
  });

  it('blocks save with the same reasons the service reported', async () => {
    const adapter = createFixtureAdapter();
    const offer = await adapter.upload(fixtureFile(Q01));
    expect(offer.blocking_reasons.map(r => r.field)).toEqual(['settlement_currency']);
    await expect(adapter.save(offer.offer_id, offer.version)).rejects.toMatchObject({ kind: 'save_blocked' });
  });

  it('corrects with version checks, then requires service revalidation before save', async () => {
    const adapter = createFixtureAdapter(() => new Date('2026-10-07T12:00:00Z'));
    const offer = await adapter.upload(fixtureFile(Q01));
    const corrected = await adapter.correct(offer.offer_id, 'settlement_currency', 'USD', offer.version);
    expect(corrected.status).toBe('corrected');
    expect(corrected.version).toBe(offer.version + 1);
    expect(corrected.fields.settlement_currency).toMatchObject({ status: 'corrected', value: 'USD', source: 'user' });
    await expect(adapter.save(offer.offer_id, offer.version)).rejects.toMatchObject({ kind: 'stale_version' });
    await expect(adapter.save(offer.offer_id, corrected.version)).rejects.toMatchObject({ kind: 'save_blocked' });
    expect(adapter.get(offer.offer_id).revalidation_required).toContain('settlement_currency');
  });

  it('validates corrected values before changing anything', async () => {
    const adapter = createFixtureAdapter();
    const offer = await adapter.upload(fixtureFile(Q01));
    await expect(adapter.correct(offer.offer_id, 'settlement_currency', 'usd', 1)).rejects.toMatchObject({ kind: 'invalid_value' });
    await expect(adapter.correct(offer.offer_id, 'items[0].quantity', { amount: '1e3', unit: 'tonne' }, 1)).rejects.toBeInstanceOf(OffersError);
    expect(adapter.get(offer.offer_id).version).toBe(1);
  });

  it('cannot save an expired quote', async () => {
    const adapter = createFixtureAdapter();
    const offer = await adapter.upload(fixtureFile('heldout/SYN-Q06_straits_aluminium_expired.pdf'));
    expect(offer.status).toBe('expired');
    await expect(adapter.save(offer.offer_id, offer.version)).rejects.toMatchObject({ kind: 'invalid_transition' });
  });

  it('requires complete bounded index terms and exact dates', () => {
    expect(valueProblem('pricing', { type: 'index_linked', index: 'LME Copper', premium: { amount: '95.00', currency: 'USD' }, per_unit: 'tonne' })).not.toBeNull();
    expect(valueProblem('pricing', { type: 'index_linked', index: 'LME Copper', averaging_period: 'M+1', premium: { amount: '-95.00', currency: 'USD' }, per_unit: 'tonne' })).toBeNull();
    expect(valueProblem('pricing', { type: 'index_linked', index: 'LME Copper', averaging_period: 'M+1', premium: { amount: '0', currency: 'USD' }, per_unit: 'tonne' })).toBeNull();
    expect(valueProblem('pricing', { type: 'index_linked', index: 'LME Copper', averaging_period: 'M+1', premium: { amount: '95.00', currency: 'USD' }, per_unit: 'tonne', fx_basis: 'ECB rate on invoice date' })).toBeNull();
    expect(valueProblem('pricing', { type: 'index_linked', index: 'LME Copper', averaging_period: '', premium: { amount: '95.00', currency: 'USD' }, per_unit: 'tonne' })).not.toBeNull();
    expect(valueProblem('quantity', { amount: '9'.repeat(400), unit: 'tonne' })).not.toBeNull();
    expect(valueProblem('quantity', { amount: '1.12345678901', unit: 'tonne' })).not.toBeNull();
    expect(valueProblem('pricing', { type: 'fixed', unit_price: { amount: '9'.repeat(400), currency: 'USD' }, per_unit: 'tonne' })).not.toBeNull();
    expect(valueProblem('date', '2026-02-30')).not.toBeNull();
    expect(valueProblem('date', '0000-01-01')).not.toBeNull();
    expect(valueProblem('date', '2028-02-29')).toBeNull();
  });

  it('preserves full index pricing terms but invalidates corrected derived money', async () => {
    const adapter = createFixtureAdapter(() => new Date('2026-10-07T12:00:00Z'));
    const q14 = adapter.list().find(o => o.fixture_id === 'Q14')!;
    const original = q14.items[0].pricing.value as Record<string, unknown>;
    const corrected = await adapter.correct(q14.offer_id, 'items[0].pricing', { ...original, premium: { amount: '-340.00', currency: 'EUR' } }, q14.version);
    expect(corrected.items[0].pricing.value).toMatchObject({ averaging_period: original.averaging_period, fx_basis: original.fx_basis });
    expect((corrected.normalized.items as { pricing: unknown }[])[0].pricing).toBeNull();
    expect(corrected.blocking_reasons.map(r => r.code)).toContain('revalidation_required');
    expect(formatValue('pricing', corrected.items[0].pricing.value)).toContain(String(original.fx_basis));
    expect(corrected.blocking_reasons.map(r => r.code)).toContain('normalization_unavailable');
    await expect(adapter.save(q14.offer_id, corrected.version)).rejects.toMatchObject({ kind: 'save_blocked' });
  });

  it('checks expiry against the current UTC action date, not extraction as-of', async () => {
    let today = '2026-10-07T12:00:00Z';
    const adapter = createFixtureAdapter(() => new Date(today));
    const q01 = adapter.list().find(o => o.fixture_id === 'Q01')!;
    const corrected = await adapter.correct(q01.offer_id, 'settlement_currency', 'USD', q01.version);
    today = '2026-11-01T00:00:00Z';
    try {
      await adapter.save(q01.offer_id, corrected.version);
      throw new Error('expected save to be blocked');
    } catch (error) {
      expect(error).toBeInstanceOf(OffersError);
      expect((error as OffersError).reasons.map(r => r.code)).toContain('expired');
    }
    expect(adapter.get(q01.offer_id).as_of).toBe('2026-10-07');
    expect(adapter.get(q01.offer_id).status).toBe('corrected');
    expect(adapter.get(q01.offer_id).blocking_reasons.map(r => r.code)).toContain('expired');
  });
});

describe('display helpers', () => {
  it('formats values with currency and unit and never rounds amounts', () => {
    expect(formatValue('pricing', { type: 'fixed', unit_price: { amount: '9412.50', currency: 'USD' }, per_unit: 'tonne' }))
      .toBe('USD 9412.50 per tonne');
    expect(formatValue('pricing', { type: 'index_linked', index: 'LME Copper', averaging_period: 'M+1', premium: { amount: '95.00', currency: 'USD' }, per_unit: 'tonne' }))
      .toBe('LME Copper (M+1) + USD 95.00 per tonne');
    expect(formatValue('pricing', { type: 'index_linked', index: 'LME Aluminium', averaging_period: 'month of delivery', premium: { amount: '340.00', currency: 'EUR' }, per_unit: 'tonne', fx_basis: 'ECB invoice-date rate' })).toContain('ECB invoice-date rate');
    expect(formatValue('quantity', { amount: '500', unit: 'tonne', period: 'month' })).toBe('500 tonne per month');
    expect(formatValue('incoterm', { code: 'CIF', place: 'Nhava Sheva, India' })).toBe('CIF Nhava Sheva, India');
    expect(formatValue('date', null)).toBe('Not stated');
  });

  it('reports how old fixture data is', () => {
    expect(dataAge('2026-10-07', new Date('2026-10-07T12:00:00Z'))).toEqual({ days: 0, old: false });
    expect(dataAge('2026-10-07', new Date('2026-10-20T12:00:00Z'))).toEqual({ days: 13, old: true });
  });
});
