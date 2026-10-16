import { useEffect, useMemo, useState } from 'react';
import type { PlanView } from './demo-api';
import { supplierDisplayName } from './demo-display';
import './demo-carbon.css';

type FactorView = { publisher: string; source_url: string; licence: string; factor_year: number; checked_on: string; factor: string; unit: string; category?: string; label?: string; note?: string; system_boundary?: string; product_match?: string; geography?: string };
type Part = { status: string; kg_co2e: string | null; reason?: string; formula?: string; factor?: FactorView | null };
export type CarbonLeg = { mode: string; distance_km: string };
export type CarbonEstimate = { label: string; scope: string; registry_version: string; material_id: string; quantity: string; unit: string;
  material: Part & { mass_tonnes: string | null }; transport: { status: string; kg_co2e: string | null; legs: (Part & CarbonLeg & { tonne_km: string | null })[] };
  total_kg_co2e: string | null; combined_status?: 'combined' | 'components_shown_separately' | 'incomplete'; combined_reason?: string;
  mass_basis?: { mass_kg: string; basis: string; source: string; used_for: string } | null; complete: boolean; factor_versions: Record<string, Record<string, { published_on: string; checked_on: string; factor_year: number; file_sha256: string }>> };
export type CarbonComparison = { company_id: string; context_version: number; plan_id: string; synthetic_inputs: true; registry_version: string; label: string; scope: string; material_id: string; quantity_unit: string | null;
  suppliers: { offer_id: string; supplier: string; estimate: CarbonEstimate }[] };
export type CarbonScope = { sessionId: string; companyId: string; contextVersion: number };

export function carbonResponseMatchesScope(value: unknown, scope: CarbonScope, planId: string): value is CarbonComparison {
  if (!value || typeof value !== 'object') return false;
  const response = value as Partial<CarbonComparison>;
  return response.company_id === scope.companyId && response.context_version === scope.contextVersion && response.plan_id === planId;
}

/** A generation belongs to one mounted panel, scope and set of entered inputs. */
export class CarbonRequestOwner {
  private generation = 0;
  private disposed = false;
  constructor(private scope: string) {}

  setScope(scope: string) {
    if (this.scope !== scope) {
      this.scope = scope;
      this.invalidate();
    }
  }

  invalidate() { this.generation += 1; }
  dispose() { this.disposed = true; this.invalidate(); }
  activate() { this.disposed = false; this.invalidate(); }

  async run<T>(request: () => Promise<T>, publish: (value: T) => void): Promise<boolean> {
    if (this.disposed) return false;
    const generation = ++this.generation;
    const scope = this.scope;
    try {
      const value = await request();
      if (this.disposed || this.generation !== generation || this.scope !== scope) return false;
      publish(value);
      return true;
    } catch (error) {
      if (this.disposed || this.generation !== generation || this.scope !== scope) return false;
      throw error;
    }
  }
}

export function invalidateCarbonInputs(owner: CarbonRequestOwner, hideResult: () => void, onInputsChange?: () => void) {
  owner.invalidate();
  hideResult();
  onInputsChange?.();
}

/** Registry keys from config/carbon_factors_v1.json; the service rejects or marks anything else unavailable. */
export const CARBON_MODES = [['sea_container', 'Sea, container ship'], ['sea_bulk', 'Sea, bulk carrier'], ['road_hgv', 'Road, HGV'], ['rail_freight', 'Rail freight'], ['air_freight', 'Air freight']] as const;

/** Display only: round decimal text half up to one place and group thousands. */
function approxKg(text: string): string {
  const match = /^(\d+)(?:\.(\d+))?$/.exec(text);
  if (!match) return text;
  const fraction = (match[2] ?? '').padEnd(2, '0');
  const tenths = BigInt(match[1] + fraction[0]) + (Number(fraction[1]) >= 5 ? 1n : 0n);
  const digits = tenths.toString().padStart(2, '0');
  return `${digits.slice(0, -1).replace(/\B(?=(\d{3})+(?!\d))/g, ',')}.${digits.slice(-1)}`;
}

