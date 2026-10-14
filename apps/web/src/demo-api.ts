export const MAX_UPLOAD_BYTES = 500 * 1024;

export type Evidence = { locator: string; quote: string; text_sha256?: string };
export type FieldResult = { name: string; kind: string; status: string; value: unknown; source: string; evidence: Evidence[]; reasons: string[]; critical?: boolean; candidates?: unknown[] };
export type OfferView = {
  offer_id: string; version: number; status: string; synthetic: true;
  served_from_cache?: boolean;
  document: { document_id: string; filename: string; media_type: string; byte_size: number; warnings?: string[] };
  extractor: string; extractor_detail: { requested?: string; call?: string; model?: string; fallback_reason?: string };
  as_of: string; fields: Record<string, FieldResult>; items: Record<string, FieldResult>[];
  review_flags: { flag: string; locator?: string | null; text?: string }[];
  normalized: Record<string, unknown>; blocking_reasons: { code: string; field: string; message: string }[];
};
export type DemoSession = { session_id: string; csrf_token: string; expires_at: string; synthetic: true; persistence: 'ephemeral'; offers: OfferView[]; provider: { model: string; available: boolean }; plan?: PlanView | null; plan_request?: PlanRequest | null; recovery?: RecoveryView | null; rfq?: RfqView | null };
export type DemoFixture = { id: string; name: string; filename: string; description: string; download_url: string };
export type PlanRequest = { offer_ids: string[]; expected_versions: Record<string, number>; material: string; quantity_kg: string; current_stock_kg: string; need_by: string; decision_date: string; reporting_currency: string; budget: string; additional_cost_per_kg: string; delay_days_by_offer?: Record<string, number> };
export type PlanView = { plan_id: string; synthetic: true; decision_date: string; need_by: string; material: string; quantity_kg: string; current_stock_kg: string; required_purchase_kg: string; reporting_currency: string; assumptions: Record<string, unknown>; alternatives: { offer_id: string; supplier: string; feasible: boolean; reasons: string[]; arrival_date: string | null; unit_price_per_kg: string | null; total_cost: string | null; quantity_kg: string }[]; selected_offer_id: string | null; total_cost: string | null; explanation: string; forecast_action: 'ABSTAIN'; customer_savings: null; offer_versions: Record<string, number> };
export type RecoveryRequest = { baseline_plan_id: string; expected_versions: Record<string, number>; delay_days_by_offer: Record<string, number> };
export type RecoveryView = { comparison_id: string; synthetic: true; baseline: PlanView; scenario: PlanView; baseline_request: PlanRequest; scenario_request: PlanRequest; cost_delta: string | null; selection_changed: boolean; outcome: 'changed' | 'unchanged' | 'infeasible' | 'stock_covers_need'; replayed: boolean };
export type RfqRequest = { plan_id: string; expected_versions: Record<string, number>; reviewed: true };
export type RfqView = { draft_id: string; plan_id: string; offer_versions: Record<string, number>; synthetic: true; status: 'draft_unsent'; supplier: string; material: string; quantity_kg: string; need_by: string; reporting_currency: string; total_cost: string; created_at: string; text: string };

export class DemoApiError extends Error {
  constructor(message: string, readonly status = 0, readonly code = 'unavailable') { super(message); this.name = 'DemoApiError'; }
}

