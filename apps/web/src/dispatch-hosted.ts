import { createContext } from 'react';

/** True when /dispatch was opened from the hosted demo, which serves only /demo and /dispatch. */
export const HostedDemoContext = createContext(false);

export const HOSTED_RECOVERY_NOTE = 'Recovery comparisons run in the local workspace and are not part of the hosted demo.';

export type HostedCapabilities = { hosted: boolean; served_pages: string[]; recovery_link: string | null };

/** The server's statement of what it serves. A local dev server answers with the SPA page, so this returns null there. */
export async function readCapabilities(send: typeof fetch = fetch): Promise<HostedCapabilities | null> {
  try {
    const response = await send('/api/demo/capabilities', { credentials: 'same-origin' });
    if (!response.ok || !(response.headers.get('Content-Type') ?? '').includes('application/json')) return null;
    const body = await response.json() as Partial<HostedCapabilities>;
    return typeof body.hosted === 'boolean' && Array.isArray(body.served_pages) ? body as HostedCapabilities : null;
  } catch { return null; }
}
