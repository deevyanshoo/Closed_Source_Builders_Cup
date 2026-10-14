import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import type { NewsView, OfferView, OperatorTurn } from './demo-api';
import { acceptLiveOperatorRequest, acceptsOperatorTurn, applyOperatorResult, canApplyProposal, formatOperatorAttention, formatOperatorWatch, initialOperatorState, OperatorPanel, operatorSeedMessage, resolveOperatorEvidence, validateAudioCapture } from './demo-operator';

const scope = { sessionId: 's1', companyId: 'solara-cables', contextVersion: 1, epoch: 0 };
const proposal = { proposal_id: 'p1', kind: 'watch' as const, summary: 'Watch copper', parameters: { material_id: 'copper', threshold: '12.5' }, plan_id: null, offer_versions: {}, context_version: 1, expires_at: '2099-10-14T12:00:00Z' };
const turn: OperatorTurn = { turn_id: 't1', company_id: 'solara-cables', context_version: 1, status: 'proposal', assistant_text: 'A reviewable watch is ready.', agent_steps: [{ agent: 'evidence', role: 'exposure specialist', status: 'completed', model: 'gemini-test', provider_call: 'real', tool: 'inspect_news', summary: 'Matched source e1' }], evidence_ids: ['e1'], proposal, read_only: true };
const news: NewsView = { company_id: 'solara-cables', context_version: 1, checked_at: '2026-10-14T10:00:00Z', cache_state: 'fresh', sources: [], fx: [], limitations: [], items: [{ item_id: 'e1', title: '<script>Freight pressure</script>', url: 'https://example.org/article', publisher: 'Example News', provider: 'eia', published_at: null, first_seen_at: null, retrieved_at: '2026-10-14T10:00:00Z', source_tier: 'primary', source_reliability: 'high', reliability_reason: '', category: 'energy', matched_materials: ['copper'], matched_routes: [], matched_currencies: [], relevance_reason: '', implication: '', implication_confidence: 'conditional', next_steps: [], synthetic: false }] };
const quote: OfferView = { offer_id: 'q1', version: 2, status: 'saved', synthetic: true, document: { document_id: 'd1', filename: 'reviewed.pdf', media_type: 'application/pdf', byte_size: 10 }, extractor: 'gemini', extractor_detail: {}, as_of: '2026-10-14', fields: { supplier_name: { name: 'supplier_name', kind: 'text', status: 'corrected', value: 'Swift Supply', source: 'buyer', evidence: [], reasons: [] } }, items: [], review_flags: [], normalized: {}, blocking_reasons: [] };

