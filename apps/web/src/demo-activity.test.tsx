import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
const modulePath = './demo-activity';
const activity = await import(modulePath).catch(() => null);

describe('assistant activity feedback', () => {
  it('provides a shared Thinking Orbs component for actual task states', () => {
    expect(activity?.AssistantOrb).toBeTypeOf('function');
    expect(activity?.ActivityIndicator).toBeTypeOf('function');
  });
  it('rests while idle, muted or failed and animates only active states', () => {
    expect(activity).not.toBeNull();
    for (const state of ['idle', 'muted', 'error']) {
      const html = renderToStaticMarkup(<activity.AssistantOrb state={state} />);
      expect(html).toContain('data-paused="true"');
    }
    for (const state of ['connecting', 'listening', 'thinking', 'speaking', 'searching']) {
      expect(renderToStaticMarkup(<activity.AssistantOrb state={state} />)).toContain('data-paused="false"');
    }
  });
  it('announces a readable task label while keeping the decorative orb out of screen-reader speech', () => {
    expect(activity).not.toBeNull();
    const html = renderToStaticMarkup(<activity.ActivityIndicator state="thinking" label="Reviewing your question" detail="Your workspace remains unchanged." />);
    expect(html).toContain('role="status"');
    expect(html).toContain('aria-hidden="true"');
    expect(html).toContain('Reviewing your question');
    expect(html).toContain('Your workspace remains unchanged.');
  });
  it('distinguishes Gemini extraction from rules and never labels deterministic comparisons as model work', () => {
    expect(activity).not.toBeNull();
    expect(activity.workspaceActivity('', 'gemini')).toBeNull();
    expect(activity.workspaceActivity('extract', 'gemini').label).toContain('Gemini');
    expect(activity.workspaceActivity('extract', 'rules').label).not.toContain('Gemini');
    expect(activity.workspaceActivity('plan', 'gemini').detail).toContain('Deterministic');
    expect(activity.workspaceActivity('recovery', 'gemini').detail).toContain('Deterministic');
    expect(activity.workspaceActivity('rfq', 'gemini').detail).toContain('unsent');
  });
});
