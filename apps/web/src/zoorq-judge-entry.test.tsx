import { renderToStaticMarkup } from 'react-dom/server';
import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import type { CompanyProfile, DemoSession } from './demo-api';
import { formatJudgeFact, ZoorqJudgeEntry } from './zoorq-judge-entry';

const company = { company_id: 'solara-cables', name: 'Solara Cables', sector: 'Electrical manufacturing', synthetic: true } as CompanyProfile;
const other = { ...company, company_id: 'loomworks', name: 'LoomWorks' };
const session = { session_id: 'existing', company: other, synthetic: true } as DemoSession;
const actions = { onNavigate: () => {}, onExploreCompanies: () => {} };

describe('judge entry', () => {
  it('shows a prepared first-visit story with supplied impact facts and direct paths', () => {
    const html = renderToStaticMarkup(<ZoorqJudgeEntry company={company} session={null} visit="first" prepared={{ status: 'ready', facts: [
      { label: 'Stock cover', value: '6 days', basis: 'Synthetic profile snapshot' },
      { label: 'Matching supplier lead', value: '10 days', basis: 'Synthetic supplier profile' },
    ] }} companyCount={10} {...actions} />);
    expect(html).toContain('Solara Cables');
    expect(html).toContain('Prepared scenario');
    expect(html).toContain('6 days');
    expect(html).toContain('10 days');
    expect(html).toContain('Route');
    expect(html).toContain('News');
    expect(html).toContain('What if');
    expect(html).toContain('Explore all 10 companies');
    expect(html).not.toMatch(/sign up|upload|aria-label="Demo workspace disclosure"/i);
  });

  it('treats a restored nonflagship session as the current company', () => {
    const html = renderToStaticMarkup(<ZoorqJudgeEntry company={other} session={session} visit="restored" prepared={{ status: 'ready', facts: [] }} companyCount={10} {...actions} />);
    expect(html).toContain('Continue LoomWorks');
    expect(html).not.toContain('Solara Cables');
    expect(html).not.toContain('Prepared scenario');
  });

  it('never presents a pending baseline as ready', () => {
    const html = renderToStaticMarkup(<ZoorqJudgeEntry company={company} session={null} visit="first" prepared={{ status: 'loading', facts: [] }} companyCount={10} {...actions} />);
    expect(html).toContain('Preparing the scenario');
    expect(html).not.toContain('Prepared scenario');
  });

  it('formats actual high-precision cover and spend for display without changing source facts', () => {
    const facts = [
      {label:'Profile stock cover', value:'6.0000600006000060000600006 days', basis:'Fictional profile stock / daily consumption'},
      {label:'Shortest profile lead', value:'10 days', basis:'Matching material supplier profile; not a shipment ETA'},
      {label:'Reviewed baseline spend', value:'82000 USD', basis:'Deterministic bundled quote comparison; not realized loss or savings'},
    ];
    const html=renderToStaticMarkup(<ZoorqJudgeEntry company={company} session={null} visit="first" prepared={{status:'ready',facts}} companyCount={10} {...actions}/>);
    expect(html).toContain('about 6 days');
    expect(html).toContain('82,000 USD');
    expect(html).toContain('Recorded stock / daily use');
    expect(html).toContain('Profile estimate');
    expect(html).toContain('Reviewed quote total');
    expect(html).toContain('Matching material supplier profile; not a shipment ETA');
    expect(html).not.toContain('>Fictional profile stock');
    expect(html).toMatch(/class="zoorq-judge-entry-fact-value"[^>]*>82,000<\/span> <span class="zoorq-judge-entry-fact-unit"[^>]*>USD<\/span>/);
    expect(html).not.toContain('6.0000600006000060000600006');
    expect(facts[0].value).toBe('6.0000600006000060000600006 days');
    expect(formatJudgeFact('unavailable')).toBe('unavailable');
  });

  it('keeps a returning company compact with all accessible quick paths', () => {
    const html=renderToStaticMarkup(<ZoorqJudgeEntry company={other} session={session} visit="restored" prepared={{status:'unavailable',facts:[]}} companyCount={10} {...actions}/>);
    expect(html).toContain('is-continuing');
    expect(html).toContain('Continue LoomWorks');
    expect(html).not.toContain('Supply signals matter');
    expect(html).not.toContain('Prepared scenario unavailable');
    expect(html).toContain('aria-label="Explore the scenario"');
    expect(html).toContain('Route');expect(html).toContain('News');expect(html).toContain('What if');
  });

  it('gives first-visit facts full-width columns while preserving the continuing mobile actions', () => {
    const css=readFileSync(new URL('./zoorq-judge-entry.css',import.meta.url),'utf8');
    expect(css).toMatch(/\.zoorq-judge-entry-facts\{[^}]*grid-column:1\/-1;[^}]*grid-template-columns:repeat\(3,minmax\(0,1fr\)\)/);
    expect(css).toMatch(/\.zoorq-judge-entry-fact-value\{[^}]*white-space:nowrap/);
    expect(css).toMatch(/@media\(max-width:760px\)\{[^}]*\}\.zoorq-judge-entry-facts\{[^}]*grid-template-columns:1fr/);
    expect(css).toMatch(/\.zoorq-judge-entry\{[^}]*max-width:100%;min-width:0;box-sizing:border-box/);
    expect(css).toMatch(/@media\(max-width:760px\)[^{]*\{[^}]*\.zoorq-judge-entry\.is-continuing\{[^}]*flex-direction:column;align-items:stretch/);
    expect(css).toMatch(/\.zoorq-judge-entry\.is-continuing \.zoorq-judge-entry-actions\{[^}]*flex:none;display:grid;grid-template-columns:repeat\(3,minmax\(0,1fr\)\)/);
    expect(css).toMatch(/\.zoorq-judge-entry\.is-continuing \.zoorq-judge-entry-actions button\{[^}]*min-height:44px;[^}]*background:var\(--zoorq-soft\);color:var\(--zoorq-ink\)/);
  });
});
