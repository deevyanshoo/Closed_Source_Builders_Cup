import { useEffect, useRef, useState } from 'react';
import { createDemoApi, type CompanyProfile, type DemoSession, type JudgeEvidence } from './demo-api';
import { ZoorqRouteMap, type RouteDisplayView } from './zoorq-route-map';
import { safePublicUrl, ZoorqAiReceipts } from './zoorq-ai-receipts';
import './zoorq-judge-workspace.css';
const api = createDemoApi();
export type JudgeScope = {sessionId: string; companyId: string; contextVersion: number; planId: string | null; epoch: number};
export function sameJudgeScope(a: JudgeScope, b: JudgeScope) {
  return a.sessionId===b.sessionId && a.companyId===b.companyId && a.contextVersion===b.contextVersion && a.planId===b.planId && a.epoch===b.epoch;
}
export function judgeScope(session: DemoSession, epoch: number): JudgeScope {
  return {sessionId:session.session_id,companyId:session.company!.company_id,contextVersion:session.context_version!,planId:session.plan?.plan_id ?? null,epoch};
}
export function JudgeRoutes({company,session,epoch,onAsk}: {company:CompanyProfile;session:DemoSession;epoch:number;onAsk:(prompt:string)=>void}) {
  const [routeId,setRouteId] = useState(company.routes[0]?.route_id ?? '');
  const route = company.routes.find(r=>r.route_id===routeId);
  const [materialId,setMaterialId] = useState(route?.materials[0] ?? '');
  const [buyer,setBuyer] = useState(0);
  const [loaded,setLoaded] = useState<{scope:JudgeScope;selection:string;view:RouteDisplayView}|null>(null);
  const [key,setKey] = useState<string|null>(null);
  const [error,setError] = useState('');
  const scope=judgeScope(session,epoch), current=useRef(scope); current.current=scope;
  const selection=`${routeId}:${materialId}:${buyer}`;
  useEffect(()=>{
    let cancelled=false; const captured=scope;
    setError(''); setLoaded(null);
    api.judgeRoutes(routeId,materialId,buyer).then(view=>{
      if (!cancelled && sameJudgeScope(captured,current.current) && view.company_id===captured.companyId && view.context_version===captured.contextVersion) setLoaded({scope:captured,selection,view});
    }).catch(()=>{if(!cancelled && sameJudgeScope(captured,current.current)) setError('Map itinerary unavailable. The profile route list remains available.');});
    api.judgeConfig().then(config=>{if(!cancelled && sameJudgeScope(captured,current.current)) setKey(config.maps_enabled ? config.maps_browser_key:null);}).catch(()=>{});
    return ()=>{cancelled=true;};
  },[routeId,materialId,buyer,session.session_id,company.company_id,session.context_version,session.plan?.plan_id,epoch]);
  const shown=loaded && sameJudgeScope(loaded.scope,scope) && loaded.selection===selection ? loaded.view : null;
  return <section className="zoorq-judge-routes" aria-label="Profile routes"><h2>Supply routes</h2><p>Illustrative company profile itineraries. These routes are not bound to a selected supplier, offer or shipment.</p>
    <div className="zoorq-judge-controls"><label>Route<select value={routeId} onChange={e=>{const next=company.routes.find(r=>r.route_id===e.target.value);setRouteId(e.target.value);setMaterialId(next?.materials[0]??'');}}>{company.routes.map(r=><option key={r.route_id} value={r.route_id}>{r.origin} → {r.destination}</option>)}</select></label>
    <label>Material<select value={materialId} onChange={e=>setMaterialId(e.target.value)}>{route?.materials.map(id=><option key={id} value={id}>{company.materials.find(m=>m.material_id===id)?.name??id}</option>)}</select></label>
    <label>Buyer location<select value={buyer} onChange={e=>setBuyer(Number(e.target.value))}>{company.locations.map((loc,i)=><option key={i} value={i}>{loc.city}, {loc.country}</option>)}</select></label></div>
    {!shown && <><p role="status">{error||'Loading accessible itinerary…'}</p><ol>{route ? <><li>Origin: {route.origin}</li>{route.via.map(v=><li key={v}>Via: {v}</li>)}<li>Destination: {route.destination}</li><li>Vendor facility and shipment binding unavailable.</li></> : <li>No profile route is available.</li>}</ol></>}
    {shown && <ZoorqRouteMap key={`${selection}:${epoch}`} view={shown} apiKey={key} onAsk={onAsk} preview={null} />}
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
