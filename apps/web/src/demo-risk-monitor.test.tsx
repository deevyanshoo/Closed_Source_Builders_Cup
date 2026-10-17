import { renderToStaticMarkup } from 'react-dom/server';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { RiskReport } from './demo-api';
import { acceptsRiskReport, RiskMonitor, selectRiskReports, summarizeRiskReports, startRiskPolling } from './demo-risk-monitor';

const scope = { sessionId: 's1', companyId: 'atlas', contextVersion: 2, epoch: 4 };
const alert = { alert_id: 'stable-1', company_id: 'atlas', category: 'inventory' as const, severity: 'high' as const,
  title: 'Low copper cover', summary: 'Synthetic cover is short.', impact: 'A synthetic order may be exposed.', material_ids: ['copper'],
  provenance: 'synthetic_profile' as const, confidence: { level: 'moderate' as const, reason: 'Catalogue assumptions only.' },
  evidence: [{ kind: 'profile' as const, ref_id: 'copper', label: 'Copper stock', detail: '12 kg as of 2026-10-14', url: null, observed_at: '2026-10-14T00:00:00Z' }],
  metrics: { stock_cover_days: '4.0' }, recovery_actions: [{ action_id: 'inspect', label: 'Inspect alternatives', message: 'Inspect copper quote alternatives for Atlas.', requires_review: true }] };
const report: RiskReport = { company_id: 'atlas', context_version: 2, checked_at: '2026-10-14T10:00:00Z', monitoring: 'while_workspace_open', news_refresh_seconds: 900,
  summary: { high: 1, medium: 0, low: 0, companies_with_alerts: 1 }, limitations: [],
  companies: [{ company_id: 'atlas', company_name: 'Atlas', sector: 'Energy', checked_at: '2026-10-14T10:00:00Z', alerts: [alert],
    coverage: { materials_evaluated: 5, news_state: 'unavailable', news_items_evaluated: 0, sources: [{ source_id: 'eia', name: 'EIA', state: 'unavailable', last_checked_at: null, reason: 'Rate limited' }] }, data_gaps: ['News source unavailable'] },
    { company_id: 'loom', company_name: 'LoomWorks', sector: 'Textiles', checked_at: '2026-10-14T10:00:00Z', alerts: [{ ...alert, alert_id: 'loom-1', company_id: 'loom' }],
      coverage: { materials_evaluated: 4, news_state: 'fresh', news_items_evaluated: 2, sources: [] }, data_gaps: [] }] };

