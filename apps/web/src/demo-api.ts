export const MAX_UPLOAD_BYTES = 500 * 1024;

export type Evidence = { locator: string; quote: string; text_sha256?: string };
export type FieldResult = { name: string; kind: string; status: string; value: unknown; source: string; evidence: Evidence[]; reasons: string[]; critical?: boolean; candidates?: unknown[] };
export type OfferView = {
  offer_id: string; version: number; status: string; synthetic: true;
  fixture_id?: string;
  served_from_cache?: boolean;
  document: { document_id: string; filename: string; media_type: string; byte_size: number; warnings?: string[] };
  extractor: string; extractor_detail: { requested?: string; call?: string; model?: string; fallback_reason?: string };
  as_of: string; fields: Record<string, FieldResult>; items: Record<string, FieldResult>[];
  review_flags: { flag: string; locator?: string | null; text?: string }[];
  normalized: Record<string, unknown>; blocking_reasons: { code: string; field: string; message: string }[];
};
export type SimulatedOutboxMessage = { message_id: string; company_id: string; material_id: string; recipient_label: string; subject: string; body: string; status: 'simulated_sent'; simulated: true; external_delivery: false; created_at: string };
export type DemoSession = { session_id: string; csrf_token: string; expires_at: string; synthetic: true; persistence: 'ephemeral'; offers: OfferView[]; provider: { model: string; available: boolean }; plan?: PlanView | null; plan_request?: PlanRequest | null; recovery?: RecoveryView | null; rfq?: RfqView | null; company?: CompanyProfile; context_version?: number; buyer_draft?: BuyerDraft | null; selected_material_id?: string | null; simulated_outbox?: SimulatedOutboxMessage[] };
export type DemoFixture = { id: string; name: string; filename: string; description: string; download_url: string; review_values?: Record<string, unknown> };
export type PlanRequest = { offer_ids: string[]; expected_versions: Record<string, number>; material: string; quantity?: string; current_stock?: string; additional_cost_per_unit?: string; quantity_unit?: string; quantity_kg?: string; current_stock_kg?: string; additional_cost_per_kg?: string; need_by: string; decision_date: string; reporting_currency: string; budget: string; delay_days_by_offer?: Record<string, number> };
export type BuyerDraft = Required<Pick<PlanRequest, 'material' | 'quantity' | 'current_stock' | 'quantity_unit' | 'need_by' | 'decision_date' | 'reporting_currency' | 'budget' | 'additional_cost_per_unit'>>;
export type PlanView = { plan_id: string; synthetic: true; decision_date: string; need_by: string; material: string; quantity?: string; current_stock?: string; required_purchase?: string; quantity_unit?: string; quantity_kg?: string; current_stock_kg?: string; required_purchase_kg?: string; reporting_currency: string; assumptions: Record<string, unknown>; alternatives: { offer_id: string; supplier: string; feasible: boolean; reasons: string[]; arrival_date: string | null; unit_price_per_unit?: string | null; unit_price_per_kg?: string | null; total_cost: string | null; quantity?: string; quantity_kg?: string }[]; selected_offer_id: string | null; total_cost: string | null; explanation: string; forecast_action: 'ABSTAIN'; customer_savings: null; offer_versions: Record<string, number> };
export type RecoveryRequest = { baseline_plan_id: string; expected_versions: Record<string, number>; delay_days_by_offer: Record<string, number> };
export type RecoveryView = { comparison_id: string; synthetic: true; baseline: PlanView; scenario: PlanView; baseline_request: PlanRequest; scenario_request: PlanRequest; cost_delta: string | null; selection_changed: boolean; outcome: 'changed' | 'unchanged' | 'infeasible' | 'stock_covers_need'; replayed: boolean };
export type RfqRequest = { plan_id: string; expected_versions: Record<string, number>; reviewed: true };
export type RfqView = { draft_id: string; plan_id: string; offer_versions: Record<string, number>; synthetic: true; status: 'draft_unsent'; supplier: string; material: string; quantity?: string; quantity_unit?: string; quantity_kg?: string; need_by: string; reporting_currency: string; total_cost: string; created_at: string; text: string };
export type CompanyProfile = {
  company_id: string; name: string; sector: string; size: string; synthetic: true; as_of: string; description: string;
  locations: { country: string; city: string }[]; reporting_currency: string;
  materials: { material_id: string; name: string; quantity_unit: string; stock: string; consumption_per_day: string; monthly_requirement: string; criticality: string; keywords: string[] }[];
  suppliers: { supplier_id: string; name: string; country: string; settlement_currency: string; materials: string[]; lead_time_days: number }[];
  orders: { order_id: string; material_id: string; quantity: string; unit: string; need_by: string; status: string }[];
  routes: { route_id: string; origin: string; destination: string; via: string[]; materials: string[]; direction: 'import' | 'export'; currency: string }[];
  currencies: string[]; news_keywords: string[];
  quote_coverage: { materials: string[]; fixture_ids: string[]; note: string; scenarios?: { material_id: string; fixture_ids: string[]; buyer_defaults: PlanRequest }[] };
  buyer_defaults: PlanRequest | null;
};
export type SourceState = { source_id: string; name: string; state: string; last_checked_at: string | null; reason: string | null };
export type RiskAlert = { alert_id: string; company_id: string; category: 'inventory' | 'supplier_delay' | 'news'; severity: 'high' | 'medium' | 'low'; title: string; summary: string; impact: string; material_ids: string[]; provenance: 'synthetic_profile' | 'synthetic_plan' | 'synthetic_delay_scenario' | 'observed_news'; confidence: { level: 'high' | 'moderate' | 'low'; reason: string }; evidence: { kind: 'profile' | 'order' | 'plan' | 'news'; ref_id: string; label: string; detail: string; url: string | null; observed_at: string | null }[]; metrics: Record<string, string | number | null>; recovery_actions: { action_id: string; label: string; message: string; requires_review: boolean }[] };
export type CompanyRiskReport = { company_id: string; company_name: string; sector: string; checked_at: string; alerts: RiskAlert[]; coverage: { materials_evaluated: number; news_state: string; news_items_evaluated: number; sources: SourceState[] }; data_gaps: string[] };
export type RiskReport = { company_id: string; context_version: number; checked_at: string; monitoring: 'while_workspace_open'; news_refresh_seconds: number; companies: CompanyRiskReport[]; summary: { high: number; medium: number; low: number; companies_with_alerts: number }; limitations: string[] };
export type NewsItem = { item_id: string; title: string; url: string; publisher: string | null; provider: string; published_at: string | null; first_seen_at: string | null; retrieved_at: string; source_tier: string; source_reliability: string; reliability_reason: string; category: string; matched_materials: string[]; matched_routes: string[]; matched_currencies: string[]; matched_operations?: string[]; relevance_kind?: 'direct_material' | 'sector' | 'route' | 'currency' | 'unmatched'; relevance_score?: number; summary?: string | null; relevance_reason: string; implication: string; implication_confidence: string; next_steps: string[]; synthetic: false };
export type FxObservation = { base: string; quote: string; rate: string; observation_date: string; provider: string; retrieved_at: string; informational: true };
export type NewsCollection = { mode?: string; status: string; attempted_at: string | null; news_success_at: string | null; fx_success_at: string | null; slot?: string; completed_at?: string; published?: boolean };
export type NewsView = { company_id: string; context_version: number; checked_at: string; cache_state: string; sources: SourceState[]; items: NewsItem[]; fx: FxObservation[]; limitations: string[]; collection?: NewsCollection };
export type OperatorOverview = { company_id: string; context_version: number; available: boolean; agents: { name: string; role: string }[]; watch_items: unknown[]; attention: unknown[]; limitations: string[] };
export type OperatorProposal = { proposal_id: string; kind: 'watch' | 'recovery' | 'rfq' | 'simulate_email' | 'review_quotes'; summary: string; parameters: Record<string, unknown>; plan_id: string | null; offer_versions: Record<string, number>; context_version: number; expires_at: string };
export type OperatorTurn = { turn_id: string; company_id: string; context_version: number; status: 'completed' | 'proposal' | 'needs_clarification' | 'unavailable'; assistant_text: string; agent_steps: { agent: string; role: string; status: string; model: string; provider_call: string; tool: string; summary: string }[]; evidence_ids: string[]; proposal: OperatorProposal | null; read_only: boolean; session?: DemoSession | null; action_result?: { tool: string; summary: string; simulated?: true; fields_changed?: string[]; view?: 'company' | 'news' | 'procurement' | 'outbox' } | null; analytics?: Record<string, unknown> | null };
export type OperatorApplied = { status: 'applied'; company_id: string; context_version: number; kind: OperatorProposal['kind']; result: unknown; session: DemoSession };
export type VoiceTranscript = { transcript: string; provider: string; model: string; call: 'real'; company_id: string; context_version: number };
export type VoiceSpeech = { audio_base64: string; mime_type: 'audio/mpeg'; provider: 'google_cloud_tts'; call: 'real'; company_id: string; context_version: number };

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

  const withContext = (contextVersion?: number) => contextVersion === undefined ? {} : { expected_context_version: contextVersion };
  const mutate = <T,>(path: string, body: Record<string, unknown>, csrf: string, contextVersion?: number) => request<T>(path, 'POST', { ...body, ...withContext(contextVersion), idempotency_key: key() }, csrf);
  async function download(path: string, failure: string): Promise<Blob> {
    let response: Response;
    try { response = await send(`/api/demo${path}`, { credentials: 'same-origin' }); }
    catch { throw new DemoApiError(`${failure} Check your connection.`); }
    if (!response.ok) throw new DemoApiError(failure, response.status);
    return response.blob();
  }
  return {
    session: () => request<DemoSession>('/session'),
    companies: async (): Promise<CompanyProfile[]> => (await request<{ companies: CompanyProfile[] }>('/companies')).companies,
    company: (csrf: string, companyId: string, contextVersion: number) => mutate<DemoSession>('/company', { company_id: companyId }, csrf, contextVersion),
    buyerDraft: (csrf: string, buyerDraft: BuyerDraft, contextVersion: number) => mutate<DemoSession>('/buyer-draft', { buyer_draft: buyerDraft }, csrf, contextVersion),
    news: () => request<NewsView>('/news'),
    risks: () => request<RiskReport>('/risks'),
    refreshNews: (csrf: string, contextVersion: number) => mutate<NewsView>('/news/refresh', {}, csrf, contextVersion),
    operator: () => request<OperatorOverview>('/operator'),
    operatorTurn: (csrf: string, message: string, contextVersion: number) => mutate<OperatorTurn>('/operator', { message }, csrf, contextVersion),
    confirmOperator: (csrf: string, proposalId: string, contextVersion: number) => mutate<OperatorApplied>('/operator/confirm', { proposal_id: proposalId, reviewed: true }, csrf, contextVersion),
    transcribeVoice: (csrf: string, audioBase64: string, mimeType: 'audio/webm' | 'audio/wav', sampleRateHertz: number, contextVersion: number) =>
      mutate<VoiceTranscript>('/voice/transcribe', { audio_base64: audioBase64, mime_type: mimeType, sample_rate_hertz: sampleRateHertz }, csrf, contextVersion),
    speakVoice: (csrf: string, value: string, contextVersion: number) => mutate<VoiceSpeech>('/voice/speak', { text: value }, csrf, contextVersion),
    fixtures: async (): Promise<DemoFixture[]> => (await request<{ fixtures: DemoFixture[] }>('/fixtures')).fixtures,
    extract: (csrf: string, source: { fixture_id: string; mode: 'gemini' | 'rules' }, contextVersion?: number) => mutate<{ offer: OfferView; provider: OfferView['extractor_detail']; replayed: boolean; served_from_cache: boolean }>('/extract', source, csrf, contextVersion),
    upload: async (csrf: string, file: File, mode: 'gemini' | 'rules', contextVersion?: number) => mutate<{ offer: OfferView; provider: OfferView['extractor_detail']; replayed: boolean; served_from_cache: boolean }>('/extract', { filename: file.name, document_base64: await encode(file), mode }, csrf, contextVersion),
    correct: (csrf: string, id: string, field: string, value: unknown, version: number, contextVersion?: number) => mutate<{ offer: OfferView; replayed: boolean }>(`/offers/${encodeURIComponent(id)}/correct`, { field, value, expected_version: version }, csrf, contextVersion),
    save: (csrf: string, id: string, version: number, contextVersion?: number) => mutate<{ offer: OfferView; replayed: boolean }>(`/offers/${encodeURIComponent(id)}/save`, { expected_version: version }, csrf, contextVersion),
    plan: (csrf: string, planRequest: PlanRequest, contextVersion?: number) => request<PlanView>('/plan', 'POST', { ...planRequest, ...withContext(contextVersion) }, csrf),
    recovery: (csrf: string, body: RecoveryRequest, contextVersion?: number) => mutate<RecoveryView>('/recovery', body, csrf, contextVersion),
    rfq: (csrf: string, body: RfqRequest, contextVersion?: number) => mutate<RfqView>('/rfq', body, csrf, contextVersion),
    downloadRfq: (id: string) => download(`/rfq/${encodeURIComponent(id)}/download`, 'The RFQ draft could not be downloaded.'),
    exportReport: () => download('/export', 'The report could not be downloaded.'),
    reset: (csrf: string, contextVersion?: number) => request<DemoSession>('/reset', 'POST', withContext(contextVersion), csrf),
  };
}
