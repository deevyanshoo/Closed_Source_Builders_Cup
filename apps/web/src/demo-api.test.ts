import { describe, expect, it, vi } from 'vitest';
import { createDemoApi, DemoApiError, MAX_UPLOAD_BYTES } from './demo-api';

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), {
  status, headers: { 'content-type': 'application/json' },
});

describe('hosted demo API', () => {
  it('presents connection, expiration, and upload limits in workspace language without changing the request boundary', async () => {
    await expect(createDemoApi(async () => { throw new Error('offline'); }).session()).rejects.toMatchObject({ message: 'The workspace could not be reached. Check your connection and try again.' });
    await expect(createDemoApi(async () => json({}, 403)).session()).rejects.toMatchObject({ message: 'This workspace session expired. Refresh the page to start again.' });
    const file = { name: 'large.pdf', size: MAX_UPLOAD_BYTES + 1, arrayBuffer: vi.fn() } as unknown as File;
    await expect(createDemoApi(vi.fn()).upload('csrf', file, 'gemini')).rejects.toMatchObject({ message: 'Choose a listed sample document smaller than 500 KB.' });
    expect(file.arrayBuffer).not.toHaveBeenCalled();
  });
  it('reads the authenticated risk snapshot without a mutation, CSRF, or model request', async () => {
    const send = vi.fn(async () => json({ companies: [], monitoring: 'while_workspace_open' }));
    await createDemoApi(send).risks();
    expect(send).toHaveBeenCalledWith('/api/demo/risks', { method: 'GET', credentials: 'same-origin', headers: {} });
  });
  it('sends CSRF, a fresh idempotency key and the exact offer version on correction', async () => {
    const send = vi.fn(async () => json({ offer: { offer_id: 'q1', version: 4 }, replayed: false }));
    const api = createDemoApi(send, () => 'new-request-key');
    await api.correct('csrf-value', 'q1', 'settlement_currency', 'USD', 3);
    const [url, options] = send.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe('/api/demo/offers/q1/correct');
    expect(options.headers).toMatchObject({ 'X-CSRF-Token': 'csrf-value' });
    expect(JSON.parse(options.body as string)).toEqual({ field: 'settlement_currency', value: 'USD', expected_version: 3, idempotency_key: 'new-request-key' });
  });

  it('keeps provider failures visible rather than returning a rules result', async () => {
    const api = createDemoApi(async () => json({ detail: { code: 'provider_unavailable', message: 'Gemini is temporarily unavailable.' } }, 503));
    await expect(api.extract('csrf', { fixture_id: 'demo-a', mode: 'gemini' })).rejects.toMatchObject({
      name: 'DemoApiError', status: 503, code: 'provider_unavailable', message: 'Gemini is temporarily unavailable.',
    });
  });

  it('shows the hosted service error envelope to the reviewer', async () => {
    const api = createDemoApi(async () => json({ error: { code: 'save_blocked', message: 'Settlement currency needs review.' } }, 422));
    await expect(api.save('csrf', 'q1', 2)).rejects.toMatchObject({
      name: 'DemoApiError', status: 422, code: 'save_blocked', message: 'Settlement currency needs review.',
    });
  });

  it('rejects oversized uploads before reading bytes or contacting the service', async () => {
    const send = vi.fn(async () => json({}));
    const file = { name: 'large.pdf', size: MAX_UPLOAD_BYTES + 1, arrayBuffer: vi.fn() } as unknown as File;
    const api = createDemoApi(send);
    await expect(api.upload('csrf', file, 'gemini')).rejects.toBeInstanceOf(DemoApiError);
    expect(file.arrayBuffer).not.toHaveBeenCalled();
    expect(send).not.toHaveBeenCalled();
  });

  it('preserves decimal text and exact accepted versions in a planning request', async () => {
    const send = vi.fn(async () => json({ plan_id: 'p1' }));
    const api = createDemoApi(send, () => 'plan-key');
    await api.plan('csrf', { offer_ids: ['q1'], expected_versions: { q1: 4 }, material: 'copper', quantity_kg: '1000.25', current_stock_kg: '50', need_by: '2026-10-30', decision_date: '2026-10-14', reporting_currency: 'USD', budget: '9999.99', additional_cost_per_kg: '0.25' });
    const [, options] = send.mock.calls[0] as unknown as [string, RequestInit];
    expect(JSON.parse(options.body as string)).toMatchObject({ offer_ids: ['q1'], expected_versions: { q1: 4 }, quantity_kg: '1000.25', budget: '9999.99' });
  });
  it('sends a company plan in its explicit unit without kg aliases', async () => {
    const send = vi.fn(async () => json({ plan_id: 'p2' }));
    await createDemoApi(send, () => 'plan-key').plan('csrf', { offer_ids: ['q1'], expected_versions: { q1: 4 }, material: 'Steel', quantity: '21.5', current_stock: '3', quantity_unit: 'tonne', need_by: '2026-10-30', decision_date: '2026-10-14', reporting_currency: 'USD', budget: '9999.99', additional_cost_per_unit: '0.25' }, 9);
    const body = JSON.parse((send.mock.calls[0] as unknown as [string, RequestInit])[1].body as string);
    expect(body).toMatchObject({ quantity: '21.5', current_stock: '3', quantity_unit: 'tonne', additional_cost_per_unit: '0.25', expected_context_version: 9 });
    expect(body).not.toHaveProperty('quantity_kg');
  });

  it('sends exact recovery and reviewed RFQ bodies with generated keys', async () => {
    const send = vi.fn(async () => json({ comparison_id: 'c1', draft_id: 'd1' }));
    const api = createDemoApi(send, () => 'generated');
    await api.recovery('csrf', { baseline_plan_id: 'p1', expected_versions: { q1: 2 }, delay_days_by_offer: { q1: 20 } });
    await api.rfq('csrf', { plan_id: 'p2', expected_versions: { q1: 2 }, reviewed: true });
    const calls = send.mock.calls as unknown as [string, RequestInit][];
    expect(calls[0][0]).toBe('/api/demo/recovery');
    expect(JSON.parse(calls[0][1].body as string)).toEqual({ baseline_plan_id: 'p1', expected_versions: { q1: 2 }, delay_days_by_offer: { q1: 20 }, idempotency_key: 'generated' });
    expect(calls[1][0]).toBe('/api/demo/rfq');
    expect(JSON.parse(calls[1][1].body as string)).toEqual({ plan_id: 'p2', expected_versions: { q1: 2 }, reviewed: true, idempotency_key: 'generated' });
  });

  it('reads company-scoped carbon and shipment views and carries an explicit shipment through recovery', async () => {
    const send = vi.fn(async () => json({}));
    const api = createDemoApi(send, () => 'review-key');
    await api.carbonAssessment();
    await api.dispatch();
    await api.recovery('csrf', { baseline_plan_id: 'plan-a', expected_versions: { 'offer-a': 2 }, delay_days_by_offer: { 'offer-a': 7 }, shipment_id: 'shipment-a' }, 5);
    const calls = send.mock.calls as unknown as [string, RequestInit][];
    expect(calls.slice(0, 2)).toEqual([
      ['/api/demo/carbon/assessment', { method: 'GET', credentials: 'same-origin', headers: {} }],
      ['/api/demo/dispatch', { method: 'GET', credentials: 'same-origin', headers: {} }],
    ]);
    expect(JSON.parse(calls[2][1].body as string)).toEqual({ baseline_plan_id: 'plan-a', expected_versions: { 'offer-a': 2 }, delay_days_by_offer: { 'offer-a': 7 }, shipment_id: 'shipment-a', expected_context_version: 5, idempotency_key: 'review-key' });
  });

  it('downloads only the requested draft as text', async () => {
    const send = vi.fn(async () => new Response('Synthetic RFQ draft'));
    const api = createDemoApi(send);
    expect(await (await api.downloadRfq('draft/1')).text()).toBe('Synthetic RFQ draft');
    expect((send.mock.calls as unknown as [string, RequestInit][])[0][0]).toBe('/api/demo/rfq/draft%2F1/download');
  });

  it('scopes every mutation to the selected company context and loads company/news routes', async () => {
    const send = vi.fn(async () => json({ companies: [], items: [], session_id: 's2' }));
    const api = createDemoApi(send, () => 'key');
    await api.companies();
    await api.news();
    await api.company('csrf', 'loomworks', 7);
    await api.refreshNews('csrf', 7);
    await api.extract('csrf', { fixture_id: 'quote-a', mode: 'rules' }, 7);
    await api.correct('csrf', 'q1', 'material', 'cotton', 2, 7);
    await api.save('csrf', 'q1', 2, 7);
    await api.plan('csrf', { offer_ids: ['q1'], expected_versions: { q1: 2 }, material: 'cotton', quantity_kg: '1000', current_stock_kg: '0', need_by: '2026-11-05', decision_date: '2026-10-14', reporting_currency: 'USD', budget: '1000', additional_cost_per_kg: '0' }, 7);
    await api.recovery('csrf', { baseline_plan_id: 'p1', expected_versions: { q1: 2 }, delay_days_by_offer: {} }, 7);
    await api.rfq('csrf', { plan_id: 'p1', expected_versions: { q1: 2 }, reviewed: true }, 7);
    await api.reset('csrf', 7);
    const calls = send.mock.calls as unknown as [string, RequestInit][];
    expect(calls.slice(0, 4).map(([path]) => path)).toEqual(['/api/demo/companies', '/api/demo/news', '/api/demo/company', '/api/demo/news/refresh']);
    expect(JSON.parse(calls[2][1].body as string)).toEqual({ company_id: 'loomworks', expected_context_version: 7, idempotency_key: 'key' });
    for (const [, options] of calls.slice(2)) expect(JSON.parse(options.body as string)).toMatchObject({ expected_context_version: 7 });
    expect(JSON.parse(calls[7][1].body as string)).toMatchObject({ material: 'cotton', quantity_kg: '1000', expected_context_version: 7 });
  });

  it('sends exact operator proposal and voice wire arguments with CSRF, context, and unique keys', async () => {
    const send = vi.fn(async () => json({ status: 'completed' }));
    let sequence = 0;
    const api = createDemoApi(send, () => `key-${++sequence}`);
    await api.operator();
    await api.operatorTurn('csrf', 'Inspect current exposure', 4);
    await api.confirmOperator('csrf', 'proposal-1', 4);
    await api.transcribeVoice('csrf', 'YWJj', 'audio/webm', 48000, 4);
    await api.speakVoice('csrf', 'Verified answer', 4);
    const calls = send.mock.calls as unknown as [string, RequestInit][];
    expect(calls.map(([path]) => path)).toEqual(['/api/demo/operator', '/api/demo/operator', '/api/demo/operator/confirm', '/api/demo/voice/transcribe', '/api/demo/voice/speak']);
    expect(JSON.parse(calls[1][1].body as string)).toEqual({ message: 'Inspect current exposure', expected_context_version: 4, idempotency_key: 'key-1' });
    expect(JSON.parse(calls[2][1].body as string)).toEqual({ proposal_id: 'proposal-1', reviewed: true, expected_context_version: 4, idempotency_key: 'key-2' });
    expect(JSON.parse(calls[3][1].body as string)).toEqual({ audio_base64: 'YWJj', mime_type: 'audio/webm', sample_rate_hertz: 48000, expected_context_version: 4, idempotency_key: 'key-3' });
    expect(JSON.parse(calls[4][1].body as string)).toEqual({ text: 'Verified answer', expected_context_version: 4, idempotency_key: 'key-4' });
    for (const [, options] of calls.slice(1)) expect(options.headers).toMatchObject({ 'X-CSRF-Token': 'csrf' });
  });
  it('reads company history and sends cancellation and finalized voice text with scoped idempotent mutations', async () => {
    const send = vi.fn(async () => json({ company_id: 'solara', context_version: 4, exchanges: [] }));
    let sequence = 0;
    const api = createDemoApi(send, () => `history-${++sequence}`);
    await api.operatorHistory();
    await api.cancelOperator('csrf', 'proposal-1', 4);
    await api.appendOperatorVoice('csrf', 'What changed?', 'input', 4);
    const calls = send.mock.calls as unknown as [string, RequestInit][];
    expect(calls.map(([path]) => path)).toEqual(['/api/demo/operator/history', '/api/demo/operator/cancel', '/api/demo/operator/history']);
    expect(JSON.parse(calls[1][1].body as string)).toEqual({ proposal_id: 'proposal-1', expected_context_version: 4, idempotency_key: 'history-1' });
    expect(JSON.parse(calls[2][1].body as string)).toEqual({ text: 'What changed?', speaker: 'input', channel: 'voice', expected_context_version: 4, idempotency_key: 'history-2' });
    expect(calls[2][1].headers).toMatchObject({ 'X-CSRF-Token': 'csrf' });
  });
  it('saves buyer draft fields with the current context and a fresh idempotency key', async () => {
    const send = vi.fn(async () => json({ session_id: 's1', buyer_draft: { material: 'copper' } }));
    const api = createDemoApi(send, () => 'draft-key');
    const draft = { material: 'copper', quantity: '1200', current_stock: '200', quantity_unit: 'kg', need_by: '2026-11-05', decision_date: '2026-10-14', reporting_currency: 'USD', budget: '9000', additional_cost_per_unit: '0.5' };
    await api.buyerDraft('csrf', draft, 8);
    const [path, options] = send.mock.calls[0] as unknown as [string, RequestInit];
    expect(path).toBe('/api/demo/buyer-draft');
    expect(options.headers).toMatchObject({ 'X-CSRF-Token': 'csrf' });
    expect(JSON.parse(options.body as string)).toEqual({ buyer_draft: draft, expected_context_version: 8, idempotency_key: 'draft-key' });
  });
});