afterEach(() => vi.useRealTimers());
describe('proactive risk monitor', () => {
  it('defaults to this company and derives counts from the visible alerts', () => {
    const html = renderToStaticMarkup(<RiskMonitor scope={scope} initialReport={report} onReport={() => {}} onOpenCompany={() => {}} onSeedPrompt={() => {}} />);
    expect(html).toContain('Company risks');
    expect(html).toContain('Atlas');
    expect(html).not.toContain('LoomWorks');
    expect(html).not.toContain('Across all company workspaces');
    const selected = selectRiskReports(report, scope.companyId, 'current', 'inventory', 'high');
    expect(summarizeRiskReports(selected)).toEqual({ high: 1, medium: 0, low: 0, companies_with_alerts: 1 });
    expect(summarizeRiskReports(selectRiskReports(report, scope.companyId, 'all', 'news', 'all'))).toEqual({ high: 0, medium: 0, low: 0, companies_with_alerts: 0 });
    expect(summarizeRiskReports(selectRiskReports(report, scope.companyId, 'all', 'all', 'high')).high).toBe(2);
  });
  it('rests before the first actual risk request rather than claiming an active scan', () => {
    const html = renderToStaticMarkup(<RiskMonitor scope={scope} onReport={() => {}} onOpenCompany={() => {}} onSeedPrompt={() => {}} />);
    expect(html).toContain('<canvas');
    expect(html).toContain('data-activity="idle"');
    expect(html).toContain('data-paused="true"');
    expect(html).not.toContain('data-activity="searching"');
    expect(html).toContain('No agent call');
  });
  it('accepts only reports for the captured session/company/context/epoch', () => {
    expect(acceptsRiskReport(report, scope, scope)).toBe(true);
    for (const changed of [{ ...scope, sessionId: 's2' }, { ...scope, companyId: 'loom' }, { ...scope, contextVersion: 3 }, { ...scope, epoch: 5 }])
      expect(acceptsRiskReport(report, scope, changed)).toBe(false);
    expect(acceptsRiskReport({ ...report, company_id: 'loom' }, scope, scope)).toBe(false);
  });
  it('scans immediately and each minute while visible, pauses hidden, resumes, and drops a late response after cleanup', async () => {
    vi.useFakeTimers();
    let visible = true, current = scope, listener = () => {};
    let release: (value: RiskReport) => void = () => {};
    const read = vi.fn(() => new Promise<RiskReport>(resolve => { release = resolve; }));
    const seen = vi.fn(), errors = vi.fn();
    const stop = startRiskPolling(scope, () => current, read, seen, errors, () => visible, callback => { listener = callback; return () => { listener = () => {}; }; });
    expect(read).toHaveBeenCalledTimes(1);
    release(report); await Promise.resolve(); await Promise.resolve();
    expect(seen).toHaveBeenCalledWith(report);
    visible = false; await vi.advanceTimersByTimeAsync(60_000);
    expect(read).toHaveBeenCalledTimes(1);
    visible = true; listener(); expect(read).toHaveBeenCalledTimes(2);
    current = { ...scope, companyId: 'loom' }; release(report); await Promise.resolve(); await Promise.resolve();
    expect(seen).toHaveBeenCalledTimes(1);
    stop(); await vi.advanceTimersByTimeAsync(60_000);
    expect(read).toHaveBeenCalledTimes(2);
    expect(errors).not.toHaveBeenCalled();
  });
  it('does not publish a pending report after the monitor is removed', async () => {
    let release: (value: RiskReport) => void = () => {};
    const seen = vi.fn(), errors = vi.fn();
    const stop = startRiskPolling(scope, () => scope, () => new Promise<RiskReport>(resolve => { release = resolve; }), seen, errors, () => true, () => () => {});
    stop(); release(report); await Promise.resolve(); await Promise.resolve();
    expect(seen).not.toHaveBeenCalled();
    expect(errors).not.toHaveBeenCalled();
  });
  it('shows impact, confidence, evidence and source gaps, and keeps other-company actions behind Open company', () => {
    const html = renderToStaticMarkup(<RiskMonitor scope={scope} initialReport={report} mode="portfolio" onReport={() => {}} onOpenCompany={() => {}} onSeedPrompt={() => {}} />);
    expect(html).toContain('Proactive risk monitor');
    expect(html).toContain('Atlas');
    expect(html).toContain('LoomWorks');
    expect(html).toContain('A synthetic order may be exposed.');
    expect(html).toContain('Catalogue assumptions only.');
    expect(html).toContain('Rate limited');
    expect(html).toContain('News source unavailable');
    expect(html).toContain('stable-1');
    expect(html).toContain('Open company');
    expect(html).toContain('Send to operator composer');
    expect(html).not.toContain('Apply reviewed proposal');
  });
  it('cleans known operational risk copy while preserving observed news and canonical alerts', () => {
    const operational = { ...alert, title: 'Copper order has synthetic lead-time exposure',
      summary: 'Stated synthetic stock covers 4 days.',
      impact: 'Potential late arrival under fictional order and supplier assumptions; no actual shipment delay has been observed.',
      confidence: { ...alert.confidence, reason: 'Dated comparison of fictional order deadline and listed supplier lead; supplier availability is not verified.' },
      recovery_actions: [{ ...alert.recovery_actions[0], label: 'Review synthetic alternatives', message: 'Load copper sample quotes for review.' }] };
    const news = { ...operational, alert_id: 'news-1', category: 'news' as const, provenance: 'observed_news' as const, title: 'Synthetic fuels in public news' };
    const source = { ...report, companies: [{ ...report.companies[0], alerts: [operational, news] }] };
    const html = renderToStaticMarkup(<RiskMonitor scope={scope} initialReport={source} onReport={() => {}} onOpenCompany={() => {}} onSeedPrompt={() => {}} />);
    expect(html).toContain('Copper order has lead-time exposure');
    expect(html).toContain('Stated stock covers 4 days.');
    expect(html).toContain('no actual shipment delay has been observed');
    expect(html).toContain('supplier availability is not verified');
    expect(html).toContain('Review alternatives');
    expect(html).toContain('Synthetic fuels in public news');
    expect(operational.title).toContain('synthetic');
    expect(operational.recovery_actions[0].message).toBe('Load copper sample quotes for review.');
  });
  it('keeps the hypothetical and unverified limits on a recorded recovery alert', () => {
    const recovery = { ...alert, provenance: 'synthetic_plan' as const,
      title: 'Copper recorded synthetic recovery', summary: 'Copper recorded synthetic recovery',
      impact: 'The hypothetical delay may change sourcing feasibility or cost; this is a synthetic projection, not an observed shipment event.',
      confidence: { ...alert.confidence, reason: 'Recomputed with the current saved, version-bound fictional offers and authoritative planner.' } };
    const source = { ...report, companies: [{ ...report.companies[0], alerts: [recovery] }] };
    const html = renderToStaticMarkup(<RiskMonitor scope={scope} initialReport={source} onReport={() => {}} onOpenCompany={() => {}} onSeedPrompt={() => {}} />);
    expect(html).toContain('Copper recorded recovery');
    expect(html).toContain('planning projection, not an observed shipment event');
    expect(html).toContain('version-bound listed offers');
    expect(recovery.impact).toContain('synthetic projection');
  });
  it('keeps ten company summaries compact by default and nests complete alert details behind disclosures', () => {
    const ten = { ...report, companies: Array.from({ length: 10 }, (_, index) => ({ ...report.companies[0], company_id: `company-${index}`, company_name: `Company ${index}`, alerts: [{ ...alert, alert_id: `alert-${index}`, company_id: `company-${index}` }] })) };
    const html = renderToStaticMarkup(<RiskMonitor scope={scope} initialReport={ten} mode="portfolio" onReport={() => {}} onOpenCompany={() => {}} onSeedPrompt={() => {}} />);
    expect(html.match(/<details class="demo-risk-company"/g)).toHaveLength(10);
    expect(html).not.toMatch(/<details class="demo-risk-company"[^>]* open/);
    expect(html).toContain('5 materials evaluated');
    expect(html).toContain('1 high');
    expect(html.match(/<details class="demo-risk-detail"/g)).toHaveLength(10);
    expect(html).toMatch(/Low copper cover.*Synthetic cover is short\..*Impact, evidence, recovery steps/s);
    expect(html).toContain('Inspect copper quote alternatives for Atlas.');
  });
});
