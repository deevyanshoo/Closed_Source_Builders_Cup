import { describe, expect, it } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import proposalRaw from '../../../contracts/examples/voice_http_text_proposal_result.json?raw';
import { VoiceProposalPanel } from './voice-page';
import type { VoiceView } from './voice';

const outcome = JSON.parse(proposalRaw).outcome;
const base: VoiceView = { kind: 'proposed', example: 'recovery', arrivals: {}, assignments: {}, source: 'saved', contexts: [], hasMore: false, snapshotText: '', proposal: outcome };

describe('text proposal presentation', () => {
  it('shows unverified changed receipt UTCs, every assignment and a deliberate confirmation action', () => {
    const html = renderToStaticMarkup(<VoiceProposalPanel view={base} onConfirm={() => {}} />);
    expect(html).toContain('Unverified interpretation');
    expect(html).toContain('2026-10-10T09:00:00Z');
    expect(html).toContain('2026-10-10T10:00:00Z');
    for (const item of outcome.call.arguments.assignments) {
      expect(html).toContain(item.order_id);
      expect(html).toContain(item.period_id);
    }
    expect(html).toContain('Compare these exact changes');
    expect(html).not.toContain('feasible');
    expect(html).not.toContain('confidence');
  });
  it('labels the proposal as offline rule-based, not Gemini (#96)', () => {
    const html = renderToStaticMarkup(<VoiceProposalPanel view={base} onConfirm={() => {}} />);
    expect(html).toContain('Offline rule-based proposal, not Gemini');
    const refused = renderToStaticMarkup(<VoiceProposalPanel view={{ ...base, kind: 'failed', proposal: undefined, message: 'Text proposal unavailable. Use the typed comparison form.' }} onConfirm={() => {}} />);
    expect(refused).toContain('Offline rule-based proposals understand, for example');
  });
  it('does not render a proposal after fixed refusal', () => {
    const html = renderToStaticMarkup(<VoiceProposalPanel view={{ ...base, kind: 'failed', proposal: undefined, message: 'Text proposal unavailable. Use the typed comparison form.' }} onConfirm={() => {}} />);
    expect(html).not.toContain('Compare these exact changes');
    expect(html).toContain('Text proposal unavailable. Use the typed comparison form.');
  });
});
