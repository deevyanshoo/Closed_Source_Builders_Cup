import { useEffect, useRef, useState } from 'react';
import { ActivityIndicator } from './demo-activity';
import { createDemoApi } from './demo-api';
import type { RiskAlert, RiskReport } from './demo-api';

const api = createDemoApi();
export type RiskScope = { sessionId: string; companyId: string; contextVersion: number; epoch: number };
export const sameRiskScope = (a: RiskScope, b: RiskScope) => a.sessionId === b.sessionId && a.companyId === b.companyId && a.contextVersion === b.contextVersion && a.epoch === b.epoch;
export const acceptsRiskReport = (report: RiskReport, captured: RiskScope, current: RiskScope) =>
  sameRiskScope(captured, current) && report.company_id === captured.companyId && report.context_version === captured.contextVersion && Array.isArray(report.companies);

export function startRiskPolling(scope: RiskScope, current: () => RiskScope, read: () => Promise<RiskReport>, onReport: (report: RiskReport) => void,
  onError: (error: unknown) => void, visible: () => boolean,
  onVisibility: (listener: () => void) => () => void): () => void {
  let active = true, pending = false;
  const scan = () => {
    if (!active || pending || !visible() || !sameRiskScope(scope, current())) return;
    pending = true;
    read().then(report => {
      if (active && acceptsRiskReport(report, scope, current())) onReport(report);
      else if (active && sameRiskScope(scope, current())) onError(new Error('The risk report did not match this workspace.'));
    }).catch(error => { if (active && sameRiskScope(scope, current())) onError(error); })
      .finally(() => { pending = false; });
  };
  const removeVisibility = onVisibility(() => { if (visible()) scan(); });
  const timer = setInterval(scan, 60_000);
  scan();
  return () => { active = false; clearInterval(timer); removeVisibility(); };
}

const safeHref = (value: string | null) => {
  if (!value) return null;
  try { const url = new URL(value); return url.protocol === 'https:' && !url.username && !url.password ? url.href : null; }
  catch { return null; }
};
const errorText = (error: unknown) => error instanceof Error ? error.message : 'The risk monitor could not refresh.';
const provenanceLabel = (value: RiskAlert['provenance']) => value === 'observed_news' ? 'Observed public news · conditional exposure' : 'Synthetic operational assumption';

