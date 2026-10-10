import { describe, expect, it, vi } from 'vitest';
import { createQuoteService, normalizeQuoteSession, QuoteServiceError } from './offers-service';

const session = { contract_version: '0.2.0', principal_id: 'alice', workspace_id: 'w-a', role: 'editor', development_only: true };
const context = { principal_id: 'alice', workspace_id: 'w-a', role: 'editor', identity_mode: 'development' };
const envelope = (key: string, value: unknown, ctx: unknown = context) => ({ contract_version: '0.2.0', context: ctx, [key]: value });
const response = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } });
const offer = { offer_id: 'offer-1', version: 1, status: 'extracted', synthetic: true,
  document: { document_id: 'sha256:x', filename: 'synthetic.csv', media_type: 'text/csv', byte_size: 8 },
  extraction_id: 'extract-1', fields: { supplier_name: { name: 'supplier_name', kind: 'text', status: 'extracted', value: 'Synthetic Supplier', critical: true, source: 'rules', reasons: [], evidence: [], candidates: [] } },
  items: [], review_flags: [], blocking_reasons: [], normalized: {}, allowed_actions: ['correct', 'save'] };
const summary = { offer_id: 'offer-1', version: 1, status: 'extracted', synthetic: true,
  filename: 'synthetic.csv', supplier_name: 'Synthetic Supplier', materials: [], blocking_reasons: 0, updated_at: '2026-10-10T00:00:00Z' };
const live = () => true;

