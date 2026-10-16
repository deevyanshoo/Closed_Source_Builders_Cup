import { useState } from 'react';
import { supplierDisplayName } from './demo-display';
import './demo-country-pricing.css';

type Conversion = { currency: string; status: 'same_currency' | 'converted' | 'fx_unavailable'; rate: string | null; amount: string | null; per_unit: string; observation_date: string | null; provider: string | null; formula: string | null };
type Benchmark = { material_id: string; name: string; unit: string; status: 'benchmark'; series: string; month: string; value: string; currency: string; per_unit: string; note: string; age_days: number; stale: boolean; per_company_unit?: { value: string; unit: string; formula: string }; conversions: Conversion[] };
type NoBenchmark = { material_id: string; name: string; unit: string; status: 'no_public_benchmark'; message: string };
type Versus = { status: 'compared' | 'fx_unavailable' | 'no_public_benchmark' | 'unit_not_comparable'; difference_percent: string | null; label: string; series?: string; benchmark_month?: string; benchmark_per_unit?: string; currency?: string; per_unit?: string; formula?: string; reason?: string };
type Quote = { offer_id: string; supplier: string | null; origin_country_code: string | null; material_id: string | null; unit_price: string; currency: string | null; per_unit: string | null; as_of: string | null; synthetic: boolean; document: string | null;
  comparable: { amount: string; currency: string; per_unit: string } | null; vs_benchmark: Versus };
export type CountryPricing = { company_id: string; reporting_currency: string; currencies: string[]; snapshot_version: string; benchmarks_synthetic: false; synthetic_quotes: true;
  source: { publisher: string; title: string; page_url: string; licence: string; retrieved_at: string; source_updated: string };
  benchmarks: (Benchmark | NoBenchmark)[]; quote_note: string; quotes: Quote[]; fx_rates_used: [string, string][]; limitations: string[] };

function versus(v: Versus): string {
  if (v.status === 'compared') return `${v.difference_percent}% vs ${v.series} ${v.benchmark_month} (synthetic quote)`;
  if (v.status === 'no_public_benchmark') return 'No public benchmark';
  if (v.status === 'fx_unavailable') return `Not compared: ${v.reason}`;
  return 'Not compared: units differ';
}

export async function requestCountryPricing(send: typeof fetch = fetch): Promise<CountryPricing> {
  let response: Response;
  try { response = await send('/api/demo/country-pricing', { credentials: 'same-origin' }); }
  catch { throw new Error('The demo service could not be reached. Try again.'); }
  if (!response.ok) throw new Error('Commodity prices could not be loaded.');
  return await response.json() as CountryPricing;
}

export function CountryPricingPanel({ view, onLoad, send = fetch }: { view: CountryPricing | null; onLoad: (value: CountryPricing) => void; send?: typeof fetch }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  async function load() {
    setBusy(true); setError('');
    try { onLoad(await requestCountryPricing(send)); }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'Commodity prices could not be loaded.'); }
    finally { setBusy(false); }
  }
  return <section className="demo-planning demo-pricing" aria-label="Country-wise commodity pricing">
    <div className="demo-heading"><div><span className="demo-kicker">Commodity pricing by country</span><h2>Benchmarks and quotes</h2></div><span className="demo-session">Public benchmark · synthetic quotes</span></div>
    <button type="button" className="demo-button" disabled={busy} onClick={load}>{busy ? 'Loading…' : view ? 'Refresh prices' : 'Show prices'}</button>
    {error && <p className="demo-error" role="alert">{error}</p>}
    {view && <div className="demo-pricing-results">
      <h3>Public benchmarks</h3>
      <p className="demo-muted"><a href={view.source.page_url} target="_blank" rel="noreferrer">{view.source.publisher}</a>, {view.source.title}. Updated {view.source.source_updated}, retrieved {view.source.retrieved_at.slice(0, 10)}. {view.source.licence}. Values unmodified; conversions are computed now from the dated rates shown.</p>
      {view.benchmarks.map(row => row.status === 'no_public_benchmark' ? <details key={row.material_id}><summary><strong>{row.name}</strong> {row.message}</summary></details> :
        <details key={row.material_id} open><summary><strong>{row.name}</strong> {row.series} · {row.month}: {row.value} {row.currency} per {row.per_unit}{row.per_company_unit && <> ({row.per_company_unit.value} {row.currency} per {row.per_company_unit.unit})</>}</summary>
          {row.stale && <p className="demo-pricing-missing">Stale: {row.age_days} days after the benchmark month.</p>}
          <p>{row.note}{row.per_company_unit && <> Unit change: <code>{row.per_company_unit.formula}</code>.</>}</p>
          <ul>{row.conversions.map(c => <li key={c.currency}>{c.status === 'fx_unavailable' ? `${c.currency}: FX unavailable` : c.status === 'same_currency' ? `${c.currency}: ${c.amount} ${c.currency} per ${c.per_unit} (source currency)` : <>{c.amount} {c.currency} per {c.per_unit} · <code>{c.formula}</code></>}</li>)}</ul>
        </details>)}
      <h3>Accepted quotes by origin (synthetic)</h3>
      <p className="demo-pricing-missing" role="note">{view.quote_note}.</p>
      {view.quotes.length ? <div className="demo-table-wrap"><table><thead><tr><th>Supplier</th><th>Origin</th><th>Material</th><th>Quoted price</th><th>Comparable price</th><th>Vs benchmark</th><th>Quote date</th></tr></thead><tbody>{view.quotes.map(q => <tr key={q.offer_id}><td>{supplierDisplayName(q.supplier ?? 'Unknown supplier')}<small>{q.document} · synthetic</small></td><td>{q.origin_country_code ?? 'Not stated'}</td><td>{q.material_id ?? 'Not stated'}</td><td>{q.unit_price} {q.currency} per {q.per_unit}</td><td>{q.comparable ? `${q.comparable.amount} ${q.comparable.currency} per ${q.comparable.per_unit}` : 'Not comparable'}</td><td>{versus(q.vs_benchmark)}</td><td>{q.as_of}</td></tr>)}</tbody></table></div>
        : <p className="demo-muted">No accepted quotes yet. Accept a reviewed quote to list it here.</p>}
      <ul className="demo-muted">{view.limitations.map(item => <li key={item}>{item}</li>)}</ul>
    </div>}
  </section>;
}
