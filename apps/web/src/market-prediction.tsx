import { useEffect, useState } from 'react';
import { createDemoApi } from './demo-api';
import type { CompanyDecisionPreview, MarketForecastRead } from './demo-api';
import './market-prediction.css';

export type MarketScope = { sessionId: string; companyId: string; contextVersion: number; epoch: number; materialId: string; planId: string | null };
export function acceptMarketResponse(expected: MarketScope, current: MarketScope, preview: CompanyDecisionPreview): boolean {
  return expected.sessionId === current.sessionId && expected.companyId === current.companyId &&
    expected.contextVersion === current.contextVersion && expected.epoch === current.epoch &&
    expected.materialId === current.materialId && expected.planId === current.planId &&
    preview.company_id === expected.companyId && preview.context_version === expected.contextVersion &&
    preview.material_id === expected.materialId;
}

export function MarketEvidence({ market, preview }: { market: MarketForecastRead; preview: CompanyDecisionPreview }) {
  const gap = preview.service_trace?.first_gap_date;
  const blocked = preview.status === 'REPLAN_REQUIRED';
  const intervals = preview.service_trace?.uncovered_intervals ??
    (preview.service_trace?.uncovered_interval ? [preview.service_trace.uncovered_interval] : []);
  const gapDays = intervals.map(({ start, end }) => start === end ? `on ${start}` : `from ${start} through ${end}`).join(', and ');
  return <section className="zoorq-market" aria-label="Market and decision context">
    <span className="demo-kicker">Decision context</span>
    <h3>{blocked ? 'Confirm incoming deliveries' : 'More facts needed before a purchase decision'}</h3>
    <p>{blocked ? 'Quoted arrivals are not confirmed receipts. Confirm incoming supply before relying on this stock-only risk scenario.' :
      'The current workspace cannot verify a safe buy or wait action from the facts on hand.'}</p>
    <div className="zoorq-market-facts">
      <div><span>Market influence</span><strong>{preview.market_influence.status === 'UNSUPPORTED' ? 'No matching model' : preview.market_influence.status === 'CONTEXT_ONLY' ? 'Context only' : preview.market_influence.status.toLowerCase().replaceAll('_', ' ')}</strong></div>
      <div><span>Source session</span><strong>{market.anchor_market_session_date}</strong></div>
    </div>
    <p className="demo-muted">{market.status === 'STALE' ? 'Stale market context' : 'Reference replay'} · {market.forecast_count} market cells · {market.artifact_role_count} model roles. Existing accepted quote prices stay fixed.</p>
    <details><summary>Evidence and missing inputs</summary>
      {blocked && gap && <p>Confirmed-stock scenario: The dated plan has uncovered demand {gapDays || `on ${gap}`}; peak shortfall is {preview.service_trace?.gap_quantity ?? 'unknown'} {preview.service_trace?.unit ?? preview.quantity_unit} under the stated daily assumption. The quote comparison above assumes quoted arrivals.</p>}
      <p>As of {market.as_of_utc}. {preview.market_influence.reason_code.replaceAll('_', ' ').toLowerCase()}.</p>
      {preview.service_trace?.assumption && <p>{preview.service_trace.assumption}</p>}
      {market.delivery_state === 'BUNDLED_REFERENCE_REMOTE_UNAVAILABLE' && <p>Private archive unavailable; bundled dated reference shown.</p>}
      {market.delivery_state === 'LAST_VERIFIED_GCS_REMOTE_UNAVAILABLE' && <p>Private archive refresh failed; last verified dated reference shown.</p>}
      {preview.missing_inputs.length > 0 && <p>Needed: {preview.missing_inputs.map(value => value.replaceAll('_', ' ')).join(', ')}.</p>}
      <p>{market.limitations.join(' ')}</p>
    </details>
  </section>;
}

const api = createDemoApi();
type ForecastCell = { forecast_kind: 'FX_USD_PAYABLE_DISPLAY_ONLY' | 'JOINT_COPPER_FX_LANDED_PROXY'; fx_pair: string; cost_currency: string; horizon_observed_rows: 1 | 3 | 5; direction_label: 'UP' | 'DOWN' | 'ABSTAIN'; direction_target: string };
export function readableForecasts(rows: unknown[]): ForecastCell[] {
  return rows.filter((row): row is ForecastCell => {
    if (!row || typeof row !== 'object') return false;
    const cell = row as Partial<ForecastCell>;
    return (cell.forecast_kind === 'FX_USD_PAYABLE_DISPLAY_ONLY' && cell.direction_target === 'DOMESTIC_USD_PAYABLE_COST_UP' ||
      cell.forecast_kind === 'JOINT_COPPER_FX_LANDED_PROXY' && cell.direction_target === 'DOMESTIC_LANDED_PROXY_COST_UP') &&
      (cell.direction_label === 'UP' || cell.direction_label === 'DOWN' || cell.direction_label === 'ABSTAIN') &&
      typeof cell.fx_pair === 'string' && /^[A-Z]{6}$/.test(cell.fx_pair) && typeof cell.cost_currency === 'string' && /^[A-Z]{3}$/.test(cell.cost_currency) &&
      (cell.horizon_observed_rows === 1 || cell.horizon_observed_rows === 3 || cell.horizon_observed_rows === 5);
  });
}