describe('quote service identity and routes', () => {
  it('normalizes only a valid development session', () => {
    expect(normalizeQuoteSession(session).identity_mode).toBe('development');
    expect(() => normalizeQuoteSession({ ...session, development_only: false, identity_mode: 'private_machine' })).toThrow(QuoteServiceError);
  });
  it('uses the seven public route forms, no browser authorization, and preserves decimal text', async () => {
    const calls: [string, RequestInit][] = [];
    const fetcher = vi.fn(async (path: RequestInfo | URL, init?: RequestInit) => {
      calls.push([String(path), init ?? {}]);
      const key = String(path).includes('sourcing-candidates') ? 'sourcing' : String(path).includes('/extractions/extract-1') ? 'extraction' : String(path).endsWith('/extractions') ? 'upload' : String(path).endsWith('/offers') ? 'offers' : 'offer';
      const value = key === 'offers' ? [summary] : key === 'upload' ? { offer, extraction_id: 'extract-1', deduplicated: false } : key === 'extraction' ? { extraction_id: 'extract-1', extraction: { document: offer.document, as_of: '2026-10-10', extractor: 'rules', synthetic: true }, segments: [] } : key === 'sourcing' ? { candidates: [{ candidate_id: 'offer-1@v1#item0', offer_id: 'offer-1', offer_version: 1, item_index: 0, document_id: offer.document.document_id, material: 'copper', quantity: { amount: '250', unit: 'tonne', kg: '250000' }, price: { type: 'fixed', per_kg: { amount: '9.4125', currency: 'USD' } }, landed_price: { status: 'incoterm_price_only', per_kg: { amount: '9.4125', currency: 'USD' } } }], excluded: [], as_of: '2026-10-10', synthetic: true, contract: 'test', notes: [] } : offer;
      return response(envelope(key, value));
    });
    const api = createQuoteService(normalizeQuoteSession(session), live, fetcher);
    await api.list(); await api.detail('offer-1'); await api.upload('synthetic.csv', 'YSxiCg=='); await api.extraction('extract-1');
    await api.correct('offer-1', 'settlement_currency', 'USD', 1, 'operation-0001'); await api.save('offer-1', 1, 'operation-0002');
    expect((await api.sourcing()).candidates[0].price.per_kg?.amount).toBe('9.4125');
    expect(calls.map(c => c[0])).toEqual(['/v2/offers', '/v2/offers/offer-1?include_events=true', '/v2/offers/extractions', '/v2/offers/extractions/extract-1', '/v2/offers/offer-1/corrections', '/v2/offers/offer-1/save', '/v2/offers/sourcing-candidates']);
    expect(calls.every(([, init]) => !Object.keys(init.headers ?? {}).some(k => k.toLowerCase() === 'authorization'))).toBe(true);
    expect(JSON.parse(String(calls[4][1].body))).toEqual({ field: 'settlement_currency', value: 'USD', expected_version: 1 });
    expect(calls[4][1].headers).toMatchObject({ 'Idempotency-Key': 'operation-0001' });
  });
  it('discards context mismatch on upload, correction and save even after an accepted session', async () => {
    for (const key of ['principal_id', 'workspace_id', 'role', 'identity_mode'] as const) {
      const changed = { ...context, [key]: key === 'role' ? 'viewer' : 'different' };
      const fetcher = vi.fn(async () => response(envelope('offer', offer, changed)));
      const api = createQuoteService(normalizeQuoteSession(session), live, fetcher);
      await expect(api.correct('offer-1', 'settlement_currency', 'USD', 1, 'operation-0001')).rejects.toMatchObject({ kind: 'identity_changed' });
      await expect(api.save('offer-1', 1, 'operation-0002')).rejects.toMatchObject({ kind: 'identity_changed' });
      fetcher.mockResolvedValue(response(envelope('upload', { offer, extraction_id: 'extract-1' }, changed)));
      await expect(api.upload('synthetic.csv', 'YSxiCg==')).rejects.toMatchObject({ kind: 'identity_changed' });
    }
  });
  it('requires matching context on every public route and blocks viewer writes before fetch', async () => {
    for (const key of ['principal_id', 'workspace_id', 'role', 'identity_mode'] as const) {
      const changed = { ...context, [key]: key === 'role' ? 'viewer' : 'different' };
      const fetcher = vi.fn(async () => response(envelope('offers', [], changed)));
      const api = createQuoteService(normalizeQuoteSession(session), live, fetcher);
      await expect(api.list()).rejects.toMatchObject({ kind: 'identity_changed' });
      await expect(api.detail('offer-1')).rejects.toMatchObject({ kind: 'identity_changed' });
      await expect(api.extraction('extract-1')).rejects.toMatchObject({ kind: 'identity_changed' });
      await expect(api.sourcing()).rejects.toMatchObject({ kind: 'identity_changed' });
    }
    const fetcher = vi.fn(async () => response(envelope('offer', offer)));
    const viewer = createQuoteService(normalizeQuoteSession({ ...session, role: 'viewer' }), live, fetcher);
    await expect(viewer.upload('synthetic.csv', 'YSxiCg==')).rejects.toMatchObject({ kind: 'forbidden' });
    await expect(viewer.correct('offer-1', 'settlement_currency', 'USD', 1, 'operation-0001')).rejects.toMatchObject({ kind: 'forbidden' });
    await expect(viewer.save('offer-1', 1, 'operation-0002')).rejects.toMatchObject({ kind: 'forbidden' });
    expect(fetcher).not.toHaveBeenCalled();
  });
  it('does not retain late mutation or detail responses after a generation change', async () => {
    for (const operation of ['detail', 'extraction', 'upload', 'correct', 'save'] as const) {
      let current = true;
      const fetcher = vi.fn(async () => { current = false; return response(envelope('offer', offer)); });
      const api = createQuoteService(normalizeQuoteSession(session), () => current, fetcher);
      const result = operation === 'detail' ? api.detail('offer-1') : operation === 'extraction' ? api.extraction('extract-1') :
        operation === 'upload' ? api.upload('synthetic.csv', 'YSxiCg==') : operation === 'correct' ? api.correct('offer-1', 'settlement_currency', 'USD', 1, 'operation-0001') : api.save('offer-1', 1, 'operation-0002');
      await expect(result).rejects.toMatchObject({ kind: 'identity_changed' });
    }
  });
  it('rejects late generation, absent context, numeric price and error without retaining data', async () => {
    let current = true;
    const fetcher = vi.fn(async () => { current = false; return response(envelope('offers', [offer])); });
    await expect(createQuoteService(normalizeQuoteSession(session), () => current, fetcher).list()).rejects.toMatchObject({ kind: 'identity_changed' });
    fetcher.mockResolvedValue(response({ contract_version: '0.2.0', offers: [offer] }));
    await expect(createQuoteService(normalizeQuoteSession(session), live, fetcher).list()).rejects.toMatchObject({ kind: 'identity_changed' });
    fetcher.mockResolvedValue(response(envelope('offers', [summary], { ...context, injected: 'extra' })));
    await expect(createQuoteService(normalizeQuoteSession(session), live, fetcher).list()).rejects.toMatchObject({ kind: 'identity_changed' });
    fetcher.mockResolvedValue(response(envelope('sourcing', { candidates: [{ price: { type: 'fixed', per_kg: { amount: 9.4, currency: 'USD' } } }], excluded: [] })));
    await expect(createQuoteService(normalizeQuoteSession(session), live, fetcher).sourcing()).rejects.toMatchObject({ kind: 'invalid_response' });
    fetcher.mockResolvedValue(response({ error: { code: 'stale_version', message: 'Do not trust this text.' } }, 409));
    await expect(createQuoteService(normalizeQuoteSession(session), live, fetcher).list()).rejects.toMatchObject({ kind: 'conflict' });
  });
});

