import { useState } from 'react';
import './demo-carbon.css';

type InputField = { name: string; label: string; unit: string; note?: string };
type Benefit = { kind: string; amount: string; currency: string; direction?: 'interest_reduction' | 'extra_cost' | 'no_difference'; label: string; formula: string; arithmetic: string; inputs: { name: string; value: string; unit: string; source: string }[] };
export type SchemeMatch = { scheme_id: string; country: string; official_name: string; publisher: string; official_url: string; checked_on: string; offers: string; offers_text: string; who: string; numbers_used: string[];
  eligibility: string; matched_on: string[]; benefit_status: 'available' | 'unavailable'; benefit_reason: string | null; benefit_label: string | null; benefit_formula: string | null; input_fields: InputField[]; missing_inputs: string[]; benefit: Benefit | null };
export type SchemeResult = { company_id: string; registry_version: string; checked_on: string; label: string; matches: SchemeMatch[]; not_verified_countries?: Record<string, string>; no_match_reason: string | null };
type Inputs = Record<string, Record<string, string>>;

const OFFERS: Record<string, string> = { certificates: 'Carbon certificates', subsidy: 'Subsidy', green_financing: 'Green financing', compliance_exposure: 'Possible compliance exposure' };
const DIRECTION: Record<string, string> = { interest_reduction: 'Interest reduction', extra_cost: 'Extra cost', no_difference: 'No difference' };
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
    <p className="demo-muted">Matches use only the fictional profile's countries, sector and export routes against a dated list of official schemes. Every figure is indicative and uses only the numbers shown; unsupported amounts are not calculated.</p>
    <button type="button" className="demo-button" disabled={busy} onClick={() => load(inputs)}>{busy ? 'Checking…' : result ? 'Refresh schemes' : 'Find possible schemes'}</button>
    {error && <p className="demo-error" role="alert">{error}</p>}
    {result && <div className="demo-carbon-results">
      <p className="demo-muted">Registry {result.registry_version}, checked {result.checked_on}. Label: {result.label}.</p>
      {result.no_match_reason && <p>{result.no_match_reason}</p>}
      {Object.entries(result.not_verified_countries ?? {}).map(([country, why]) => <p key={country} className="demo-muted">{country}: {why}</p>)}
      {result.matches.map(match => <details key={match.scheme_id} open><summary><strong>{match.official_name}</strong> {match.country} · {OFFERS[match.offers] ?? match.offers} · {sentence(match.eligibility)}</summary>
        <p>{match.offers_text}</p><p><strong>Who:</strong> {match.who}</p><p>Matched on: {match.matched_on.join('; ')}</p>
        <ul>{match.numbers_used.map(item => <li key={item}>{item}</li>)}</ul>
        <p><a href={match.official_url} target="_blank" rel="noreferrer">{match.publisher}</a> · checked {match.checked_on}</p>
        {match.benefit_status === 'unavailable' ? <p className="demo-carbon-missing">Benefit amount unavailable: {match.benefit_reason}</p> : <>
          <h3>{sentence(match.benefit_label ?? '')}: {match.benefit_formula}</h3>
          <form className="demo-carbon-legs" onSubmit={event => { event.preventDefault(); load(inputs); }}>{match.input_fields.map(field => <label key={field.name}><span>{field.label} ({field.unit})</span>
            <input inputMode={field.name === 'import_quarter' ? undefined : 'decimal'} placeholder="Not entered" value={value(match.scheme_id, field.name)} onChange={e => set(match.scheme_id, field.name, e.target.value)} />
            {field.note && <small>{field.note}</small>}</label>)}
            <button type="submit" className="demo-link" disabled={busy}>Calculate</button></form>
          {match.benefit ? <p className="demo-scheme-result">{`${match.benefit.direction ? DIRECTION[match.benefit.direction] : sentence(match.benefit.label)}: ${match.benefit.arithmetic}`}{match.benefit.direction && ` (${match.benefit.label})`}.</p>
            : <p className="demo-carbon-missing">Enter {match.missing_inputs.map(name => match.input_fields.find(field => field.name === name)?.label.toLowerCase() ?? name).join(' and ')} to see the {match.benefit_label}.</p>}
        </>}
      </details>)}
    </div>}
  </section>;
}
