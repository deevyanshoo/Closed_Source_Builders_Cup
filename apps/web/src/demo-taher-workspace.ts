import type { CarbonAssessment, DemoSession, DispatchView, OfferView, PlanView } from './demo-api';
import type { CarbonComparison } from './demo-carbon';

export type TaherScope = { sessionId: string; companyId: string; contextVersion: number; epoch: number; planId: string | null };

export function sameTaherScope(a: TaherScope | null, b: TaherScope | null): boolean {
  return !!a && !!b && a.sessionId === b.sessionId && a.companyId === b.companyId &&
    a.contextVersion === b.contextVersion && a.epoch === b.epoch && a.planId === b.planId;
}

export function acceptScopedResult(captured: TaherScope, current: TaherScope | null, request: number, latestRequest: number): boolean {
  return request === latestRequest && sameTaherScope(captured, current);
}

const sameVersions = (a: Record<string, number>, b: Record<string, number>) =>
  Object.keys(a).length === Object.keys(b).length && Object.entries(a).every(([id, version]) => b[id] === version);

export function currentCarbonAssessment(session: DemoSession, plan: PlanView | null, dirty: boolean): CarbonAssessment | null {
  const record = session.carbon_assessment;
  if (dirty || !plan || !record || !session.company || record.company_id !== session.company.company_id ||
      record.plan_id !== plan.plan_id || record.material_id !== plan.material ||
      record.result?.plan_id !== plan.plan_id || !sameVersions(record.offer_versions, plan.offer_versions)) return null;
  return record;
}

export function currentCarbonResult(session: DemoSession | null, plan: PlanView | null, dirty: boolean,
  scope: TaherScope | null, snapshot: { scope: TaherScope; result: CarbonComparison | null } | null,
  editedScope: TaherScope | null): CarbonComparison | null {
  if (!session || !plan || dirty || !scope || sameTaherScope(editedScope, scope)) return null;
  if (snapshot && sameTaherScope(snapshot.scope, scope)) return snapshot.result;
  return currentCarbonAssessment(session, plan, false)?.result ?? null;
}

export function linkedDelayRequest(handoff: { shipmentId: string; offerId: string }, selectedOfferId: string,
  daysText: string): { shipmentId: string; daysByOffer: Record<string, number> } | null {
  if (!handoff.shipmentId || handoff.offerId !== selectedOfferId || !/^\d+$/.test(daysText)) return null;
  const days = Number(daysText);
  if (!Number.isSafeInteger(days) || days > 3650) return null;
  return { shipmentId: handoff.shipmentId, daysByOffer: { [handoff.offerId]: days } };
}

export function currentDispatchView(view: DispatchView | null, scope: TaherScope | null): DispatchView | null {
  return view && scope && view.company_id === scope.companyId && view.context_version === scope.contextVersion &&
    view.plan_id === scope.planId ? view : null;
}

export function eligibleShipmentDelay(view: DispatchView, shipmentId: string, days: number, scope: TaherScope, plan: PlanView | null, offers: OfferView[], dirty: boolean): { shipmentId: string; offerId: string; days: number } | null {
  if (dirty || !plan || scope.planId !== plan.plan_id || !currentDispatchView(view, scope) ||
      !Number.isInteger(days) || days < 0 || days > 3650) return null;
  const rows = view.shipments.filter(row => row.shipment_id === shipmentId);
  if (rows.length !== 1) return null;
  const row = rows[0];
  const offerId = row.offer_id;
  if (!row.recovery_available || row.company_id !== scope.companyId || row.plan_id !== plan.plan_id ||
      !offerId || offerId !== plan.selected_offer_id || row.offer_version == null ||
      plan.offer_versions[offerId] !== row.offer_version ||
      !offers.some(offer => offer.offer_id === offerId && offer.version === row.offer_version &&
        (offer.status === 'saved' || offer.status === 'used_in_plan'))) return null;
  return { shipmentId, offerId, days };
}
