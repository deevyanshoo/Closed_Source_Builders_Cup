import { renderToStaticMarkup } from 'react-dom/server';
import { expect, it, vi } from 'vitest';
import { createDemoApi } from './demo-api';
import type { CompanyProfile } from './demo-api';
import { ZoorqAiReceipts } from './zoorq-ai-receipts';
import { ZoorqJudgeEntry } from './zoorq-judge-entry';
it('uses canonical judge endpoints with context and CSRF only for explicit actions', async () => {
  const send = vi.fn(async () => new Response('{}', {status:200}));
  const api = createDemoApi(send, () => 'judge-key');
  await api.judgeConfig(); await api.judgeRoutes('route /a','cotton',1); await api.judgeEvidence();
  await api.judgeStart('csrf',4); await api.interpretHindi('csrf',4);
  const calls = send.mock.calls as unknown as [string,RequestInit][];
  expect(calls.map(c=>c[0])).toEqual(['/api/demo/judge/config','/api/demo/judge/routes?route_id=route+%2Fa&material_id=cotton&buyer_location_index=1','/api/demo/judge/evidence','/api/demo/judge/start','/api/demo/judge/interpret-hindi']);
  expect(calls[0][1].method).toBe('GET');
  expect(JSON.parse(calls[4][1].body as string)).toEqual({expected_context_version:4,idempotency_key:'judge-key'});
  expect(calls[4][1].headers).toMatchObject({'X-CSRF-Token':'csrf'});
});
it('renders public source title and date for generic receipts and rejects userinfo and unusual ports', () => {
  const receipt={input_source_id:'source-1',model_id:'gemini-test',model_version:'v1',provider_call_id:null,called_at:'2026-10-17T00:00:00Z',status:'success' as const,output:'summary',evidence_refs:['source-1'],reviewed_flags:[],source_title:'A dated public source',source_url:'https://example.org/public',source_as_of:'2026-10-01'};
  const html = renderToStaticMarkup(<ZoorqAiReceipts state="ready" aggregate={{schema:1,interpreted_articles:1,flagged_for_review:0,receipts:[receipt]}} />);
  expect(html).toContain('A dated public source'); expect(html).toContain('2026-10-01'); expect(html).toContain('href="https://example.org/public"');
  for (const url of ['https://user@www.pib.gov.in/a','https://www.pib.gov.in:444/a']) {
    const bad=renderToStaticMarkup(<ZoorqAiReceipts state="ready" aggregate={{schema:1,interpreted_articles:1,flagged_for_review:0,receipts:[{...receipt,source_url:url}]}} />);
    expect(bad).not.toContain('1 source interpreted'); expect(bad).not.toContain('href="'+url+'"');
  }
});

