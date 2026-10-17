import { useEffect, useRef, useState } from 'react';
import './demo-price-outlook.css';

type Backtest = { split: string; selected_model: string; n_origins: number | null; mape: number | null; naive_mape: number | null; mape_ratio: number | null; directional_acc: number | null; coverage: number | null; naive_coverage: number | null; skill: number | null; bootstrap_ci_lower: number | null; bootstrap_ci_upper: number | null };
type Horizon = { horizon: number; target_month: string; status: 'PREDICTION' | 'RANGE_ONLY' | 'NO_RELIABLE_FORECAST'; label: string; model: string; p10: number | null; p50: number | null; p90: number | null; naive_value: number; backtest: Backtest };
export type OutlookSeries = { series_id: string; kind: string; unit: string; source_series: string; data_date: string; latest_value: number; issue_month: string; freshness_state: string; age_days: number; stale: boolean; stale_label: string | null;
  source: { publisher: string; url: string; licence: string; attribution: string; retrieved_at: string }; selection: { selected_model: string }; horizons: Horizon[] };
export type PriceOutlook = { kind: 'price'; company_id: string; context_version: number | null; material_id: string; label: string; status: 'available' | 'no_series' | 'unknown_material' | 'unknown_company'; match?: 'exact' | 'proxy'; note?: string; message?: string; series?: OutlookSeries };
export type FxOutlook = { kind: 'fx'; company_id: string; context_version: number | null; label: string; status: string; reporting_currency?: string;
  currencies: ({ currency: string; status: 'available'; series: OutlookSeries } | { currency: string; status: 'no history available'; reason: string })[] };
export type OutlookOwner = { sessionId: string; companyId: string; contextVersion: number; generation: number };
type Money = { amount: string; display: string; change?: string; change_display?: string; change_percent?: string };
type BudgetHorizon = { horizon: number; target_month: string; status: Horizon['status']; label: string; reason?: string; low?: Money; mid?: Money; high?: Money;
  hit_rate?: { value: string; basis: string; test_origins: number } };
export type BudgetAtRisk = { kind: 'budget'; company_id: string; context_version: number | null; plan_id: string | null; material_id: string; status: string; reason?: string; currency?: string; label?: string;
  match?: 'exact' | 'proxy'; note?: string; quantity?: { value: string; unit: string; benchmark_quantity?: string; benchmark_unit?: string }; data_date?: string; today?: Money; horizons: BudgetHorizon[];
  fx?: { rate: string; quote: string; month: string; series_id: string; source: string; publisher: string; url: string } | null; fx_note?: string | null };
export type BudgetOwner = OutlookOwner & { planId: string | null };

const NO_RELIABLE = 'No reliable forecast: the model does not beat a simple baseline on past data';
const number = new Intl.NumberFormat('en-US', { maximumSignificantDigits: 5 });  // display only; the export keeps full values
const pct = (x: number | null) => x == null ? 'n/a' : `${(x * 100).toFixed(1)}%`;

/** A reply is shown only if it was requested for the current session, company, context and generation. */
export function acceptOutlook(owner: OutlookOwner, current: OutlookOwner, reply: { company_id: string; context_version: number | null }): boolean {
  return owner.sessionId === current.sessionId && owner.companyId === current.companyId && owner.contextVersion === current.contextVersion
    && owner.generation === current.generation && reply.company_id === current.companyId && reply.context_version === current.contextVersion;
}

/** A budget reply must also belong to the plan the page is showing; a re-run comparison drops the old figure. */
export function acceptBudget(owner: BudgetOwner, current: BudgetOwner, reply: BudgetAtRisk): boolean {
  return acceptOutlook(owner, current, reply) && owner.planId === current.planId && (reply.plan_id === current.planId || reply.plan_id === null);
}

async function read<T>(send: typeof fetch, url: string): Promise<T> {
  let response: Response;
  try { response = await send(url, { credentials: 'same-origin' }); }
  catch { throw new Error('The demo service could not be reached. Try again.'); }
  if (!response.ok) throw new Error('The price outlook could not be loaded.');
  return await response.json() as T;
}
export const requestPriceOutlook = (send: typeof fetch, materialId: string) => read<PriceOutlook>(send, `/api/demo/price-outlook?material_id=${encodeURIComponent(materialId)}`);
export const requestFxOutlook = (send: typeof fetch) => read<FxOutlook>(send, '/api/demo/fx-outlook');
export const requestBudgetAtRisk = (send: typeof fetch, materialId: string, planId: string | null) =>
  read<BudgetAtRisk>(send, `/api/demo/budget-at-risk?material_id=${encodeURIComponent(materialId)}${planId ? `&plan_id=${encodeURIComponent(planId)}` : ''}`);

