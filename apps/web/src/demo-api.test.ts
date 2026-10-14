import { describe, expect, it, vi } from 'vitest';
import { createDemoApi, DemoApiError, MAX_UPLOAD_BYTES } from './demo-api';

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), {
  status, headers: { 'content-type': 'application/json' },
});

describe('hosted demo API', () => {
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

  it('downloads only the requested draft as text', async () => {
    const send = vi.fn(async () => new Response('Synthetic RFQ draft'));
    const api = createDemoApi(send);
    expect(await (await api.downloadRfq('draft/1')).text()).toBe('Synthetic RFQ draft');
    expect((send.mock.calls as unknown as [string, RequestInit][])[0][0]).toBe('/api/demo/rfq/draft%2F1/download');
  });
});
