import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import type { ResearchResult } from './demo-api';
import { ResearchOutcome, researchPreview } from './demo-research';

const result: ResearchResult = { research_id: 'r1', query: 'Public supply conditions', status: 'completed', checked_at: '2026-10-15T10:00:00Z', answer: '', sources: [] };

describe('research brief preview', () => {
  it('uses the first content under a heading rather than the later Details section', () => {
    const answer = '## Summary\nAnswer one. Answer two.\n\n## Details\nLater detail.';
    expect(researchPreview(answer)).toBe('Answer one. Answer two.');
    const html = renderToStaticMarkup(<ResearchOutcome result={{ ...result, answer }} />);
    expect(html.indexOf('Answer one. Answer two.')).toBeLessThan(html.indexOf('Show full research answer'));
    expect(html.indexOf('Later detail.')).toBeGreaterThan(html.indexOf('Show full research answer'));
  });

  it('ends on a word boundary and keeps long markdown links intact as readable labels', () => {
    const longLink = `[Port authority report](${`https://example.org/${'report-'.repeat(65)}`})`;
    const answer = `## Summary\nThe ${longLink} says ships are moving. ${'Supply remains uncertain '.repeat(30)}today.`;
    const preview = researchPreview(answer);
    expect(preview).toContain('Port authority report');
    expect(preview).not.toContain('](');
    expect(preview).not.toContain('https://example.org');
    expect(preview.length).toBeLessThanOrEqual(361);
    expect(preview).toMatch(/\w…$/);
    expect(renderToStaticMarkup(<ResearchOutcome result={{ ...result, answer }} />)).toContain('Show full research answer');
  });
});
