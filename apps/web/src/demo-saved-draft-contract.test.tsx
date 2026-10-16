import { expect, it, vi } from 'vitest';
import type { DemoSession, OperatorTurn, PlanView, OfferView } from './demo-api';
import { acceptsOperatorTurn, matchesUtilitySession, applyOperatorResult, initialOperatorState, canApplyProposal, reviewableOperatorTurn, reconcileOperatorSessionOnChange } from './demo-operator';
import { acceptsLiveTurn } from './demo-live-voice';
import { renderToStaticMarkup } from 'react-dom/server';
import { OperatorPanel } from './demo-operator';
import { currentSessionProposal } from './demo-operator-contract';
const scope={sessionId:'s',companyId:'solara',contextVersion:2,epoch:1};
const plan={plan_id:'plan',offer_versions:{q:3}} as unknown as PlanView;
const offers=[{offer_id:'q',version:3}] as OfferView[];
it('restores a recovery proposal against the exact first or existing baseline only',()=>{
  const proposal={proposal_id:'recover',kind:'recovery' as const,summary:'Compare supplier delay',parameters:{delay_days:2},plan_id:'plan',offer_versions:{q:3},context_version:2,expires_at:'2099-01-01T00:00:00Z'};
  const session={session_id:'s',csrf_token:'csrf',expires_at:'2099-01-01T00:00:00Z',synthetic:true,persistence:'ephemeral',provider:{model:'test',available:false},company:{company_id:'solara'},context_version:2,plan,offers,pending_operator_proposal:proposal} as unknown as DemoSession;
  expect(currentSessionProposal(session,scope)).toEqual(proposal);
  const restored=renderToStaticMarkup(<OperatorPanel scope={scope} csrf="csrf" dirty={false} session={session} plan={plan} onSession={()=>{}} onUnknown={()=>{}} />);
  expect(restored).toContain('Current proposal requiring review');
  expect(restored).not.toContain('checked=""');
  const recovery={baseline:plan,scenario:{...plan,plan_id:'scenario'}} as NonNullable<DemoSession['recovery']>;
  expect(currentSessionProposal({...session,plan:{...plan,plan_id:'other'},recovery},scope)).toEqual(proposal);
  expect(currentSessionProposal({...session,recovery:{...recovery,baseline:{...plan,plan_id:'other'}}},scope)).toBeNull();
  expect(currentSessionProposal({...session,recovery:{...recovery,baseline:undefined} as unknown as DemoSession['recovery']},scope)).toBeNull();
  expect(currentSessionProposal({...session,recovery,pending_operator_proposal:{...proposal,plan_id:'scenario'}},scope)).toBeNull();
  expect(currentSessionProposal({...session,plan:{...plan,plan_id:'other'}},scope)).toBeNull();
  expect(currentSessionProposal({...session,pending_operator_proposal:{...proposal,offer_versions:{q:4}}},scope)).toBeNull();
  expect(currentSessionProposal({...session,pending_operator_proposal:{...proposal,context_version:3}},scope)).toBeNull();
  expect(currentSessionProposal(session,{...scope,companyId:'other'})).toBeNull();
});
function saved(kind:'rfq'|'simulate_email'): OperatorTurn {
  const proposal: NonNullable<OperatorTurn['proposal']>={proposal_id:'p',kind,summary:'Review draft',parameters:kind==='rfq'?{}:{recipient_label:'Supplier',subject:'Stock',body:'Please confirm.'},plan_id:kind==='rfq'?'plan':null,offer_versions:kind==='rfq'?{q:3}:{},context_version:2,expires_at:'2099-01-01T00:00:00Z'};
  const session={session_id:'s',company:{company_id:'solara'},context_version:2,plan,offers,drafts:[{draft_id:'p',company_id:'solara',kind:kind==='rfq'?'rfq':'email',status:'draft_unsent',recipient_label:'Supplier',subject:'Stock',body:'Please confirm.'}]} as DemoSession;
  return {turn_id:'t',company_id:'solara',context_version:2,status:'completed',read_only:false,assistant_text:'Saved unsent draft',agent_steps:[],evidence_ids:[],proposal,session,action_result:{tool:kind==='rfq'?'prepare_rfq':'prepare_simulated_email',summary:'Saved draft'}};
}
for(const kind of ['rfq','simulate_email'] as const) it(`accepts only the current completed saved ${kind} plus pending review contract in typed and live`,()=>{
  const turn=saved(kind);
  expect(matchesUtilitySession(turn,scope)).toBe(true);expect(acceptsOperatorTurn(turn,scope)).toBe(true);expect(acceptsLiveTurn(turn,scope,scope)).toBe(true);
  const invalid=[{...turn,status:'proposal' as const},{...turn,read_only:true},{...turn,action_result:{tool:'fill_form',summary:'Changed'}},{...turn,session:{...turn.session!,session_id:'foreign'}},{...turn,session:{...turn.session!,pending_operator_proposal:null}},{...turn,company_id:'foreign'},{...turn,proposal:{...turn.proposal!,context_version:1}},{...turn,proposal:{...turn.proposal!,expires_at:'2000-01-01'}},{...turn,session:{...turn.session!,drafts:[]}}, ...(kind==='rfq'?[{...turn,session:{...turn.session!,offers:[{...offers[0],version:4}]}}]:[])];
  for(const bad of invalid) {expect(acceptsOperatorTurn(bad,scope)).toBe(false);expect(acceptsLiveTurn(bad,scope,scope)).toBe(false);}
  for(const bad of [
    {...turn,proposal:{...turn.proposal!,parameters:[]}},
    {...turn,proposal:{...turn.proposal!,summary:null}},
    {...turn,session:{...turn.session!,drafts:[null]}},
  ]) {expect(acceptsOperatorTurn(bad as never,scope)).toBe(false);expect(acceptsLiveTurn(bad,scope,scope)).toBe(false);}
  const artifact=turn.session!.drafts![0];
  for(const field of ['recipient_label','subject','body'] as const) {
    const bad={...turn,proposal:{...turn.proposal!,parameters:{...turn.proposal!.parameters,draft_preview:{...artifact,[field]:'Different'}}}};
    expect(acceptsOperatorTurn(bad,scope)).toBe(false);
    expect(acceptsLiveTurn(bad,scope,scope)).toBe(false);
    if(kind==='simulate_email') {
      const changed={...turn,proposal:{...turn.proposal!,parameters:{...turn.proposal!.parameters,[field]:'Different'}}};
      expect(acceptsOperatorTurn(changed,scope)).toBe(false);
    }
  }
  expect(acceptsLiveTurn(turn,scope,{...scope,epoch:2})).toBe(false);
  const state={...initialOperatorState,scope,turn};
  expect(canApplyProposal(state,scope,false,Date.now(),plan,offers)).toBe(false);
  expect(canApplyProposal({...state,reviewed:true},scope,false,Date.now(),plan,offers)).toBe(true);
  expect(canApplyProposal({...state,reviewed:true},scope,true,Date.now(),plan,offers)).toBe(false);
});
it('restores only the authoritative current proposal as an unreviewed card and reconciles later sessions',()=>{
  const turn=saved('rfq');
  const session={...turn.session!,pending_operator_proposal:turn.proposal};
  const html=renderToStaticMarkup(<OperatorPanel scope={scope} csrf="csrf" dirty={false} session={session} plan={plan} onSession={()=>{}} onUnknown={()=>{}} />);
  expect(html).toContain('Current proposal requiring review');
  expect(html).toContain('Full draft preview');
  expect(html).toContain('Please confirm.');
  expect(html).not.toContain('checked=""');
  const initial={...initialOperatorState,scope,turn,reviewed:true};
  const replacement={...turn.proposal!,proposal_id:'new'};
  const replaced=applyOperatorResult(initial,{type:'session',scope,session:{...session,drafts:[{...session.drafts![0],draft_id:'new'}],pending_operator_proposal:replacement}});
  expect(replaced.currentProposal?.proposal_id).toBe('new');
  expect(replaced.turn?.proposal?.proposal_id).not.toBe('p');
  expect(replaced.reviewed).toBe(false);
  const same=applyOperatorResult(initial,{type:'session',scope,session});
  expect(same.currentProposal?.proposal_id).toBe('p');
  expect(same.reviewed).toBe(true);
  const refreshed={...turn.proposal!,parameters:{...turn.proposal!.parameters,draft_preview:{...session.drafts![0]}}};
  const changed=applyOperatorResult(initial,{type:'session',scope,session:{...session,pending_operator_proposal:refreshed}});
  expect(changed.reviewed).toBe(false);
  expect(reviewableOperatorTurn(changed)?.proposal?.parameters.draft_preview).toEqual(refreshed.parameters.draft_preview);
  const staleDraft=applyOperatorResult(initial,{type:'session',scope,session:{...session,drafts:[]}});
  expect(staleDraft.currentProposal).toBeNull();
  expect(staleDraft.turn?.proposal).toBeFalsy();
  const removed=applyOperatorResult(initial,{type:'session',scope,session:{...session,pending_operator_proposal:null}});
  expect(removed.turn?.proposal).toBeFalsy();
  expect(removed.reviewed).toBe(false);
  expect(applyOperatorResult(initial,{type:'session',scope:{...scope,companyId:'other'},session:{...session,pending_operator_proposal:null}})).toEqual(initial);
  const transient=applyOperatorResult({...initialOperatorState,scope,requestId:1},{type:'turn',scope,requestId:1,turn});
  const older={...session,pending_operator_proposal:null};
  expect(reconcileOperatorSessionOnChange(transient,scope,older,older).currentProposal?.proposal_id).toBe('p');
  expect(reconcileOperatorSessionOnChange(transient,scope,older,{...older}).currentProposal).toBeNull();
  const start=applyOperatorResult(initial,{type:'start',scope,requestId:1});
  const cancel={...turn,proposal:null,status:'completed' as const,action_result:{tool:'cancel_proposal',summary:'Cancelled'}};
  expect(applyOperatorResult(start,{type:'turn',scope,requestId:1,turn:cancel}).turn?.proposal).toBeFalsy();
});
it('pins a current proposal through unrelated typed and streamed reads, clears matching apply and expiry',()=>{
  const turn=saved('simulate_email');const state={...initialOperatorState,scope,turn,reviewed:true};
  const started=applyOperatorResult(state,{type:'start',scope,requestId:1,question:'Read inventory'});
  expect(started.turn).toEqual(turn);expect(started.reviewed).toBe(true);
  const read={...turn,turn_id:'read',proposal:null,session:null,action_result:null,read_only:true};
  const answered=applyOperatorResult(started,{type:'turn',scope,requestId:1,turn:read});
  expect(answered.turn).toEqual(turn);expect(answered.exchanges.at(-1)?.turn).toEqual(read);
  const unrelated={...read,read_only:false,session:turn.session,action_result:{tool:'navigate_workspace',summary:'Opened routes'}};
  const streamed=applyOperatorResult({...answered,requestId:2},{type:'turn',scope,requestId:2,turn:unrelated});expect(streamed.turn).toEqual(turn);
  expect(applyOperatorResult({...streamed,requestId:3},{type:'turn',scope,requestId:3,turn:{...unrelated,action_result:{tool:'watch',summary:'Applied different kind'}}}).turn).toEqual(turn);
  const applied={...unrelated,action_result:{tool:'simulate_email',summary:'Recorded simulation'}};
  expect(applyOperatorResult({...streamed,requestId:3},{type:'turn',scope,requestId:3,turn:applied}).turn?.proposal).toBeNull();
  const expired={...state,turn:{...turn,proposal:{...turn.proposal!,expires_at:'2000-01-01'}}};
  expect(applyOperatorResult(expired,{type:'start',scope,requestId:4}).turn).toBeNull();
  expect(applyOperatorResult(state,{type:'scope',scope:{...scope,contextVersion:3}}).turn).toBeNull();expect(applyOperatorResult(state,{type:'cancel'}).turn).toBeNull();
});
it('expires only the matching card and never revives a historical approval',()=>{
  const turn=saved('simulate_email'), state={...initialOperatorState,scope,turn,reviewed:true};
  vi.useFakeTimers();
  try {
    vi.setSystemTime(new Date('2100-01-01'));
    expect(applyOperatorResult(state,{type:'expire',proposalId:'different'})).toEqual(state);
    expect(applyOperatorResult(state,{type:'expire',proposalId:'p'}).turn).toBeNull();
  } finally {vi.useRealTimers();}
  const history={company_id:'solara',context_version:2,exchanges:[{id:'h',channel:'text' as const,question:null,turn,created_at:'2026-10-17'}]};
  const restored=applyOperatorResult({...initialOperatorState,scope},{type:'history',scope,history});
  expect(restored.turn).toBeNull();expect(restored.exchanges[0].turn?.proposal).toBeNull();
});
