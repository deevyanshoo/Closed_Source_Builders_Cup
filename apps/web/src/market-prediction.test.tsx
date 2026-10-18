import { renderToStaticMarkup } from 'react-dom/server';
import { readFileSync } from 'node:fs';
import { expect, it } from 'vitest';
import { acceptMarketResponse, MarketEvidence, MarketPredictions, readableForecasts } from './market-prediction';
import type { CompanyDecisionPreview, MarketForecastRead } from './demo-api';

const scope = { sessionId: 's', companyId: 'a', contextVersion: 1, epoch: 2, materialId: 'copper', planId: 'p' };
const market = { contract_version: 'prediction.v1', forecasts: [], status: 'REFERENCE_REPLAY', publication_mode: 'REFERENCE_REPLAY', anchor_market_session_date: '2026-10-16', as_of_utc: '2026-10-17T11:11:33Z', forecast_count: 18, artifact_role_count: 27, run_hash: 'hash', limitations: [], coverage: { direct_joint_cost_reporting_currencies: [] } } as MarketForecastRead;
const preview = { contract_version: 'prediction.v1', decision_id: 'd', quantity_unit: 'kg', operations_baseline: null, evidence: {market_as_of_utc: market.as_of_utc, publication_mode: 'REFERENCE_REPLAY'}, company_id: 'a', context_version: 1, material_id: 'copper', status: 'REPLAN_REQUIRED', recommendation: null, missing_inputs: [], market_influence: { status: 'CONTEXT_ONLY', reason_code: 'BASIS_MISMATCH', as_of_utc: market.as_of_utc }, service_trace: { first_gap_date: '2026-10-21', gap_quantity: '10', unit: 'kg', trace: [] } } as CompanyDecisionPreview;

it('renders all 18 dated reference cells without requiring a plan', () => {
  const snapshot = JSON.parse(readFileSync(new URL('../../../procurement-engine/jobs/market_forecast/reference_snapshot.json', import.meta.url), 'utf8'));
  const rows = readableForecasts(snapshot.forecasts);
  expect(rows).toHaveLength(18);
  const html = renderToStaticMarkup(<MarketPredictions market={{ ...market, forecasts: snapshot.forecasts }} />);
  expect(html).toContain('Market predictions');
  expect(html).toContain('18 readable cells');
  expect(html).toContain('1 observed session');
  expect(html).toContain('3 observed sessions');
  expect(html).toContain('5 observed sessions');
  expect(html).toContain('Domestic currency cost of a fixed USD payable');
  expect(html).toContain('Domestic currency copper landed cost proxy');
  expect(html).toContain('Cost down');
  expect(html).toContain('Abstain');
  expect(html).not.toMatch(/confidence|buy now|future price/i);
});

it('skips malformed and unknown cells and states when none are readable', () => {
  expect(readableForecasts([null, {}, { forecast_kind: 'UNKNOWN' }])).toEqual([]);
  const html = renderToStaticMarkup(<MarketPredictions market={{ ...market, forecasts: [null, {}] }} />);
  expect(html).toContain('No readable market forecast cells');
});

it('discards company switched responses and explains unconfirmed deliveries before the stock-only trace', () => {
  expect(acceptMarketResponse(scope, { ...scope, companyId: 'b' }, preview)).toBe(false);
  expect(acceptMarketResponse(scope, scope, { ...preview, company_id: 'b' })).toBe(false);
  const html = renderToStaticMarkup(<MarketEvidence market={market} preview={preview} />);
  expect(html).toContain('Confirm incoming deliveries');
  expect(html).toContain('Quoted arrivals are not confirmed receipts');
  expect(html).toContain('Confirmed-stock scenario');
  expect(html).toContain('peak shortfall is 10 kg');
  expect(html).toContain('The quote comparison above assumes quoted arrivals');
  expect(html.indexOf('Evidence and missing inputs')).toBeLessThan(html.indexOf('Confirmed-stock scenario'));
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