export function carbonCell(comparison: CarbonComparison, offerId: string): string {
  const row = comparison.suppliers.find(item => item.offer_id === offerId);
  if (!row) return 'Not estimated';
  const { estimate } = row;
  const combined = estimate.combined_status ? estimate.combined_status === 'combined' : estimate.complete;
  if (estimate.total_kg_co2e != null && combined) return `About ${approxKg(estimate.total_kg_co2e)} kg CO2e`;
  if (estimate.combined_status === 'components_shown_separately' && estimate.material.kg_co2e != null && estimate.transport.kg_co2e != null)
    return `Material about ${approxKg(estimate.material.kg_co2e)} kg CO2e · freight about ${approxKg(estimate.transport.kg_co2e)} kg CO2e (components shown separately)`;
  const production = estimate.material.kg_co2e != null ? `production about ${approxKg(estimate.material.kg_co2e)} kg CO2e` :
    estimate.material.status === 'unit_mismatch' ? 'production needs a mass quantity' : 'production factor unavailable';
  const transport = estimate.transport.kg_co2e != null ? `transport about ${approxKg(estimate.transport.kg_co2e)} kg CO2e` :
    estimate.transport.status === 'not_entered' ? 'transport not entered' : 'transport incomplete';
  const text = `${production} · ${transport}`;
  return text[0].toUpperCase() + text.slice(1);
}

export type CarbonMass = { mass_kg: string; basis: string };

export async function requestCarbonEstimate(send: typeof fetch, csrf: string, planId: string, legsByOffer: Record<string, CarbonLeg[]>, contextVersion?: number, massByOffer: Record<string, CarbonMass> = {}): Promise<CarbonComparison> {
  let response: Response;
  try {
    response = await send('/api/demo/carbon/estimate', { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
      body: JSON.stringify({ plan_id: planId, legs_by_offer: legsByOffer, ...(Object.keys(massByOffer).length ? { mass_by_offer: massByOffer } : {}), ...(contextVersion === undefined ? {} : { expected_context_version: contextVersion }) }) });
  } catch { throw new Error('The demo service could not be reached. Try again.'); }
  let body: unknown = null;
  try { body = await response.json(); } catch { /* handled below */ }
  if (!response.ok) {
    const error = body && typeof body === 'object' && 'error' in body ? (body as { error: { message?: unknown } }).error : null;
    throw new Error(typeof error?.message === 'string' ? error.message : 'The carbon estimate could not be calculated.');
  }
  return body as CarbonComparison;
}

function FactorLine({ part }: { part: Part }) {
  if (!part.factor) return <p className="demo-carbon-missing">{part.reason ?? 'Factor unavailable.'}</p>;
  const f = part.factor;
  return <p>{part.formula ? <><code>{part.formula}</code>{part.kg_co2e != null && <> = {part.kg_co2e} kg CO2e</>}. </> : <span className="demo-carbon-missing">{part.reason} </span>}
    Factor {f.category ?? f.label}: {f.factor} {f.unit}, {f.factor_year} · <a href={f.source_url} target="_blank" rel="noreferrer">{f.publisher}</a> · {f.licence} · checked {f.checked_on}{f.note ? ` · ${f.note}` : ''}{f.system_boundary ? ` · Boundary: ${f.system_boundary}` : ''}{f.product_match ? ` · Product match: ${f.product_match}` : ''}{f.geography ? ` · Geography: ${f.geography}` : ''}</p>;
}

type CarbonPanelProps = { plan: PlanView; csrf: string; scope: CarbonScope; contextVersion?: number; result: CarbonComparison | null; onResult: (value: CarbonComparison) => void; onInputsChange?: () => void; dirty: boolean; send?: typeof fetch };

