import type { CompanyProfile, DemoSession } from './demo-api';
import './zoorq-judge-entry.css';

export type JudgeEntryPrepared = { status: 'loading' | 'ready' | 'unavailable'; facts: { label: string; value: string; basis: string }[] };
export function formatJudgeFact(value: string): string {
  const match = /^(-?\d+(?:\.\d+)?)\s+(days|[A-Z]{3})$/.exec(value.trim());
  if (!match || match[1].length > 80) return value;
  const amount = Number(match[1]);
  if (!Number.isFinite(amount) || Math.abs(amount) > 1e12) return value;
  const display = new Intl.NumberFormat('en-US', {maximumFractionDigits: 2}).format(amount);
  if (amount > 0 && display === '0') return `less than 0.01 ${match[2]}`;
  const rounded = Number(display.replaceAll(',', ''));
  return `${rounded !== amount ? 'about ' : ''}${display} ${match[2]}`;
}
function compactFactBasis(label: string, basis: string): string {
  if (label === 'Profile stock cover') return 'Recorded stock / daily use';
  if (label === 'Shortest profile lead') return 'Profile estimate';
  if (label === 'Reviewed baseline spend') return 'Reviewed quote total';
  return basis;
}
export type JudgeEntryDestination = 'route' | 'news' | 'what-if';
export type JudgeEntryProps = {
  company: CompanyProfile;
  session: DemoSession | null;
  visit: 'first' | 'restored';
  prepared: JudgeEntryPrepared;
  companyCount: number;
  onNavigate: (destination: JudgeEntryDestination) => void;
  onExploreCompanies: () => void;
};

export function ZoorqJudgeEntry({ company, session, visit, prepared, companyCount, onNavigate, onExploreCompanies }: JudgeEntryProps) {
  const continuing = visit === 'restored' && session !== null;
  const ready = prepared.status === 'ready';
  return <section className={`zoorq-judge-entry${continuing ? ' is-continuing' : ''}`} aria-label="Procurement resilience story">
    <div className="zoorq-judge-entry-copy">
      <span className="zoorq-judge-entry-kicker">Procurement resilience · {company.name}</span>
      <h2>{continuing ? `Continue ${company.name}` : 'See the exposure. Review the next move.'}</h2>
      {!continuing && <><p>Supply signals matter when they reach a material, route and sourcing decision. Trace the evidence, then compare a possible response.</p>
      <div className="zoorq-judge-entry-status" role="status">{ready ? 'Prepared scenario · ready to explore' : prepared.status === 'loading' ? 'Preparing the scenario…' : 'Prepared scenario unavailable; you can still explore this company.'}</div></>}
    </div>
    {!continuing && <div className="zoorq-judge-entry-facts-shell">
      {ready && prepared.facts.length > 0 ? <dl className="zoorq-judge-entry-facts" aria-label="Scenario facts">{prepared.facts.map(fact => {
        const display = formatJudgeFact(fact.value);
        const parts = /^(.*) (days|[A-Z]{3})$/.exec(display);
        return <div key={`${fact.label}:${fact.value}`}><dt>{fact.label}</dt><dd>{parts ? <><span className="zoorq-judge-entry-fact-value">{parts[1]}</span> <span className="zoorq-judge-entry-fact-unit">{parts[2]}</span></> : display}<small title={fact.basis}><span aria-hidden="true">{compactFactBasis(fact.label, fact.basis)}</span><span className="sr-only">Source basis: {fact.basis}</span></small></dd></div>;
      })}</dl> : prepared.status === 'loading' ? <div className="zoorq-judge-entry-facts-skeleton" aria-hidden="true">{[0,1,2].map(index => <div key={index}><span /><strong /><span /></div>)}</div> : <p className="zoorq-judge-entry-facts-unavailable">Scenario figures are unavailable for this profile.</p>}
    </div>}
    <nav className="zoorq-judge-entry-actions" aria-label="Explore the scenario">
      <button type="button" onClick={() => onNavigate('route')}>Route <span aria-hidden="true">↗</span></button>
      <button type="button" onClick={() => onNavigate('news')}>News <span aria-hidden="true">↗</span></button>
      <button type="button" onClick={() => onNavigate('what-if')}>What if <span aria-hidden="true">↗</span></button>
      <button type="button" className="zoorq-judge-entry-explore" onClick={onExploreCompanies}>Explore all {companyCount} companies <span aria-hidden="true">→</span></button>
    </nav>
  </section>;
}