export function createDemoApi(send: typeof fetch = fetch, key: () => string = () => crypto.randomUUID()) {
  async function request<T>(path: string, method = 'GET', body?: unknown, csrf?: string): Promise<T> {
    let response: Response;
    try {
      response = await send(`/api/demo${path}`, {
        method, credentials: 'same-origin',
        headers: { ...(body === undefined ? {} : { 'Content-Type': 'application/json' }), ...(csrf ? { 'X-CSRF-Token': csrf } : {}) },
        ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      });
    } catch {
      throw new DemoApiError('The demo service could not be reached. Check your connection and try again.');
    }
    if (!response.ok) {
      let error: unknown;
      try { error = await response.json(); } catch { /* Keep a safe public message. */ }
      const detail = (error && typeof error === 'object' && 'error' in error) ? error.error :
        (error && typeof error === 'object' && 'detail' in error) ? error.detail : error;
      const code = detail && typeof detail === 'object' && 'code' in detail && typeof detail.code === 'string' ? detail.code : 'request_failed';
      const message = typeof detail === 'string' ? detail : detail && typeof detail === 'object' && 'message' in detail && typeof detail.message === 'string' ? detail.message :
        response.status === 409 ? 'This quote changed. Refresh the session and try again.' :
        response.status === 403 ? 'This demo session expired. Refresh the page to start again.' :
        response.status === 503 ? 'The extraction service is unavailable. Try again shortly.' :
        'The request could not be completed. Review the inputs and try again.';
      throw new DemoApiError(message, response.status, code);
    }
    if (response.status === 204) return undefined as T;
    try { return await response.json() as T; }
    catch { throw new DemoApiError('The demo service returned an unreadable response. Please try again.'); }
  }

  async function encode(file: File): Promise<string> {
    if (file.size > MAX_UPLOAD_BYTES) throw new DemoApiError('Choose a synthetic document smaller than 500 KB.', 0, 'file_too_large');
    const bytes = new Uint8Array(await file.arrayBuffer());
    if (bytes.byteLength > MAX_UPLOAD_BYTES) throw new DemoApiError('Choose a synthetic document smaller than 500 KB.', 0, 'file_too_large');
    let binary = '';
    for (let i = 0; i < bytes.length; i += 0x8000) binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
    return btoa(binary);
  }

  const mutate = <T,>(path: string, body: Record<string, unknown>, csrf: string) => request<T>(path, 'POST', { ...body, idempotency_key: key() }, csrf);
  async function download(path: string, failure: string): Promise<Blob> {
    let response: Response;
    try { response = await send(`/api/demo${path}`, { credentials: 'same-origin' }); }
    catch { throw new DemoApiError(`${failure} Check your connection.`); }
    if (!response.ok) throw new DemoApiError(failure, response.status);
    return response.blob();
  }
  return {
    session: () => request<DemoSession>('/session'),
    fixtures: async (): Promise<DemoFixture[]> => (await request<{ fixtures: DemoFixture[] }>('/fixtures')).fixtures,
    extract: (csrf: string, source: { fixture_id: string; mode: 'gemini' | 'rules' }) => mutate<{ offer: OfferView; provider: OfferView['extractor_detail']; replayed: boolean; served_from_cache: boolean }>('/extract', source, csrf),
    upload: async (csrf: string, file: File, mode: 'gemini' | 'rules') => mutate<{ offer: OfferView; provider: OfferView['extractor_detail']; replayed: boolean; served_from_cache: boolean }>('/extract', { filename: file.name, document_base64: await encode(file), mode }, csrf),
    correct: (csrf: string, id: string, field: string, value: unknown, version: number) => mutate<{ offer: OfferView; replayed: boolean }>(`/offers/${encodeURIComponent(id)}/correct`, { field, value, expected_version: version }, csrf),
    save: (csrf: string, id: string, version: number) => mutate<{ offer: OfferView; replayed: boolean }>(`/offers/${encodeURIComponent(id)}/save`, { expected_version: version }, csrf),
    plan: (csrf: string, planRequest: PlanRequest) => request<PlanView>('/plan', 'POST', planRequest, csrf),
    recovery: (csrf: string, body: RecoveryRequest) => mutate<RecoveryView>('/recovery', body, csrf),
    rfq: (csrf: string, body: RfqRequest) => mutate<RfqView>('/rfq', body, csrf),
    downloadRfq: (id: string) => download(`/rfq/${encodeURIComponent(id)}/download`, 'The RFQ draft could not be downloaded.'),
    exportReport: () => download('/export', 'The report could not be downloaded.'),
    reset: (csrf: string) => request<DemoSession>('/reset', 'POST', {}, csrf),
  };
}
