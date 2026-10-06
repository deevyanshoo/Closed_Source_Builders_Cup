import { describe, expect, it } from 'vitest';
import { createPlanningRequest, fetchMaterials, fetchPlan, formatDecimal, initialDraft, responseState, validateDraft } from './planning';
import type { PlanResponse } from '../../../contracts/generated/types';

describe('planning request', () => {
  it('preserves exact decimal spellings through JSON serialization', () => {
    const draft = { ...initialDraft, quantity: '9007199254740993.123456789012345678', budget: '12345678901234567890.123456789012345678' };
    const json = JSON.stringify(createPlanningRequest(draft));
    expect(JSON.parse(json).quantity.amount).toBe(draft.quantity);
    expect(JSON.parse(json).budget.amount).toBe(draft.budget);
    expect(json).not.toContain('9.007199254740993e');
  });

  it('keeps grouping exact without Number conversion', () => {
    expect(formatDecimal('12345678901234567890.123456789012345678')).toBe('12,345,678,901,234,567,890.123456789012345678');
  });

  it('rejects malformed decimals with field-level feedback', () => {
    expect(validateDraft({ ...initialDraft, currentStock: '-1' }).currentStock).toMatch(/non-negative/i);
    expect(validateDraft({ ...initialDraft, quantity: '1e6' }).quantity).toMatch(/decimal/i);
    expect(validateDraft({ ...initialDraft, budget: '0' }).budget).toMatch(/positive/i);
  });
});

describe('response state', () => {
  const base: Pick<PlanResponse, 'contract_version' | 'request' | 'warnings'> = { contract_version: '0.1.0', request: createPlanningRequest(initialDraft), warnings: [] };
  it('classifies semantic 200 responses without displaying a plan', () => {
    const unavailable: PlanResponse = { ...base, status: 'unavailable', availability: { code: 'unsupported_context', reason: 'No adapter' }, plan: null };
    const empty: PlanResponse = { ...base, status: 'empty', availability: { code: 'empty_result', reason: 'No schedule' }, plan: null };
    expect(responseState(unavailable)).toBe('unavailable');
    expect(responseState(empty)).toBe('empty');
  });
  it('treats an inconsistent ready response as an error', () => {
    expect(() => responseState({ ...base, status: 'ready', availability: { code: 'available', reason: '' }, plan: null })).toThrow();
  });
  it('submits exact string values and classifies unavailable without a plan', async () => {
    const originalFetch = globalThis.fetch;
    let submitted: string | undefined;
    globalThis.fetch = async (_url, init) => {
      submitted = String(init?.body);
      return new Response(JSON.stringify({ ...base, status: 'unavailable', availability: { code: 'unsupported_context', reason: 'No adapter' }, plan: null }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    };
    try {
      const request = createPlanningRequest({ ...initialDraft, quantity: '9007199254740993.123456789012345678', budget: '12345678901234567890.123456789012345678' });
      const result = await fetchPlan(request);
      expect(JSON.parse(submitted!).quantity.amount).toBe(request.quantity.amount);
      expect(JSON.parse(submitted!).budget.amount).toBe(request.budget?.amount);
      expect(responseState(result)).toBe('unavailable');
    } finally { globalThis.fetch = originalFetch; }
  });
  it('surfaces provider failure and rejects malformed success payloads', async () => {
    const originalFetch = globalThis.fetch;
    try {
      globalThis.fetch = async () => new Response(JSON.stringify({ contract_version: '0.1.0', error: { code: 'provider_unavailable', message: 'Python service unavailable' } }), { status: 503 });
      await expect(fetchPlan(createPlanningRequest(initialDraft))).rejects.toThrow('Python service unavailable');
      globalThis.fetch = async () => new Response(JSON.stringify({ status: 'ready' }), { status: 200 });
      await expect(fetchPlan(createPlanningRequest(initialDraft))).rejects.toThrow('invalid result');
    } finally { globalThis.fetch = originalFetch; }
  });
  it('rejects incomplete ready plans and malformed catalog rows before rendering', async () => {
    const originalFetch = globalThis.fetch;
    try {
      globalThis.fetch = async () => new Response(JSON.stringify({ ...base, status: 'ready', availability: { code: 'available', reason: '' }, plan: {} }), { status: 200 });
      await expect(fetchPlan(createPlanningRequest(initialDraft))).rejects.toThrow('invalid');
      globalThis.fetch = async () => new Response(JSON.stringify({ contract_version: '0.1.0', availability: { code: 'available', reason: '' }, materials: [{}] }), { status: 200 });
      await expect(fetchMaterials()).rejects.toThrow('invalid');
    } finally { globalThis.fetch = originalFetch; }
  });
});
