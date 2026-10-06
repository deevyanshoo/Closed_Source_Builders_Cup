import type { ActionApproval, ActionReceipt, ApprovalsResponse, AuditEvent, AuditResponse, ExecuteActionRequest, PlanningRequest, RfqDraftArguments, SavedPlan, SavedPlanResponse, SavedPlansResponse, SessionResponse } from '../../../contracts/generated/types';
import { responseState } from './planning';

export class StateError extends Error {
  constructor(message: string, readonly status: number, readonly code: string, readonly field?: string) { super(message); }
}

type StateEnvelope = { contract_version: '0.2.0' };
type StateKey = 'plan' | 'plans' | 'approval' | 'approvals' | 'receipt' | 'receipts' | 'events';
const object = (value: unknown): value is Record<string, unknown> => !!value && typeof value === 'object' && !Array.isArray(value);
const string = (value: unknown): value is string => typeof value === 'string';
function savedPlan(value: unknown): value is SavedPlan {
  if (!object(value) || !string(value.saved_plan_id) || !string(value.workspace_id) || !Number.isSafeInteger(value.revision) || (value.revision as number) < 1 || !object(value.snapshot) || !object(value.snapshot.request) || !string(value.created_at) || !string(value.updated_at)) return false;
  const request = value.snapshot.request;
  if (request.contract_version !== '0.1.0' || request.workspace_id !== value.workspace_id || !object(request.buyer) || !string(request.buyer.id) || !string(request.buyer.destination_country) || !string(request.material) || !object(request.quantity) || !string(request.quantity.amount) || !string(request.quantity.unit)) return false;
  try { responseState(value.snapshot as unknown as SavedPlan['snapshot']); return true; }
  catch { return false; }
}
const approval = (value: unknown): value is ActionApproval => object(value) && string(value.approval_id) && string(value.saved_plan_id) && Number.isSafeInteger(value.revision) && string(value.plan_version) && object(value.arguments) && string(value.approved_by) && (value.status === 'confirmed' || value.status === 'executed');
const receipt = (value: unknown): value is ActionReceipt => object(value) && string(value.receipt_id) && string(value.saved_plan_id) && value.status === 'draft_prepared' && value.supplier_communication === false && object(value.arguments);
const audit = (value: unknown): value is AuditEvent => object(value) && string(value.audit_id) && string(value.event_type) && string(value.principal_id) && string(value.created_at);
const valid: Record<StateKey, (value: unknown) => boolean> = {
  plan: savedPlan, plans: value => Array.isArray(value) && value.every(savedPlan),
  approval, approvals: value => Array.isArray(value) && value.every(approval),
  receipt, receipts: value => Array.isArray(value) && value.every(receipt),
  events: value => Array.isArray(value) && value.every(audit),
};

export async function stateRequest<T extends StateEnvelope>(path: string, key: StateKey, init?: RequestInit): Promise<T> {
  let response: Response;
  try { response = await fetch(path, { ...init, headers: init?.body ? { 'Content-Type': 'application/json' } : undefined }); }
  catch (error) { if (init?.signal?.aborted) throw error; throw new StateError('Cannot reach the local gateway. Retry when it is available.', 0, 'network_unavailable'); }
  let data: unknown;
  try { data = await response.json(); }
  catch { throw new StateError('The gateway returned an unreadable response.', response.status, 'invalid_response'); }
  if (!response.ok) {
    const problem = object(data) && object(data.error) ? data.error : {};
    throw new StateError(string(problem.message) ? problem.message : `Request failed (${response.status}).`, response.status, string(problem.code) ? problem.code : 'request_failed', string(problem.field) ? problem.field : undefined);
  }
  if (!object(data) || data.contract_version !== '0.2.0' || !valid[key](data[key])) throw new StateError('The gateway returned an invalid state response.', response.status, 'invalid_response');
  return data as T;
}

