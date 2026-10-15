import { useState } from 'react';
import './demo-carbon.css';

type InputField = { name: string; label: string; unit: string; default?: string; default_source?: string; note?: string };
type Saving = { status: 'calculated' | 'missing_inputs'; amount: string | null; missing?: string[]; currency: string; wording: string; formula: string; arithmetic?: string; inputs: { name: string; value: string; unit: string; source: string }[] };
export type SchemeMatch = { scheme_id: string; country: string; official_name: string; publisher: string; official_url: string; checked_on: string; offers: string; offers_text: string; who: string; numbers_used: string[]; eligibility: string; matched_on: string[]; input_fields: InputField[]; saving: Saving };
export type SchemeResult = { company_id: string; registry_version: string; checked_on: string; label: string; matches: SchemeMatch[]; no_match_reason: string | null };
type Inputs = Record<string, Record<string, string>>;

const OFFERS: Record<string, string> = { credit: 'Tradeable credit', subsidy: 'Subsidy', cheaper_loan: 'Cheaper loan', avoided_cost: 'Avoided cost' };
const sentence = (text: string) => text ? text[0].toUpperCase() + text.slice(1) : text;

export async function requestSchemes(send: typeof fetch, csrf: string, inputs: Inputs, contextVersion?: number): Promise<SchemeResult> {
  const entered = Object.fromEntries(Object.entries(inputs).map(([id, values]) => [id, Object.fromEntries(Object.entries(values).filter(([, v]) => v.trim() !== '').map(([k, v]) => [k, v.trim()]))]));
  let response: Response;
  try {
    response = await send('/api/demo/carbon/schemes', { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
      body: JSON.stringify({ inputs_by_scheme: entered, ...(contextVersion === undefined ? {} : { expected_context_version: contextVersion }) }) });
  } catch { throw new Error('The demo service could not be reached. Try again.'); }
  let body: unknown = null;
  try { body = await response.json(); } catch { /* handled below */ }
  if (!response.ok) {
    const error = body && typeof body === 'object' && 'error' in body ? (body as { error: { message?: unknown } }).error : null;
    throw new Error(typeof error?.message === 'string' ? error.message : 'Schemes could not be listed.');
  }
  return body as SchemeResult;
}

export function SchemesPanel({ csrf, contextVersion, companyName, result, onResult, send = fetch }: { csrf: string; contextVersion?: number; companyName: string; result: SchemeResult | null; onResult: (value: SchemeResult) => void; send?: typeof fetch }) {
  const [inputs, setInputs] = useState<Inputs>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  async function load(next: Inputs) {
    setBusy(true); setError('');
    try { onResult(await requestSchemes(send, csrf, next, contextVersion)); }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'Schemes could not be listed.'); }
    finally { setBusy(false); }
  }
  const value = (id: string, name: string) => inputs[id]?.[name] ?? '';
  const set = (id: string, name: string, text: string) => setInputs(old => ({ ...old, [id]: { ...old[id], [name]: text } }));
  return <section className="demo-planning demo-carbon" aria-label="Carbon schemes and green finance">
    <div className="demo-heading"><div><span className="demo-kicker">Carbon schemes and green finance</span><h2>What might {companyName} use?</h2></div><span className="demo-session">Indicative, not financial or legal advice</span></div>
    <p className="demo-muted">Matches use only the fictional profile's countries, sector and export routes against a dated list of official schemes. Every saving is approximate and uses only the numbers shown.</p>
    <button type="button" className="demo-button" disabled={busy} onClick={() => load(inputs)}>{busy ? 'Checking…' : result ? 'Refresh schemes' : 'Find possible schemes'}</button>
    {error && <p className="demo-error" role="alert">{error}</p>}
    {result && <div className="demo-carbon-results">
      <p className="demo-muted">Registry {result.registry_version}, checked {result.checked_on}. Label: {result.label}.</p>
      {result.no_match_reason && <p>{result.no_match_reason}</p>}
      {result.matches.map(match => <details key={match.scheme_id} open><summary><strong>{match.official_name}</strong> {match.country} · {OFFERS[match.offers] ?? match.offers} · {sentence(match.eligibility)}</summary>
        <p>{match.offers_text}</p><p><strong>Who:</strong> {match.who}</p><p>Matched on: {match.matched_on.join('; ')}</p>
        <ul>{match.numbers_used.map(item => <li key={item}>{item}</li>)}</ul>
        <p><a href={match.official_url} target="_blank" rel="noreferrer">{match.publisher}</a> · checked {match.checked_on}</p>
        <h3>Approximate saving: {match.saving.formula}</h3>
        <form className="demo-carbon-legs" onSubmit={event => { event.preventDefault(); load(inputs); }}>{match.input_fields.map(field => <label key={field.name}><span>{field.label} ({field.unit})</span>
          {field.name === 'enterprise_category' ? <select value={value(match.scheme_id, field.name)} onChange={e => set(match.scheme_id, field.name, e.target.value)}><option value="">Choose</option><option value="micro">Micro</option><option value="small">Small</option><option value="medium">Medium</option></select>
            : <input inputMode="decimal" placeholder={field.default ?? 'Not entered'} value={value(match.scheme_id, field.name)} onChange={e => set(match.scheme_id, field.name, e.target.value)} />}
          {field.default && <small>Default {field.default}: {field.default_source}</small>}{field.note && <small>{field.note}</small>}</label>)}
          <button type="submit" className="demo-link" disabled={busy}>Calculate approximate saving</button></form>
        {match.saving.status === 'calculated' ? <p><strong>{sentence(match.saving.wording)}:</strong> <code>{match.saving.arithmetic}</code>. Inputs: {match.saving.inputs.map(item => `${item.name} ${item.value} ${item.unit} (${item.source})`).join('; ')}.</p>
          : <p className="demo-carbon-missing">Enter {(match.saving.missing ?? []).map(name => match.input_fields.find(field => field.name === name)?.label.toLowerCase() ?? name).join(' and ')} to calculate.</p>}
      </details>)}
    </div>}
  </section>;
}
