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
  it('rounds excessive stock-cover duration precision in ordinary and bold answer prose', () => {
    const html = renderToStaticMarkup(<MarkdownAnswer text={'Atlas inventory has **26.76056338028169014084507042days** of stock cover. Another material has 0.0000042 days.'} />);
    expect(html).toContain('<strong>26.8 days</strong>');
    expect(html).toContain('less than 0.1 days');
    expect(html).not.toContain('26.76056338028169014084507042');
    expect(html).not.toContain('0.0 days');
  });
  it('preserves concise durations, other units, prices, URLs, link labels, and backtick code', () => {
    const raw = '4.25 days; $26.76056338028169014084507042 per kg; 26.76056338028169014084507042 kg; '
      + '[26.76056338028169014084507042days](https://example.org/26.76056338028169014084507042days) '
      + 'https://example.org/?q=26.76056338028169014084507042days '
      + '`26.76056338028169014084507042days` -26.76056338028169014084507042days '
      + '1,026.76056338028169014084507042days 123456.76056338028169014084507042days';
    const html = renderToStaticMarkup(<MarkdownAnswer text={raw} />);
    expect(html).toContain('4.25 days');
    expect(html).toContain('$26.76056338028169014084507042 per kg');
    expect(html).toContain('26.76056338028169014084507042 kg');
    expect(html).toContain('>26.76056338028169014084507042days</a>');
    expect(html).toContain('href="https://example.org/26.76056338028169014084507042days"');
    expect(html).toContain('https://example.org/?q=26.76056338028169014084507042days');
    expect(html).toContain('`26.76056338028169014084507042days`');
    expect(html).toContain('-26.76056338028169014084507042days');
    expect(html).toContain('1,026.76056338028169014084507042days');
    expect(html).toContain('123456.76056338028169014084507042days');
  });
});
