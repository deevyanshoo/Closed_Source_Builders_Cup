import { useEffect, useRef, useState } from 'react';
import './demo-export-opportunities.css';

type Status<T> = { status: string; reason?: string; year?: number | null } & Partial<Omit<T, 'year'>>;
export type ExportMarket = { code: number; name: string; years_reported: number[]; years_not_reported: number[]; existing_export_market: boolean;
  import_value: Status<{ value_usd: string; year: number; basis: string; source: string }>;
  unit_value: Status<{ usd_per_kg: string; year: number; basis: string; net_weight_kg: string; label: string }>;
  growth: Status<{ cagr: string; from_year: number; to_year: number; years_used: number[] }>;
  home_share: Status<{ share: string; year: number; basis: string }>;
  mirror: { value_usd: string; year: number; label: string; meaning: string } | null; margin: MarginResult | null };
export type ExportOpportunities = { kind: 'export_opportunities'; company_id: string; context_version: number | null; status: string; label: string; sort: string; sorts?: string[];
  home_country?: string; reporting_currency?: string; candidate_discovery?: string; markets: ExportMarket[];
  product?: { product: string; hs_heading: string; hs_description: string; note: string; reason: string };
  source?: { provider: string; attribution: string; use_policy: string; terms_summary: string; endpoint: string; years: number[]; built_on: string; hs_revision: string } };
type MarginInput = { name: string; label: string; value: string | null; source: string };
export type MarginResult = { status: 'available' | 'unavailable'; label: string; currency: string; basis?: string; inputs: MarginInput[]; missing: string[]; notes: string[];
  fx: { rate: string; quote: string; month: string; publisher: string; url: string } | null; margin_per_kg?: string; volume_kg?: string; contribution?: string; formula?: string; arithmetic?: string[] };
export type ExportMargin = { company_id: string; context_version: number | null; market_code: number; market_name: string; margin: MarginResult };
export type ExportOwner = { sessionId: string; companyId: string; contextVersion: number; generation: number };

const SORT_LABELS: Record<string, string> = { market_size: 'Market size', growth: 'Growth', home_share: 'Home-country share', margin: 'Margin scenario' };
const INPUTS: [string, string][] = [['unit_cost_per_kg', 'unit production cost ({c} per kg)'], ['freight_insurance_per_kg', 'freight and insurance to this market ({c} per kg)'],
  ['tariff_rate_percent', 'tariff rate on the customs value (percent)'], ['capacity_kg_per_year', 'available export capacity (kg per year)'], ['target_share_percent', "target share of this market's import volume (percent)"]];
const usd = new Intl.NumberFormat('en-US', { maximumFractionDigits: 0 });
const money = new Intl.NumberFormat('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const pct = (text: string, digits: number) => `${(Number(text) * 100).toFixed(digits)}%`;

/** A reply is shown only if it was requested for the current session, company, context and generation. */
export function acceptExports(owner: ExportOwner, current: ExportOwner, reply: { company_id: string; context_version: number | null }): boolean {
  return owner.sessionId === current.sessionId && owner.companyId === current.companyId && owner.contextVersion === current.contextVersion
    && owner.generation === current.generation && reply.company_id === current.companyId && reply.context_version === current.contextVersion;
}

async function readJson<T>(response: Response, fallback: string): Promise<T> {
  const data = await response.json().catch(() => null) as ({ error?: { message?: string } } & T) | null;
  if (!response.ok) throw new Error(data?.error?.message ?? fallback);
  return data as T;
}

export async function requestExportOpportunities(send: typeof fetch, sort: string): Promise<ExportOpportunities> {
  let response: Response;
  try { response = await send(`/api/demo/export-opportunities?sort=${encodeURIComponent(sort)}`, { credentials: 'same-origin' }); }
  catch { throw new Error('The demo service could not be reached. Try again.'); }
  return readJson<ExportOpportunities>(response, 'Export opportunities could not be loaded.');
}

export async function requestExportMargin(send: typeof fetch, csrf: string, payload: { market_code: number; inputs: Record<string, string>; expected_context_version: number }): Promise<ExportMargin> {
  let response: Response;
  try { response = await send('/api/demo/export-opportunities/margin', { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf }, body: JSON.stringify(payload) }); }
  catch { throw new Error('The demo service could not be reached. Try again.'); }
  return readJson<ExportMargin>(response, 'The margin scenario could not be calculated.');
}

