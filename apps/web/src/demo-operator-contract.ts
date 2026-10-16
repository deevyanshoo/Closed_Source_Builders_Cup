import type { DemoSession, OperatorProposal, OperatorTurn, PreparedDraft } from './demo-api';

type Scope = { sessionId: string; companyId: string; contextVersion: number };
const record = (value: unknown): Record<string, unknown> | null => value !== null && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : null;
const draftFields = ['recipient_label', 'subject', 'body'] as const;
const exactDraftFields = (source: Record<string, unknown>, draft: PreparedDraft) => draftFields.every(field =>
  typeof source[field] === 'string' && source[field] === draft[field]);
const sameVersions = (a: Record<string, number>, b: Record<string, number>) => {
  const ids=Object.keys(a);
  return ids.length===Object.keys(b).length && ids.every(id=>Number.isSafeInteger(a[id]) && a[id]===b[id]);
};
export function currentProposalDraft(proposal: OperatorProposal, session: DemoSession, scope: Scope): PreparedDraft | null {
  if (session.session_id !== scope.sessionId || session.company?.company_id !== scope.companyId || session.context_version !== scope.contextVersion ||
      !Array.isArray(session.drafts) || !proposal.proposal_id || !['rfq', 'simulate_email'].includes(proposal.kind)) return null;
  const owned = session.drafts.filter(item=>item?.draft_id===proposal.proposal_id && item.company_id===scope.companyId &&
    item.kind===(proposal.kind==='rfq'?'rfq':'email') && item.status==='draft_unsent');
  if (owned.length!==1) return null;
  const draft=owned[0];
  if (!draft || !draftFields.every(field=>typeof draft[field]==='string' && !!draft[field].trim())) return null;
  const parameters=record(proposal.parameters);
  if (!parameters) return null;
  const preview=parameters.draft_preview;
  if (preview !== undefined && (!record(preview) || !exactDraftFields(record(preview)!,draft))) return null;
  if (proposal.kind==='simulate_email' && !exactDraftFields(parameters,draft)) return null;
  if (proposal.kind==='rfq' && (draft.plan_id !== undefined && draft.plan_id !== proposal.plan_id ||
      draft.offer_versions && !sameVersions(draft.offer_versions,proposal.offer_versions))) return null;
  return draft;
}
export function currentSessionProposal(session: DemoSession, scope: Scope, now=Date.now()): OperatorProposal | null {
  const proposal=session.pending_operator_proposal;
  if (!proposal || session.session_id!==scope.sessionId || session.company?.company_id!==scope.companyId || session.context_version!==scope.contextVersion ||
      typeof proposal.proposal_id!=='string' || !proposal.proposal_id || typeof proposal.summary!=='string' ||
      !record(proposal.parameters) || !record(proposal.offer_versions) || proposal.context_version!==scope.contextVersion ||
      typeof proposal.expires_at!=='string' || !Number.isFinite(Date.parse(proposal.expires_at)) || Date.parse(proposal.expires_at)<=now) return null;
  if (proposal.kind==='rfq' || proposal.kind==='simulate_email') {
    if (!currentProposalDraft(proposal,session,scope)) return null;
    if (proposal.kind==='simulate_email') return proposal.plan_id===null && Object.keys(proposal.offer_versions).length===0 ? proposal : null;
  }
  if (proposal.kind==='rfq' || proposal.kind==='recovery') {
    const plan=proposal.kind==='recovery' ? (session.recovery ? session.recovery.baseline : session.plan) : session.recovery?.scenario ?? session.plan;
    const ids=Object.keys(proposal.offer_versions);
    return plan?.plan_id===proposal.plan_id && ids.length>0 && ids.length===Object.keys(plan.offer_versions).length &&
      Array.isArray(session.offers) && ids.every(id=>Number.isSafeInteger(proposal.offer_versions[id]) &&
        proposal.offer_versions[id]===plan.offer_versions[id] && session.offers.some(offer=>offer?.offer_id===id && offer.version===proposal.offer_versions[id])) ? proposal : null;
  }
  return ['watch','research','review_quotes'].includes(proposal.kind) ? proposal : null;
}
/** The saved artifact is complete; the distinct proposed workflow change still needs review. */
export function isCurrentSavedDraft(turn: Partial<OperatorTurn>, scope: Scope, now = Date.now()): boolean {
  const p=turn.proposal, session=turn.session;
  if (turn.status !== 'completed' || turn.read_only !== false || !p ||
      !((p.kind === 'rfq' && turn.action_result?.tool === 'prepare_rfq') ||
        (p.kind === 'simulate_email' && turn.action_result?.tool === 'prepare_simulated_email')) ||
      turn.company_id !== scope.companyId || turn.context_version !== scope.contextVersion ||
      session?.session_id !== scope.sessionId || session.company?.company_id !== scope.companyId || session.context_version !== scope.contextVersion ||
      typeof p.proposal_id !== 'string' || !p.proposal_id || typeof p.summary !== 'string' || p.context_version !== scope.contextVersion ||
      typeof p.expires_at !== 'string' ||
      !Number.isFinite(Date.parse(p.expires_at)) || Date.parse(p.expires_at) <= now ||
      !p.parameters || typeof p.parameters !== 'object' || Array.isArray(p.parameters) || !p.offer_versions || typeof p.offer_versions !== 'object' || Array.isArray(p.offer_versions) ||
      (session.pending_operator_proposal !== undefined && (session.pending_operator_proposal?.proposal_id !== p.proposal_id ||
        session.pending_operator_proposal.kind !== p.kind || session.pending_operator_proposal.context_version !== p.context_version)) ||
      !currentProposalDraft(p,session,scope)) return false;
  if (p.kind === 'simulate_email') return p.plan_id === null && Object.keys(p.offer_versions).length === 0;
  const plan=session.recovery?.scenario ?? session.plan;
  const ids=Object.keys(p.offer_versions);
  return !!p.plan_id && plan?.plan_id === p.plan_id && !!plan.offer_versions && ids.length > 0 &&
    ids.length === Object.keys(plan.offer_versions).length && Array.isArray(session.offers) && ids.every(id=>
      Number.isSafeInteger(p.offer_versions[id]) && p.offer_versions[id] === plan.offer_versions[id] &&
      session.offers.some(offer=>!!offer && offer.offer_id === id && offer.version === p.offer_versions[id]));
}
