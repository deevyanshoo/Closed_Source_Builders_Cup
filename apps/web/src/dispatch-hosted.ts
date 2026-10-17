import { createContext } from 'react';

/** True when /dispatch was opened from the hosted demo, which serves only /demo and /dispatch. */
export const HostedDemoContext = createContext(false);

export const HOSTED_RECOVERY_NOTE = 'Recovery comparisons run in the local workspace and are not part of the hosted demo.';
