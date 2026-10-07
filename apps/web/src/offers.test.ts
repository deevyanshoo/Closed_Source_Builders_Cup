import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { createFixtureAdapter, dataAge, formatValue, OffersError } from './offers';

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

  it('corrects, then saves, with version checks', async () => {
    const adapter = createFixtureAdapter();
    const offer = await adapter.upload(fixtureFile(Q01));
    const corrected = await adapter.correct(offer.offer_id, 'settlement_currency', 'USD', offer.version);
    expect(corrected.status).toBe('corrected');
    expect(corrected.version).toBe(offer.version + 1);
    expect(corrected.fields.settlement_currency).toMatchObject({ status: 'corrected', value: 'USD', source: 'user' });
    await expect(adapter.save(offer.offer_id, offer.version)).rejects.toMatchObject({ kind: 'stale_version' });
    const saved = await adapter.save(offer.offer_id, corrected.version);
    expect(saved.status).toBe('saved');
    expect(saved.blocking_reasons).toEqual([]);
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
});

describe('display helpers', () => {
  it('formats values with currency and unit and never rounds amounts', () => {
    expect(formatValue('pricing', { type: 'fixed', unit_price: { amount: '9412.50', currency: 'USD' }, per_unit: 'tonne' }))
      .toBe('USD 9412.50 per tonne');
    expect(formatValue('pricing', { type: 'index_linked', index: 'LME Copper', averaging_period: 'M+1', premium: { amount: '95.00', currency: 'USD' }, per_unit: 'tonne' }))
      .toBe('LME Copper (M+1) + USD 95.00 per tonne');
    expect(formatValue('quantity', { amount: '500', unit: 'tonne', period: 'month' })).toBe('500 tonne per month');
    expect(formatValue('incoterm', { code: 'CIF', place: 'Nhava Sheva, India' })).toBe('CIF Nhava Sheva, India');
    expect(formatValue('date', null)).toBe('Not stated');
  });

  it('reports how old fixture data is', () => {
    expect(dataAge('2026-10-07', new Date('2026-10-07T12:00:00Z'))).toEqual({ days: 0, old: false });
    expect(dataAge('2026-10-07', new Date('2026-10-20T12:00:00Z'))).toEqual({ days: 13, old: true });
  });
});
