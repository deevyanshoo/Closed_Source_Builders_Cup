import { describe, expect, it, vi } from 'vitest';
import { createCompanyPreviewApi, createLatestRequest } from './company-preview-api';
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });
describe('public company preview API', () => {
  it('prepares without confirmation and sends CSRF, context and retry key', async () => {
    const send = vi.fn(async () => json({ proposal: { proposal_id: 'p1' } }));
    await createCompanyPreviewApi(send, () => 'key-1').prepare('csrf', 'Müller & Söhne GmbH', 3);
    expect(send).toHaveBeenCalledTimes(1);
    const [path, options] = send.mock.calls[0] as unknown as [string, RequestInit];
    expect(path).toBe('/api/demo/company-preview/prepare');
    expect(options.credentials).toBe('same-origin');
    expect(options.headers).toMatchObject({ 'X-CSRF-Token': 'csrf' });
    expect(JSON.parse(options.body as string)).toEqual({ company_name: 'Müller & Söhne GmbH', expected_context_version: 3, idempotency_key: 'key-1' });
  });
  it('confirms only the reviewed proposal and exposes service errors', async () => {
    const send = vi.fn(async () => json({ error: { code: 'research_limit', message: 'Daily allowance used.' } }, 429));
    await expect(createCompanyPreviewApi(send, () => 'key-2').confirm('csrf', 'p1', 3)).rejects.toMatchObject({ code: 'research_limit', status: 429 });
    const [path, options] = send.mock.calls[0] as unknown as [string, RequestInit];
    expect(path).toBe('/api/demo/company-preview/confirm');
    expect(JSON.parse(options.body as string)).toEqual({ proposal_id: 'p1', reviewed: true, expected_context_version: 3, idempotency_key: 'key-2' });
  });
  it('drops an older response after the input or session changes', () => {
    const latest = createLatestRequest();
    const old = latest.begin();
    const current = latest.begin();
    expect(latest.current(old)).toBe(false);
    expect(latest.current(current)).toBe(true);
    latest.cancel();
    expect(latest.current(current)).toBe(false);
  });
});