export function MarketPredictions({ market }: { market: MarketForecastRead }) {
  const rows = readableForecasts(Array.isArray(market.forecasts) ? market.forecasts : []);
  const groups = [
    { kind: 'FX_USD_PAYABLE_DISPLAY_ONLY', title: 'FX · USD payable cost', target: 'Domestic currency cost of a fixed USD payable' },
    { kind: 'JOINT_COPPER_FX_LANDED_PROXY', title: 'Copper + FX · landed proxy', target: 'Domestic currency copper landed cost proxy' },
  ] as const;
  return <section className="zoorq-predictions" aria-label="Market predictions"><div className="zoorq-predictions-head"><span className="demo-kicker">Market reference library</span><h2>Market predictions</h2><p>Source market session {market.anchor_market_session_date} · as of {market.as_of_utc}. Shared cross-market reference data; no company-specific model or executable buy/wait advice.</p><span className="zoorq-predictions-badge">{market.status === 'STALE' ? 'Stale reference' : 'Dated reference'} · {rows.length} readable cells</span></div>
    {rows.length === 0 ? <p role="status">No readable market forecast cells are available for this reference.</p> : groups.map(group => {
      const matching = rows.filter(row => row.forecast_kind === group.kind);
      const pairs = [...new Set(matching.map(row => row.fx_pair))];
      return matching.length > 0 && <section key={group.kind} className="zoorq-predictions-group"><h3>{group.title}</h3><p>Direction target: {group.target}. UP/DOWN describe cost direction, not the FX quote price.</p><div className="zoorq-predictions-table-wrap"><table><thead><tr><th scope="col">FX pair / cost currency</th>{[1, 3, 5].map(horizon => <th key={horizon} scope="col">{horizon} observed {horizon === 1 ? 'session' : 'sessions'}</th>)}</tr></thead><tbody>{pairs.map(pair => <tr key={pair}><th scope="row">{pair}<small>{matching.find(row => row.fx_pair === pair)?.cost_currency} cost</small></th>{[1, 3, 5].map(horizon => { const cell = matching.find(row => row.fx_pair === pair && row.horizon_observed_rows === horizon); return <td key={horizon}>{cell ? <span className={`zoorq-predictions-direction is-${cell.direction_label.toLowerCase()}`}>{cell.direction_label === 'ABSTAIN' ? 'Abstain' : `${cell.direction_label === 'UP' ? 'Cost up' : 'Cost down'}`}</span> : '—'}</td>; })}</tr>)}</tbody></table></div></section>;
    })}
    <details><summary>Evidence and limitations</summary><p>Observed market sessions are not calendar days. Accepted quote prices remain fixed. These direction labels are experimental reference outputs, not prices, likelihood estimates, or purchase instructions.</p>{market.delivery_state && <p>Delivery state: {market.delivery_state.replaceAll('_', ' ').toLowerCase()}.</p>}{market.limitations?.map((limitation, index) => <p key={index}>{limitation}</p>)}</details>
  </section>;
}

export function PredictionsWorkspace({ companyKey }: { companyKey: string }) {
  const [attempt, setAttempt] = useState(0);
  const [market, setMarket] = useState<MarketForecastRead | null>(null);
  const [error, setError] = useState(false);
  useEffect(() => {
    let cancelled = false;
    setMarket(null); setError(false);
    void api.marketForecasts().then(result => { if (!cancelled) setMarket(result); }).catch(() => { if (!cancelled) setError(true); });
    return () => { cancelled = true; };
  }, [companyKey, attempt]);
  if (error) return <section className="zoorq-predictions" role="status"><h2>Market predictions</h2><p>Market reference could not load.</p><button type="button" className="demo-link" onClick={() => setAttempt(value => value + 1)}>Retry predictions</button></section>;
  if (!market) return <section className="zoorq-predictions" role="status"><h2>Market predictions</h2><p>Loading dated market reference…</p></section>;
  return <MarketPredictions market={market} />;
}
export function ScopedMarketContext({ scope, csrf }: { scope: MarketScope; csrf: string }) {
  const [attempt, setAttempt] = useState(0);
  const [result, setResult] = useState<{ market: MarketForecastRead; preview: CompanyDecisionPreview } | null>(null);
  const [error, setError] = useState('');
  useEffect(() => {
    let cancelled = false;
    setResult(null);
    setError('');
    void Promise.all([api.marketForecasts(), api.companyDecisionPreview(csrf, scope.contextVersion, scope.materialId)])
      .then(([market, preview]) => {
        if (!cancelled && acceptMarketResponse(scope, scope, preview)) setResult({ market, preview });
      }).catch(() => { if (!cancelled) setError('Dated market context could not load.'); });
    return () => { cancelled = true; };
  }, [scope.sessionId, scope.companyId, scope.contextVersion, scope.epoch, scope.materialId, scope.planId, csrf, attempt]);
  if (error) return <section className="zoorq-market" role="status"><p>{error}</p><button type="button" className="demo-link" onClick={() => setAttempt(value => value + 1)}>Retry context</button></section>;
  if (!result) return <section className="zoorq-market" role="status">Loading decision context…</section>;
  return <MarketEvidence market={result.market} preview={result.preview} />;
}
