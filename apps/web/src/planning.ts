import type { MaterialsResponse, PlanResponse, PlanningRequest, ErrorResponse } from '../../../contracts/generated/types';

export type PlanningDraft = {
  workspaceId: string;
  buyerId: string;
  destinationCountry: string;
  material: string;
  unit: string;
  quantity: string;
  currentStock: string;
  monthlyConsumption: string;
  needByMonth: string;
  leadTimeMonths: string;
  quoteCurrency: string;
  settlementCurrency: string;
  reportingCurrency: string;
  riskAppetite: PlanningRequest['risk_appetite'];
  budget: string;
};

export const initialDraft: PlanningDraft = {
  workspaceId: 'local-workspace',
  buyerId: '',
  destinationCountry: 'IN',
  material: '',
  unit: 'kg',
  quantity: '',
  currentStock: '0',
  monthlyConsumption: '',
  needByMonth: '3',
  leadTimeMonths: '1',
  quoteCurrency: 'USD',
  settlementCurrency: 'INR',
  reportingCurrency: 'INR',
  riskAppetite: 'medium',
  budget: '',
};

const decimal = /^(?:0|[1-9]\d*)(?:\.\d+)?$/;
const integer = /^(?:0|[1-9]\d*)$/;
const code = /^[A-Z]{3}$/;

export function validateDraft(draft: PlanningDraft): Partial<Record<keyof PlanningDraft, string>> {
  const errors: Partial<Record<keyof PlanningDraft, string>> = {};
  if (!draft.workspaceId.trim()) errors.workspaceId = 'Enter a workspace ID.';
  if (!draft.buyerId.trim()) errors.buyerId = 'Enter a buyer ID.';
  if (!/^[A-Z]{2}$/.test(draft.destinationCountry)) errors.destinationCountry = 'Enter a two-letter country code.';
  if (!draft.material) errors.material = 'Select a catalog material.';
  if (!draft.unit.trim()) errors.unit = 'Enter the quantity unit.';
  for (const key of ['quantity', 'currentStock', 'monthlyConsumption', 'budget'] as const) {
    const value = draft[key].trim();
    if (key === 'budget' && !value) continue;
    if (!decimal.test(value) || ((key === 'quantity' || key === 'monthlyConsumption' || key === 'budget') && /^0(?:\.0+)?$/.test(value))) {
      errors[key] = key === 'currentStock' || key === 'budget'
        ? key === 'budget' ? 'Enter a positive decimal without separators or exponent notation.' : 'Enter a non-negative decimal without separators or exponent notation.'
        : 'Enter a positive decimal without separators or exponent notation.';
    }
  }
  if (!integer.test(draft.needByMonth) || Number(draft.needByMonth) < 1 || Number(draft.needByMonth) > 6) errors.needByMonth = 'Enter a whole number from 1 to 6.';
  if (!integer.test(draft.leadTimeMonths) || Number(draft.leadTimeMonths) > 3) errors.leadTimeMonths = 'Enter a whole number from 0 to 3.';
  for (const key of ['quoteCurrency', 'settlementCurrency', 'reportingCurrency'] as const) {
    if (!code.test(draft[key])) errors[key] = 'Enter an uppercase currency code.';
  }
  return errors;
}

export function createPlanningRequest(draft: PlanningDraft): PlanningRequest {
  const request: PlanningRequest = {
    contract_version: '0.1.0', workspace_id: draft.workspaceId.trim(),
    buyer: { id: draft.buyerId.trim(), destination_country: draft.destinationCountry.trim() },
    material: draft.material, quantity: { amount: draft.quantity.trim(), unit: draft.unit.trim() },
    current_stock: { amount: draft.currentStock.trim(), unit: draft.unit.trim() },
    monthly_consumption: { amount: draft.monthlyConsumption.trim(), unit: draft.unit.trim() },
    need_by_month: Number(draft.needByMonth), lead_time_months: Number(draft.leadTimeMonths),
    quote_currency: draft.quoteCurrency.trim(), settlement_currency: draft.settlementCurrency.trim(),
    reporting_currency: draft.reportingCurrency.trim(), risk_appetite: draft.riskAppetite,
  };
  if (draft.budget.trim()) request.budget = { amount: draft.budget.trim(), currency: draft.reportingCurrency.trim() };
  return request;
}

export function draftFromRequest(request: PlanningRequest): PlanningDraft {
  return {
    workspaceId: request.workspace_id, buyerId: request.buyer.id, destinationCountry: request.buyer.destination_country,
    material: request.material, unit: request.quantity.unit, quantity: request.quantity.amount,
    currentStock: request.current_stock.amount, monthlyConsumption: request.monthly_consumption.amount,
    needByMonth: String(request.need_by_month), leadTimeMonths: String(request.lead_time_months),
    quoteCurrency: request.quote_currency, settlementCurrency: request.settlement_currency,
    reportingCurrency: request.reporting_currency, riskAppetite: request.risk_appetite, budget: request.budget?.amount ?? '',
  };
}