describe('operator response boundary', () => {
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
  it('keeps voice first, scoped answers readable, and the typed composer last with evidence collapsed', () => {
    const first = { ...turn, turn_id: 'first', status: 'completed' as const, proposal: null, assistant_text: 'A first answer.' };
    const second = { ...turn, turn_id: 'second', status: 'completed' as const, proposal: null, assistant_text: 'A second answer.' };
    const html = renderToStaticMarkup(<OperatorPanel scope={scope} csrf="csrf" dirty={false}
      initialState={{ ...initialOperatorState, scope, turn: second, turns: [first, second] }}
      news={news} onSession={() => {}} onUnknown={() => {}} />);
    expect(html).toContain('aria-label="Assistant conversation"');
    expect(html.indexOf('Start conversation')).toBeLessThan(html.indexOf('A first answer.'));
    expect(html.indexOf('A first answer.')).toBeLessThan(html.indexOf('A second answer.'));
    expect(html.indexOf('A second answer.')).toBeLessThan(html.indexOf('Your message'));
    expect(html.match(/<details class="demo-assistant-trace"/g)).toHaveLength(2);
    expect(html).not.toMatch(/<details class="demo-assistant-trace"[^>]* open/);
    expect(html).toContain('Evidence and specialist activity');
    expect(html).not.toContain('<script>');
  });
  it('puts the voice console immediately after the heading even with risks and an unavailable provider', () => {
    const overview = { company_id: scope.companyId, context_version: scope.contextVersion, available: false,
      agents: [], attention: [], watch_items: [], limitations: [] };
    const html = renderToStaticMarkup(<OperatorPanel scope={scope} csrf="csrf" dirty={true}
      initialState={{ ...initialOperatorState, scope, overview }}
      riskSummary={{ high: 1, medium: 0, low: 0, checkedAt: '2026-10-15' }} onSession={() => {}} onUnknown={() => {}} />);
    expect(html.indexOf('>Operator</h2>')).toBeLessThan(html.indexOf('Start conversation'));
    expect(html.indexOf('Start conversation')).toBeLessThan(html.indexOf('Current company risks:'));
    expect(html.indexOf('Start conversation')).toBeLessThan(html.indexOf('Company attention'));
    expect(html.indexOf('Start conversation')).toBeLessThan(html.indexOf('The agent provider is unavailable'));
    expect(html).toContain('The agent provider is unavailable');
    expect(html).toContain('Your message');
  });
  it('keeps attention compact while review arguments, checkbox, and Apply remain outside closed traces', () => {
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
    expect(html.indexOf('Exact proposed arguments')).toBeGreaterThan(html.lastIndexOf('</details>'));
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
    expect(html).not.toMatch(/disabled=""[^>]*>Apply reviewed proposal/);
    const changed = renderToStaticMarkup(<OperatorPanel scope={scope} csrf="csrf" dirty={false} initialState={{ ...initialOperatorState, scope, turn: { ...turn, proposal: review }, reviewed: true }} offers={[{ ...original, version: 4 }]} onSession={() => {}} onUnknown={() => {}} />);
    expect(changed).toContain('A quote source or version changed');
    expect(changed).toMatch(/disabled=""[^>]*>Apply reviewed proposal/);
  });
  it('shows a reviewable simulated email with exact arguments and a server-confirmed outbox outcome', () => {
    const email = { ...proposal, kind: 'simulate_email' as const, summary: 'Prepare a simulated supplier message', parameters: { material_id: 'copper', recipient_label: 'Supplier desk', subject: 'Availability', body: 'Please confirm stock.' } };
    const html = renderToStaticMarkup(<OperatorPanel scope={scope} csrf="csrf" dirty={false} initialState={{ ...initialOperatorState, scope, turn: { ...turn, proposal: email }, reviewed: true }} onSession={() => {}} onUnknown={() => {}} />);
    expect(html).toContain('simulate_email');
    expect(html).toContain('Please confirm stock.');
    expect(html).toContain('No external delivery');
    const outcomeHtml = renderToStaticMarkup(<OperatorPanel scope={scope} csrf="csrf" dirty={false} initialState={{ ...initialOperatorState, scope, outcome: { status: 'applied', company_id: scope.companyId, context_version: 1, kind: 'simulate_email', result: { message_id: 'm1' }, session: { session_id: scope.sessionId, csrf_token: 'csrf', expires_at: '2026-10-15T00:00:00Z', synthetic: true, persistence: 'ephemeral', offers: [], provider: { model: 'test', available: true } } } }} onSession={() => {}} onUnknown={() => {}} />);
    expect(outcomeHtml).toContain('simulated sent record is available in the outbox');
  });
  it('renders server inventory analytics as a table with stated units', () => {
    const html = renderToStaticMarkup(<OperatorPanel scope={scope} csrf="csrf" dirty={false} initialState={{ ...initialOperatorState, scope, turn: { ...turn, status: 'completed', proposal: null, analytics: { source: 'fictional_company_profile', as_of: '2026-10-14', materials: [{ name: 'Copper', unit: 'kg', stock: '2000', stock_cover_days: '20' }], materials_by_cover: [{ name: 'Copper', unit: 'kg', stock_cover_days: '20' }] } } }} onSession={() => {}} onUnknown={() => {}} />);
    expect(html).toContain('Computed analytics');
    expect(html.match(/<table>/g)).toHaveLength(2);
    expect(html).toContain('Materials by cover');
    expect(html).toContain('Fictional company profile · 2026-10-14');
    expect(html).toContain('Copper');
    expect(html).toContain('kg');
    expect(html).toContain('20');
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
    expect(html).toContain('Synthetic watch item');
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
