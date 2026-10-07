import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { Editor } from './offers-page';
import { createFixtureAdapter } from './offers';

describe('offer review editor', () => {
  it('renders existing Q14 averaging and FX basis for a reviewer to preserve or correct', () => {
    const q14 = createFixtureAdapter(() => new Date('2026-10-07T12:00:00Z')).list().find(o => o.fixture_id === 'Q14')!;
    const html = renderToStaticMarkup(<Editor field={q14.items[0].pricing} onSubmit={() => {}} onCancel={() => {}} busy={false} />);
    expect(html).toContain('Averaging period');
    expect(html).toContain('average of the month of delivery');
    expect(html).toContain('FX basis');
    expect(html).toContain('converted to EUR at the ECB reference rate on the invoice date');
    expect(html).toContain('Quotational period');
  });
});
