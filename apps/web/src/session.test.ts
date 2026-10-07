import { describe, expect, it } from 'vitest';
import type { SessionResponse } from '../../../contracts/generated/types';
import { sessionPresentation } from './page';
import { fetchSession } from './state';

const base: SessionResponse = {
  contract_version: '0.2.0', principal_id: 'service-1', workspace_id: 'workspace-1',
  role: 'viewer', development_only: true,
};

async function responseFor(session: unknown): Promise<SessionResponse> {
  const previous = globalThis.fetch;
  let headers: HeadersInit | undefined;
  globalThis.fetch = async (_url, init) => {
    headers = init?.headers;
    return new Response(JSON.stringify(session), { status: 200 });
  };
  try {
    const value = await fetchSession();
    expect(headers).toBeUndefined();
    return value;
  } finally { globalThis.fetch = previous; }
}

describe('session identity mode boundary', () => {
  it('accepts old and explicit local development sessions', async () => {
    expect(await responseFor(base)).toEqual(base);
    expect(await responseFor({ ...base, identity_mode: 'development' })).toMatchObject({ identity_mode: 'development' });
  });

  it('accepts only explicitly marked private machine sessions with development_only false', async () => {
    const privateMachine: SessionResponse = { ...base, development_only: false, identity_mode: 'private_machine' };
    expect(await responseFor(privateMachine)).toEqual(privateMachine);
  });

  it.each([
    { ...base, development_only: false },
    { ...base, development_only: false, identity_mode: 'development' },
    { ...base, identity_mode: 'private_machine' },
    { ...base, identity_mode: 'unknown' },
    { ...base, development_only: 'false', identity_mode: 'private_machine' },
  ])('rejects incompatible or forged mode flags: %j', async invalid => {
    const previous = globalThis.fetch;
    globalThis.fetch = async () => new Response(JSON.stringify(invalid), { status: 200 });
    try { await expect(fetchSession()).rejects.toMatchObject({ code: 'invalid_response' }); }
    finally { globalThis.fetch = previous; }
  });

  it('labels a machine identity as a service caller and an anonymous browser as unavailable', () => {
    const machine = sessionPresentation({ ...base, development_only: false, identity_mode: 'private_machine' }, '');
    expect(machine.masthead).toMatch(/private machine/i);
    expect(machine.details).toMatch(/service caller/i);
    expect(machine.details).toMatch(/sign-in unavailable/i);
    const anonymous = sessionPresentation(null, 'Authentication required.');
    expect(anonymous.details).toMatch(/sign-in unavailable/i);
    expect(anonymous.masthead).not.toMatch(/editor|viewer/i);
    const staleMachine = sessionPresentation({ ...base, development_only: false, identity_mode: 'private_machine' }, 'Authentication required.');
    expect(staleMachine.masthead).toMatch(/unavailable/i);
    expect(staleMachine.details).not.toMatch(/service caller/i);
  });
});