const enc = encodeURIComponent;
const json = (body: unknown) => JSON.stringify(body);
export async function fetchSession(signal?: AbortSignal): Promise<SessionResponse> {
  let response: Response;
  try { response = await fetch('/v2/session', { signal }); }
  catch (error) { if (signal?.aborted) throw error; throw new StateError('Cannot reach the local session.', 0, 'network_unavailable'); }
  let data: unknown;
  try { data = await response.json(); } catch { throw new StateError('The session response is unreadable.', response.status, 'invalid_response'); }
  if (!response.ok) { const problem = object(data) && object(data.error) ? data.error : {}; throw new StateError(string(problem.message) ? problem.message : 'Session unavailable.', response.status, string(problem.code) ? problem.code : 'session_unavailable'); }
  if (!object(data) || data.contract_version !== '0.2.0' || !string(data.principal_id) || !string(data.workspace_id) || (data.role !== 'editor' && data.role !== 'viewer') || !((data.development_only === true && (data.identity_mode === undefined || data.identity_mode === 'development')) || (data.development_only === false && data.identity_mode === 'private_machine'))) throw new StateError('The gateway returned an invalid session.', response.status, 'invalid_response');
  return data as unknown as SessionResponse;
}
export const listPlans = (workspaceId: string, signal?: AbortSignal) => stateRequest<SavedPlansResponse>(`/v2/workspaces/${enc(workspaceId)}/plans`, 'plans', { signal });
export const getPlan = (id: string, signal?: AbortSignal) => stateRequest<SavedPlanResponse>(`/v2/plans/${enc(id)}`, 'plan', { signal });
export const savePlan = (workspaceId: string, planningRequest: PlanningRequest, signal?: AbortSignal) => stateRequest<SavedPlanResponse>(`/v2/workspaces/${enc(workspaceId)}/plans`, 'plan', { method: 'POST', body: json({ contract_version: '0.2.0', planning_request: planningRequest }), signal });
export const revisePlan = (plan: SavedPlan, planningRequest: PlanningRequest, signal?: AbortSignal) => stateRequest<SavedPlanResponse>(`/v2/plans/${enc(plan.saved_plan_id)}`, 'plan', { method: 'PUT', body: json({ contract_version: '0.2.0', expected_revision: plan.revision, planning_request: planningRequest }), signal });
export const listApprovals = (id: string, signal?: AbortSignal) => stateRequest<ApprovalsResponse>(`/v2/plans/${enc(id)}/approvals`, 'approvals', { signal });
export const listReceipts = (id: string, signal?: AbortSignal) => stateRequest<{ contract_version: '0.2.0'; receipts: ActionReceipt[] }>(`/v2/plans/${enc(id)}/receipts`, 'receipts', { signal });
export const listAudit = (id: string, signal?: AbortSignal) => stateRequest<AuditResponse>(`/v2/plans/${enc(id)}/audit`, 'events', { signal });

export async function loadHistory(id: string, isCurrent: () => boolean) {
  const [approvalList, receiptList, auditList] = await Promise.all([listApprovals(id), listReceipts(id), listAudit(id)]);
  if (!isCurrent()) return null;
  return { approvals: approvalList.approvals, receipts: receiptList.receipts, audit: auditList.events };
}

export function actionArguments(plan: SavedPlan): RfqDraftArguments {
  const source = plan.snapshot.request;
  return { buyer_id: source.buyer.id, material: source.material, quantity: source.quantity, destination_country: source.buyer.destination_country, quote_currency: source.quote_currency, settlement_currency: source.settlement_currency, reporting_currency: source.reporting_currency };
}
export function planVersion(plan: SavedPlan): string {
  if (plan.snapshot.status !== 'ready' || !plan.snapshot.plan) throw new Error('Only a ready saved plan can prepare an RFQ.');
  return plan.snapshot.plan.plan_version;
}
export const confirmAction = (plan: SavedPlan, signal?: AbortSignal) => stateRequest<{ contract_version: '0.2.0'; approval: ActionApproval }>(`/v2/plans/${enc(plan.saved_plan_id)}/approvals`, 'approval', { method: 'POST', body: json({ contract_version: '0.2.0', expected_revision: plan.revision, expected_plan_version: planVersion(plan), action_kind: 'prepare_rfq', arguments: actionArguments(plan) }), signal });
export function executionFor(plan: SavedPlan, approvalId: string, idempotencyKey: string): ExecuteActionRequest {
  return { contract_version: '0.2.0', workspace_id: plan.workspace_id, saved_plan_id: plan.saved_plan_id, expected_revision: plan.revision, expected_plan_version: planVersion(plan), approval_id: approvalId, action_kind: 'prepare_rfq', arguments: actionArguments(plan), idempotency_key: idempotencyKey };
}
export const executeAction = (request: ExecuteActionRequest, signal?: AbortSignal) => stateRequest<{ contract_version: '0.2.0'; receipt: ActionReceipt }>('/v2/actions/prepare_rfq', 'receipt', { method: 'POST', body: json(request), signal });
