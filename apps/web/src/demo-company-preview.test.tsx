import { renderToStaticMarkup } from 'react-dom/server';
import { expect, it } from 'vitest';
import { CompanyResearchPreview, CompanyPreviewOutcome } from './demo-company-preview';
it('shows the public-only boundary and keeps a missing session inert', () => {
  const html = renderToStaticMarkup(<CompanyResearchPreview session={null} />);
  expect(html).toContain('Explore your company');
  expect(html).toContain('Workspace is opening');
  expect(html).toContain('Tata Steel');
  expect(html).toContain('<details');
  expect(html).toContain('disabled');
});

it('renders a grounded result as safe sections with attribution and Search suggestions', () => {
  const html = renderToStaticMarkup(<CompanyPreviewOutcome result={{
    research_id: 'r1', query: 'Research public sources for Tata Steel', status: 'completed',
    checked_at: '2026-10-17T00:00:00Z',
    answer: '## Industry\nSteel manufacturing.\n\n## Materials\nPublic reports name iron ore.',
    sources: [{ source_id: 's1', title: 'Company report', url: 'https://example.org/report' }],
    search_suggestions_html: '<div>Google Search</div>',
  }} onReset={() => {}} />);
  expect(html).toContain('Steel manufacturing.');
  expect(html).toContain('Show full research answer');
  expect(html).toContain('Company report');
  expect(html).toContain('Google Search Suggestions');
  expect(html).toContain('sandbox="allow-popups allow-popups-to-escape-sandbox"');
});
