import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import type { SessionResponse, ShipmentRecoveryContext } from '../../../contracts/generated/types';
import { isCurrentShipmentRun, refreshShipmentOnForeground, ShipmentOutcomeView, ShipmentRecoveryPage, runShipmentComparison, subscribeShipmentForeground } from './shipment-recovery-page';
import { MemoryRouter } from 'react-router-dom';

it('opens shipment recovery with a plain local-context boundary', () => {
  const html = renderToStaticMarkup(<MemoryRouter><ShipmentRecoveryPage /></MemoryRouter>);
  expect(html).toContain('Host-configured shipment contexts');
  expect(html).not.toContain('LOCAL · SYNTHETIC');
});
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
    const run = { generation: 3, session, contextId: context.context_id, revision: 1,
      requestSha256: context.request_sha256, routeKey: 'route-1', aborted: false };
    expect(isCurrentShipmentRun(run, 3, session, context, false, 'route-1')).toBe(true);
    expect(isCurrentShipmentRun(run, 3, { ...session, identity_mode: undefined }, context, false, 'route-1')).toBe(true);
    expect(isCurrentShipmentRun(run, 4, session, context, false, 'route-1')).toBe(false);
    expect(isCurrentShipmentRun(run, 3, { ...session, workspace_id: 'other' }, context, false, 'route-1')).toBe(false);
    expect(isCurrentShipmentRun(run, 3, { ...session, principal_id: 'other' }, context, false, 'route-1')).toBe(false);
    expect(isCurrentShipmentRun(run, 3, { ...session, role: 'editor' }, context, false, 'route-1')).toBe(false);
    expect(isCurrentShipmentRun(run, 3, { ...session, identity_mode: 'private_machine' }, context, false, 'route-1')).toBe(false);
    expect(isCurrentShipmentRun(run, 3, session, { ...context, revision: 2 }, false, 'route-1')).toBe(false);
    expect(isCurrentShipmentRun(run, 3, session, { ...context, request_sha256: 'c'.repeat(64) }, false, 'route-1')).toBe(false);
    expect(isCurrentShipmentRun(run, 3, session, context, false, 'route-2')).toBe(false);
    expect(isCurrentShipmentRun(run, 3, session, context, true, 'route-1')).toBe(false);
  });

  it('does not send a comparison when the run is invalidated while preflight identity is pending', async () => {
    for (const invalidate of ['cancel', 'route', 'selection'] as const) {
      let resolveIdentity!: (value: SessionResponse) => void;
      const identity = new Promise<SessionResponse>(resolve => { resolveIdentity = resolve; });
      let current = true;
      let sends = 0;
      const run = { generation: 3, session, contextId: context.context_id, revision: 1,
        requestSha256: context.request_sha256, routeKey: 'route-1', aborted: false };
      const pending = runShipmentComparison({
        run, context, signal: new AbortController().signal, isCurrent: () => current,
        fetchIdentity: () => identity,
        compare: async () => { sends++; return { status: 'evaluated' }; },
      });
      // Simulate an abort-insensitive transport: the fetch promise still resolves.
      current = false;
      resolveIdentity(session);
      expect(await pending, invalidate).toBeNull();
      expect(sends, invalidate).toBe(0);
    }
  });

  it('clears synchronously before foreground reload and removes its visibility listener', () => {
    let visibilityState: DocumentVisibilityState = 'hidden';
    let listener: EventListener | undefined;
    let removed = false;
    const documentLike = {
      get visibilityState() { return visibilityState; },
      addEventListener: (_type: string, callback: EventListenerOrEventListenerObject) => { listener = callback as EventListener; },
      removeEventListener: (_type: string, callback: EventListenerOrEventListenerObject) => { removed = callback === listener; listener = undefined; },
    } as unknown as Pick<Document, 'visibilityState' | 'addEventListener' | 'removeEventListener'>;
    const events: string[] = [];
    const cleanup = subscribeShipmentForeground(documentLike, () => refreshShipmentOnForeground(
      () => { events.push('clear-session-context-result-provenance-handoff'); },
      () => { events.push('reload-identity-and-catalog'); },
    ));
    listener?.(new Event('visibilitychange'));
    expect(events).toEqual([]);
    visibilityState = 'visible';
    listener?.(new Event('visibilitychange'));
    expect(events).toEqual(['clear-session-context-result-provenance-handoff', 'reload-identity-and-catalog']);
    cleanup();
    expect(removed).toBe(true);
    visibilityState = 'hidden';
    listener?.(new Event('visibilitychange'));
    expect(events).toHaveLength(2);
  });
});
