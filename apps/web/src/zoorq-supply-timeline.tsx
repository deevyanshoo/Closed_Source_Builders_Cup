import type { CompanyProfile } from './demo-api';
import './zoorq-supply-timeline.css';

type Material = CompanyProfile['materials'][number];
type Supplier = CompanyProfile['suppliers'][number];

export type SupplyAssessment = { coverDays: number | null; profileLeadDays: number | null; leadDays: number | null; gapDays: number | null; scenarioDelayDays: number };

function finiteNumber(value: string | number): number | null {
  if (typeof value === 'string' && !value.trim()) return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

export function assessMaterialSupply(material: Material, suppliers: Supplier[], scenarioDelayDays = 0): SupplyAssessment {
  const stock = finiteNumber(material.stock);
  const demand = finiteNumber(material.consumption_per_day);
  const coverDays = stock !== null && demand !== null && stock >= 0 && demand > 0 ? stock / demand : null;
  const leads = suppliers.filter(supplier => supplier.materials.includes(material.material_id))
    .map(supplier => finiteNumber(supplier.lead_time_days)).filter((lead): lead is number => lead !== null && lead >= 0);
  const profileLeadDays = leads.length ? Math.min(...leads) : null;
  const safeDelay = Number.isInteger(scenarioDelayDays) && scenarioDelayDays >= 0 && scenarioDelayDays <= 30 ? scenarioDelayDays : 0;
  const leadDays = profileLeadDays === null ? null : profileLeadDays + safeDelay;
  return { coverDays, profileLeadDays, leadDays, gapDays: coverDays === null || leadDays === null ? null : Math.max(leadDays - coverDays, 0), scenarioDelayDays: safeDelay };
}

export function displayDays(days: number): string { return `${Number(days.toFixed(1))} days`; }
export function displayGap(days: number): string { return days > 0 && days < .1 ? 'less than 0.1 day' : `approximately ${displayDays(days)}`; }

export function SupplyTimeline({ assessment, compact = false }: { assessment: SupplyAssessment; compact?: boolean }) {
  const { coverDays, leadDays, gapDays, profileLeadDays, scenarioDelayDays } = assessment;
  const known = coverDays !== null && leadDays !== null && gapDays !== null;
  const max = known ? Math.max(coverDays, leadDays, 1) : 1;
  const coverPosition = known ? coverDays / max * 100 : 0;
  const leadPosition = known ? leadDays / max * 100 : 0;
  const markerLeft = (position: number) => position >= 100 ? 'calc(100% - 2px)' : position <= 0 ? '0px' : `${position}%`;
  const labelClass = (position: number) => position <= 25 ? 'zoorq-milestone-start' : position >= 75 ? 'zoorq-milestone-end' : 'zoorq-milestone-middle';
  const milestones = known ? [
    { kind: 'cover', days: coverDays, position: coverPosition, label: `${displayDays(coverDays)} cover` },
    { kind: 'lead', days: leadDays, position: leadPosition, label: `${displayDays(leadDays)} ${scenarioDelayDays ? 'scenario lead' : 'profile lead'}` },
  ].sort((a, b) => a.days - b.days || (a.kind === 'cover' ? -1 : 1)) : [];
  const description = known
    ? `Planning start; ${displayDays(coverDays)} of stock cover; ${displayDays(gapDays)} uncovered gap; ${displayDays(leadDays)} ${scenarioDelayDays ? 'scenario lead' : 'profile lead'}`
    : `Stock cover ${coverDays === null ? 'unknown' : displayDays(coverDays)}; profile lead ${profileLeadDays === null ? 'unknown' : displayDays(profileLeadDays)}; gap unknown`;
  return <div className={`zoorq-timeline ${compact ? 'zoorq-timeline-compact' : ''}`}>
    <span className="zoorq-timeline-origin" aria-hidden="true">Planning start · day 0</span>
    <div className="zoorq-timeline-track" role="img" aria-label={description}>
      {known ? <><span className="zoorq-timeline-cover" style={{ width: `${coverPosition}%` }} /><span className="zoorq-timeline-gap" style={{ left: `${coverPosition}%`, width: `${Math.max(0, leadPosition - coverPosition)}%` }} /><span className="zoorq-timeline-marker zoorq-timeline-cover-marker" style={{ left: markerLeft(coverPosition) }} aria-hidden="true" /><span className="zoorq-timeline-marker zoorq-timeline-lead-marker" style={{ left: markerLeft(leadPosition) }} aria-hidden="true" /></> : <span className="zoorq-timeline-unknown" />}
    </div>
    {known ? <div className="zoorq-timeline-milestones" aria-hidden="true">{milestones.map((milestone, index) => <span key={milestone.kind} className={`zoorq-timeline-milestone ${labelClass(milestone.position)} ${index === 0 ? 'zoorq-milestone-first' : 'zoorq-milestone-second'}`} style={{ left: `${milestone.position}%` }}>{milestone.label}</span>)}</div>
      : <div className="zoorq-timeline-unknown-labels" aria-hidden="true"><span>{coverDays === null ? 'Cover unknown' : `${displayDays(coverDays)} cover`}</span><span>{leadDays === null ? 'Lead unknown' : `${displayDays(leadDays)} ${scenarioDelayDays ? 'scenario lead' : 'profile lead'}`}</span></div>}
    {!compact && <p className="zoorq-timeline-readout">{known ? gapDays > 0 ? `${displayDays(gapDays)} uncovered gap before ${scenarioDelayDays ? `scenario lead (profile lead with a delay of ${scenarioDelayDays} days)` : 'profile lead'}.` : `Stock cover meets or exceeds ${scenarioDelayDays ? 'scenario lead' : 'profile lead'}.` : 'Gap unknown until stock, daily demand, and a matching supplier lead are available.'}</p>}
  </div>;
}