export function RiskMonitor({ scope, initialReport = null, onReport, onOpenCompany, onSeedPrompt }: {
  scope: RiskScope; initialReport?: RiskReport | null; onReport: (report: RiskReport, scope: RiskScope) => void;
  onOpenCompany: (companyId: string) => void; onSeedPrompt: (message: string, companyId: string, scope: RiskScope) => void;
}) {
  const [snapshot, setSnapshot] = useState<{ scope: RiskScope; report: RiskReport | null }>({ scope, report: initialReport });
  const [error, setError] = useState('');
  const [checking, setChecking] = useState(false);
  const [filter, setFilter] = useState<'all' | 'current'>('all');
  const [category, setCategory] = useState<'all' | RiskAlert['category']>('all');
  const [severity, setSeverity] = useState<'all' | RiskAlert['severity']>('all');
  const scopeRef = useRef(scope), reportRef = useRef(onReport);
  scopeRef.current = scope; reportRef.current = onReport;
  const report = sameRiskScope(snapshot.scope, scope) ? snapshot.report : null;
  useEffect(() => {
    const captured = scope;
    let active = true;
    setError('');
    setChecking(false);
    const stop = startRiskPolling(captured, () => scopeRef.current, () => {
      setChecking(true);
      return api.risks().finally(() => {
        if (active && sameRiskScope(scopeRef.current, captured)) setChecking(false);
      });
    }, value => {
      setSnapshot({ scope: captured, report: value }); setError(''); reportRef.current(value, captured);
    }, cause => setError(errorText(cause)), () => document.visibilityState === 'visible', listener => {
      document.addEventListener('visibilitychange', listener);
      return () => document.removeEventListener('visibilitychange', listener);
    });
    return () => { active = false; stop(); };
  }, [scope.sessionId, scope.companyId, scope.contextVersion, scope.epoch]);
  const reports = report?.companies.filter(company => filter === 'all' || company.company_id === scope.companyId) ?? [];
  return <section className="demo-risk-monitor demo-company-panel" aria-label="Proactive risk monitor">
    <div className="demo-heading"><div><span className="demo-kicker">01 / Across all company workspaces</span><h2>Proactive risk monitor</h2></div><span className="demo-session">Checks while this workspace is open · every 60 seconds</span></div>
    <p>Deterministic inventory and supplier checks plus cached public news. Alerts describe possible exposure, not confirmed shipment delays or price moves. No agent call or business action runs automatically.</p>
    {checking && <ActivityIndicator state={report ? 'working' : 'searching'} label={report ? 'Refreshing company risk signals' : 'Scanning the ten company workspaces…'} detail={report ? 'The report below keeps its last confirmed check time until this read completes.' : 'Checking synthetic inventory and supplier scenarios against available public news. No agent call.'} />}
    {!report && !error && !checking && <ActivityIndicator state="idle" label="Risk monitor ready" detail="Checks start when this workspace is visible. No agent call runs automatically." />}
    {report ? <><div className="demo-risk-summary"><strong>{report.summary.high} high</strong><strong>{report.summary.medium} medium</strong><strong>{report.summary.low} low</strong><span>{report.summary.companies_with_alerts} companies with alerts · checked {report.checked_at}</span></div>
      {report.companies.length !== 10 && <p className="demo-alert">Coverage incomplete: {report.companies.length} of 10 company reports returned.</p>}
      <div className="demo-risk-filters"><label>Companies<select value={filter} onChange={event => setFilter(event.target.value as 'all' | 'current')}><option value="all">All companies</option><option value="current">Current company</option></select></label><label>Category<select value={category} onChange={event => setCategory(event.target.value as typeof category)}><option value="all">All categories</option><option value="inventory">Inventory</option><option value="supplier_delay">Supplier delay</option><option value="news">News</option></select></label><label>Severity<select value={severity} onChange={event => setSeverity(event.target.value as typeof severity)}><option value="all">All severities</option><option value="high">High</option><option value="medium">Medium</option><option value="low">Low</option></select></label></div>
      <div className="demo-risk-companies">{reports.map(company => {
        const alerts = company.alerts.filter(item => (category === 'all' || item.category === category) && (severity === 'all' || item.severity === severity));
        const counts = { high: company.alerts.filter(item => item.severity === 'high').length,
          medium: company.alerts.filter(item => item.severity === 'medium').length,
          low: company.alerts.filter(item => item.severity === 'low').length };
        return <article key={company.company_id}><details className="demo-risk-company"><summary><span className="demo-kicker">{company.sector} · {company.company_id === scope.companyId ? 'Current company' : 'Other company'}</span><strong className="demo-risk-company-name">{company.company_name}</strong><span className="demo-risk-company-counts">{counts.high} high · {counts.medium} medium · {counts.low} low</span><span className="demo-risk-company-coverage">{company.coverage.materials_evaluated} materials evaluated · news {company.coverage.news_state}</span></summary><div className="demo-risk-company-body">
          <div className="demo-risk-company-head">{company.company_id !== scope.companyId && <button type="button" className="demo-link" onClick={() => onOpenCompany(company.company_id)}>Open company</button>}<span className="demo-muted">{company.alerts.length} alerts · {company.coverage.news_items_evaluated} news items evaluated · checked {company.checked_at}</span></div>
          {company.data_gaps.length > 0 && <p className="demo-risk-gap"><strong>Data gaps:</strong> {company.data_gaps.join('; ')}</p>}
          {company.coverage.sources.some(source => source.state !== 'fresh' && source.state !== 'available') && <ul className="demo-risk-sources">{company.coverage.sources.filter(source => source.state !== 'fresh' && source.state !== 'available').map(source => <li key={source.source_id}>{source.name}: {source.state}{source.reason ? ` · ${source.reason}` : ''}{source.last_checked_at ? ` · checked ${source.last_checked_at}` : ''}</li>)}</ul>}
          {alerts.length ? <div className="demo-risk-alerts">{alerts.map(item => <div key={item.alert_id} className="demo-risk-alert" data-severity={item.severity}><span className="demo-kicker">{item.severity} · {item.category.replace('_', ' ')} · {provenanceLabel(item.provenance)}</span><h4>{item.title}</h4><p>{item.summary}</p><details className="demo-risk-detail"><summary>Impact, evidence, recovery steps</summary><div className="demo-risk-detail-body"><p><strong>Potential impact:</strong> {item.impact}</p><p><strong>Confidence {item.confidence.level}:</strong> {item.confidence.reason}</p><p className="demo-muted">Materials: {item.material_ids.join(', ') || 'None mapped'} · Alert ID: <code>{item.alert_id}</code></p>
            {Object.keys(item.metrics).length > 0 && <dl className="demo-risk-metrics">{Object.entries(item.metrics).map(([name, value]) => <div key={name}><dt>{name.replaceAll('_', ' ')}</dt><dd>{value ?? 'Unavailable'}</dd></div>)}</dl>}
            {item.evidence.length > 0 && <div className="demo-risk-evidence"><strong>Evidence</strong><ul>{item.evidence.map(evidence => { const href = safeHref(evidence.url); return <li key={`${evidence.kind}:${evidence.ref_id}`}><span>{evidence.kind} · {evidence.ref_id}</span> {href ? <a href={href} target="_blank" rel="noopener noreferrer">{evidence.label} ↗</a> : <strong>{evidence.label}</strong>}<small>{evidence.detail}{evidence.observed_at ? ` · observed ${evidence.observed_at}` : ''}</small></li>; })}</ul></div>}
            {item.recovery_actions.length > 0 && <div className="demo-risk-actions"><strong>Suggested next steps</strong>{item.recovery_actions.map(action => <div key={action.action_id}><p>{action.label} · {action.message}</p>{company.company_id === scope.companyId ? <button type="button" className="demo-link" onClick={() => onSeedPrompt(action.message, company.company_id, snapshot.scope)}>Send to operator composer</button> : <span>Open company before using this suggestion.</span>}<small>{action.requires_review ? 'Any resulting proposal still needs review and Apply.' : 'You must send this message yourself.'}</small></div>)}</div>}
          </div></details></div>)}</div> : <p className="demo-muted">{company.alerts.length ? 'No alerts match these filters.' : 'No alerts returned for this company.'}</p>}
        </div></details></article>;
      })}</div>
      {report.limitations.length > 0 && <p className="demo-risk-gap"><strong>Monitor limits:</strong> {report.limitations.join('; ')}</p>}
      <p className="demo-muted">Public news cache refresh interval: {report.news_refresh_seconds / 60} minutes. Monitoring stops when this page closes.</p>
    </> : <p role="status">Checking company risks…</p>}
    {error && <p className="demo-alert" role="alert">Risk monitor refresh failed: {error}. {report ? 'Last confirmed report remains visible.' : 'Risk data is unavailable; company, quotes and operator remain available.'}</p>}
  </section>;
}