export function priceOutlookExport(price: PriceOutlook, fx: FxOutlook, budget: BudgetAtRisk | null = null) {
  if ([fx, budget].some(view => view && (view.company_id !== price.company_id || view.context_version !== price.context_version))) throw new Error('price, FX and budget outlook scope differ');
  return { schema: 'price-outlook-export.v1', company_id: price.company_id, context_version: price.context_version, label: price.label, price, fx, budget };
}

const signed = (text: string) => text.startsWith('-') ? text : `+${text}`;
const change = (m: Money) => `${signed(m.change_display ?? '')} (${signed(m.change_percent ?? '')}%)`;

function BudgetBlock({ budget }: { budget: BudgetAtRisk }) {
  const c = budget.currency ?? '';
  return <div className="zoorq-outlook-budget" aria-label="Budget at risk for this purchase">
    <h4>Budget at risk for this purchase</h4>
    {budget.status !== 'available' || !budget.today || !budget.quantity
      ? <p className="zoorq-outlook-muted">{budget.reason}</p>
      : <>
        <p className="zoorq-outlook-budget-today">Benchmark cost today for {budget.quantity.value} {budget.quantity.unit} {budget.material_id}: <strong>{c} {budget.today.display}</strong><small>at the {budget.data_date} benchmark</small></p>
        <div className="zoorq-outlook-table"><table><thead><tr><th>Horizon</th><th>Benchmark cost range</th><th>Change from today</th><th>Range held (untouched final 24 months)</th></tr></thead>
          <tbody>{budget.horizons.map(h => h.low && h.mid && h.high && h.hit_rate
            ? <tr key={h.horizon}><td>{h.horizon} mo<small>{h.target_month}</small></td><td><strong>{c} {h.low.display} to {c} {h.high.display}</strong><small>middle {c} {h.mid.display} · {h.label}</small></td>
              <td>{change(h.low)} to {change(h.high)}</td><td>range held {Math.round(Number(h.hit_rate.value) * 100)}% of the time on {h.hit_rate.test_origins} test months ({h.hit_rate.basis})</td></tr>
            : <tr key={h.horizon}><td>{h.horizon} mo<small>{h.target_month}</small></td><td colSpan={3}>{h.reason}</td></tr>)}</tbody></table></div>
        {budget.fx && <p className="zoorq-outlook-muted">FX: {budget.fx.rate} {budget.fx.quote} ({budget.fx.month}, {budget.fx.publisher}). {budget.fx_note}</p>}
      </>}
    {budget.match === 'proxy' && budget.note && <p className="zoorq-outlook-muted">Proxy benchmark: {budget.note}</p>}
    {budget.label && <p className="zoorq-outlook-budget-label">{budget.label}</p>}
  </div>;
}

function HorizonRow({ h, unit }: { h: Horizon; unit: string }) {
  const b = h.backtest;
  const value = h.status === 'PREDICTION' && h.p10 != null && h.p90 != null
    ? <><strong>P10 {number.format(h.p10)} · P50 {number.format(h.p50!)} · P90 {number.format(h.p90)} {unit}</strong><small>{h.label}</small></>
    : h.status === 'RANGE_ONLY' && h.p10 != null && h.p90 != null
      ? <><strong>Range {number.format(h.p10)} to {number.format(h.p90)} {unit}</strong><small>{h.label}; latest {number.format(h.naive_value)}</small></>
      : <><strong>{NO_RELIABLE}.</strong><small>Naive baseline, not a prediction: {number.format(h.naive_value)} {unit}</small></>;
  const score = b.selected_model !== 'naive'
    ? `${b.selected_model} MAPE ${pct(b.mape)} vs naive ${pct(b.naive_mape)} · coverage ${pct(b.coverage)} · skill ${b.skill == null ? 'n/a' : b.skill.toFixed(2)} · ${b.n_origins} test origins`
    : `naive selected · naive MAPE ${pct(b.naive_mape)} · naive band coverage ${pct(b.naive_coverage)} · ${b.n_origins} test origins`;
  return <tr><td>{h.horizon} mo<small>{h.target_month}</small></td><td data-status={h.status}>{h.status.replaceAll('_', ' ').toLowerCase()}</td><td>{value}</td><td>{score}</td></tr>;
}

