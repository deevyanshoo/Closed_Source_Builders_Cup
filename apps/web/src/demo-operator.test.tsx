import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';
import type { NewsView, OfferView, OperatorTurn, OperatorHistory, PlanView, RecoveryView } from './demo-api';
import { acceptLiveOperatorRequest, acceptsOperatorTurn, applyOperatorResult, canApplyProposal, claimOperatorVoiceRequest, confirmVoiceTransition, focusCurrentProposal, focusValidOperatorSeed, focusVoiceReturn, formatOperatorAttention, formatOperatorWatch, formatRiskCheckedAt, initialOperatorState, latestVoiceResearch, OperatorPanel, operatorSeedMessage, proposalPreviewKey, proposalPreviewReady, researchFromTurn, resolveOperatorEvidence, routeVoiceExit, scopedPreparedDrafts, scopedVoiceHistory, shouldFollowOperatorHistory, validateAudioCapture, VoiceConversationSurface, voiceRequestIsCurrent } from './demo-operator';

const scope = { sessionId: 's1', companyId: 'solara-cables', contextVersion: 1, epoch: 0 };
const proposal = { proposal_id: 'p1', kind: 'watch' as const, summary: 'Watch copper', parameters: { material_id: 'copper', threshold: '12.5' }, plan_id: null, offer_versions: {}, context_version: 1, expires_at: '2099-10-14T12:00:00Z' };
const turn: OperatorTurn = { turn_id: 't1', company_id: 'solara-cables', context_version: 1, status: 'proposal', assistant_text: 'A reviewable watch is ready.', agent_steps: [{ agent: 'evidence', role: 'exposure specialist', status: 'completed', model: 'gemini-test', provider_call: 'real', tool: 'inspect_news', summary: 'Matched source e1' }], evidence_ids: ['e1'], proposal, read_only: true };
const news: NewsView = { company_id: 'solara-cables', context_version: 1, checked_at: '2026-10-14T10:00:00Z', cache_state: 'fresh', sources: [], fx: [], limitations: [], items: [{ item_id: 'e1', title: '<script>Freight pressure</script>', url: 'https://example.org/article', publisher: 'Example News', provider: 'eia', published_at: null, first_seen_at: null, retrieved_at: '2026-10-14T10:00:00Z', source_tier: 'primary', source_reliability: 'high', reliability_reason: '', category: 'energy', matched_materials: ['copper'], matched_routes: [], matched_currencies: [], relevance_reason: '', implication: '', implication_confidence: 'conditional', next_steps: [], synthetic: false }] };
const quote: OfferView = { offer_id: 'q1', version: 2, status: 'saved', synthetic: true, document: { document_id: 'd1', filename: 'reviewed.pdf', media_type: 'application/pdf', byte_size: 10 }, extractor: 'gemini', extractor_detail: {}, as_of: '2026-10-14', fields: { supplier_name: { name: 'supplier_name', kind: 'text', status: 'corrected', value: 'Swift Supply', source: 'buyer', evidence: [], reasons: [] } }, items: [], review_flags: [], normalized: {}, blocking_reasons: [] };

