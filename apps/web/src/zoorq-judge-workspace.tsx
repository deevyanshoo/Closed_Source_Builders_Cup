import { lazy, Suspense, useEffect, useRef, useState } from 'react';
import { createDemoApi, type CompanyProfile, type DemoSession, type JudgeEvidence } from './demo-api';
import type { RouteDisplayView } from './zoorq-route-map';
import { safePublicUrl, ZoorqAiReceipts } from './zoorq-ai-receipts';
import './zoorq-judge-workspace.css';
const api = createDemoApi();
const ZoorqRouteMap = lazy(() => import('./zoorq-route-map').then(module => ({default:module.ZoorqRouteMap})));
export type JudgeScope = {sessionId: string; companyId: string; contextVersion: number; planId: string | null; epoch: number};
export function sameJudgeScope(a: JudgeScope, b: JudgeScope) {
  return a.sessionId===b.sessionId && a.companyId===b.companyId && a.contextVersion===b.contextVersion && a.planId===b.planId && a.epoch===b.epoch;
}
export function judgeScope(session: DemoSession, epoch: number): JudgeScope {
  return {sessionId:session.session_id,companyId:session.company!.company_id,contextVersion:session.context_version!,planId:session.plan?.plan_id ?? null,epoch};
}
type RouteChoice = { routeId: string; materialId: string; materialName: string; routeNumber: number };
type RouteGroup = { key: string; origin: string; destination: string; via: string[]; options: RouteChoice[] };
export function routeChoices(company: CompanyProfile, materialId: string): RouteGroup[] {
  const groups = new Map<string, RouteGroup>();
  for (const [routeIndex,route] of company.routes.entries()) {
    const key = [route.direction,route.origin, ...route.via, route.destination].map(part => part.trim().toLocaleLowerCase()).join('|');
    for (const id of route.materials) {
      if (materialId !== 'all' && id !== materialId) continue;
      const group = groups.get(key) ?? {key,origin:route.origin,destination:route.destination,via:route.via,options:[]};
      group.options.push({routeId:route.route_id,materialId:id,materialName:company.materials.find(item=>item.material_id===id)?.name ?? id,routeNumber:routeIndex+1});
      groups.set(key,group);
    }
  }
  return [...groups.values()];
}
export function matchingBuyerIndex(company: CompanyProfile, destination: string): number | null {
  const normalized = destination.trim().toLocaleLowerCase();
  const matches = company.locations.flatMap((location,index)=>`${location.city}, ${location.country}`.toLocaleLowerCase()===normalized?[index]:[]);
  return matches.length===1 ? matches[0] : null;
}
type ChoiceCoverage = {mode:'sea'|'land'|'unknown';display:'map'|'schematic'} | undefined;
export function describeRouteChoiceStatus(profile: ChoiceCoverage, selected: boolean, shown: RouteDisplayView | null,
  buyerMissing: boolean, error: string): string {
  if (selected && buyerMissing) return 'Choose buyer for route status';
  if (selected && !shown) return error ? 'Selected itinerary unavailable' : 'Checking selected buyer';
  const mode = selected ? shown?.itinerary?.mode ?? profile?.mode : profile?.mode;
  if (!mode) return error ? 'Coverage unavailable' : 'Coverage checking';
  const label = mode === 'unknown' ? 'Mode unconfirmed' : mode === 'sea' ? 'Sea' : 'Land';
  const display = selected ? shown?.itinerary?.display : profile?.display;
  return `${label} · ${selected ? display === 'map' ? 'approximate map for selected buyer' : 'schematic for selected buyer' : display === 'map' ? 'mapped profile corridor' : 'schematic profile'}`;
}
export function JudgeRoutes({company,session,epoch,onAsk}: {company:CompanyProfile;session:DemoSession;epoch:number;onAsk:(prompt:string)=>void}) {
  const [selection,setSelection] = useState(()=>({routeId:company.routes[0]?.route_id ?? '',materialId:company.routes[0]?.materials[0] ?? '',buyer:company.routes[0] ? matchingBuyerIndex(company,company.routes[0].destination):null}));
  const [materialFilter,setMaterialFilter] = useState('all');
  const {routeId,materialId,buyer}=selection;
  const route = company.routes.find(r=>r.route_id===routeId);
  const groups=routeChoices(company,materialFilter);
  const optionCount=company.routes.reduce((count,item)=>count+item.materials.length,0);
  const [loaded,setLoaded] = useState<{scope:JudgeScope;selection:string;view:RouteDisplayView}|null>(null);
  const [coverage,setCoverage] = useState<{scope:JudgeScope;value:RouteDisplayView['coverage']}|null>(null);
  const [key,setKey] = useState<string|null>(null);
  const [error,setError] = useState('');
  const scope=judgeScope(session,epoch), current=useRef(scope); current.current=scope;
  const selectionKey=`${routeId}:${materialId}:${buyer ?? 'unselected'}`;
  useEffect(()=>{
    let cancelled=false; const captured=scope;
    setError(''); setLoaded(null);
    if (!routeId || !materialId || buyer===null) return ()=>{cancelled=true;};
    api.judgeRoutes(routeId,materialId,buyer).then(view=>{
      if (!cancelled && sameJudgeScope(captured,current.current) && view.company_id===captured.companyId && view.context_version===captured.contextVersion) {setLoaded({scope:captured,selection:selectionKey,view});setCoverage({scope:captured,value:view.coverage});}
    }).catch(()=>{if(!cancelled && sameJudgeScope(captured,current.current)) setError('Map itinerary unavailable. The profile route list remains available.');});
    api.judgeConfig().then(config=>{if(!cancelled && sameJudgeScope(captured,current.current)) setKey(config.maps_enabled ? config.maps_browser_key:null);}).catch(()=>{});
    return ()=>{cancelled=true;};
  },[routeId,materialId,buyer,session.session_id,company.company_id,session.context_version,session.plan?.plan_id,epoch]);
  const shown=loaded && sameJudgeScope(loaded.scope,scope) && loaded.selection===selectionKey ? loaded.view : null;
  const coverageByOption=new Map(coverage && sameJudgeScope(coverage.scope,scope) ? coverage.value?.options?.map(item=>[`${item.route_id}:${item.material_id}`,item]) ?? [] : []);
  function choose(nextRouteId:string,nextMaterialId:string) {
    const next=company.routes.find(item=>item.route_id===nextRouteId);
    setSelection({routeId:nextRouteId,materialId:nextMaterialId,buyer:next?matchingBuyerIndex(company,next.destination):null});
  }
  function chooseFilter(nextMaterialId:string) {
    setMaterialFilter(nextMaterialId);
    if (nextMaterialId==='all' || nextMaterialId===materialId) return;
    const first=routeChoices(company,nextMaterialId)[0]?.options[0];
    if (first) choose(first.routeId,first.materialId);
    else setSelection({routeId:'',materialId:'',buyer:null});
  }
  const routeOptions=groups.flatMap(group=>group.options.map(option=>({group,option})));
  const selectedOptionIndex=routeOptions.findIndex(item=>item.option.routeId===routeId && item.option.materialId===materialId);
  const selectedStatus=describeRouteChoiceStatus(coverageByOption.get(`${routeId}:${materialId}`),true,shown,buyer===null,error);
  return <section className="zoorq-judge-routes" aria-label="Profile routes">
    <div className="zoorq-judge-routes-head"><h2>Supply routes</h2><span>Illustrative profile itineraries</span></div>
    <div className="zoorq-judge-primary-controls">
      <label>Material <select value={materialFilter} onChange={event=>chooseFilter(event.target.value)}><option value="all">All materials</option>{company.materials.map(item=><option key={item.material_id} value={item.material_id}>{item.name}</option>)}</select></label>
      <label>Route <select value={selectedOptionIndex} onChange={event=>{const next=routeOptions[Number(event.target.value)]?.option;if(next)choose(next.routeId,next.materialId);}} disabled={!routeOptions.length}>{routeOptions.length ? routeOptions.map(({group,option},index)=><option key={`${option.routeId}:${option.materialId}`} value={index}>{option.materialName} · {group.origin} → {group.destination}{group.via.length ? ` · via ${group.via.join(', ')}` : ''} · route {option.routeNumber}</option>) : <option value={-1}>No declared route</option>}</select></label>
      <label>Buyer location <select value={buyer ?? ''} onChange={event=>setSelection(value=>({...value,buyer:event.target.value===''?null:Number(event.target.value)}))} disabled={!route}><option value="">Choose buyer location</option>{company.locations.map((loc,index)=><option key={`${loc.city}:${index}`} value={index}>{loc.city}, {loc.country}</option>)}</select></label>
    </div>
    {route && !shown && <p className="zoorq-judge-current" role="status"><strong>{route.origin} → {route.destination}</strong><span>{company.materials.find(item=>item.material_id===materialId)?.name ?? materialId}{route.via.length ? ` · via ${route.via.join(' → ')}` : ''} · {selectedStatus}</span></p>}
    {buyer===null && route && <p role="status" className="zoorq-judge-buyer-notice">Choose the buyer location for this route. No single exact match was found for {route.destination}.</p>}
    {!shown && <div className="zoorq-judge-pending"><p role="status">{error||(buyer===null?'Schematic profile itinerary':'Loading itinerary…')}</p><ol className="zoorq-judge-schematic">{route ? <><li>Origin: {route.origin}</li>{route.via.map((v,index)=><li key={`${v}:${index}`}>Via: {v}</li>)}<li>Destination: {route.destination}</li><li>Vendor facility and shipment binding unavailable.</li></> : <li>No profile route is available.</li>}</ol></div>}
    {shown && <Suspense fallback={<p role="status">Opening selected itinerary…</p>}><ZoorqRouteMap key={`${selectionKey}:${epoch}`} view={shown} apiKey={key} onAsk={onAsk} preview={null} /></Suspense>}
    <details className="zoorq-judge-context"><summary>Profile assumptions &amp; route coverage</summary><p>Illustrative company profile routes. No supplier, accepted offer or tracked shipment is linked.</p><p>{company.routes.length} declared routes · {optionCount} material itineraries · {company.materials.length} materials.</p><p>{coverage?.value && sameJudgeScope(coverage.scope,scope) ? `${coverage.value.mapped_route_material_pairs} mapped and ${coverage.value.schematic_route_material_pairs} schematic of ${coverage.value.declared_route_material_pairs} declared material itineraries.` : 'Coverage is shown for the selected itinerary.'}</p></details>
    <details className="zoorq-judge-browser"><summary>Browse declared routes · {groups.length} corridors · {routeOptions.length} material itineraries</summary><div className="zoorq-judge-network" role="group" aria-label="Declared route choices">{groups.map(group=><article key={group.key} className="zoorq-judge-corridor"><strong>{group.origin} → {group.destination}</strong><span>{group.via.length ? `Via ${group.via.join(' → ')}` : 'No via point declared'}{group.options.length>1 ? ` · ${group.options.length} declared material itineraries` : ''}</span><div>{group.options.map(option=>{const selected=routeId===option.routeId && materialId===option.materialId;const status=coverageByOption.get(`${option.routeId}:${option.materialId}`);return <button type="button" key={`${option.routeId}:${option.materialId}`} aria-pressed={selected} onClick={()=>choose(option.routeId,option.materialId)}>{option.materialName}{group.options.filter(item=>item.materialId===option.materialId).length>1 ? ` · route ${option.routeNumber}` : ''}<small>{describeRouteChoiceStatus(status,selected,shown,buyer===null,error)}</small><span className="zoorq-visually-hidden"> · {group.origin} to {group.destination} · ID {option.routeId}</span></button>;})}</div></article>)}</div>{!groups.length && <p>No declared route for this material.</p>}</details>
    <button type="button" className="demo-link" onClick={()=>onAsk(`What inputs are needed to preview a port delay for ${company.materials.find(m=>m.material_id===materialId)?.name??materialId}?`)}>Ask about a route assumption</button>
  </section>;
}
export function replayEvidenceAt(evidence: JudgeEvidence['replay'], day: number) {
  const cutoff=`2021-03-${String(day).padStart(2,'0')}`;
  return evidence.timeline?.find(frame=>frame.as_of.slice(0,10)===cutoff)?.documented_evidence ?? []; // Server pure replay formatter owns chronology.
}
export function JudgeEvidencePanel({session,epoch}: {session:DemoSession;epoch:number}) {
  const [loaded,setLoaded]=useState<{scope:JudgeScope;value:JudgeEvidence}|null>(null);
  const [busy,setBusy]=useState(false), [error,setError]=useState('');
  const [day,setDay]=useState(24);
  const scope=judgeScope(session,epoch), current=useRef(scope); current.current=scope;
  const operation=useRef(0);
  useEffect(()=>{let cancelled=false; const captured=scope; ++operation.current; setLoaded(null);setBusy(false);setError('');
    api.judgeEvidence().then(value=>{if(!cancelled && sameJudgeScope(captured,current.current) && value.company_id===captured.companyId && value.context_version===captured.contextVersion)setLoaded({scope:captured,value});}).catch(()=>{if(!cancelled)setError('Evidence is unavailable.');});return()=>{cancelled=true;++operation.current;};
  },[session.session_id,session.company?.company_id,session.context_version,session.plan?.plan_id,epoch]);
  const evidence=loaded && sameJudgeScope(loaded.scope,scope)?loaded.value:null;
  async function interpret(){const captured=scope,token=++operation.current;setBusy(true);setError('');try{const value=await api.interpretHindi(session.csrf_token,captured.contextVersion);if(token===operation.current && sameJudgeScope(captured,current.current) && value.company_id===captured.companyId && value.context_version===captured.contextVersion)setLoaded({scope:captured,value});}catch{if(token===operation.current && sameJudgeScope(captured,current.current))setError('Interpretation unavailable. No verified translation is claimed.');}finally{if(token===operation.current)setBusy(false);}}
  return <section className="zoorq-judge-evidence" aria-label="Decision evidence">
    {error && <p role="status">{error}</p>}
    {evidence?.hindi_case && <ZoorqAiReceipts state="ready" aggregate={evidence.aggregate} hindiCase={evidence.hindi_case} onRequestTranslation={interpret} translationBusy={busy} />}
    {evidence && <details><summary>Historical Suez replay · documented evidence and simulated response</summary><p>Historical carrier advisories, read at end of each UTC day. No actual ZOORQ alert history or company shipment impact is asserted.</p>
      <label>Replay date: March {day}, 2021<input type="range" min="23" max="30" step="1" value={day} onChange={e=>setDay(Number(e.target.value))} /></label>
      <h3>Documented by this date</h3><ul>{replayEvidenceAt(evidence.replay,day).map(row=><li key={row.evidence_id}><time>{row.published_at}</time> {row.fact} {safePublicUrl(row.source_url)&&<a href={safePublicUrl(row.source_url)} target="_blank" rel="noopener noreferrer">Carrier advisory</a>}</li>)}</ul>
      <h3>Simulated planning response</h3><p>{day<24?'No advisory was available at this replay date.':'Illustrative workflow: review the advisory, identify an owned shipment, then enter an explicit duration assumption. No actual alert or recovery was executed.'}</p></details>}
  </section>;
}