const snapshotDir = process.env.QUOTE_SYNTHETIC_SNAPSHOT_DIR;
it.skipIf(!snapshotDir)('accepts the frozen synthetic Python wire examples behind the public context', async () => {
  const { readFileSync, readdirSync } = await import('node:fs');
  const sample = (name: string) => JSON.parse(readFileSync(`${snapshotDir}/${name}.json`, 'utf8')).data;
  const cases: [string, string, unknown][] = readdirSync(snapshotDir!).filter(name => name.endsWith('.json')).map(filename => {
    const name = filename.slice(0, -5), data = sample(name);
    return [name.includes('_list_') ? 'offers' : name.endsWith('_upload') ? 'upload' : name.endsWith('_extraction') ? 'extraction' :
      name.includes('_sourcing_') ? 'sourcing' : name.includes('_correct_') ? 'correct' : name.endsWith('_save') ? 'save' : 'offer',
      name, name.includes('_list_') ? data.offers : data];
  });
  expect(cases).toHaveLength(19);
  for (const [kind, name, data] of cases) {
    const fetcher = vi.fn(async () => response(envelope(kind === 'correct' || kind === 'save' ? 'offer' : kind, data)));
    const api = createQuoteService(normalizeQuoteSession(session), live, fetcher);
    if (kind === 'upload') await expect(api.upload('synthetic.csv', 'YSxiCg==')).resolves.toHaveProperty('offer.offer_id');
    if (kind === 'offers') await expect(api.list()).resolves.toEqual(data);
    if (kind === 'offer') await expect(api.detail((data as { offer_id: string }).offer_id)).resolves.toHaveProperty('offer_id');
    if (kind === 'correct') await expect(api.correct((data as { offer_id: string; version: number }).offer_id,
      'settlement_currency', 'USD', (data as { version: number }).version - 1, 'operation-0001')).resolves.toHaveProperty('version');
    if (kind === 'save') await expect(api.save((data as { offer_id: string; version: number }).offer_id,
      (data as { version: number }).version - 1, 'operation-0002')).resolves.toHaveProperty('status', 'saved');
    if (kind === 'extraction') await expect(api.extraction((data as { extraction_id: string }).extraction_id)).resolves.toHaveProperty('segments');
    if (kind === 'sourcing') await expect(api.sourcing()).resolves.toHaveProperty('candidates');
    expect(fetcher, name).toHaveBeenCalledTimes(1);
  }
  const { bindOfferExtraction } = await import('./offers-service');
  for (const label of ['Q01', 'Q10']) {
    const detail = sample(`${label}_detail_saved`), extraction = sample(`${label}_extraction`);
    await expect(bindOfferExtraction(detail, extraction, live)).resolves.toHaveProperty('segments');
    let checks = 0;
    await expect(bindOfferExtraction(detail, extraction, () => ++checks < 3)).rejects.toMatchObject({ kind: 'identity_changed' });
  }
});