describe('operator response boundary', () => {
  it('names each reviewable action plainly and makes the email simulation outcome explicit', () => {
    const labels = {
      rfq: 'Unsent RFQ draft', simulate_email: 'Email simulation', review_quotes: 'Quote review',
      recovery: 'Supplier-delay scenario', research: 'Internet research', watch: 'Watch item',
    } as const;
    for (const [kind, label] of Object.entries(labels)) {
      const html = renderToStaticMarkup(<OperatorPanel scope={scope} csrf="csrf" dirty={false}
        initialState={{ ...initialOperatorState, scope, turn: { ...turn, proposal: { ...proposal, kind: kind as keyof typeof labels } } }}
        onSession={() => {}} onUnknown={() => {}} />);
      const card = html.slice(html.indexOf('aria-label="Current proposal requiring review"'), html.indexOf('aria-label="Assistant conversation"'));
      expect(card).toContain(`Current proposal · ${label}`);
      expect(card).not.toContain(`Reviewable ${kind} proposal`);
    }
    const simulated = renderToStaticMarkup(<OperatorPanel scope={scope} csrf="csrf" dirty={false}
      initialState={{ ...initialOperatorState, scope, turn: { ...turn, proposal: { ...proposal, kind: 'simulate_email', parameters: { recipient_label: 'Supplier', subject: 'Availability', body: 'Please confirm stock.' } } } }}
      onSession={() => {}} onUnknown={() => {}} />);
    expect(simulated).toContain('Record simulated sent message');
    expect(simulated).toContain('No external delivery occurs.');
  });
  it('gives a pending proposal the main review flow before attention and history', () => {
    const overview = { company_id: scope.companyId, context_version: scope.contextVersion, available: true, agents: [], attention: [], watch_items: [], limitations: [] };
    const html = renderToStaticMarkup(<OperatorPanel scope={scope} csrf="csrf" dirty={false}
      initialState={{ ...initialOperatorState, scope, turn, overview }} riskSummary={{ high: 1, medium: 2, low: 3, checkedAt: '2026-10-15T23:25:33Z' }}
      onSession={() => {}} onUnknown={() => {}} />);
    const reviewStart = html.indexOf('class="demo-assistant-review-flow"');
    const proposalAt = html.indexOf('aria-label="Current proposal requiring review"');
    const attentionAt = html.indexOf('Company attention', proposalAt);
    const historyAt = html.indexOf('aria-label="Assistant conversation"');
    const composerAt = html.indexOf('class="demo-operator-chat demo-assistant-composer"');
    expect(reviewStart).toBeGreaterThan(0);
    expect(reviewStart).toBeLessThan(proposalAt);
    expect(proposalAt).toBeLessThan(attentionAt);
    expect(attentionAt).toBeLessThan(historyAt);
    expect(historyAt).toBeLessThan(composerAt);
    expect(html).not.toContain('2026-10-15T23:25:33Z');
  });
  it('formats risk check times for people and gives invalid values a safe fallback', () => {
    expect(formatRiskCheckedAt('2026-10-15T23:25:33.039261+00:00')).toMatch(/2026/);
    expect(formatRiskCheckedAt('2026-10-15T23:25:33.039261+00:00')).not.toContain('T23:25:33');
    expect(formatRiskCheckedAt('not-a-date')).toBe('time unavailable');
  });
  it('offers recent earlier voice exchanges only for the current company and session', () => {
    const answered = { ...turn, status: 'completed' as const, proposal: null, assistant_text: 'Earlier copper answer.' };
    const exchanges = [{ id: 'old', question: 'Earlier question?', turn: answered }, { id: 'latest', question: 'Latest question?', turn: { ...answered, assistant_text: 'Latest answer.' } }];
    const current = { ...initialOperatorState, scope, exchanges };
    expect(scopedVoiceHistory(current, scope)).toEqual([exchanges[0]]);
    expect(scopedVoiceHistory(current, { ...scope, companyId: 'another-company' })).toEqual([]);
    expect(scopedVoiceHistory(current, { ...scope, sessionId: 'another-session' })).toEqual([]);
    const many = { ...current, exchanges: Array.from({ length: 10 }, (_, index) => ({ id: index, question: `Question ${index}`, answer: `Answer ${index}` })) };
    expect(scopedVoiceHistory(many, scope).map(exchange => exchange.question)).toEqual(['Question 4', 'Question 5', 'Question 6', 'Question 7', 'Question 8']);
    const voice = renderToStaticMarkup(<VoiceConversationSurface open onClose={() => {}} voice={<p>Listening</p>}
      activity={<article>Latest question? Latest answer.</article>}
      history={<ol>{scopedVoiceHistory(current, scope).map(exchange => <li key={exchange.id}>{exchange.question} {exchange.turn?.assistant_text}</li>)}</ol>} />);
    expect(voice).toContain('Multilingual voice &amp; chat · Gemini on Vertex AI');
    expect(voice).toContain('Talk naturally in your preferred language. Review proposed actions before they run.');
    expect(voice).toContain('Action approvals');
    expect(voice).toContain('Recent conversation');
    expect(voice).toMatch(/<details class="demo-voice-history"><summary>Recent conversation<\/summary>/);
    expect(voice).toContain('Earlier question?');
    expect(voice).toContain('Earlier copper answer.');
    expect(voice).toContain('Latest question?');
    expect(voice.match(/Latest answer\./g)).toHaveLength(1);
  });
  it('requires opening the full draft for the exact proposal before confirmation', () => {
    const draft = { ...turn, proposal: { ...proposal, kind: 'simulate_email' as const,
      parameters: { recipient_label: 'Supplier desk', subject: 'Availability', body: 'Please confirm a 400 kg shipment.' } } };
    const html = renderToStaticMarkup(<OperatorPanel scope={scope} csrf="csrf" dirty={false}
      initialState={{ ...initialOperatorState, scope, turn: draft, reviewed: true }} onSession={() => {}} onUnknown={() => {}} />);
    const card = html.slice(html.indexOf('aria-label="Current proposal requiring review"'), html.indexOf('aria-label="Assistant conversation"'));
    expect(card).toContain('Supplier desk');
    expect(card).toContain('Availability');
    expect(card).toMatch(/<details[^>]*><summary>Full draft preview<\/summary>/);
    expect(card).toContain('Please confirm a 400 kg shipment.');
    expect(card.indexOf('Full draft preview')).toBeLessThan(card.indexOf('Record simulated sent message'));
    expect(card).toContain('Open the full draft preview before confirming.');
    expect(card).toMatch(/<input type="checkbox"[^>]*disabled=""/);
    expect(card).toMatch(/disabled=""[^>]*>Record simulated sent message<\/button>/);
    const opened = proposalPreviewKey(scope, draft.proposal);
    expect(proposalPreviewReady(scope, draft, null)).toBe(false);
    expect(proposalPreviewReady(scope, draft, opened)).toBe(true);
    expect(proposalPreviewReady({ ...scope, companyId: 'other-company' }, draft, opened)).toBe(false);
    expect(proposalPreviewReady(scope, { ...draft, proposal: { ...draft.proposal, proposal_id: 'next-proposal' } }, opened)).toBe(false);
    expect(opened).not.toBe(proposalPreviewKey({ ...scope, companyId: 'other-company' }, draft.proposal));
    expect(opened).not.toBe(proposalPreviewKey(scope, { ...draft.proposal, proposal_id: 'next-proposal' }));
  });
  it('shows an RFQ proposal without its repeated classification header', () => {
    const body = 'SYNTHETIC TEST DOCUMENT — UNSENT BRIDGE REQUEST FOR QUOTATION DRAFT\r\nTo: Supplier desk\r\nTerms remain unsent.';
    const rfqTurn = { ...turn, proposal: { ...proposal, kind: 'rfq' as const,
      parameters: { draft_preview: { recipient_label: 'Supplier desk', subject: 'Bridge request', body } } } };
    const html = renderToStaticMarkup(<OperatorPanel scope={scope} csrf="csrf" dirty={false}
      initialState={{ ...initialOperatorState, scope, turn: rfqTurn }} onSession={() => {}} onUnknown={() => {}} />);
    const preview = html.slice(html.indexOf('Full draft preview'), html.indexOf('View draft in workspace'));
    expect(preview).toContain('UNSENT BRIDGE REQUEST FOR QUOTATION DRAFT');
    expect(preview).toContain('Terms remain unsent.');
    expect(preview).not.toContain('SYNTHETIC TEST DOCUMENT');
    expect(body).toContain('SYNTHETIC TEST DOCUMENT');
  });
  it('requires opening the full quote fields for the current proposal before review', () => {
    const review = { ...proposal, kind: 'review_quotes' as const, parameters: { material_id: 'copper', offer_ids: ['q1'], preview: [{ offer_id: 'q1', fixture_id: 'fixture-copper', source_filename: 'reviewed.pdf', expected_version: 2, review_values: { lead_time_days: '10' } }] }, offer_versions: { q1: 2 } };
    const html = renderToStaticMarkup(<OperatorPanel scope={scope} csrf="csrf" dirty={false} offers={[{ ...quote, fixture_id: 'fixture-copper' }]}
      initialState={{ ...initialOperatorState, scope, turn: { ...turn, proposal: review }, reviewed: true }} onSession={() => {}} onUnknown={() => {}} />);
    const card = html.slice(html.indexOf('aria-label="Current proposal requiring review"'), html.indexOf('aria-label="Assistant conversation"'));
    expect(card.indexOf('Full quote preview')).toBeLessThan(card.indexOf('Apply reviewed proposal'));
    expect(card).toContain('Open the full quote preview before confirming.');
    expect(card).toMatch(/<input type="checkbox"[^>]*disabled=""/);
    expect(card).toMatch(/disabled=""[^>]*>Apply reviewed proposal<\/button>/);
  });
  it('rejects missing or blank RFQ and email previews even with an opened key and checked review', () => {
    for (const kind of ['rfq', 'simulate_email'] as const) {
      for (const parameters of [
        { recipient_label: 'Supplier desk', subject: 'Availability' },
        { recipient_label: '  ', subject: 'Availability', body: 'Please confirm stock.' },
        { recipient_label: 'Supplier desk', subject: ' ', body: 'Please confirm stock.' },
        { recipient_label: 'Supplier desk', subject: 'Availability', body: '  ' },
      ]) {
        const invalid = { ...turn, proposal: { ...proposal, kind, parameters } };
        expect(proposalPreviewReady(scope, invalid, proposalPreviewKey(scope, invalid.proposal))).toBe(false);
        const html = renderToStaticMarkup(<OperatorPanel scope={scope} csrf="csrf" dirty={false}
          initialState={{ ...initialOperatorState, scope, turn: invalid, reviewed: true }} onSession={() => {}} onUnknown={() => {}} />);
        const card = html.slice(html.indexOf('aria-label="Current proposal requiring review"'), html.indexOf('aria-label="Assistant conversation"'));
        expect(card).toContain('Draft preview unavailable. Request a new proposal.');
        expect(card).toMatch(/<input type="checkbox"[^>]*disabled=""/);
        expect(card).toMatch(kind === 'simulate_email' ? /disabled=""[^>]*>Record simulated sent message<\/button>/ : /disabled=""[^>]*>Apply reviewed proposal<\/button>/);
      }
    }
  });
  it('keeps exact research consent and confirmation ahead of optional argument details', () => {
    const research = { ...turn, proposal: { ...proposal, kind: 'research' as const,
      parameters: { query: 'Public port status', destination: 'Google Search via Vertex AI' } } };
    const html = renderToStaticMarkup(<OperatorPanel scope={scope} csrf="csrf" dirty={false}
      initialState={{ ...initialOperatorState, scope, turn: research }} onSession={() => {}} onUnknown={() => {}} />);
    const card = html.slice(html.indexOf('aria-label="Current proposal requiring review"'), html.indexOf('aria-label="Assistant conversation"'));
    expect(card.indexOf('Public port status')).toBeLessThan(card.indexOf('Research web'));
    expect(card.indexOf('Google Search via Vertex AI')).toBeLessThan(card.indexOf('Research web'));
    expect(card.indexOf('Research web')).toBeLessThan(card.indexOf('Exact proposed arguments'));
  });
  it('places a current voice proposal before earlier activity and scrolls mobile focus to consent', () => {
    const html = renderToStaticMarkup(<VoiceConversationSurface open onClose={() => {}} voice={<p>Listening</p>}
      activity={<article aria-label="Latest operator exchange">Earlier answer</article>}
      proposal={<div role="region" aria-label="Current proposal requiring review">Public query to share: port status</div>} />);
    expect(html.indexOf('Current proposal requiring review')).toBeLessThan(html.indexOf('Latest operator exchange'));
    const focus = vi.fn();
    const card = { getClientRects: () => ({ length: 1 }), focus };
    focusCurrentProposal(card, true, true);
    expect(focus).toHaveBeenCalledWith();
    focus.mockClear();
    focusCurrentProposal(card, true, false);
    expect(focus).toHaveBeenCalledWith({ preventScroll: true });
  });
  it('starts only a newer request for the exact session, company, context and epoch', () => {
    const request = { id: 12, scope, returnFocusId: 'talk-button' };
    expect(voiceRequestIsCurrent(request, scope, 11)).toBe(true);
    expect(voiceRequestIsCurrent(request, scope, 12)).toBe(false);
    expect(voiceRequestIsCurrent({ ...request, id: 0 }, scope, 0)).toBe(false);
    for (const changed of [
      { ...scope, sessionId: 's2' }, { ...scope, companyId: 'other' },
      { ...scope, contextVersion: 2 }, { ...scope, epoch: 1 },
    ]) expect(voiceRequestIsCurrent(request, changed, 11)).toBe(false);
  });
  it('claims a request once per mounted panel and permits the same ID after a real remount', () => {
    const request = { id: 12, scope, returnFocusId: 'zoorq-talk-live' };
    const firstMount = { current: 0 }, secondMount = { current: 0 };
    expect(claimOperatorVoiceRequest(request, scope, firstMount)).toBe(true);
    expect(claimOperatorVoiceRequest(request, scope, firstMount)).toBe(false);
    expect(claimOperatorVoiceRequest(request, scope, secondMount)).toBe(true);
    expect(claimOperatorVoiceRequest(request, { ...scope, epoch: 1 }, secondMount)).toBe(false);
  });
  it('routes only external End or Escape to the outer dock close', () => {
    const closeVoice = vi.fn(), closeDock = vi.fn();
    routeVoiceExit(false, closeVoice, closeDock);
    expect(closeVoice).toHaveBeenCalledOnce();
    expect(closeDock).not.toHaveBeenCalled();
    routeVoiceExit(true, closeVoice, closeDock);
    expect(closeVoice).toHaveBeenCalledTimes(2);
    expect(closeDock).toHaveBeenCalledOnce();
    const switchToText = vi.fn(), escape = vi.fn();
    const surface = VoiceConversationSurface({ open: true, onClose: switchToText, onEscape: escape, voice: <p>Listening</p>, companyLabel: 'Solara Cables' });
    surface.props.children[0].props.children[1].props.onClick();
    surface.props.onKeyDown({ key: 'Escape', preventDefault: () => {}, stopPropagation: () => {} });
    expect(switchToText).toHaveBeenCalledOnce();
    expect(escape).toHaveBeenCalledOnce();
    expect(renderToStaticMarkup(surface)).toContain('Solara Cables');
  });
  it('shows a server-backed answer and its source once while voice is closed', () => {
    const answered = { ...turn, status: 'completed' as const, proposal: null, assistant_text: 'Copper needs review.' };
    const html = renderToStaticMarkup(<OperatorPanel scope={scope} csrf="csrf" dirty={false} news={news}
      initialState={{ ...initialOperatorState, scope, exchanges: [{ id: 1, question: 'What needs review?', turn: answered }] }}
      onSession={() => {}} onUnknown={() => {}} />);
    expect(html).toContain('Copper needs review.');
    expect(html).toContain('href="https://example.org/article"');
    expect(html).toContain('Example News');
    expect(html.match(/Copper needs review\./g)).toHaveLength(1);
  });
  it('returns focus to the global Talk trigger after external voice closes, with a local fallback', () => {
    const calls: string[] = [];
    const target = { isConnected: true, focus: () => { calls.push('global'); } };
    const mic = { disabled: false, focus: () => { calls.push('mic'); } };
    const text = { focus: () => { calls.push('text'); } };
    focusVoiceReturn('global-talk', () => target, mic, text);
    focusVoiceReturn('removed-talk', () => null, mic, text);
    expect(calls).toEqual(['global', 'mic']);
  });
  it('names the selected company in a compact branded header and keeps close available', () => {
    const html = renderToStaticMarkup(<OperatorPanel scope={scope} csrf="csrf" dirty={false}
      onClose={() => {}} onSession={() => {}} onUnknown={() => {}} />);
    expect(html).toContain('ZOORQ operator');
    expect(html).toContain('Solara Cables');
    expect(html).toContain('Talk naturally in your preferred language. Review proposed actions before they run.');
    expect(html).toContain('Gemini on Vertex AI');
    expect(html).toContain('Action approvals');
    expect(html).toContain('When asked, say the exact approval phrase shown.');
    expect(html).not.toContain('Ask in English, Hindi or Portuguese');
    expect(html).toContain('aria-label="Close operator chat"');
  });
  it('keeps current review and cancel actions visible above history while details stay collapsed', () => {
    const html = renderToStaticMarkup(<OperatorPanel scope={scope} csrf="csrf" dirty={false}
      initialState={{ ...initialOperatorState, scope, turn }} onSession={() => {}} onUnknown={() => {}} />);
    const card = html.slice(html.indexOf('aria-label="Current proposal requiring review"'), html.indexOf('aria-label="Assistant conversation"'));
    expect(card).toContain('Current proposal · Watch item');
    expect(card).toContain('I reviewed the exact arguments');
    expect(card).toContain('Apply reviewed proposal');
    expect(card).toContain('Cancel proposal');
    expect(card).toMatch(/<details[^>]*><summary>What happens next<\/summary>/);
    expect(card).not.toMatch(/<details[^>]* open/);
  });
  it('follows loaded and near-bottom history while preserving manual readback', () => {
    expect(shouldFollowOperatorHistory({ scrollTop: 0, scrollHeight: 0, clientHeight: 300 })).toBe(true);
    expect(shouldFollowOperatorHistory({ scrollTop: 640, scrollHeight: 1000, clientHeight: 300 })).toBe(true);
    expect(shouldFollowOperatorHistory({ scrollTop: 100, scrollHeight: 1000, clientHeight: 300 })).toBe(false);
  });
  it('keeps the current proposal and composer outside conversation history, with a dock close control', () => {
    const html = renderToStaticMarkup(<OperatorPanel scope={scope} csrf="csrf" dirty={false}
      initialState={{ ...initialOperatorState, scope, turn }} onClose={() => {}} onSession={() => {}} onUnknown={() => {}} />);
    expect(html).toContain('aria-label="Close operator chat"');
    expect(html.indexOf('Current proposal requiring review')).toBeLessThan(html.indexOf('aria-label="Assistant conversation"'));
    expect(html.indexOf('aria-label="Assistant conversation"')).toBeLessThan(html.indexOf('class="demo-operator-chat demo-assistant-composer"'));
  });
  it('keeps one voice connection for reviewed research and closes it for business Apply', () => {
    let ends = 0, revisions = 0;
    const close = () => { ends += 1; };
    const reset = () => { revisions += 1; };
    expect(confirmVoiceTransition('research', true, close, reset)).toBe(true);
    expect(confirmVoiceTransition('research', true, close, reset)).toBe(true);
    expect([ends, revisions]).toEqual([0, 0]);
    expect(confirmVoiceTransition('rfq', true, close, reset)).toBe(false);
    expect([ends, revisions]).toEqual([1, 1]);
    expect(confirmVoiceTransition('research', false, close, reset)).toBe(false);
    expect([ends, revisions]).toEqual([1, 2]);
  });
  it('shows server-confirmed research in the same voice surface after its proposal clears', () => {
    const result = { research_id: 'r-voice-confirm', query: 'Public port status', status: 'completed' as const, checked_at: '2026-10-15T10:00:00Z', answer: 'Ports remain open.', sources: [{ source_id: 's1', title: 'Port authority', url: 'https://example.org/port' }] };
    const applied = { status: 'applied' as const, company_id: scope.companyId, context_version: scope.contextVersion, kind: 'research' as const, result,
      session: { session_id: scope.sessionId, company: { company_id: scope.companyId }, context_version: scope.contextVersion } as never };
    const next = applyOperatorResult({ ...initialOperatorState, scope, turn: { ...turn, proposal: { ...proposal, kind: 'research' } } }, { type: 'applied', scope, outcome: applied });
    expect(next.turn).toBeNull();
    expect(latestVoiceResearch(next, scope)).toEqual(result);
    const html = renderToStaticMarkup(<VoiceConversationSurface open onClose={() => {}} voice={<p>Listening</p>} research={latestVoiceResearch(next, scope)} />);
    expect(html).toContain('Ports remain open.');
    expect(html).toContain('Port authority');
    expect(html).toContain('Listening');
    expect(html.match(/Ports remain open\./g)).toHaveLength(1);
    expect(latestVoiceResearch(next, { ...scope, companyId: 'other' })).toBeNull();
  });
  it('focuses an exact-scope seeded composer whether the dock is newly opened or already open', () => {
    const seed = { id: 8, ...scope, message: 'Ask about this public story.' };
    let focusCount = 0;
    expect(focusValidOperatorSeed(seed, scope, () => { focusCount += 1; })).toBe(seed.message);
    expect(focusValidOperatorSeed({ ...seed, id: 9 }, scope, () => { focusCount += 1; })).toBe(seed.message);
    expect(focusCount).toBe(2);
    expect(focusValidOperatorSeed({ ...seed, companyId: 'other' }, scope, () => { focusCount += 1; })).toBeNull();
    expect(focusCount).toBe(2);
  });
  it('keeps a compact voice trigger next to Send while the dedicated voice surface is closed', () => {
    const html = renderToStaticMarkup(<OperatorPanel scope={scope} csrf="csrf" dirty={false} onSession={() => {}} onUnknown={() => {}} />);
    expect(html).toContain('aria-label="Open voice conversation"');
    expect(html.indexOf('aria-label="Open voice conversation"')).toBeGreaterThan(html.indexOf('Your message'));
    expect(html.indexOf('aria-label="Open voice conversation"')).toBeLessThan(html.indexOf('>Send</button>'));
    expect(html).toMatch(/class="demo-voice-screen"[^>]* hidden=""/);
  });
  it('places the current proposal in the dedicated voice surface with one review card', () => {
    const html = renderToStaticMarkup(<VoiceConversationSurface open onClose={() => {}} voice={<p>Listening</p>} proposal={<div role="region" aria-label="Current proposal requiring review">Public query to share: Hormuz status</div>} />);
    expect(html).toContain('role="dialog" aria-modal="true"');
    expect(html).toContain('Listening');
    expect(html).toContain('Public query to share: Hormuz status');
    expect(html.match(/Current proposal requiring review/g)).toHaveLength(1);
  });
  it('uses only complete current-company prepared drafts to refresh the Drafts workspace', () => {
    const full = { draft_id: 'd1', kind: 'email', status: 'draft_unsent', company_id: scope.companyId, material_id: 'copper', recipient_label: 'Supplier', subject: 'Availability', body: 'Please confirm.', created_at: '2026-10-15T10:00:00Z' };
    expect(scopedPreparedDrafts([full], scope)).toEqual([full]);
    expect(scopedPreparedDrafts([{ draft_id: 'summary-only', kind: 'email', status: 'draft_unsent' }], scope)).toBeNull();
    expect(scopedPreparedDrafts([{ ...full, company_id: 'another-company' }], scope)).toBeNull();
  });
  it('shows the exact public research query and destination, with explicit review before search', () => {
    const research = { ...turn, proposal: { ...proposal, kind: 'research' as const, parameters: { query: 'Hormuz shipping status October 2026', destination: 'Google Search via Vertex AI' } } };
    const state = { ...initialOperatorState, scope, turn: research };
    const html = renderToStaticMarkup(<OperatorPanel scope={scope} csrf="csrf" dirty={false} initialState={state} onSession={() => {}} onUnknown={() => {}} />);
    expect(html).toContain('Hormuz shipping status October 2026');
    expect(html).toContain('Google Search via Vertex AI');
    expect(html).toContain('Research web');
    expect(html).toContain('Only the exact public query shown above is shared');
    expect(html).not.toContain('No private workspace details are sent');
    expect(canApplyProposal(state, scope, false, Date.now())).toBe(false);
    expect(canApplyProposal({ ...state, reviewed: true }, scope, false, Date.now())).toBe(true);
  });
  it('hydrates current-company history without restoring approval and rejects stale history', () => {
    const historicalResearch = { ...turn, proposal: { ...proposal, kind: 'research' as const, parameters: { query: 'Old public query', destination: 'Google Search via Vertex AI' } } };
    const history: OperatorHistory = { company_id: scope.companyId, context_version: scope.contextVersion, exchanges: [{ id: 'h1', channel: 'text', question: 'Old question', turn: historicalResearch, created_at: '2026-10-15T10:00:00Z' }] };
    const hydrated = applyOperatorResult({ ...initialOperatorState, scope }, { type: 'history', scope, history });
    expect(hydrated.exchanges).toHaveLength(1);
    expect(hydrated.turn).toBeNull();
    expect(renderToStaticMarkup(<OperatorPanel scope={scope} csrf="csrf" dirty={false} initialState={hydrated} onSession={() => {}} onUnknown={() => {}} />)).not.toContain('Research web');
    expect(renderToStaticMarkup(<OperatorPanel scope={scope} csrf="csrf" dirty={false} initialState={hydrated} onSession={() => {}} onUnknown={() => {}} />)).not.toContain('Old public query');
    expect(applyOperatorResult({ ...initialOperatorState, scope }, { type: 'history', scope: { ...scope, companyId: 'other' }, history }).exchanges).toEqual([]);
    expect(applyOperatorResult({ ...initialOperatorState, scope }, { type: 'history', scope, history: { ...history, context_version: 2 } }).exchanges).toEqual([]);
  });
  it('does not duplicate a live turn when an earlier history request returns after it', () => {
    const started = applyOperatorResult({ ...initialOperatorState, scope }, { type: 'start', scope, requestId: 1, question: 'What changed?' });
    const live = applyOperatorResult(started, { type: 'turn', scope, requestId: 1, turn });
    const history: OperatorHistory = { company_id: scope.companyId, context_version: scope.contextVersion, exchanges: [{ id: 'server-h1', channel: 'text', question: 'What changed?', turn, created_at: '2026-10-15T10:00:00Z' }] };
    const hydrated = applyOperatorResult(live, { type: 'history', scope, history });
    expect(hydrated.exchanges).toHaveLength(1);
    expect(hydrated.turn).toEqual(turn);
  });
  it('does not enable research confirmation when the server proposal has a missing public query or unexpected destination', () => {
    const malformed = { ...turn, proposal: { ...proposal, kind: 'research' as const, parameters: { query: '', destination: 'Other provider' } } };
    const state = { ...initialOperatorState, scope, turn: malformed, reviewed: true };
    expect(canApplyProposal(state, scope, false, Date.now())).toBe(false);
  });
  it('renders a dated research result with only safe HTTPS citation links and sandboxed suggestions', () => {
    const outcome = { status: 'applied' as const, company_id: scope.companyId, context_version: scope.contextVersion, kind: 'research' as const,
      result: { research_id: 'r1', query: 'Hormuz status', status: 'completed' as const, checked_at: '2026-10-15T10:00:00Z', answer: 'Shipping remains uncertain.', sources: [{ source_id: 's1', title: 'Valid source', url: 'https://example.org/report' }, { source_id: 's2', title: 'Bad source', url: 'javascript:alert(1)' }], search_suggestions_html: '<a href="https://www.google.com/search?q=Hormuz" target="_blank">More</a>' },
      session: { session_id: scope.sessionId, csrf_token: 'csrf', expires_at: '2026-10-22T00:00:00Z', synthetic: true as const, persistence: 'firestore' as const, offers: [], provider: { model: 'test', available: true }, company: { company_id: scope.companyId } as never, context_version: scope.contextVersion } };
    const html = renderToStaticMarkup(<OperatorPanel scope={scope} csrf="csrf" dirty={false} initialState={{ ...initialOperatorState, scope, outcome }} onSession={() => {}} onUnknown={() => {}} />);
    expect(html).toContain('Shipping remains uncertain.');
    expect(html).toContain('2026-10-15');
    expect(html).toContain('href="https://example.org/report"');
    expect(html).not.toContain('href="javascript:');
    expect(html).toContain('sandbox="allow-popups allow-popups-to-escape-sandbox"');
    expect(html).not.toContain('allow-scripts');
    expect(html).not.toContain('allow-same-origin');
    expect(html).not.toContain('<script>');
  });
  it('shows validated current-scope voice research with dated citations once and falls back safely for malformed payloads', () => {
    const result = { research_id: 'r-voice', query: 'Public port status', status: 'completed' as const, checked_at: '2026-10-15T10:00:00Z', answer: 'Ports remain open.', sources: [{ source_id: 'source-1', title: 'Port authority', url: 'https://example.org/ports' }] };
    const recalled = { ...turn, status: 'completed' as const, proposal: null, assistant_text: result.answer, workspace: { research: result } };
    expect(researchFromTurn(recalled, scope)).toEqual(result);
    const html = renderToStaticMarkup(<OperatorPanel scope={scope} csrf="csrf" dirty={false} initialState={{ ...initialOperatorState, scope, turn: recalled }} onSession={() => {}} onUnknown={() => {}} />);
    expect(html).toContain('Public port status');
    expect(html).toContain('Port authority');
    expect(html).toContain('https://example.org/ports');
    expect(html.match(/Ports remain open\./g)).toHaveLength(1);
    expect(researchFromTurn(recalled, { ...scope, companyId: 'other' })).toBeNull();
    const withoutSuggestions = { ...recalled, workspace: { research: { ...result, search_suggestions_html: null } } };
    expect(researchFromTurn(withoutSuggestions, scope)).toEqual(withoutSuggestions.workspace.research);
    const withoutSuggestionsHtml = renderToStaticMarkup(<OperatorPanel scope={scope} csrf="csrf" dirty={false} initialState={{ ...initialOperatorState, scope, turn: withoutSuggestions }} onSession={() => {}} onUnknown={() => {}} />);
    expect(withoutSuggestionsHtml).toContain('Checked 2026-10-15 10:00 UTC');
    expect(withoutSuggestionsHtml).toContain('Port authority');
    expect(withoutSuggestionsHtml).not.toContain('Google Search Suggestions');
    for (const invalid of [5, 'x'.repeat(12001)]) {
      expect(researchFromTurn({ ...recalled, workspace: { research: { ...result, search_suggestions_html: invalid } } }, scope)).toBeNull();
    }
    for (const invalid of [
      { ...result, answer: 'x'.repeat(8001) },
      { ...result, query: 'x'.repeat(401) },
      { ...result, research_id: 'x'.repeat(129) },
      { ...result, sources: Array.from({ length: 25 }, (_, index) => ({ source_id: String(index), title: 'Source', url: 'https://example.org/ports' })) },
      { ...result, sources: [{ source_id: 's', title: 'x'.repeat(201), url: 'https://example.org/ports' }] },
      { ...result, sources: [{ source_id: 's', title: 'Source', url: 'http://example.org/ports' }] },
      { ...result, sources: [{ source_id: 's', title: 'Source', url: `https://example.org/${'x'.repeat(1200)}` }] },
    ]) expect(researchFromTurn({ ...recalled, workspace: { research: invalid } }, scope)).toBeNull();
    const fullSourceSet = { ...result, sources: Array.from({ length: 24 }, (_, index) => ({ source_id: String(index), title: `Source ${index}`, url: `https://example.org/${index}` })) };
    expect(researchFromTurn({ ...recalled, workspace: { research: fullSourceSet } }, scope)).toEqual(fullSourceSet);
    const malformed = { ...recalled, workspace: { research: { ...result, sources: [null] } } };
    expect(researchFromTurn(malformed, scope)).toBeNull();
    expect(renderToStaticMarkup(<OperatorPanel scope={scope} csrf="csrf" dirty={false} initialState={{ ...initialOperatorState, scope, turn: malformed }} onSession={() => {}} onUnknown={() => {}} />)).toContain('Ports remain open.');
  });
  it('displays confirmed mutable voice research in the conversation and current approval near voice', () => {
    const research = { research_id: 'r-confirmed', query: 'Public port status', status: 'completed' as const, checked_at: '2026-10-15T10:00:00Z', answer: '## Status\n\n**Open** today.', sources: [] };
    const session = { session_id: scope.sessionId, company: { company_id: scope.companyId }, context_version: scope.contextVersion };
    const confirmed = { ...turn, status: 'completed' as const, proposal: null, read_only: false, session: session as never,
      action_result: { tool: 'confirm_research', summary: 'Research complete' }, workspace: { research } };
    expect(acceptsOperatorTurn(confirmed, scope)).toBe(true);
    expect(researchFromTurn(confirmed, scope)).toEqual(research);
    const html = renderToStaticMarkup(<OperatorPanel scope={scope} csrf="csrf" dirty={false}
      initialState={{ ...initialOperatorState, scope, turn: confirmed }} onSession={() => {}} onUnknown={() => {}} />);
    expect(html).toContain('Public port status');
    expect(html).toContain('<h3>Status</h3>');
    const pending = renderToStaticMarkup(<OperatorPanel scope={scope} csrf="csrf" dirty={false}
      initialState={{ ...initialOperatorState, scope, turn, exchanges: Array.from({ length: 20 }, (_, index) => ({ id: index, question: 'Old question', answer: 'Old answer' })) }} onSession={() => {}} onUnknown={() => {}} />);
    expect(pending.indexOf('Current proposal · Watch item')).toBeLessThan(pending.indexOf('Old question'));
    expect(pending).toContain('role="region" aria-label="Current proposal requiring review"');
  });
  it('discloses the email body and a direct draft review link while keeping recipient and subject visible', () => {
    const email = { ...turn, proposal: { ...proposal, kind: 'simulate_email' as const,
      parameters: { material_id: 'copper', recipient_label: 'Supplier', subject: 'Availability', body: 'Please confirm lead time.' } } };
    const html = renderToStaticMarkup(<OperatorPanel scope={scope} csrf="csrf" dirty={false}
      initialState={{ ...initialOperatorState, scope, turn: email }} onSession={() => {}} onUnknown={() => {}} />);
    expect(html).toMatch(/<strong>Subject:<\/strong> Availability/);
    expect(html).toContain('Please confirm lead time.');
    expect(html).toContain('View draft');
  });
  it('keeps approved research after the next question and deduplicates a late history read', () => {
    const research = { research_id: 'research-1', query: 'Public port status', status: 'completed' as const, checked_at: '2026-10-15T10:00:00Z', answer: 'Port is open.', sources: [{ source_id: 'source-1', title: 'Port authority', url: 'https://example.org/ports' }] };
    const outcome = { status: 'applied' as const, company_id: scope.companyId, context_version: scope.contextVersion, kind: 'research' as const, result: research,
      session: { session_id: scope.sessionId, csrf_token: 'csrf', expires_at: '2026-10-22T00:00:00Z', synthetic: true as const, persistence: 'firestore' as const, offers: [], provider: { model: 'test', available: true }, company: { company_id: scope.companyId } as never, context_version: scope.contextVersion } };
    const applied = applyOperatorResult({ ...initialOperatorState, scope }, { type: 'applied', scope, outcome });
    expect(applied.exchanges).toMatchObject([{ id: 'research-1', research }]);
    const started = applyOperatorResult(applied, { type: 'start', scope, requestId: 2, question: 'What next?' });
    const html = renderToStaticMarkup(<OperatorPanel scope={scope} csrf="csrf" dirty={false} initialState={started} onSession={() => {}} onUnknown={() => {}} />);
    expect(html).toContain('Port authority');
    expect(html.match(/Port is open\./g)).toHaveLength(1);
    const history: OperatorHistory = { company_id: scope.companyId, context_version: scope.contextVersion, exchanges: [{ id: 'research-1', channel: 'text', question: null, answer: research.answer, research, created_at: research.checked_at }] };
    expect(applyOperatorResult(started, { type: 'history', scope, history }).exchanges).toHaveLength(1);
  });
  it('hides old company questions, answers, attention and proposals immediately before scope effects run', () => {
    const requested = applyOperatorResult({ ...initialOperatorState, scope }, { type: 'start', scope, requestId: 1, question: 'Old company copper question' });
    const answered = applyOperatorResult(requested, { type: 'turn', scope, requestId: 1, turn });
    const html = renderToStaticMarkup(<OperatorPanel scope={{ ...scope, companyId: 'atlas-petrochem', contextVersion: 2 }} csrf="csrf" dirty={false}
      initialState={answered} onSession={() => {}} onUnknown={() => {}} />);
    expect(html).not.toContain('Old company copper question');
    expect(html).not.toContain('A reviewable watch is ready.');
    expect(html).not.toContain('Watch copper');
    expect(html).not.toContain('Exact proposed arguments');
    expect(html).toContain('Start conversation');
  });
  it('keeps scoped answers readable, voice mode closed, and the composer last with evidence collapsed', () => {
    const first = { ...turn, turn_id: 'first', status: 'completed' as const, proposal: null, assistant_text: 'A first answer.' };
    const second = { ...turn, turn_id: 'second', status: 'completed' as const, proposal: null, assistant_text: 'A second answer.' };
    const html = renderToStaticMarkup(<OperatorPanel scope={scope} csrf="csrf" dirty={false}
      initialState={{ ...initialOperatorState, scope, turn: second, turns: [first, second] }}
      news={news} onSession={() => {}} onUnknown={() => {}} />);
    expect(html).toContain('aria-label="Assistant conversation"');
    expect(html).toMatch(/class="demo-voice-screen"[^>]* hidden=""/);
    expect(html.indexOf('A first answer.')).toBeLessThan(html.indexOf('A second answer.'));
    expect(html.indexOf('A second answer.')).toBeLessThan(html.indexOf('Your message'));
    expect(html.match(/<details class="demo-assistant-trace"/g)).toHaveLength(2);
    expect(html).not.toMatch(/<details class="demo-assistant-trace"[^>]* open/);
    expect(html).toContain('Evidence and specialist activity');
    expect(html).not.toContain('<script>');
  });
  it('shows clean known supplier names in answers and quote evidence but keeps exact proposal arguments', () => {
    const raw = 'Europa Copper SA [SYNTHETIC]';
    const namedQuote = { ...quote, fields: { ...quote.fields, supplier_name: { ...quote.fields.supplier_name!, value: raw } } };
    const namedTurn = { ...turn, assistant_text: `Review a simulated unsent message to ${raw}.`, evidence_ids: ['q1'],
      proposal: { ...proposal, kind: 'simulate_email' as const, summary: `Message to ${raw}`,
        parameters: { material_id: 'copper', recipient_label: raw, subject: 'Availability', body: 'Please confirm.' } } };
    const html = renderToStaticMarkup(<OperatorPanel scope={scope} csrf="csrf" dirty={false}
      initialState={{ ...initialOperatorState, scope, turn: namedTurn, turns: [namedTurn] }}
      offers={[namedQuote]} onSession={() => {}} onUnknown={() => {}} />);
    expect(html).toContain('Review a simulated unsent message to Europa Copper SA.');
    expect(html).toContain('Message to Europa Copper SA');
    expect(html).toContain('Quote v2');
    expect(html).toContain('recipient_label');
    expect(html).toContain(raw);
    expect(resolveOperatorEvidence('q1', scope, null, [namedQuote]).title).toBe('Europa Copper SA');
  });
  it('keeps the compact chat and disabled mic available with risks and an unavailable provider', () => {
    const overview = { company_id: scope.companyId, context_version: scope.contextVersion, available: false,
      agents: [], attention: [], watch_items: [], limitations: [] };
    const html = renderToStaticMarkup(<OperatorPanel scope={scope} csrf="csrf" dirty={true}
      initialState={{ ...initialOperatorState, scope, overview }}
      riskSummary={{ high: 1, medium: 0, low: 0, checkedAt: '2026-10-15' }} onSession={() => {}} onUnknown={() => {}} />);
    expect(html.indexOf('>ZOORQ operator</h2>')).toBeLessThan(html.indexOf('Current company risks:'));
    expect(html.indexOf('Current company risks:')).toBeLessThan(html.indexOf('Your message'));
    expect(html).toContain('AI model temporarily unavailable; workspace calculations and saved data still work.');
    expect(html).toContain('Your message');
    expect(html).toMatch(/id="demo-operator-message"[^>]*><\/textarea>/);
    expect(html).toMatch(/aria-label="Open voice conversation"[^>]* disabled=""/);
  });
  it('keeps attention compact after the active proposal while review controls stay outside closed traces', () => {
    const overview = { company_id: scope.companyId, context_version: scope.contextVersion, available: true,
      agents: [], attention: [{ material_id: 'copper', synthetic: true, stock_cover_days: '12', minimum_lead_time_days: 15 }],
      watch_items: [], limitations: [] };
    const html = renderToStaticMarkup(<OperatorPanel scope={scope} csrf="csrf" dirty={false}
      initialState={{ ...initialOperatorState, scope, overview, turn, turns: [turn] }}
      materials={[{ material_id: 'copper', name: 'Copper' }]} onSession={() => {}} onUnknown={() => {}} />);
    expect(html).toMatch(/<details class="demo-assistant-attention"[^>]*><summary[^>]*>.*Company attention/s);
    expect(html).toContain('1 signal');
    expect(html).toContain('Stock cover: 12 days');
    expect(html).toContain('Exact proposed arguments');
    expect(html.indexOf('Exact proposed arguments')).toBeLessThan(html.indexOf('Stock cover: 12 days'));
    expect(html.indexOf('Exact proposed arguments')).toBeLessThan(html.indexOf('Assistant conversation'));
    expect(html).toContain('I reviewed the exact arguments');
    expect(html).toContain('Apply reviewed proposal');
  });
  it('seeds only an exact current-company risk message, without sending or approving it', () => {
    const seed = { id: 7, ...scope, message: 'Inspect copper quote alternatives for Atlas.' };
    expect(operatorSeedMessage(seed, scope)).toBe(seed.message);
    expect(operatorSeedMessage({ ...seed, message: `  ${seed.message}  ` }, scope)).toBe(`  ${seed.message}  `);
    expect(operatorSeedMessage({ ...seed, companyId: 'loom' }, scope)).toBeNull();
    expect(operatorSeedMessage({ ...seed, sessionId: 'old' }, scope)).toBeNull();
    expect(operatorSeedMessage({ ...seed, contextVersion: 2 }, scope)).toBeNull();
    expect(operatorSeedMessage({ ...seed, epoch: 2 }, scope)).toBeNull();
    expect(operatorSeedMessage({ ...seed, message: '' }, scope)).toBeNull();
    const html = renderToStaticMarkup(<OperatorPanel scope={scope} csrf="csrf" dirty={false} seed={seed} onSession={() => {}} onUnknown={() => {}} />);
    expect(html).not.toContain('Apply reviewed proposal');
  });
  it('rejects stale session, company, context, epoch, and cancelled agent results', () => {
    const base = { ...initialOperatorState, scope };
    const pending = applyOperatorResult(base, { type: 'start', requestId: 1, scope });
    expect(applyOperatorResult(pending, { type: 'turn', requestId: 1, scope, turn }).turn).toEqual(turn);
    expect(applyOperatorResult(pending, { type: 'turn', requestId: 1, scope: { ...scope, sessionId: 's2' }, turn }).turn).toBeNull();
    expect(applyOperatorResult(pending, { type: 'turn', requestId: 1, scope: { ...scope, companyId: 'loomworks' }, turn }).turn).toBeNull();
    expect(applyOperatorResult(pending, { type: 'turn', requestId: 1, scope: { ...scope, contextVersion: 2 }, turn }).turn).toBeNull();
    expect(applyOperatorResult(pending, { type: 'turn', requestId: 1, scope: { ...scope, epoch: -1 }, turn }).turn).toBeNull();
    const cancelled = applyOperatorResult(pending, { type: 'cancel' });
    expect(applyOperatorResult(cancelled, { type: 'turn', requestId: 1, scope, turn }).turn).toBeNull();
    const newSession = applyOperatorResult(pending, { type: 'scope', scope: { ...scope, sessionId: 's2' } });
    expect(newSession.turn).toBeNull();
  });
  it('retains accepted turn history within a scope and clears it on session change', () => {
    const first = applyOperatorResult(applyOperatorResult({ ...initialOperatorState, scope }, { type: 'start', scope, requestId: 1 }), { type: 'turn', scope, requestId: 1, turn });
    const second = applyOperatorResult(applyOperatorResult(first, { type: 'start', scope, requestId: 2 }), { type: 'turn', scope, requestId: 2, turn: { ...turn, turn_id: 't2', assistant_text: 'A second answer.' } });
    expect(second.turns.map(item => item.turn_id)).toEqual(['t1', 't2']);
    expect(applyOperatorResult(second, { type: 'scope', scope: { ...scope, sessionId: 's2' } }).turns).toEqual([]);
  });
  it('pairs typed questions with accepted answers or explicit failures, and isolates each company session', () => {
    const first = applyOperatorResult({ ...initialOperatorState, scope }, { type: 'start', scope, requestId: 1, question: 'What changed for copper?' });
    expect(first.exchanges).toEqual([]);
    const answered = applyOperatorResult(first, { type: 'turn', scope, requestId: 1, turn: { ...turn, proposal: null, status: 'completed' } });
    expect(answered.exchanges).toMatchObject([{ question: 'What changed for copper?', turn: { turn_id: 't1' } }]);
    const pending = applyOperatorResult(answered, { type: 'start', scope, requestId: 2, question: 'Can we compare?' });
    const failed = applyOperatorResult(pending, { type: 'failed', scope, requestId: 2, error: 'Provider unavailable' });
    expect(failed.exchanges).toMatchObject([{ question: 'What changed for copper?', turn: { turn_id: 't1' } }, { question: 'Can we compare?', error: 'Provider unavailable' }]);
    const html = renderToStaticMarkup(<OperatorPanel scope={scope} csrf="csrf" dirty={false} initialState={failed} onSession={() => {}} onUnknown={() => {}} />);
    expect(html.indexOf('What changed for copper?')).toBeLessThan(html.indexOf('A reviewable watch is ready.'));
    expect(html.indexOf('Can we compare?')).toBeLessThan(html.indexOf('Provider unavailable'));
    expect(html).toContain('Request failed');
    const edited = applyOperatorResult(failed, { type: 'scope', scope: { ...scope, epoch: 1 } });
    expect(edited.exchanges).toEqual(failed.exchanges);
    expect(applyOperatorResult(edited, { type: 'scope', scope: { ...scope, companyId: 'loomworks', epoch: 2 } }).exchanges).toEqual([]);
    expect(applyOperatorResult(edited, { type: 'scope', scope: { ...scope, sessionId: 's2', epoch: 2 } }).exchanges).toEqual([]);
  });
  it('omits cancelled, superseded, and out-of-scope typed questions from the transcript', () => {
    const pending = applyOperatorResult({ ...initialOperatorState, scope }, { type: 'start', scope, requestId: 1, question: 'Cancelled question' });
    const cancelled = applyOperatorResult(pending, { type: 'cancel' });
    expect(applyOperatorResult(cancelled, { type: 'turn', scope, requestId: 1, turn }).exchanges).toEqual([]);
    expect(applyOperatorResult(cancelled, { type: 'failed', scope, requestId: 1, error: 'Late error' }).exchanges).toEqual([]);
    const live = applyOperatorResult(pending, { type: 'start', scope, requestId: 2 });
    const accepted = applyOperatorResult(live, { type: 'turn', scope, requestId: 2, turn });
    expect(accepted.exchanges).toMatchObject([{ question: null, turn: { turn_id: 't1' } }]);
    expect(applyOperatorResult(accepted, { type: 'failed', scope, requestId: 1, error: 'Late error' }).exchanges).toEqual(accepted.exchanges);
    const moved = applyOperatorResult(pending, { type: 'scope', scope: { ...scope, epoch: 1 } });
    expect(applyOperatorResult(moved, { type: 'turn', scope, requestId: 1, turn }).exchanges).toEqual([]);
  });
  it('bounds local exchanges without separating a question from its result', () => {
    let state = { ...initialOperatorState, scope };
    for (let id = 1; id <= 25; id += 1) {
      state = applyOperatorResult(state, { type: 'start', scope, requestId: id, question: `Question ${id}` });
      state = applyOperatorResult(state, { type: 'turn', scope, requestId: id, turn: { ...turn, turn_id: `t${id}`, assistant_text: `Answer ${id}` } });
    }
    expect(state.exchanges).toHaveLength(20);
    expect(state.exchanges[0]).toMatchObject({ question: 'Question 6', turn: { assistant_text: 'Answer 6' } });
    expect(state.exchanges.at(-1)).toMatchObject({ question: 'Question 25', turn: { assistant_text: 'Answer 25' } });
  });
  it('lets a live result supersede a typed request without leaving the panel stuck asking', () => {
    const typed = { generation: 4, requestId: 2, busy: 'asking' };
    const live = acceptLiveOperatorRequest(typed);
    expect(live).toEqual({ generation: 5, requestId: 3, busy: '' });
    expect(typed.generation === live.generation).toBe(false);
    const pending = applyOperatorResult({ ...initialOperatorState, scope }, { type: 'start', scope, requestId: typed.requestId });
    const accepted = applyOperatorResult(applyOperatorResult(pending, { type: 'start', scope, requestId: live.requestId }), { type: 'turn', scope, requestId: live.requestId, turn });
    expect(accepted.turn).toEqual(turn);
    expect(applyOperatorResult(accepted, { type: 'turn', scope, requestId: typed.requestId, turn: { ...turn, turn_id: 'late' } }).turn?.turn_id).toBe('t1');
  });
  it('keeps conversation history across buyer edits but clears current proposal and all history on company switch', () => {
    const accepted = applyOperatorResult(applyOperatorResult({ ...initialOperatorState, scope }, { type: 'start', scope, requestId: 1 }), { type: 'turn', scope, requestId: 1, turn });
    const edited = applyOperatorResult(accepted, { type: 'scope', scope: { ...scope, epoch: 1 } });
    expect(edited.turns.map(item => item.turn_id)).toEqual(['t1']);
    expect(edited.turn).toBeNull();
    expect(edited.reviewed).toBe(false);
    expect(applyOperatorResult(edited, { type: 'scope', scope: { ...scope, companyId: 'loomworks', epoch: 2 } }).turns).toEqual([]);
  });

  it('requires an explicit review of current exact arguments and clean inputs before apply', () => {
    const reviewed = { ...initialOperatorState, scope, turn, reviewed: true };
    expect(canApplyProposal(reviewed, scope, false, Date.now())).toBe(true);
    expect(canApplyProposal({ ...reviewed, reviewed: false }, scope, false, Date.now())).toBe(false);
    expect(canApplyProposal(reviewed, scope, true, Date.now())).toBe(false);
    expect(canApplyProposal(reviewed, { ...scope, epoch: 1 }, false, Date.now())).toBe(false);
    expect(canApplyProposal({ ...reviewed, turn: { ...turn, proposal: { ...proposal, expires_at: '2000-01-01T00:00:00Z' } } }, scope, false, Date.now())).toBe(false);
    const html = renderToStaticMarkup(<OperatorPanel scope={scope} csrf="csrf" dirty={true} onSession={() => {}} onUnknown={() => {}} />);
    expect(html).toContain('Run a new comparison');
    expect(html).toMatch(/disabled=""[^>]*>Send/);
    const cleanPreview = renderToStaticMarkup(<OperatorPanel scope={scope} csrf="csrf" dirty={false} initialState={reviewed} onSession={() => {}} onUnknown={() => {}} />);
    expect(cleanPreview).toContain('&quot;threshold&quot;: &quot;12.5&quot;');
    expect(cleanPreview).toContain('Evidence in this workspace');
    expect(cleanPreview).toContain('e1');
    expect(cleanPreview).toMatch(/>Apply reviewed proposal</);
    expect(cleanPreview).not.toMatch(/disabled=""[^>]*>Apply reviewed proposal</);
    const dirtyPreview = renderToStaticMarkup(<OperatorPanel scope={scope} csrf="csrf" dirty={true} initialState={reviewed} onSession={() => {}} onUnknown={() => {}} />);
    expect(dirtyPreview).toMatch(/disabled=""[^>]*>Apply reviewed proposal</);
  });
  it('blocks clean recovery and RFQ proposals when the displayed comparison or quote changes', () => {
    const boundPlan = { plan_id: 'plan-one', offer_versions: { q1: 2 } } as unknown as PlanView;
    for (const kind of ['recovery', 'rfq'] as const) {
      const bound = { ...proposal, kind, plan_id: 'plan-one', offer_versions: { q1: 2 } };
      const state = { ...initialOperatorState, scope, turn: { ...turn, proposal: bound }, reviewed: true };
      expect(canApplyProposal(state, scope, false, Date.now(), boundPlan, [quote])).toBe(true);
      for (const [currentPlan, currentOffers] of [
        [{ ...boundPlan, plan_id: 'plan-two' }, [quote]],
        [boundPlan, [{ ...quote, version: 3 }]],
        [{ ...boundPlan, offer_versions: { q1: 3 } }, [quote]],
      ] as [PlanView, OfferView[]][]) {
        expect(canApplyProposal(state, scope, false, Date.now(), currentPlan, currentOffers)).toBe(false);
        const html = renderToStaticMarkup(<OperatorPanel scope={scope} csrf="csrf" dirty={false} initialState={state} plan={currentPlan} offers={currentOffers} onSession={() => {}} onUnknown={() => {}} />);
        expect(html).toContain('Request a new proposal');
        expect(html).toMatch(/disabled=""[^>]*>Apply reviewed proposal</);
      }
    }
  });
  it('uses the authoritative original baseline for a second recovery while RFQ stays bound to the active scenario', () => {
    const baseline = { plan_id: 'baseline', offer_versions: { q1: 2 } } as unknown as PlanView;
    const active = { plan_id: 'scenario', offer_versions: { q1: 2 } } as unknown as PlanView;
    const recovery = { baseline, scenario: active } as RecoveryView;
    const nextRecovery = { ...proposal, kind: 'recovery' as const, plan_id: 'baseline', offer_versions: { q1: 2 } };
    const recoveryState = { ...initialOperatorState, scope, turn: { ...turn, proposal: nextRecovery }, reviewed: true };
    expect(canApplyProposal(recoveryState, scope, false, Date.now(), active, [quote], recovery)).toBe(true);
    const html = renderToStaticMarkup(<OperatorPanel scope={scope} csrf="csrf" dirty={false} initialState={recoveryState}
      plan={active} recovery={recovery} offers={[quote]} onSession={() => {}} onUnknown={() => {}} />);
    expect(html).not.toMatch(/disabled=""[^>]*>Apply reviewed proposal</);
    expect(canApplyProposal(recoveryState, scope, true, Date.now(), active, [quote], recovery)).toBe(false);
    expect(canApplyProposal(recoveryState, scope, false, Date.now(), active, [quote], null)).toBe(false);
    expect(canApplyProposal(recoveryState, scope, false, Date.now(), active, [quote],
      { ...recovery, scenario: { ...active, plan_id: 'stale-scenario' } })).toBe(false);
    expect(canApplyProposal(recoveryState, scope, false, Date.now(), active, [quote],
      { ...recovery, baseline: { ...baseline, offer_versions: { q1: 3 } } })).toBe(false);
    const rfqState = { ...initialOperatorState, scope, turn: { ...turn, proposal: {
      ...proposal, kind: 'rfq' as const, plan_id: 'scenario', offer_versions: { q1: 2 } } }, reviewed: true };
    expect(canApplyProposal(rfqState, scope, false, Date.now(), active, [quote], recovery)).toBe(true);
    expect(canApplyProposal(rfqState, scope, false, Date.now(), baseline, [quote], recovery)).toBe(false);
  });
  it('accepts a same-scope server utility session and keeps dirty Q&A available while blocking stale proposal apply', () => {
    const canonical = { session_id: 's1', csrf_token: 'csrf', expires_at: '2026-10-15T12:00:00Z', synthetic: true as const, persistence: 'ephemeral' as const, offers: [], provider: { model: 'test', available: true }, company: { company_id: 'solara-cables' }, context_version: 1 };
    const utility: OperatorTurn = { ...turn, turn_id: 'utility', status: 'completed', proposal: null, read_only: false, assistant_text: 'I filled the draft with 410 barrels.', session: canonical as never, action_result: { tool: 'fill_form', summary: 'Buyer draft updated', fields_changed: ['quantity'] } };
    const pending = applyOperatorResult({ ...initialOperatorState, scope }, { type: 'start', scope, requestId: 1 });
    const accepted = applyOperatorResult(pending, { type: 'turn', scope, requestId: 1, turn: utility });
    expect(accepted.turn?.assistant_text).toContain('410 barrels');
    expect(accepted.turns).toHaveLength(1);
    expect(applyOperatorResult(pending, { type: 'turn', scope, requestId: 1, turn: { ...utility, session: { ...canonical, session_id: 's2' } as never } }).turn).toBeNull();
    const html = renderToStaticMarkup(<OperatorPanel scope={scope} csrf="csrf" dirty={true} initialState={{ ...initialOperatorState, scope, turn }} onSession={() => {}} onUnknown={() => {}} />);
    expect(html).toMatch(/<textarea[^>]*><\/textarea>/);
    expect(html).not.toMatch(/<textarea[^>]*disabled/);
    expect(html).not.toMatch(/disabled=""[^>]*>Start conversation/);
    expect(html).toMatch(/disabled=""[^>]*>Apply reviewed proposal</);
  });
  it('accepts read-only navigation and no-op quote loading only with a current canonical session and action result', () => {
    const canonical = { session_id: scope.sessionId, csrf_token: 'csrf', expires_at: '2026-10-15T00:00:00Z', synthetic: true as const, persistence: 'ephemeral' as const, offers: [], provider: { model: 'test', available: true }, company: { company_id: scope.companyId }, context_version: scope.contextVersion };
    for (const action_result of [{ tool: 'navigate', summary: 'Open news view.', view: 'news' as const }, { tool: 'load_quotes', summary: 'Quotes already ready.', fields_changed: [] }]) {
      const response: OperatorTurn = { ...turn, status: 'completed', proposal: null, read_only: true, session: canonical as never, action_result };
      expect(acceptsOperatorTurn(response, scope)).toBe(true);
      const accepted = applyOperatorResult(applyOperatorResult({ ...initialOperatorState, scope }, { type: 'start', scope, requestId: 1 }), { type: 'turn', scope, requestId: 1, turn: response });
      expect(accepted.turn).toEqual(response);
      expect(acceptsOperatorTurn({ ...response, session: { ...canonical, session_id: 'old' } as never }, scope)).toBe(false);
      expect(acceptsOperatorTurn({ ...response, action_result: null }, scope)).toBe(false);
    }
  });
  it('shows exact review-quote IDs, versions, source fields and suggested document values before Apply', () => {
    const original = { ...quote, fixture_id: 'fixture-copper', status: 'extracted', version: 3, fields: { ...quote.fields, lead_time_days: { name: 'lead_time_days', kind: 'quantity', status: 'extracted', value: '18', source: 'document', evidence: [], reasons: [] } } };
    const review = { ...proposal, kind: 'review_quotes' as const, summary: 'Review quote values', parameters: { material_id: 'copper', offer_ids: ['q1'], preview: [{ offer_id: 'q1', fixture_id: 'fixture-copper', source_filename: 'reviewed.pdf', expected_version: 3, review_values: { lead_time_days: '10' } }] }, offer_versions: { q1: 3 } };
    const html = renderToStaticMarkup(<OperatorPanel scope={scope} csrf="csrf" dirty={false} initialState={{ ...initialOperatorState, scope, turn: { ...turn, proposal: review }, reviewed: true }} offers={[original]} onSession={() => {}} onUnknown={() => {}} />);
    expect(html).toContain('Review sample quotes');
    expect(html).toContain('Material: copper');
    expect(html).toContain('Offer IDs: q1');
    expect(html).toContain('Expected version 3');
    expect(html).toContain('Original: 18');
    expect(html).toContain('Reviewer value: 10');
    expect(html).toMatch(/disabled=""[^>]*>Apply reviewed proposal/);
    const changed = renderToStaticMarkup(<OperatorPanel scope={scope} csrf="csrf" dirty={false} initialState={{ ...initialOperatorState, scope, turn: { ...turn, proposal: review }, reviewed: true }} offers={[{ ...original, version: 4 }]} onSession={() => {}} onUnknown={() => {}} />);
    expect(changed).toContain('A quote source or version changed');
    expect(changed).toMatch(/disabled=""[^>]*>Apply reviewed proposal/);
  });
  it('shows a reviewable simulated email with exact arguments and a server-confirmed outbox outcome', () => {
    const email = { ...proposal, kind: 'simulate_email' as const, summary: 'Prepare a simulated supplier message', parameters: { material_id: 'copper', recipient_label: 'Supplier desk', subject: 'Availability', body: 'Please confirm stock.' } };
    const html = renderToStaticMarkup(<OperatorPanel scope={scope} csrf="csrf" dirty={false} initialState={{ ...initialOperatorState, scope, turn: { ...turn, proposal: email }, reviewed: true }} onSession={() => {}} onUnknown={() => {}} />);
    expect(html).toContain('Current proposal · Email simulation');
    expect(html).toContain('Please confirm stock.');
    expect(html).toContain('No external delivery');
    const outcomeHtml = renderToStaticMarkup(<OperatorPanel scope={scope} csrf="csrf" dirty={false} initialState={{ ...initialOperatorState, scope, outcome: { status: 'applied', company_id: scope.companyId, context_version: 1, kind: 'simulate_email', result: { message_id: 'm1' }, session: { session_id: scope.sessionId, csrf_token: 'csrf', expires_at: '2026-10-15T00:00:00Z', synthetic: true, persistence: 'ephemeral', offers: [], provider: { model: 'test', available: true } } } }} onSession={() => {}} onUnknown={() => {}} />);
    expect(outcomeHtml).toContain('Applied to server session · Email simulation');
    expect(outcomeHtml).toContain('simulated sent record is available in the outbox');
  });
  it('shows one bounded all-material profile table with units, rounded cover and review state', () => {
    const materialList = Array.from({length: 8}, (_, index) => ({material_id: `stone-${index}`, name: `Material ${index + 1}`, quantity_unit: 'kg'}));
    const rows = materialList.map((material, index) => ({material_id: material.material_id, name: material.name, unit: 'kg', stock: '2000.0000', consumption_per_day: '333.3300', stock_cover_days: '6.0000600006000060000600006', shortest_supplier_lead_days: 10 + index, stock_cover_at_or_below_lead: true, suppliers: [{supplier_id: 's1', name: 'Supplier [SYNTHETIC]'}], synthetic: true}));
    const current = {...scope, companyId:'stonebridge-materials'};
    const result = {...turn, company_id:current.companyId, status:'completed' as const, proposal:null, evidence_ids:materialList.map(m => `profile:${current.companyId}:${m.material_id}`), analytics:{source:'fictional_company_profile', company_id:current.companyId, as_of:'2026-10-14', materials:rows, materials_by_cover:[...rows].reverse()}};
    const html = renderToStaticMarkup(<OperatorPanel scope={current} csrf="csrf" dirty={false} materials={materialList} initialState={{...initialOperatorState, scope:current, turn:result}} onSession={() => {}} onUnknown={() => {}} />);
    expect(html).toContain('Computed analytics');
    expect(html.match(/<table\b/g)).toHaveLength(1);
    expect(html.match(/<tr(?: |>|\/)/g)).toHaveLength(9);
    expect(html).toContain('Profile · 2026-10-14');
    expect(html).toContain('Material 1');
    expect(html).toContain('Material 8');
    expect(html).toContain('2,000 kg');
    expect(html).toContain('333.33 kg');
    expect(html).toContain('6 days');
    expect(html).toContain('10 days');
    expect(html).toContain('Review cover vs listed lead');
    expect(html).not.toContain('6.0000600006000060000600006');
    expect(html).not.toContain('materials_by_cover');
    expect(html).not.toContain('supplier_id');
    expect(html).not.toContain('[SYNTHETIC]');
    expect(html).not.toContain('profile:stonebridge-materials:');
    expect(html).not.toContain('stone-0');
    const foreign = renderToStaticMarkup(<OperatorPanel scope={scope} csrf="csrf" dirty={false} materials={materialList} initialState={{...initialOperatorState, scope, turn:{...result, company_id:scope.companyId}}} onSession={() => {}} onUnknown={() => {}} />);
    expect(foreign).not.toContain('Material 1');
  });

  it('does not display nonfinite profile numbers or change other analytics tables', () => {
    const profile = {...turn, status:'completed' as const, proposal:null, analytics:{source:'fictional_company_profile', company_id:scope.companyId, materials:[{material_id:'copper', unit:'kg', stock:'NaN', consumption_per_day:'Infinity', stock_cover_days:'1e999', shortest_supplier_lead_days:Infinity, stock_cover_at_or_below_lead:null}]}};
    const html = renderToStaticMarkup(<OperatorPanel scope={scope} csrf="csrf" dirty={false} materials={[{material_id:'copper',name:'Copper',quantity_unit:'kg'}]} initialState={{...initialOperatorState, scope, turn:profile}} onSession={() => {}} onUnknown={() => {}} />);
    expect(html).toContain('Coverage unknown');
    expect(html).toContain('Unavailable kg');
    expect(html).not.toContain('NaN');
    expect(html).not.toContain('Infinity');
    expect(html).not.toContain('1e999');
    const generic = renderToStaticMarkup(<OperatorPanel scope={scope} csrf="csrf" dirty={false} initialState={{...initialOperatorState, scope, turn:{...profile, analytics:{source:'other', checks:[{label:'Checked', value:'yes'}]}}}} onSession={() => {}} onUnknown={() => {}} />);
    expect(generic).toContain('Checks');
    expect(generic).toContain('Checked');
    expect(generic.match(/<table>/g)).toHaveLength(1);
  });

  it('resolves profile evidence only for exact current-company known materials', () => {
    const materials = [{material_id:'copper', name:'Copper cathode'}];
    expect(resolveOperatorEvidence('profile:solara-cables:copper', scope, null, [], null, materials)).toMatchObject({kind:'profile', title:'Copper cathode profile', href:null});
    expect(resolveOperatorEvidence('profile:loomworks:copper', scope, null, [], null, materials).kind).toBe('unavailable');
    expect(resolveOperatorEvidence('profile:solara-cables:unknown', scope, null, [], null, materials).kind).toBe('unavailable');
    expect(resolveOperatorEvidence('profile:solara-cables:copper:extra', scope, null, [], null, materials).kind).toBe('unavailable');
    expect(resolveOperatorEvidence('profile:solara-cables:copper', scope, null, [], null).kind).toBe('unavailable');
  });

  it('enforces actual audio limits, real sample rate, and WebM Opus support', () => {
    expect(validateAudioCapture({ bytes: 500000, durationMs: 20000, sampleRate: 48000, mimeType: 'audio/webm;codecs=opus' })).toBeNull();
    expect(validateAudioCapture({ bytes: 500001, durationMs: 1000, sampleRate: 48000, mimeType: 'audio/webm;codecs=opus' })).toMatch(/500 KB/);
    expect(validateAudioCapture({ bytes: 100, durationMs: 20001, sampleRate: 48000, mimeType: 'audio/webm;codecs=opus' })).toMatch(/20 seconds/);
    expect(validateAudioCapture({ bytes: 100, durationMs: 1000, sampleRate: undefined, mimeType: 'audio/webm;codecs=opus' })).toMatch(/sample rate/);
    expect(validateAudioCapture({ bytes: 100, durationMs: 1000, sampleRate: 48000, mimeType: 'audio/ogg' })).toMatch(/WebM Opus/);
  });

  it('resolves only current company news and visible quote evidence with safe links and explicit unavailable labels', () => {
    expect(resolveOperatorEvidence('e1', scope, news, [quote])).toMatchObject({ kind: 'news', title: '<script>Freight pressure</script>', publisher: 'Example News', href: 'https://example.org/article' });
    expect(resolveOperatorEvidence('e1', scope, { ...news, company_id: 'loomworks' }, [quote]).kind).toBe('unavailable');
    expect(resolveOperatorEvidence('e1', scope, { ...news, context_version: 2 }, [quote]).kind).toBe('unavailable');
    expect(resolveOperatorEvidence('e1', scope, { ...news, items: [{ ...news.items[0], url: 'javascript:alert(1)' }] }, [quote]).href).toBeNull();
    expect(resolveOperatorEvidence('q1', scope, news, [quote])).toMatchObject({ kind: 'quote', title: 'Swift Supply' });
    expect(resolveOperatorEvidence('q1', scope, news, [{ ...quote, fields: {} }])).toMatchObject({ kind: 'quote', title: 'Supplier unavailable' });
    expect(resolveOperatorEvidence('missing', scope, news, [quote])).toMatchObject({ kind: 'unavailable', title: 'Evidence unavailable in this workspace' });
    const preview = renderToStaticMarkup(<OperatorPanel scope={scope} csrf="csrf" dirty={false} initialState={{ ...initialOperatorState, scope, turn }} news={news} offers={[quote]} onSession={() => {}} onUnknown={() => {}} />);
    expect(preview).toContain('Example News');
    expect(preview).toContain('https://example.org/article');
    expect(preview).toContain('&lt;script&gt;Freight pressure&lt;/script&gt;');
    expect(preview).not.toContain('<script>');
  });

  it('shows synthetic attention and saved watches as material and dated facts, with a safe unknown fallback', () => {
    const materials = [{ material_id: 'copper', name: 'Copper' }];
    const attention = { material_id: 'copper', unit: 'kg', stock_cover_days: '12.5', minimum_lead_time_days: 15, synthetic: true, summary: 'Synthetic stock cover comparison.' };
    const watch = { watch_id: 'w1', company_id: 'solara-cables', material_id: 'copper', evidence_ids: ['e1'], created_at: '2026-10-14T09:30:00Z', synthetic: true };
    expect(formatOperatorAttention(attention, materials)).toEqual({ title: 'Copper', detail: 'Stock cover: 12.5 days · shortest listed supplier lead: 15 days', synthetic: true });
    expect(formatOperatorWatch(watch, materials, scope.companyId)).toEqual({ title: 'Copper', detail: 'Saved 2026-10-14 09:30 UTC', synthetic: true });
    expect(formatOperatorWatch({ ...watch, company_id: 'loomworks' }, materials, scope.companyId)).toMatchObject({ title: 'Watch details unavailable', synthetic: false });
    expect(formatOperatorAttention({ unexpected: '<script>' }, materials)).toMatchObject({ title: 'Attention details unavailable', synthetic: false });
    expect(formatOperatorWatch({ unexpected: '<script>' }, materials)).toMatchObject({ title: 'Watch details unavailable', synthetic: false });
    const overview = { company_id: 'solara-cables', context_version: 1, available: true, agents: [], attention: [attention, { unexpected: '<script>' }], watch_items: [watch, { unexpected: '<script>' }], limitations: [] };
    const html = renderToStaticMarkup(<OperatorPanel scope={scope} csrf="csrf" dirty={false} initialState={{ ...initialOperatorState, scope, overview }} materials={materials} onSession={() => {}} onUnknown={() => {}} />);
    expect(html).toContain('Stock cover: 12.5 days');
    expect(html).toContain('shortest listed supplier lead: 15 days');
    expect(html).toContain('Saved 2026-10-14 09:30 UTC');
    expect(html).not.toContain('Synthetic watch item');
    expect(html).toContain('Attention details unavailable');
    expect(html).toContain('Watch details unavailable');
    expect(html).not.toContain('stock_cover_days');
    expect(html).not.toContain('&lt;script&gt;');
    const applied = { status: 'applied' as const, company_id: 'solara-cables', context_version: 1, kind: 'watch' as const, result: watch, session: { session_id: 's1', csrf_token: 'csrf', expires_at: '2026-10-14T12:00:00Z', synthetic: true as const, persistence: 'ephemeral' as const, offers: [], provider: { model: 'test', available: true } } };
    const outcomeHtml = renderToStaticMarkup(<OperatorPanel scope={scope} csrf="csrf" dirty={false} initialState={{ ...initialOperatorState, scope, outcome: applied }} materials={materials} onSession={() => {}} onUnknown={() => {}} />);
    expect(outcomeHtml).toContain('Applied to server session');
    expect(outcomeHtml).not.toContain('&quot;watch_id&quot;');
  });
});
