import { describe, expect, it } from 'vitest';
import type { SavedPlan, SessionResponse } from '../../../contracts/generated/types';
import { actionArguments, executionFor, fetchSession, loadHistory, savePlan, stateRequest } from './state';
import { createPlanningRequest, initialDraft } from './planning';

const request = createPlanningRequest({ ...initialDraft, workspaceId: 'w', buyerId: 'buyer', material: 'steel', quantity: '100.25' });
const plan: SavedPlan = {
  saved_plan_id: 'plan-1', workspace_id: 'w', revision: 3, created_at: '2026-10-07T00:00:00Z', updated_at: '2026-10-07T00:00:00Z',
  snapshot: { contract_version: '0.1.0', status: 'unavailable', availability: { code: 'unsupported_context', reason: 'fixture' }, request, plan: null, warnings: [] },
};

describe('persistent planning boundary', () => {
  it('binds every RFQ argument to the saved snapshot rather than edited form input', () => {
    expect(actionArguments(plan)).toEqual({ buyer_id: 'buyer', material: 'steel', quantity: { amount: '100.25', unit: 'kg' }, destination_country: 'IN', quote_currency: 'USD', settlement_currency: 'INR', reporting_currency: 'INR' });
  });

  it('keeps the execution key stable for a retry of the same approved ready snapshot', () => {
    const ready = { ...plan, snapshot: { ...plan.snapshot, status: 'ready' as const, plan: { plan_version: 'v3' } as SavedPlan['snapshot']['plan'] } };
    const first = executionFor(ready, 'approval-1', 'key-1');
    expect(executionFor(ready, 'approval-1', first.idempotency_key)).toEqual(first);
    expect(first).toMatchObject({ contract_version: '0.2.0', workspace_id: 'w', saved_plan_id: 'plan-1', expected_revision: 3, approval_id: 'approval-1', idempotency_key: 'key-1' });
  });

  it('refuses execution from an unavailable saved snapshot', () => {
    expect(() => executionFor(plan, 'approval-1', 'key-1')).toThrow(/ready saved plan/i);
  });

  it('discards late history when the selected plan changes during requests', async () => {
    const old = globalThis.fetch;
    let release!: () => void;
    const gate = new Promise<void>(resolve => { release = resolve; });
    globalThis.fetch = async url => {
      await gate;
      const path = String(url);
      const key = path.endsWith('/approvals') ? 'approvals' : path.endsWith('/receipts') ? 'receipts' : 'events';
      return new Response(JSON.stringify({ contract_version: '0.2.0', [key]: [] }), { status: 200 });
    };
    let current = true;
    try {
      const pending = loadHistory('plan-1', () => current);
      current = false; release();
      expect(await pending).toBeNull();
    } finally { globalThis.fetch = old; }
  });

  it('rejects a malformed session and does not send an actor or bearer header', async () => {
    const old = globalThis.fetch;
    let init: RequestInit | undefined;
    globalThis.fetch = async (_url, value) => { init = value; return new Response(JSON.stringify({ contract_version: '0.2.0', role: 'editor' }), { status: 200 }); };
    try { await expect(fetchSession()).rejects.toThrow(/invalid/i); expect(init?.headers).toBeUndefined(); }
    finally { globalThis.fetch = old; }
  });

  it('sends a fresh planning request for save and classifies conflict', async () => {
    const old = globalThis.fetch;
    let body: unknown;
    globalThis.fetch = async (_url, init) => { body = JSON.parse(String(init?.body)); return new Response(JSON.stringify({ contract_version: '0.2.0', error: { code: 'stale_plan', message: 'Current revision changed' } }), { status: 409 }); };
    try { await expect(savePlan('w', request)).rejects.toMatchObject({ status: 409, code: 'stale_plan' }); expect(body).toEqual({ contract_version: '0.2.0', planning_request: request }); }
    finally { globalThis.fetch = old; }
  });

  it('accepts a complete session envelope', async () => {
    const old = globalThis.fetch;
    const session: SessionResponse = { contract_version: '0.2.0', principal_id: 'dev-editor', workspace_id: 'w', role: 'editor', development_only: true };
    globalThis.fetch = async () => new Response(JSON.stringify(session), { status: 200 });
    try { expect(await fetchSession()).toEqual(session); }
    finally { globalThis.fetch = old; }
  });

  it('rejects unsupported state versions before they reach UI', async () => {
    const old = globalThis.fetch;
    globalThis.fetch = async () => new Response(JSON.stringify({ contract_version: '0.1.0', plans: [] }), { status: 200 });
    try { await expect(stateRequest('/v2/workspaces/w/plans', 'plans')).rejects.toThrow(/invalid/i); }
    finally { globalThis.fetch = old; }
  });

  it('rejects a saved plan whose nested snapshot cannot be rendered', async () => {
    const old = globalThis.fetch;
    globalThis.fetch = async () => new Response(JSON.stringify({ contract_version: '0.2.0', plan: { ...plan, snapshot: { contract_version: '0.1.0', status: 'ready', request, plan: null, availability: { code: 'available', reason: '' }, warnings: [] } } }), { status: 200 });
    try { await expect(stateRequest('/v2/plans/plan-1', 'plan')).rejects.toThrow(/invalid/i); }
    finally { globalThis.fetch = old; }
  });
});
