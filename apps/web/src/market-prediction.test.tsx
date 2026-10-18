import { renderToStaticMarkup } from 'react-dom/server';
import { expect, it } from 'vitest';
import { acceptMarketResponse, MarketEvidence } from './market-prediction';
import type { CompanyDecisionPreview, MarketForecastRead } from './demo-api';

const scope = { sessionId: 's', companyId: 'a', contextVersion: 1, epoch: 2, materialId: 'copper', planId: 'p' };
const market = { contract_version: 'prediction.v1', forecasts: [], status: 'REFERENCE_REPLAY', publication_mode: 'REFERENCE_REPLAY', anchor_market_session_date: '2026-10-16', as_of_utc: '2026-10-17T11:11:33Z', forecast_count: 18, artifact_role_count: 27, run_hash: 'hash', limitations: [], coverage: { direct_joint_cost_reporting_currencies: [] } } as MarketForecastRead;
const preview = { contract_version: 'prediction.v1', decision_id: 'd', quantity_unit: 'kg', operations_baseline: null, evidence: {market_as_of_utc: market.as_of_utc, publication_mode: 'REFERENCE_REPLAY'}, company_id: 'a', context_version: 1, material_id: 'copper', status: 'REPLAN_REQUIRED', recommendation: null, missing_inputs: [], market_influence: { status: 'CONTEXT_ONLY', reason_code: 'BASIS_MISMATCH', as_of_utc: market.as_of_utc }, service_trace: { first_gap_date: '2026-10-21', gap_quantity: '10', unit: 'kg', trace: [] } } as CompanyDecisionPreview;

it('discards company switched responses and reports replan before market evidence', () => {
  expect(acceptMarketResponse(scope, { ...scope, companyId: 'b' }, preview)).toBe(false);
  expect(acceptMarketResponse(scope, scope, { ...preview, company_id: 'b' })).toBe(false);
  const html = renderToStaticMarkup(<MarketEvidence market={market} preview={preview} />);
  expect(html).toContain('Replan required');
  expect(html).toContain('2026-10-21');
  expect(html).toContain('2026-10-16');
  expect(html).not.toMatch(/guaranteed|savings|accuracy|probability/i);
});

it('keeps unsupported and stale market states as capability context', () => {
  const unsupported = renderToStaticMarkup(<MarketEvidence market={market} preview={{...preview, status:'MISSING_OPERATIONS_INPUT', service_trace:null, market_influence:{...preview.market_influence,status:'UNSUPPORTED'}}} />);
  expect(unsupported).toContain('No matching model');
  expect(unsupported).toContain('More facts needed');
  const stale = renderToStaticMarkup(<MarketEvidence market={{...market,status:'STALE'}} preview={{...preview, status:'MISSING_OPERATIONS_INPUT', service_trace:null}} />);
  expect(stale).toContain('Stale market context');
  expect(stale).not.toMatch(/guaranteed|savings|accuracy|probability/i);
});

it('names separate uncovered intervals without spanning covered days', () => {
  const service = { ...preview.service_trace!, first_gap_date: '2026-10-19', gap_quantity: '10',
    uncovered_interval: { start: '2026-10-19', end: '2026-10-19' },
    uncovered_intervals: [
      { start: '2026-10-19', end: '2026-10-19' },
      { start: '2026-10-22', end: '2026-10-22' },
    ] };
  const html = renderToStaticMarkup(<MarketEvidence market={market} preview={{ ...preview, service_trace: service }} />);
  expect(html).toContain('2026-10-19');
  expect(html).toContain('2026-10-22');
  expect(html).not.toContain('2026-10-19 through 2026-10-22');
});
