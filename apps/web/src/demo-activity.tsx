import { ThinkingOrb } from 'thinking-orbs';
import type { OrbState } from 'thinking-orbs';
import './demo-activity.css';

export type ActivityState = 'idle' | 'connecting' | 'listening' | 'thinking' | 'speaking' | 'muted' | 'error' | 'working' | 'searching' | 'solving' | 'composing';
type OrbProps = { state: ActivityState; size?: 20 | 64; theme?: 'light' | 'dark'; className?: string };
const orbStates: Record<ActivityState, OrbState> = {
  idle: 'breathing', muted: 'breathing', error: 'breathing', connecting: 'connecting',
  listening: 'listening', thinking: 'solving', speaking: 'composing', working: 'working',
  searching: 'searching', solving: 'solving', composing: 'composing',
};

export function AssistantOrb({ state, size = 64, theme = 'light', className = '' }: OrbProps) {
  const paused = state === 'idle' || state === 'muted' || state === 'error';
  return <span className={`demo-orb ${className}`} data-activity={state} data-paused={paused}>
    <ThinkingOrb state={orbStates[state]} size={size} theme={theme} paused={paused} gravity={false} aria-hidden="true" />
  </span>;
}

export function ActivityIndicator({ state, label, detail, size = 20, theme = 'light', className = '' }: OrbProps & { label: string; detail?: string }) {
  return <div className={`demo-activity ${className}`} role="status" aria-live="polite" aria-atomic="true" data-activity={state}>
    <AssistantOrb state={state} size={size} theme={theme} />
    <span className="demo-activity-copy"><strong>{label}</strong>{detail && <small>{detail}</small>}</span>
  </div>;
}

export function workspaceActivity(busy: string, extractor: 'gemini' | 'rules'): { state: ActivityState; label: string; detail: string } | null {
  if (!busy) return null;
  if (busy === 'extract') return extractor === 'gemini'
    ? { state: 'thinking', label: 'Gemini is reading the quote', detail: 'Extracting document terms. Review the returned fields before acceptance.' }
    : { state: 'working', label: 'Reading the quote with rules', detail: 'Rule-based extraction; this request does not call an AI model.' };
  if (busy === 'plan') return { state: 'solving', label: 'Comparing supplier options', detail: 'Deterministic cost, quantity and arrival checks using your reviewed inputs.' };
  if (busy === 'recovery') return { state: 'solving', label: 'Checking recovery options', detail: 'Deterministic comparison under the hypothetical supplier delay.' };
  if (busy === 'rfq') return { state: 'composing', label: 'Preparing the RFQ draft', detail: 'Building an unsent draft from the reviewed plan. No supplier message is sent.' };
  if (busy === 'company') return { state: 'connecting', label: 'Opening the company workspace', detail: 'Restoring this company workspace and its current context.' };
  return { state: 'working', label: 'Updating the workspace', detail: 'Waiting for the service to confirm the current state.' };
}
