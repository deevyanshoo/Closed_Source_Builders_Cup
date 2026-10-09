import { describe, expect, it } from 'vitest';
import { csvCell, toCsv } from './report';
import { createFixtureAdapter, offerReport } from './offers';
import { createDispatchFixtureAdapter, dispatchReport } from './dispatch';

const AT = new Date('2026-10-09T10:00:00Z');

describe('csv helpers', () => {
  it('quotes separators and neutralises spreadsheet formulas from untrusted text', () => {
    expect(csvCell('plain')).toBe('plain');
    expect(csvCell('a, "b"')).toBe('"a, ""b"""');
    expect(csvCell('=HYPERLINK("x")')).toBe(`"'=HYPERLINK(""x"")"`);
    expect(csvCell('+1')).toBe("'+1");
    expect(csvCell('-2')).toBe("'-2");
    expect(csvCell('@cmd')).toBe("'@cmd");
    expect(csvCell(null)).toBe('');
    expect(toCsv(['a', 'b'], [['1', 'x\ny']])).toBe('a,b\r\n1,"x\ny"\r\n');
  });
});

describe('offer report', () => {
  const offer = createFixtureAdapter().list().find(o => o.fixture_id === 'Q01')!;
  const report = offerReport(offer, AT);

  it('is labelled synthetic and names a file', () => {
    const json = JSON.parse(report.json);
    expect(json.synthetic).toBe(true);
    expect(json.notice).toMatch(/^SYNTHETIC/);
    expect(json.generated_at).toBe('2026-10-09T10:00:00.000Z');
    expect(json.source).toBe('local_fixture_adapter');
    expect(report.basename).toBe(`synthetic-offer-${offer.offer_id}-v${offer.version}`);
  });

  it('keeps every field with its status, exact value and evidence', () => {
    const json = JSON.parse(report.json);
    const quantity = json.fields.find((f: { name: string }) => f.name === 'items[0].quantity');
    expect(quantity.value).toEqual(offer.items[0].quantity.value);
    const incoterm = json.fields.find((f: { name: string }) => f.name === 'incoterm');
    expect(incoterm.evidence[0].locator).toBe('p1:l11');
    expect(json.fields.find((f: { name: string }) => f.name === 'settlement_currency').status).toBe('missing');
    expect(json.blocking_reasons.length).toBeGreaterThan(0);
  });

  it('marks every CSV row synthetic and includes evidence rows', () => {
    const lines = report.csv.trimEnd().split('\r\n');
    expect(lines[0].split(',').at(-1)).toBe('synthetic');
    expect(lines.slice(1).every(line => line.endsWith(',true'))).toBe(true);
    expect(lines[1].startsWith('notice,')).toBe(true);
    expect(report.csv).toContain('evidence,incoterm,,,document,p1:l11,');
  });
});

describe('dispatch report', () => {
  it('captures status, timeline, lead time, route evidence and hazards, labelled synthetic', async () => {
    const a = createDispatchFixtureAdapter();
    const list = await a.listShipments();
    const row = list.shipments.find(s => s.shipment_id === 'SHP-SYN-002')!;
    const report = dispatchReport({ row, timeline: await a.timeline('SHP-SYN-002'), evidence: await a.routeEvidence('SHP-SYN-002'),
      hazards: await a.hazards('SHP-SYN-002') }, AT);
    const json = JSON.parse(report.json);
    expect([json.synthetic, json.shipment.status, json.cutoff]).toEqual([true, 'on_hold', '2026-10-08T12:00:00+00:00']);
    expect(json.timeline.map((e: { event_id: string }) => e.event_id)).toEqual(['evt-002-hold']);
    expect(json.route_evidence.confirmed.map((e: { evidence_id: string }) => e.evidence_id)).toEqual(['rev-port-002']);
    expect(json.hazards.map((h: { state: string }) => h.state)).toContain('exposure_found');
    expect(json.lead_time.working_days).toBe(2);
    expect(report.basename).toBe('synthetic-dispatch-SHP-SYN-002');
    const lines = report.csv.trimEnd().split('\r\n');
    expect(lines.slice(1).every(line => line.endsWith(',true'))).toBe(true);
    expect(report.csv).toContain('route_evidence,rev-port-002,confirmed');
    expect(report.csv).toContain('hazard,');
  });

  it('exports without hazards when they have not loaded', async () => {
    const a = createDispatchFixtureAdapter();
    const row = (await a.listShipments()).shipments[0];
    const json = JSON.parse(dispatchReport({ row, timeline: await a.timeline(row.shipment_id), evidence: await a.routeEvidence(row.shipment_id),
      hazards: null }, AT).json);
    expect(json.hazards).toBeNull();
  });
});

describe('download buttons', () => {
  it('offers JSON and CSV, labelled synthetic', async () => {
    const { renderToStaticMarkup } = await import('react-dom/server');
    const { ReportDownloads } = await import('./report-downloads');
    const html = renderToStaticMarkup(<ReportDownloads label="this offer" build={() => ({ basename: 'x', json: '{}', csv: '' })} />);
    expect(html).toContain('Download synthetic JSON');
    expect(html).toContain('Download synthetic CSV');
    expect(html).toContain('aria-label="Download this offer as synthetic JSON"');
  });
});