/** Margin ordering uses the scenarios entered in this panel; other sorts keep the server order. */
export function orderMarkets(markets: ExportMarket[], sort: string, margins: Record<number, ExportMargin>): ExportMarket[] {
  if (sort !== 'margin') return markets;
  const value = (m: ExportMarket) => { const r = margins[m.code]?.margin; return r?.status === 'available' && r.contribution != null ? Number(r.contribution) : null; };
  return [...markets].sort((a, b) => { const x = value(a), y = value(b);
    if (x === null || y === null) return x === y ? a.name.localeCompare(b.name) : x === null ? 1 : -1;
    return y - x || a.name.localeCompare(b.name); });
}

function MarketRow({ m }: { m: ExportMarket }) {
  const iv = m.import_value, uv = m.unit_value, g = m.growth, s = m.home_share;
  return <tr>
    <td><strong>{m.name}</strong>{m.existing_export_market && <small className="zoorq-exports-tag">Existing export route</small>}</td>
    <td>{iv.status === 'available' && iv.value_usd ? <>USD {usd.format(Number(iv.value_usd))}<small>{iv.year} · {iv.basis} · {iv.source}</small></> : <>Not reported<small>{iv.reason}</small></>}
      {m.mirror && <small className="zoorq-exports-mirror">{m.mirror.label}: {m.mirror.meaning}, USD {usd.format(Number(m.mirror.value_usd))} ({m.mirror.year})</small>}</td>
    <td>{g.status === 'available' && g.cagr ? <>{pct(g.cagr, 1)} a year, {g.from_year} to {g.to_year}</> : <small>{g.reason}</small>}</td>
    <td>{s.status === 'available' && s.share ? <>{pct(s.share, 2)} ({s.year}, {s.basis})</> : <small>{s.reason}</small>}</td>
    <td>{uv.status === 'available' && uv.usd_per_kg ? <>{Number(uv.usd_per_kg).toFixed(4)} USD/kg ({uv.basis}, {uv.year})<small>{uv.label}</small></> : <small>Unit value suppressed: {uv.reason}</small>}</td>
    <td><small>reported: {m.years_reported.length ? m.years_reported.join(', ') : 'none'}</small>{m.years_not_reported.length > 0 && <small>not reported: {m.years_not_reported.join(', ')}</small>}</td>
  </tr>;
}

function MarginBlock({ reply }: { reply: ExportMargin }) {
  const r = reply.margin;
  return <div className="zoorq-exports-result" role="status">
    {r.status === 'available'
      ? <><p className="zoorq-exports-figure">Contribution: {r.currency} {money.format(Number(r.contribution))}<small>Margin per kg: {r.currency} {money.format(Number(r.margin_per_kg))} · volume {usd.format(Number(r.volume_kg))} kg · {r.basis} basis</small></p>
        <p className="zoorq-exports-muted">{r.formula}</p>
        <ol className="zoorq-exports-arithmetic">{r.arithmetic?.map(line => <li key={line}>{line}</li>)}</ol>
        {r.fx && <p className="zoorq-exports-muted">FX {r.fx.rate} {r.fx.quote} ({r.fx.month}, {r.fx.publisher}), held at that month.</p>}</>
      : <p>No margin figure. Missing: {r.missing.join(', ')}</p>}
    {r.notes.map(note => <p key={note} className="zoorq-exports-muted">{note}</p>)}
    <p className="zoorq-exports-label">{r.label}</p>
  </div>;
}

