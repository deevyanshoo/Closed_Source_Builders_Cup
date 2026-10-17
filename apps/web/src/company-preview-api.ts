import type { DemoSession, ResearchResult } from './demo-api';

export type CompanyPreviewProposal = { proposal_id: string; kind: 'research'; summary: string; parameters: { query: string; destination: string; shared_data: string }; context_version: number; expires_at: string };
export type CompanyPreviewResult = ResearchResult;
export class CompanyPreviewError extends Error {
  constructor(message: string, readonly status = 0, readonly code = 'unavailable') { super(message); this.name = 'CompanyPreviewError'; }
}

export function createLatestRequest() {
  let generation = 0;
  return { begin: () => ++generation, current: (token: number) => token === generation, cancel: () => { generation += 1; } };
}

export function createCompanyPreviewApi(send: typeof fetch = fetch, key: () => string = () => crypto.randomUUID()) {
  async function post<T>(path: string, session: Pick<DemoSession, 'csrf_token' | 'context_version'>, body: Record<string, unknown>, signal?: AbortSignal): Promise<T> {
    let response: Response;
    try {
      response = await send(`/api/demo/company-preview/${path}`, { method: 'POST', credentials: 'same-origin', signal,
        headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': session.csrf_token },
        body: JSON.stringify({ ...body, expected_context_version: session.context_version, idempotency_key: key() }) });
    } catch (error) {
      if (signal?.aborted) throw error;
      throw new CompanyPreviewError('Public research is unavailable. Check your connection.');
    }
    let data: unknown;
    try { data = await response.json(); } catch { throw new CompanyPreviewError('The research service returned an unreadable response.', response.status); }
    if (!response.ok) {
      const detail = (data && typeof data === 'object' && 'error' in data) ? data.error : data;
      const item = detail && typeof detail === 'object' ? detail as Record<string, unknown> : {};
      throw new CompanyPreviewError(typeof item.message === 'string' ? item.message : 'Public research could not complete.', response.status, typeof item.code === 'string' ? item.code : 'request_failed');
    }
    return data as T;
  }
  return {
    prepare: (csrf: string, companyName: string, contextVersion: number, signal?: AbortSignal) => post<{ proposal: CompanyPreviewProposal }>('prepare', { csrf_token: csrf, context_version: contextVersion }, { company_name: companyName }, signal),
    confirm: (csrf: string, proposalId: string, contextVersion: number, signal?: AbortSignal) => post<{ result: CompanyPreviewResult }>('confirm', { csrf_token: csrf, context_version: contextVersion }, { proposal_id: proposalId, reviewed: true }, signal),
  };
}
