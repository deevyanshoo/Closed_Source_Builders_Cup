import { useEffect, useRef, useState } from 'react';
import type { DemoSession } from './demo-api';
import { createCompanyPreviewApi, createLatestRequest, type CompanyPreviewProposal, type CompanyPreviewResult } from './company-preview-api';
import { ResearchOutcome } from './demo-research';
import './company-preview.css';

const api = createCompanyPreviewApi();

export function CompanyResearchPreview({ session }: { session: DemoSession | null }) {
  const [name, setName] = useState('');
  const [proposal, setProposal] = useState<CompanyPreviewProposal | null>(null);
  const [result, setResult] = useState<CompanyPreviewResult | null>(null);
  const [error, setError] = useState('');
  const [pending, setPending] = useState(false);
  const latest = useRef(createLatestRequest());
  const controller = useRef<AbortController | null>(null);
  const sessionKey = `${session?.session_id ?? ''}:${session?.context_version ?? ''}`;
  useEffect(() => {
    latest.current.cancel(); controller.current?.abort();
    setProposal(null); setResult(null); setPending(false); setError('');
    return () => { latest.current.cancel(); controller.current?.abort(); };
  }, [sessionKey]);

  function resetForInput(value: string) {
    latest.current.cancel(); controller.current?.abort();
    setName(value); setProposal(null); setResult(null); setError(''); setPending(false);
  }
  async function prepare() {
    if (!session || pending) return;
    controller.current?.abort(); controller.current = new AbortController();
    const token = latest.current.begin(); setPending(true); setError(''); setProposal(null); setResult(null);
    try {
      const response = await api.prepare(session.csrf_token, name.trim(), session.context_version ?? 1, controller.current.signal);
      if (latest.current.current(token)) setProposal(response.proposal);
    } catch (cause) {
      if (latest.current.current(token)) setError(cause instanceof Error ? cause.message : 'Research preparation failed.');
    } finally { if (latest.current.current(token)) setPending(false); }
  }
  async function confirm() {
    if (!session || !proposal || pending || Date.parse(proposal.expires_at) <= Date.now()) return;
    controller.current?.abort(); controller.current = new AbortController();
    const token = latest.current.begin(); setPending(true); setError('');
    try {
      const response = await api.confirm(session.csrf_token, proposal.proposal_id, session.context_version ?? 1, controller.current.signal);
      if (latest.current.current(token)) { setResult(response.result); setProposal(null); }
    } catch (cause) {
      if (latest.current.current(token)) setError(cause instanceof Error ? cause.message : 'Public research failed.');
    } finally { if (latest.current.current(token)) setPending(false); }
  }
  return <section className="company-preview" aria-labelledby="company-preview-title">
    <p className="company-preview-kicker">Public research</p>
    <h2 id="company-preview-title">Explore your company</h2>
    <p>Enter a company to preview its industry, materials, regions and risks from public sources.</p>
    <details className="company-preview-limits"><summary>What this preview can show</summary><p>Public research is a starting point. It does not establish your inventory, suppliers, contracts or operational state. Source publication dates appear only when verified; the checked time is when research was retrieved.</p></details>
    <form onSubmit={event => { event.preventDefault(); void prepare(); }}>
      <label htmlFor="company-preview-name">Company name</label>
      <div className="company-preview-input-row"><input id="company-preview-name" value={name} onChange={event => resetForInput(event.target.value)} maxLength={100} autoComplete="organization" placeholder="e.g. Tata Steel" disabled={!session} />
      <button type="submit" disabled={!session || pending || name.trim().length < 2}>Prepare public query</button></div>
    </form>
    {!session && <p role="status">Workspace is opening...</p>}
    {proposal && <div className="company-preview-consent"><h3>Review before searching</h3><p>Exact public query sent to Google Search via Vertex AI:</p><blockquote>{proposal.parameters.query}</blockquote><p>{proposal.parameters.shared_data} No workspace data is included. Consent expires {new Date(proposal.expires_at).toLocaleString()}.</p><button type="button" onClick={() => void confirm()} disabled={pending || Date.parse(proposal.expires_at) <= Date.now()}>I consent · research public sources</button></div>}
    {pending && <p role="status">{proposal ? 'Researching public sources…' : 'Preparing consent…'}</p>}
    {error && <p role="alert">{error}</p>}
    {result && <CompanyPreviewOutcome result={result} onReset={() => resetForInput(name)} />}
  </section>;
}

export function CompanyPreviewOutcome({ result, onReset }: { result: CompanyPreviewResult; onReset: () => void }) {
  return <div className="company-preview-result" aria-live="polite">
    <ResearchOutcome result={result} />
    <p><strong>Next fields needed for operations:</strong> reviewed materials, locations, supplier offers, stock, demand and delivery dates.</p>
    <button type="button" onClick={onReset}>Prepare a new query</button>
  </div>;
}
