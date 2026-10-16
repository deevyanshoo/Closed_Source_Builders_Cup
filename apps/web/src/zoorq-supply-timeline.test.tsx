import { describe, expect, it } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import type { CompanyProfile } from './demo-api';
import { assessMaterialSupply, SupplyTimeline } from './zoorq-supply-timeline';

const material: CompanyProfile['materials'][number] = { material_id: 'copper', name: 'Copper', quantity_unit: 'kg', stock: '600', consumption_per_day: '100', monthly_requirement: '3000', criticality: 'high', keywords: [] };
const suppliers: CompanyProfile['suppliers'] = [
  { supplier_id: 'unrelated', name: 'Polymer Works', country: 'India', settlement_currency: 'USD', materials: ['polymer'], lead_time_days: 1 },
  { supplier_id: 'copper', name: 'Copper Works', country: 'India', settlement_currency: 'USD', materials: ['copper'], lead_time_days: 10 },
];

describe('material supply comparison', () => {
  it('compares valid same-material stock and lead and renders an accessible 6/4/10 timeline', () => {
    const assessment = assessMaterialSupply(material, suppliers);
    expect(assessment).toMatchObject({ coverDays: 6, leadDays: 10, gapDays: 4 });
    const html = renderToStaticMarkup(<SupplyTimeline assessment={assessment} />);
    expect(html).toContain('6 days of stock cover');
    expect(html).toContain('4 days uncovered gap');
    expect(html).toContain('10 days profile lead');
    expect(html).toContain('role="img"');
    expect(html).toContain('Planning start · day 0');
    expect(html).toMatch(/zoorq-timeline-cover-marker" style="left:60%"/);
    expect(html).toMatch(/zoorq-timeline-lead-marker" style="left:calc\(100% - 2px\)"/);
  });

  it('places an earlier profile lead before the later stock exhaustion milestone', () => {
    const assessment = assessMaterialSupply({ ...material, stock: '2000' }, suppliers);
    const html = renderToStaticMarkup(<SupplyTimeline assessment={assessment} />);
    expect(assessment).toMatchObject({ coverDays: 20, leadDays: 10, gapDays: 0 });
    expect(html).toMatch(/zoorq-timeline-lead-marker" style="left:50%"/);
    expect(html).toMatch(/zoorq-timeline-cover-marker" style="left:calc\(100% - 2px\)"/);
    const labels = html.slice(html.indexOf('zoorq-timeline-milestones'));
    expect(labels.indexOf('10 days profile lead')).toBeLessThan(labels.indexOf('20 days cover'));
    expect(html).toContain('Stock cover meets or exceeds profile lead.');
  });

  it('handles coincident day-zero milestones and unknown inputs without false positions', () => {
    const zero = renderToStaticMarkup(<SupplyTimeline assessment={assessMaterialSupply({ ...material, stock: '0' }, [{ ...suppliers[1], lead_time_days: 0 }])} />);
    expect(zero).toContain('0 days cover');
    expect(zero).toContain('0 days profile lead');
    expect(zero).toMatch(/zoorq-timeline-cover-marker" style="left:0px"/);
    expect(zero).toMatch(/zoorq-timeline-lead-marker" style="left:0px"/);
    const unknown = renderToStaticMarkup(<SupplyTimeline assessment={assessMaterialSupply({ ...material, consumption_per_day: '0' }, suppliers)} />);
    expect(unknown).toContain('Stock cover unknown');
    expect(unknown).not.toContain('zoorq-timeline-lead-marker');
    expect(unknown).not.toContain('Today');
  });

  it('preserves a no-gap state and rejects missing or invalid inputs', () => {
    expect(assessMaterialSupply(material, [{ ...suppliers[1], lead_time_days: 4 }]).gapDays).toBe(0);
    expect(assessMaterialSupply({ ...material, stock: '-1' }, suppliers).coverDays).toBeNull();
    expect(assessMaterialSupply({ ...material, consumption_per_day: '0' }, suppliers).gapDays).toBeNull();
    expect(assessMaterialSupply(material, [suppliers[0]]).leadDays).toBeNull();
    expect(assessMaterialSupply(material, [{ ...suppliers[1], lead_time_days: -5 }]).leadDays).toBeNull();
  });

  it('changes a local delay scenario without changing the profile supplier lead', () => {
    const original = assessMaterialSupply(material, suppliers);
    const delayed = assessMaterialSupply(material, suppliers, 3);
    expect(delayed).toMatchObject({ profileLeadDays: 10, leadDays: 13, gapDays: 7 });
    expect(renderToStaticMarkup(<SupplyTimeline assessment={delayed} />)).toContain('scenario lead (profile lead with a delay of 3 days)');
    expect(original).toMatchObject({ leadDays: 10, gapDays: 4 });
    expect(suppliers[1].lead_time_days).toBe(10);
  });
});