function SeriesBlock({ title, series, note }: { title: string; series: OutlookSeries; note?: string }) {
  return <div className="zoorq-outlook-series">
    <h4>{title}</h4>
    <p className="zoorq-outlook-meta">{series.source_series} · Issued {series.issue_month} from data through {series.data_date} · {series.freshness_state === 'HISTORICAL_NOT_CURRENT' ? 'historical, not current' : 'issue month is this month'}{note ? ` · ${note}` : ''}</p>
    {series.stale_label && <p className="zoorq-outlook-stale" role="status">{series.stale_label}</p>}
    <div className="zoorq-outlook-table"><table><thead><tr><th>Horizon</th><th>Status</th><th>Outlook</th><th>Backtest (untouched final 24 months)</th></tr></thead>
      <tbody>{series.horizons.map(h => <HorizonRow key={h.horizon} h={h} unit={series.unit} />)}</tbody></table></div>
    <p className="zoorq-outlook-source"><a href={series.source.url} target="_blank" rel="noreferrer">{series.source.publisher}</a> · {series.source.licence} · retrieved {series.source.retrieved_at.slice(0, 10)}</p>
  </div>;
}

export function PriceOutlookView({ price, fx, budget = null }: { price: PriceOutlook | null; fx: FxOutlook | null; budget?: BudgetAtRisk | null }) {
  return <div className="zoorq-outlook-body">
    <p className="zoorq-outlook-label">{price?.label ?? fx?.label ?? 'Projection with uncertainty, not a recommendation'}</p>
    {budget && <BudgetBlock budget={budget} />}
    {price && (price.status === 'available' && price.series
      ? <SeriesBlock title={`Benchmark price: ${price.material_id}${price.match === 'proxy' ? ' (proxy series)' : ''}`} series={price.series} note={price.note} />
      : <p className="zoorq-outlook-muted">{price.message ?? 'No benchmark outlook for this material.'}</p>)}
    {fx && fx.currencies.map(row => row.status === 'available'
      ? <SeriesBlock key={row.currency} title={`Exchange rate: ${row.currency} per USD`} series={row.series} />
      : <p key={row.currency} className="zoorq-outlook-muted">{row.currency}: no history available. {row.reason}</p>)}
  </div>;
}

export function PriceOutlookPanel({ scope, materialId, planId = null, send = fetch }: { scope: Omit<OutlookOwner, 'generation'>; materialId: string; planId?: string | null; send?: typeof fetch }) {
  const generation = useRef(0);
  const current = useRef<BudgetOwner>({ ...scope, generation: 0, planId });
  const [price, setPrice] = useState<PriceOutlook | null>(null);
  const [fx, setFx] = useState<FxOutlook | null>(null);
  const [budget, setBudget] = useState<BudgetAtRisk | null>(null);
  const [error, setError] = useState('');
  useEffect(() => {
    const owner = { ...scope, generation: ++generation.current, planId };
    current.current = owner;
    setPrice(null); setFx(null); setBudget(null); setError('');
    Promise.all([requestPriceOutlook(send, materialId), requestFxOutlook(send), requestBudgetAtRisk(send, materialId, planId)])
      .then(([p, f, b]) => { if (acceptOutlook(owner, current.current, p) && acceptOutlook(owner, current.current, f) && acceptBudget(owner, current.current, b)) { setPrice(p); setFx(f); setBudget(b); } })
      .catch(reason => { if (owner.generation === current.current.generation) setError(reason instanceof Error ? reason.message : 'The price outlook could not be loaded.'); });
  }, [scope.sessionId, scope.companyId, scope.contextVersion, materialId, planId, send]);
  return <section className="zoorq-outlook" aria-label="Price outlook">
    <div className="zoorq-outlook-head"><span>Market reference</span><h3>Price outlook</h3></div>
    {error ? <p role="alert">{error}</p> : price || fx ? <PriceOutlookView price={price} fx={fx} budget={budget} /> : <p className="zoorq-outlook-muted">Loading the dated outlook…</p>}
  </section>;
}
