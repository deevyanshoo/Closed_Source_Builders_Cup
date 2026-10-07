import type { ProductionContextRef, ProductionSnapshot, RecoveryComparisonRequest, StoredProductionContext } from '../../../contracts/generated/types';
import { voiceMatches } from './voice';

export function contextRefMatches(value: unknown, expected: ProductionContextRef): boolean {
  if (!voiceMatches(value, 'ProductionContextRef')) return false;
  const ref = value as ProductionContextRef;
  return Number.isSafeInteger(ref.revision) && ref.context_id === expected.context_id && ref.workspace_id === expected.workspace_id && ref.revision === expected.revision;
}

export function parseSnapshot(text: string, workspace: string): ProductionSnapshot {
  let value: unknown;
  try { value = JSON.parse(text); } catch { throw new Error('Enter complete JSON for a synthetic production snapshot.'); }
  if (!voiceMatches(value, 'ProductionSnapshot')) throw new Error('The production snapshot is incomplete or has unsupported fields.');
  const snapshot = value as ProductionSnapshot;
  if (snapshot.workspace_id !== workspace || snapshot.synthetic !== true) throw new Error('The synthetic snapshot must use the current workspace.');
  return snapshot;
}

export function savedScenario(snapshot: ProductionSnapshot): RecoveryComparisonRequest {
  return {
    contract_version: '0.2.0', snapshot: structuredClone(snapshot),
    scenario: { workspace_id: snapshot.workspace_id, expected_plan_version: snapshot.plan_version, source_snapshot_id: snapshot.snapshot_id, scenario_id: 'pending-server-session', synthetic: true, arrival_replacements: [] },
    recovery: { workspace_id: snapshot.workspace_id, expected_plan_version: snapshot.plan_version, source_snapshot_id: snapshot.snapshot_id, scenario_id: 'pending-server-session', synthetic: true, assignments: structuredClone(snapshot.baseline_assignments) },
  };
}

export function contextRef(context: StoredProductionContext): ProductionContextRef {
  return { context_id: context.context_id, workspace_id: context.workspace_id, revision: context.revision };
}
