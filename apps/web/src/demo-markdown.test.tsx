import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { MarkdownAnswer } from './demo-markdown';

describe('assistant answer formatting', () => {
  it('renders headings, lists, bold text and safe source links as readable HTML', () => {
    const html = renderToStaticMarkup(<MarkdownAnswer text={'## Findings\n\n- **Port:** Open\n- [Source](https://example.org/report)'} />);
    expect(html).toContain('<h3>Findings</h3>');
    expect(html).toContain('<ul>');
    expect(html).toContain('<strong>Port:</strong>');
    expect(html).toContain('href="https://example.org/report"');
  });
  it('escapes arbitrary HTML and never turns unsafe links into navigation', () => {
    const html = renderToStaticMarkup(<MarkdownAnswer text={'<script>alert(1)</script> [click](javascript:alert(1)) **safe**'} />);
    expect(html).not.toContain('<script>');
    expect(html).not.toContain('href="javascript:');
    expect(html).toContain('&lt;script&gt;');
    expect(html).toContain('<strong>safe</strong>');
  });
});
