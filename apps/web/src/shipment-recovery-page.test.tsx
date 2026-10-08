import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import type { SessionResponse, ShipmentRecoveryContext } from '../../../contracts/generated/types';
import { isCurrentShipmentRun, ShipmentOutcomeView } from './shipment-recovery-page';
import type { ShipmentViewOutcome } from './shipment-recovery';

const context: ShipmentRecoveryContext = {
  context_id: 'a'.repeat(32), revision: 1, workspace_id: 'W', plan_version: 'v7',
  propagation_snapshot_id: 'graph-1', production_snapshot_id: 's1', as_of: '2026-10-09T00:00:00Z',
  synthetic: true, source: 'host_configured_synthetic', persistence: 'ephemeral',
  source_authenticity: 'not_verified', request_sha256: 'b'.repeat(64),
};
const session = { contract_version: '0.2.0', principal_id: 'operator', workspace_id: 'W', role: 'viewer', identity_mode: 'development', development_only: true } as SessionResponse;

describe('shipment demo presentation and current-run guard', () => {
  it('shows complete no-eligible evidence and explicit local limits', () => {
    const raw = '{"status":"no_eligible_scenario","decimal":"1000000000000000000.000000000000000001"}';
    const value: ShipmentViewOutcome = { kind: 'no_eligible_scenario', context, raw, outcome: {
      adapter_version: '1', synthetic: true, status: 'no_eligible_scenario',
      evidence: { scenarios: [], excluded: [{ event_id: 'E1', reason: 'invalid_milestone' }], normalization_rejected: [], normalization_duplicates: [] }, comparisons: [],
    } };
    const html = renderToStaticMarkup(<ShipmentOutcomeView value={value} />);
    expect(html).toContain('No eligible source scenario');
    expect(html).toContain('invalid_milestone');
    expect(html).toContain('1000000000000000000.000000000000000001');
    expect(html).toContain('independent');
    expect(html).toContain('Source authenticity not verified');
  });

  it('discards a result when generation, session, context or abort state changes', () => {
    const run = { generation: 3, session, contextId: context.context_id, revision: 1, aborted: false };
    expect(isCurrentShipmentRun(run, 3, session, context, false)).toBe(true);
    expect(isCurrentShipmentRun(run, 4, session, context, false)).toBe(false);
    expect(isCurrentShipmentRun(run, 3, { ...session, workspace_id: 'other' }, context, false)).toBe(false);
    expect(isCurrentShipmentRun(run, 3, session, { ...context, revision: 2 }, false)).toBe(false);
    expect(isCurrentShipmentRun(run, 3, session, context, true)).toBe(false);
  });
});