export function CarbonPanel(props: CarbonPanelProps) {
  const { scope, plan, dirty } = props;
  const key = JSON.stringify([scope.sessionId, scope.companyId, scope.contextVersion, plan.plan_id, dirty]);
  return <CarbonPanelContent key={key} {...props} requestScope={key} />;
}

function CarbonPanelContent({ plan, csrf, scope, contextVersion, result, onResult, onInputsChange, dirty, send = fetch, requestScope }: CarbonPanelProps & { requestScope: string }) {
  const massUnit = ['kg', 'tonne'].includes(plan.quantity_unit ?? 'kg');
  const [masses, setMasses] = useState<Record<string, CarbonMass>>({});
  const [legs, setLegs] = useState<Record<string, CarbonLeg>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [locallyEdited, setLocallyEdited] = useState(false);
  const [localResult, setLocalResult] = useState<CarbonComparison | null>(null);
  const owner = useMemo(() => new CarbonRequestOwner(requestScope), [requestScope]);
  useEffect(() => { owner.activate(); owner.setScope(requestScope); return () => owner.dispose(); }, [owner, requestScope]);
  const shown = locallyEdited ? localResult : result;
  const current = !dirty && shown?.plan_id === plan.plan_id && shown.company_id === scope.companyId &&
    (!locallyEdited || carbonResponseMatchesScope(shown, scope, plan.plan_id)) ? shown : null;
  const leg = (offerId: string) => legs[offerId] ?? { mode: 'sea_container', distance_km: '' };
  function inputsChanged() {
    invalidateCarbonInputs(owner, () => {
      setLocallyEdited(true);
      setLocalResult(null);
      setBusy(false);
      setError('');
    }, onInputsChange);
  }
  async function estimate() {
    if (dirty || busy) return;
    if (contextVersion !== undefined && contextVersion !== scope.contextVersion) {
      setError('The comparison changed. Run it again before estimating emissions.');
      return;
    }
    setBusy(true); setError('');
    const entered = Object.fromEntries(plan.alternatives.map(a => [a.offer_id, leg(a.offer_id)]).filter(([, value]) => (value as CarbonLeg).distance_km.trim() !== '')
      .map(([id, value]) => [id, [{ mode: (value as CarbonLeg).mode, distance_km: (value as CarbonLeg).distance_km.trim() }]]));
    const massEntered = Object.fromEntries(Object.entries(masses).filter(([, m]) => m.mass_kg.trim() && m.basis.trim()).map(([id, m]) => [id, { mass_kg: m.mass_kg.trim(), basis: m.basis.trim() }]));
    await owner.run(async () => {
      try { return { value: await requestCarbonEstimate(send, csrf, plan.plan_id, entered, scope.contextVersion, massUnit ? {} : massEntered) }; }
      catch (reason) { return { reason }; }
    }, outcome => {
      setBusy(false);
      if ('reason' in outcome) {
        setError(outcome.reason instanceof Error ? outcome.reason.message : 'The carbon estimate could not be calculated.');
        return;
      }
      const value = outcome.value;
      if (!carbonResponseMatchesScope(value, scope, plan.plan_id)) {
        setError('The comparison changed. Run it again before estimating emissions.');
        return;
      }
      setLocallyEdited(true);
      setLocalResult(value);
      onResult(value);
    });
  }
  return <section className="demo-planning demo-carbon" aria-label="Carbon footprint estimate">
    <div className="demo-heading"><div><span className="demo-kicker">Emissions estimate</span><h2>Carbon alongside cost</h2></div><span className="demo-session">Estimate from published average factors</span></div>
    <p className="demo-muted demo-carbon-intro">Compare material production and the freight legs you enter.</p>
    <details className="demo-carbon-scope"><summary>Scope and assumptions</summary><p>{current?.scope ?? 'Cradle-to-gate material production from published factors, plus the transport legs you enter.'} Distances are what you enter; nothing is inferred from the route.</p></details>
    <div className="demo-carbon-legs">{plan.alternatives.map(a => { const name = supplierDisplayName(a.supplier); const value = leg(a.offer_id); return <div key={a.offer_id} className="demo-carbon-leg"><strong>{name}</strong>
      <label><span>Transport mode</span><select aria-label={`Transport mode for ${name}`} value={value.mode} onChange={e => { inputsChanged(); setLegs(old => ({ ...old, [a.offer_id]: { ...value, mode: e.target.value } })); }}>{CARBON_MODES.map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label>
      <label><span>Distance (km)</span><input aria-label={`Distance in km for ${name}`} inputMode="decimal" placeholder="Unknown until entered" value={value.distance_km} onChange={e => { inputsChanged(); setLegs(old => ({ ...old, [a.offer_id]: { ...value, distance_km: e.target.value } })); }} /></label>
      {!massUnit && <><label><span>Shipping mass (kg)</span><input aria-label={`Shipping mass in kg for ${name}`} inputMode="decimal" placeholder="Unknown until entered" value={masses[a.offer_id]?.mass_kg ?? ''} onChange={e => { inputsChanged(); setMasses(old => ({ ...old, [a.offer_id]: { basis: old[a.offer_id]?.basis ?? '', mass_kg: e.target.value } })); }} /></label>
        <label><span>Mass basis</span><input aria-label={`Mass basis for ${name}`} placeholder="For example: packing list" maxLength={200} value={masses[a.offer_id]?.basis ?? ''} onChange={e => { inputsChanged(); setMasses(old => ({ ...old, [a.offer_id]: { mass_kg: old[a.offer_id]?.mass_kg ?? '', basis: e.target.value } })); }} /></label></>}</div>; })}</div>
    {!massUnit && <p className="demo-muted">Quantities are in {plan.quantity_unit}. A shipping mass you enter is used for freight only and never inferred; material production stays unavailable without a reviewed material mass.</p>}
    {dirty && <p className="demo-carbon-missing" role="status">Buyer inputs changed. Run the comparison again before estimating emissions. The earlier saved assessment describes the previous comparison.</p>}
    {!dirty && locallyEdited && !current && <p className="demo-carbon-missing" role="status">Entered transport inputs changed. The earlier saved assessment is hidden; estimate again to see these inputs.</p>}
    <button type="button" className="demo-button" disabled={busy || dirty} onClick={estimate}>{busy ? 'Estimating…' : 'Estimate emissions'}</button>
    {error && <p className="demo-error" role="alert">{error}</p>}
    {current && <div className="demo-carbon-results"><p className="demo-carbon-results-label">Supplier estimates <span>kg CO2e · published average factors</span></p>{current.suppliers.map(row => <details key={row.offer_id}><summary><strong>{supplierDisplayName(row.supplier)}</strong> <span>{carbonCell(current, row.offer_id)}</span></summary>
      <h3>Material production ({row.estimate.quantity} {row.estimate.unit}{row.estimate.material.mass_tonnes ? `, ${row.estimate.material.mass_tonnes} t` : ''})</h3><FactorLine part={row.estimate.material} />{row.estimate.mass_basis && <p>Shipping mass {row.estimate.mass_basis.mass_kg} kg ({row.estimate.mass_basis.basis}; {row.estimate.mass_basis.source}; {row.estimate.mass_basis.used_for}).</p>}
      <h3>Transport</h3>{row.estimate.transport.legs.length ? row.estimate.transport.legs.map((item, index) => <FactorLine key={index} part={item} />) : <p className="demo-carbon-missing">No transport leg entered.</p>}
      {row.estimate.combined_reason && <p>{row.estimate.combined_reason}</p>}
      <p className="demo-muted">Factor set {current.registry_version}{Object.values(row.estimate.factor_versions).flatMap(Object.entries).map(([source, meta]) => ` · ${source} published ${meta.published_on}`).join('')}. Label: {row.estimate.label}.</p></details>)}</div>}
  </section>;
}
