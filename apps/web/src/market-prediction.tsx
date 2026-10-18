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
    <h3>{blocked ? 'Replan required' : 'More facts needed before a purchase decision'}</h3>
    <p>{blocked && gap ? `The dated plan has uncovered demand ${gapDays || `on ${gap}`}; peak shortfall is ${preview.service_trace?.gap_quantity ?? 'unknown'} ${preview.service_trace?.unit ?? preview.quantity_unit} under the stated daily assumption.` :
      'The current workspace cannot verify a safe buy or wait action from the facts on hand.'}</p>
    <div className="zoorq-market-facts">
      <div><span>Market influence</span><strong>{preview.market_influence.status === 'UNSUPPORTED' ? 'No matching model' : preview.market_influence.status === 'CONTEXT_ONLY' ? 'Context only' : preview.market_influence.status.toLowerCase().replaceAll('_', ' ')}</strong></div>
      <div><span>Source session</span><strong>{market.anchor_market_session_date}</strong></div>
    </div>
    <p className="demo-muted">{market.status === 'STALE' ? 'Stale market context' : 'Reference replay'} · {market.forecast_count} market cells · {market.artifact_role_count} model roles. Existing accepted quote prices stay fixed.</p>
    <details><summary>Evidence and missing inputs</summary>
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