export function ExportOpportunitiesView({ view, margins, selected, values, busy, error, onSort, onSelect, onValue, onSubmit }: {
  view: ExportOpportunities; margins: Record<number, ExportMargin>; selected: number | null; values: Record<string, string>; busy: boolean; error: string;
  onSort: (sort: string) => void; onSelect: (code: number) => void; onValue: (name: string, value: string) => void; onSubmit: () => void }) {
  if (view.status !== 'available' || !view.product) return <p className="zoorq-exports-muted">No export ranking is available for this company.</p>;
  const c = view.reporting_currency ?? '';
  const markets = orderMarkets(view.markets, view.sort, margins);
  const chosen = view.markets.find(m => m.code === selected) ?? view.markets[0];
  return <div className="zoorq-exports-body">
    <p className="zoorq-exports-product"><strong>{view.product.product}</strong> · HS {view.product.hs_heading} · {view.product.hs_description}<small>{view.product.note}. Home country: {view.home_country}.</small></p>
    <p className="zoorq-exports-label">{view.label}</p>
    <label className="zoorq-exports-sort"><span>Sort by</span><select value={view.sort} onChange={e => onSort(e.target.value)}>
      {(view.sorts ?? Object.keys(SORT_LABELS)).map(s => <option key={s} value={s}>{SORT_LABELS[s] ?? s}</option>)}</select></label>
    {view.candidate_discovery === 'mirror_importers' && <p className="zoorq-exports-muted">{view.home_country} reported no exports of this heading in these years; candidate markets come from their reported imports from {view.home_country} (mirror data).</p>}
    <div className="zoorq-exports-table"><table><thead><tr><th>Market</th><th>Imports of this heading</th><th>Growth</th><th>{view.home_country} share</th><th>Unit value</th><th>Data years</th></tr></thead>
      <tbody>{markets.map(m => <MarketRow key={m.code} m={m} />)}</tbody></table></div>
    {chosen && <form className="zoorq-exports-form" onSubmit={e => { e.preventDefault(); onSubmit(); }}>
      <h4>Margin scenario</h4>
      <label><span>Market</span><select value={chosen.code} onChange={e => onSelect(Number(e.target.value))}>{view.markets.map(m => <option key={m.code} value={m.code}>{m.name}</option>)}</select></label>
      <p className="zoorq-exports-muted">All inputs are yours; nothing is filled in. Leave the tariff empty if you have not checked the official schedule.</p>
      <div className="zoorq-exports-inputs">{INPUTS.map(([name, label]) => <label key={name}><span>{label.replace('{c}', c)}</span>
        <input inputMode="decimal" value={values[name] ?? ''} onChange={e => onValue(name, e.target.value)} /></label>)}</div>
      <button className="demo-button" disabled={busy}>{busy ? 'Calculating…' : 'Calculate scenario'}</button>
      {error && <p role="alert">{error}</p>}
      {margins[chosen.code] && <MarginBlock reply={margins[chosen.code]} />}
    </form>}
    {view.source && <p className="zoorq-exports-source">{view.source.attribution} <a href={view.source.use_policy} target="_blank" rel="noreferrer">Use and re-dissemination terms</a>. {view.source.terms_summary} {view.source.hs_revision} · annual data {view.source.years[0]} to {view.source.years[view.source.years.length - 1]} · snapshot built {view.source.built_on}.</p>}
  </div>;
}

export function ExportOpportunitiesPanel({ scope, csrfToken, send = fetch }: { scope: Omit<ExportOwner, 'generation'>; csrfToken: string; send?: typeof fetch }) {
  const generation = useRef(0);
  const current = useRef<ExportOwner>({ ...scope, generation: 0 });
  const [sort, setSort] = useState('market_size');
  const [view, setView] = useState<ExportOpportunities | null>(null);
  const [margins, setMargins] = useState<Record<number, ExportMargin>>({});
  const [selected, setSelected] = useState<number | null>(null);
  const [values, setValues] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => { setMargins({}); setSelected(null); setValues({}); }, [scope.sessionId, scope.companyId, scope.contextVersion]);
  useEffect(() => {
    const owner = { ...scope, generation: ++generation.current };
    current.current = owner;
    setError('');
    requestExportOpportunities(send, sort === 'margin' ? 'market_size' : sort)
      .then(reply => { if (acceptExports(owner, current.current, reply)) setView({ ...reply, sort }); })
      .catch(reason => { if (owner.generation === current.current.generation) setError(reason instanceof Error ? reason.message : 'Export opportunities could not be loaded.'); });
  }, [scope.sessionId, scope.companyId, scope.contextVersion, sort, send]);
  const submit = () => {
    if (!view) return;
    const code = selected ?? view.markets[0]?.code;
    const owner = current.current;
    setBusy(true); setError('');
    const inputs = Object.fromEntries(Object.entries(values).filter(([, v]) => v.trim() !== ''));
    requestExportMargin(send, csrfToken, { market_code: code, inputs, expected_context_version: scope.contextVersion })
      .then(reply => { if (acceptExports(owner, current.current, reply)) setMargins(old => ({ ...old, [reply.market_code]: reply })); })
      .catch(reason => { if (owner.generation === current.current.generation) setError(reason instanceof Error ? reason.message : 'The margin scenario could not be calculated.'); })
      .finally(() => setBusy(false));
  };
  return <section className="zoorq-exports" aria-label="Export opportunities">
    <div className="zoorq-exports-head"><span>Export opportunities</span><h3>Where does this product already trade?</h3></div>
    {error && !view ? <p role="alert">{error}</p> : view ? <ExportOpportunitiesView view={view} margins={margins} selected={selected} values={values} busy={busy} error={error}
      onSort={setSort} onSelect={code => setSelected(code)} onValue={(name, value) => setValues(old => ({ ...old, [name]: value }))} onSubmit={submit} />
      : <p className="zoorq-exports-muted">Loading the dated trade snapshot…</p>}
  </section>;
}