export function formatDecimal(value: string): string {
  const match = /^(-?)(\d+)(\.\d+)?$/.exec(value);
  if (!match) return value;
  return `${match[1]}${match[2].replace(/\B(?=(\d{3})+(?!\d))/g, ',')}${match[3] ?? ''}`;
}

export type ResponseState = 'ready' | 'unavailable' | 'empty';
export function responseState(response: PlanResponse): ResponseState {
  if (response.contract_version !== '0.1.0') throw new Error('Unexpected recommendation contract version.');
  if (response.status === 'ready' && response.plan && response.availability.code === 'available') {
    if (!validPlan(response.plan)) throw new Error('The recommendation service returned an invalid plan.');
    return 'ready';
  }
  if (response.status === 'unavailable' && response.plan === null) return 'unavailable';
  if (response.status === 'empty' && response.plan === null) return 'empty';
  throw new Error('The recommendation service returned an inconsistent result.');
}

export class ApiError extends Error {
  constructor(message: string, readonly field?: string) { super(message); }
}

async function readJson(response: Response): Promise<unknown> {
  try { return await response.json(); } catch { throw new ApiError('The gateway returned an unreadable response. Try again.'); }
}

export async function fetchMaterials(signal?: AbortSignal): Promise<MaterialsResponse> {
  let response: Response;
  try { response = await fetch('/v2/materials', { signal }); }
  catch (error) { if (signal?.aborted) throw error; throw new ApiError('Cannot reach the catalog. Check the local gateway and retry.'); }
  const data = await readJson(response);
  if (!response.ok) throw toApiError(data, 'Catalog request failed.');
  if (!isObject(data) || data.contract_version !== '0.1.0' || !Array.isArray(data.materials) || !validAvailability(data.availability) || !data.materials.every(validMaterial)) {
    throw new ApiError('The catalog response is invalid. Retry after checking the gateway.');
  }
  return data as unknown as MaterialsResponse;
}

export async function fetchPlan(request: PlanningRequest, signal?: AbortSignal): Promise<PlanResponse> {
  let response: Response;
  try {
    response = await fetch('/v2/plans/recommendations', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(request), signal,
    });
  } catch (error) { if (signal?.aborted) throw error; throw new ApiError('Cannot reach the recommendation service. Check the local gateway and retry.'); }
  const data = await readJson(response);
  if (!response.ok) throw toApiError(data, `Recommendation request failed (${response.status}).`);
  if (!isObject(data) || !validAvailability(data.availability) || !isObject(data.request) || !Array.isArray(data.warnings) || !data.warnings.every(isString)) {
    throw new ApiError('The recommendation service returned an invalid result.');
  }
  const plan = data as unknown as PlanResponse;
  responseState(plan);
  return plan;
}

function isObject(value: unknown): value is Record<string, unknown> { return typeof value === 'object' && value !== null && !Array.isArray(value); }
function isString(value: unknown): value is string { return typeof value === 'string'; }
function stringOrNull(value: unknown): boolean { return value === null || isString(value); }
function validAvailability(value: unknown): boolean { return isObject(value) && isString(value.code) && isString(value.reason); }
function validMaterial(value: unknown): boolean { return isObject(value) && isString(value.key) && isString(value.display_name) && isString(value.source_series) && isString(value.source_unit); }
function validMoney(value: unknown): boolean { return isObject(value) && isString(value.amount) && isString(value.currency); }
function validQuantity(value: unknown): boolean { return isObject(value) && isString(value.amount) && isString(value.unit); }
function validUnitPrice(value: unknown): boolean { return isObject(value) && validMoney(value.money) && isString(value.per_unit); }
function validPurchase(value: unknown): boolean {
  return isObject(value) && typeof value.purchase_month === 'number' && Number.isInteger(value.purchase_month) && isString(value.calendar_month) && validQuantity(value.quantity) && isString(value.percent) && validUnitPrice(value.expected_unit_price);
}
function validComparison(value: unknown): boolean {
  return isObject(value) && isString(value.name) && isString(value.schedule) && validMoney(value.expected_cost) && validMoney(value.p90_cost) && validMoney(value.stockout_penalty);
}
function validEvidence(value: unknown): boolean {
  return isObject(value) && isString(value.source) && isString(value.used) && isString(value.status) && stringOrNull(value.retrieved_at) && stringOrNull(value.observation_end) && isString(value.basis);
}
function validPlan(value: unknown): boolean {
  return isObject(value) && isString(value.plan_id) && isString(value.plan_version) && value.computation_only === true &&
    isString(value.action) && isString(value.summary) && validUnitPrice(value.current_price) &&
    Array.isArray(value.schedule) && value.schedule.every(validPurchase) &&
    Array.isArray(value.comparisons) && value.comparisons.every(validComparison) &&
    Array.isArray(value.evidence) && value.evidence.every(validEvidence) &&
    stringOrNull(value.legacy_recommendation_id) && value.precision === 'legacy_float_rounded';
}
function toApiError(value: unknown, fallback: string): ApiError {
  const error = isObject(value) ? (value as unknown as ErrorResponse).error : undefined;
  return new ApiError(error && typeof error.message === 'string' ? error.message : fallback,
    error && typeof error.field === 'string' ? error.field : undefined);
}