describe('repaired resource and evidence boundary', () => {
  it('requires nested extraction metadata with its declared types', async () => {
    const valid = { extraction_id: 'extract-1', extraction: {
      document: offer.document, as_of: '2026-10-10', extractor: 'rules', synthetic: true }, segments: [] };
    const api = (body: unknown) => createQuoteService(normalizeQuoteSession(session), live,
      vi.fn(async () => response(envelope('extraction', body))));
    await expect(api(valid).extraction('extract-1')).resolves.toHaveProperty('extraction.synthetic', true);
    for (const key of ['as_of', 'extractor', 'synthetic'] as const) {
      const { [key]: _unused, ...missing } = valid.extraction;
      await expect(api({ ...valid, extraction: missing }).extraction('extract-1')).rejects.toMatchObject({ kind: 'invalid_response' });
      await expect(api({ ...valid, extraction: { ...valid.extraction, [key]: key === 'synthetic' ? 'yes' : 10 } })
        .extraction('extract-1')).rejects.toMatchObject({ kind: 'invalid_response' });
    }
  });
  it('rejects a forged quote even when its locator and segment hash are correct', async () => {
    const { bindOfferExtraction } = await import('./offers-service');
    const text = 'Copper index premium USD 0.095 per kg';
    const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text));
    const hash = Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('');
    const segment = { locator: 'line-1', text, text_sha256: hash, flags: [] };
    const extraction = { extraction_id: 'extract-1', extraction: {
      document: offer.document, as_of: '2026-10-10', extractor: 'rules', synthetic: true }, segments: [segment] };
    const evidence = { document_id: offer.document.document_id, locator: segment.locator,
      text_sha256: hash, segment_text: text, quote: 'invented source assertion' };
    const cited = { ...offer, fields: { ...offer.fields, supplier_name: {
      ...offer.fields.supplier_name, evidence: [evidence], candidates: [],
    } } };
    await expect(bindOfferExtraction(cited as any, extraction, live)).rejects.toMatchObject({ kind: 'invalid_response' });
    const candidateCited = { ...offer, fields: { ...offer.fields, supplier_name: {
      ...offer.fields.supplier_name, evidence: [], candidates: [{ value: 'Copper', verified: true, problems: [], evidence: [evidence] }],
    } } };
    await expect(bindOfferExtraction(candidateCited as any, extraction, live)).rejects.toMatchObject({ kind: 'invalid_response' });
    const itemCited = { ...offer, items: [{ material: { ...offer.fields.supplier_name, name: 'material', evidence: [evidence] } }] };
    await expect(bindOfferExtraction(itemCited as any, extraction, live)).rejects.toMatchObject({ kind: 'invalid_response' });
    evidence.quote = 'premium USD 0.095';
    await expect(bindOfferExtraction(cited as any, extraction, live)).resolves.toHaveProperty('segments');
    await expect(bindOfferExtraction(candidateCited as any, extraction, live)).resolves.toHaveProperty('segments');
    await expect(bindOfferExtraction(itemCited as any, extraction, live)).resolves.toHaveProperty('segments');
    evidence.quote = '';
    await expect(bindOfferExtraction(cited as any, extraction, live)).rejects.toMatchObject({ kind: 'invalid_response' });
  });
  it('rejects detail, extraction and upload bodies for a different requested identity', async () => {
    const changedOffer = { ...offer, offer_id: 'offer-other' };
    const detail = createQuoteService(normalizeQuoteSession(session), live, vi.fn(async () => response(envelope('offer', changedOffer))));
    await expect(detail.detail('offer-1')).rejects.toMatchObject({ kind: 'invalid_response' });
    const extraction = createQuoteService(normalizeQuoteSession(session), live, vi.fn(async () => response(envelope('extraction', {
      extraction_id: 'extract-other', extraction: { document: offer.document }, segments: [] }))));
    await expect(extraction.extraction('extract-1')).rejects.toMatchObject({ kind: 'invalid_response' });
    const missingDocument = createQuoteService(normalizeQuoteSession(session), live, vi.fn(async () => response(envelope('extraction', {
      extraction_id: 'extract-1', extraction: {}, segments: [] }))));
    await expect(missingDocument.extraction('extract-1')).rejects.toMatchObject({ kind: 'invalid_response' });
    const upload = createQuoteService(normalizeQuoteSession(session), live, vi.fn(async () => response(envelope('upload', {
      offer: { ...offer, extraction_id: 'extract-other' }, extraction_id: 'extract-1' }))));
    await expect(upload.upload('synthetic.csv', 'YSxiCg==')).rejects.toMatchObject({ kind: 'invalid_response' });
  });
  it('rejects mismatched or unverified exact text and stale async hash completion', async () => {
    const { bindOfferExtraction } = await import('./offers-service');
    const segment = { locator: 'p1:l2', text: 'source line', text_sha256: '0'.repeat(64), flags: [] };
    const extraction = { extraction_id: 'extract-1', extraction: { document: offer.document }, segments: [segment] };
    await expect(bindOfferExtraction(offer as any, extraction as any, live)).rejects.toMatchObject({ kind: 'invalid_response' });
    const evidenceOffer = { ...offer, fields: { supplier_name: { ...offer.fields.supplier_name, evidence: [{ document_id: offer.document.document_id,
      locator: 'p1:missing', quote: 'source', text_sha256: '0'.repeat(64), segment_text: 'source line' }] } } };
    await expect(bindOfferExtraction(evidenceOffer as any, extraction as any, live)).rejects.toMatchObject({ kind: 'invalid_response' });
  });
  it('rejects malformed shipment and verified candidate values before rendering', async () => {
    const malformed = { ...offer, fields: { ...offer.fields, shipments: { name: 'shipments', kind: 'shipments', status: 'extracted', value: { invalid: true },
      critical: false, source: 'rules', reasons: [], evidence: [], candidates: [] } } };
    const fetcher = vi.fn(async () => response(envelope('offer', malformed)));
    await expect(createQuoteService(normalizeQuoteSession(session), live, fetcher).detail('offer-1')).rejects.toMatchObject({ kind: 'invalid_response' });
    malformed.fields.shipments = { ...malformed.fields.shipments, kind: 'text', value: 'good', candidates: [{ value: { bad: true }, verified: 'yes', problems: [], evidence: [] }] } as any;
    await expect(createQuoteService(normalizeQuoteSession(session), live, fetcher).detail('offer-1')).rejects.toMatchObject({ kind: 'invalid_response' });
  });
});