import { applyDemoResult, initialDemoState, judgeEntryAdmission, prepareJudgeEntrance } from './demo-page';
import type { DemoSession, JudgeStart } from './demo-api';
import { JudgeRoutes, sameJudgeScope, replayEvidenceAt, routeChoices, matchingBuyerIndex, describeRouteChoiceStatus } from './zoorq-judge-workspace';
import { pushWorkspaceDestination, readSupplyLocation, readWorkspaceLocation, workspaceSearch } from './zoorq-workspace';
it('opens overview by default and preserves an explicit companies URL', () => {
  expect(readWorkspaceLocation('').view).toBe('overview');
  expect(readWorkspaceLocation(workspaceSearch('', 'hub','add')).view).toBe('hub');
});
it('routes the quick link to Routes and keeps it in browser history', () => {
  const search = workspaceSearch('', 'inventory', 'add', 'routes');
  expect(readWorkspaceLocation(search).view).toBe('inventory');
  expect(readSupplyLocation(search)).toBe('routes');
});
it('writes one supply destination history entry so Back returns to the prior view', () => {
  const entries=['/demo?view=overview'];
  const history={pushState:(_state:unknown,_title:string,url:string)=>entries.push(url)};
  pushWorkspaceDestination(history,{pathname:'/demo',search:'?view=overview',hash:''},'inventory','add','routes');
  expect(entries).toEqual(['/demo?view=overview','/demo?view=inventory&supply=routes']);
  expect(entries.at(-2)).toBe('/demo?view=overview');
});
it('uses the selected buyer response for the selected route status and restores the map label on matching buyer', () => {
  const profile={mode:'land' as const,display:'map' as const};
  const mismatched={route_id:'r1',material_id:'cotton',itinerary:{mode:'land',display:'schematic'}} as never;
  const matched={route_id:'r1',material_id:'cotton',itinerary:{mode:'land',display:'map'}} as never;
  expect(describeRouteChoiceStatus(profile,true,mismatched,false,'')).toBe('Land · schematic for selected buyer');
  expect(describeRouteChoiceStatus(profile,true,matched,false,'')).toBe('Land · approximate map for selected buyer');
  expect(describeRouteChoiceStatus(profile,false,mismatched,false,'')).toBe('Land · mapped profile corridor');
  expect(describeRouteChoiceStatus(profile,true,null,false,'')).toBe('Checking selected buyer');
});
it('groups identical corridors while preserving each owned material and route identity', () => {
  const company={company_id:'a', routes:[
    {route_id:'r1',origin:'Singapore',destination:'Pune',via:['Chennai'],materials:['copper'],direction:'import'},
    {route_id:'r2',origin:'Singapore',destination:'Pune',via:['Chennai'],materials:['polymer'],direction:'import'},
    {route_id:'r3',origin:'Hamburg',destination:'Chennai',via:['Suez'],materials:['copper'],direction:'import'},
  ],materials:[{material_id:'copper',name:'Copper'},{material_id:'polymer',name:'Polymer'}],locations:[{city:'Pune',country:'India'},{city:'Chennai',country:'India'}]} as unknown as CompanyProfile;
  const choices=routeChoices(company, 'all');
  expect(choices).toHaveLength(2);
  expect(choices[0].options.map(option=>[option.routeId,option.materialId])).toEqual([['r1','copper'],['r2','polymer']]);
  expect(routeChoices(company,'polymer')).toHaveLength(1);
  expect(matchingBuyerIndex(company,'Chennai, India')).toBe(1);
  expect(matchingBuyerIndex({...company,locations:[{city:'Chennai',country:'India'},{city:'Chennai',country:'India'}]},'Chennai, India')).toBeNull();
  expect(matchingBuyerIndex(company,'Unknown')).toBeNull();
});
it('posts startup only for an empty flagship and retains a restored nonflagship', async () => {
  const fresh={session_id:'s',csrf_token:'csrf',company:{company_id:'solara-cables'},context_version:1,offers:[],plan:null} as unknown as DemoSession;
  const prepared={seeded:true,session:{...fresh,plan:{plan_id:'prepared'}},facts:[]} as unknown as JudgeStart;
  const start=vi.fn(async()=>prepared);
  expect((await prepareJudgeEntrance(fresh,start)).session).toBe(prepared.session);
  expect(start).toHaveBeenCalledOnce();
  const restored={...fresh,company:{...fresh.company!,company_id:'loomworks'}};
  expect((await prepareJudgeEntrance(restored,start)).session).toBe(restored);
  expect(start).toHaveBeenCalledOnce();
  expect((await prepareJudgeEntrance({...fresh,offers:[{offer_id:'user'} as never]},start)).prepared).toBeNull();
});
it('moves from prepared first Solara to current Atlas without showing a failed Solara seed', () => {
  const fresh={session_id:'s',csrf_token:'csrf',company:{company_id:'solara-cables'},context_version:1,offers:[],plan:null} as unknown as DemoSession;
  expect(judgeEntryAdmission(fresh,null,false,null,'loading')).toMatchObject({visit:'first',prepared:{status:'loading'}});
  expect(judgeEntryAdmission(fresh,null,false,null,'failed')).toMatchObject({visit:'first',prepared:{status:'unavailable'}});
  const prepared={seeded:true,session:{...fresh,plan:{plan_id:'prepared'},plan_request:{material:'copper'}},facts:[{label:'Profile stock cover',value:'6.0000600006000060000600006 days',basis:'Profile stock'}]} as unknown as JudgeStart;
  const solara=applyDemoResult(initialDemoState,{type:'session',session:prepared.session,epoch:0});
  expect(judgeEntryAdmission(solara.session,solara.plan,false,prepared,'done')).toMatchObject({visit:'first',prepared:{status:'ready'}});
  const switching=applyDemoResult(solara,{type:'company-start'});
  const atlas={...fresh,company:{...fresh.company!,company_id:'atlas-petrochem',name:'Atlas Petrochem'},context_version:2,offers:[],plan:null} as DemoSession;
  const current=applyDemoResult(switching,{type:'session',session:atlas,epoch:1});
  const admission=judgeEntryAdmission(current.session,current.plan,false,prepared,'done');
  expect(admission.visit).toBe('restored');
  expect(admission.prepared.status).not.toBe('unavailable');
  const html=renderToStaticMarkup(<ZoorqJudgeEntry company={atlas.company!} session={current.session} visit={admission.visit} prepared={admission.prepared} companyCount={10} onNavigate={()=>{}} onExploreCompanies={()=>{}}/>);
  expect(html).toContain('Continue Atlas Petrochem');
  expect(html).not.toContain('Prepared scenario unavailable');
});
it('discards company, plan and epoch changes and shows accessible profile fallback on first render', () => {
  const scope={sessionId:'s',companyId:'a',contextVersion:1,planId:'p',epoch:1};
  for(const patch of [{companyId:'b'},{planId:'p2'},{epoch:2},{contextVersion:2}])expect(sameJudgeScope(scope,{...scope,...patch})).toBe(false);
  const company={company_id:'a',routes:[{route_id:'r',origin:'Hamburg',destination:'Chennai, India',via:['Suez'],materials:['cotton'],direction:'import'}],materials:[{material_id:'cotton',name:'Cotton'}],locations:[{city:'Chennai',country:'India'}]} as never;
  const session={session_id:'s',company,context_version:1} as unknown as DemoSession;
  const html=renderToStaticMarkup(<JudgeRoutes company={company} session={session} epoch={1} onAsk={()=>{}} />);
  expect(html).toContain('class="zoorq-judge-schematic"');expect(html).toContain('Hamburg');expect(html).toContain('No supplier, accepted offer or tracked shipment is linked');
  expect(html.indexOf('zoorq-judge-schematic')).toBeLessThan(html.indexOf('zoorq-judge-network'));
  expect(html).toContain('Browse declared routes');
  expect(html).not.toContain('maps.googleapis.com');
});
it('gates historical advisories by date and keeps subsequent dates hidden', () => {
  const replay={replay_id:'test',source_url:'https://example.org',advisories:[{evidence_id:'a',published_at:'2021-03-24',fact:'blocked',source_url:'https://example.org'},{evidence_id:'b',published_at:'2021-03-29',fact:'refloated',source_url:'https://example.org'}]};
  const projected={...replay,timeline:[{as_of:'2021-03-24T23:59:59.999999+00:00',documented_evidence:[replay.advisories[0]],simulation:null}]};
  expect(replayEvidenceAt(projected,23)).toHaveLength(0);expect(replayEvidenceAt(projected,24).map(r=>r.fact)).toEqual(['blocked']);
});
