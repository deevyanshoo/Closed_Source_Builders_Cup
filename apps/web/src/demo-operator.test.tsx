import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import type { NewsView, OfferView, OperatorTurn } from './demo-api';
import { applyOperatorResult, canApplyProposal, formatOperatorAttention, formatOperatorWatch, initialOperatorState, OperatorPanel, resolveOperatorEvidence, validateAudioCapture } from './demo-operator';

const scope = { sessionId: 's1', companyId: 'solara-cables', contextVersion: 1, epoch: 0 };
const proposal = { proposal_id: 'p1', kind: 'watch' as const, summary: 'Watch copper', parameters: { material_id: 'copper', threshold: '12.5' }, plan_id: null, offer_versions: {}, context_version: 1, expires_at: '2099-10-14T12:00:00Z' };
const turn: OperatorTurn = { turn_id: 't1', company_id: 'solara-cables', context_version: 1, status: 'proposal', assistant_text: 'A reviewable watch is ready.', agent_steps: [{ agent: 'evidence', role: 'exposure specialist', status: 'completed', model: 'gemini-test', provider_call: 'real', tool: 'inspect_news', summary: 'Matched source e1' }], evidence_ids: ['e1'], proposal, read_only: true };
const news: NewsView = { company_id: 'solara-cables', context_version: 1, checked_at: '2026-10-14T10:00:00Z', cache_state: 'fresh', sources: [], fx: [], limitations: [], items: [{ item_id: 'e1', title: '<script>Freight pressure</script>', url: 'https://example.org/article', publisher: 'Example News', provider: 'eia', published_at: null, first_seen_at: null, retrieved_at: '2026-10-14T10:00:00Z', source_tier: 'primary', source_reliability: 'high', reliability_reason: '', category: 'energy', matched_materials: ['copper'], matched_routes: [], matched_currencies: [], relevance_reason: '', implication: '', implication_confidence: 'conditional', next_steps: [], synthetic: false }] };
const quote: OfferView = { offer_id: 'q1', version: 2, status: 'saved', synthetic: true, document: { document_id: 'd1', filename: 'reviewed.pdf', media_type: 'application/pdf', byte_size: 10 }, extractor: 'gemini', extractor_detail: {}, as_of: '2026-10-14', fields: { supplier_name: { name: 'supplier_name', kind: 'text', status: 'corrected', value: 'Swift Supply', source: 'buyer', evidence: [], reasons: [] } }, items: [], review_flags: [], normalized: {}, blocking_reasons: [] };

describe('operator response boundary', () => {
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
