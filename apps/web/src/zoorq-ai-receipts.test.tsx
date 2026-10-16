import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { ZoorqAiReceipts, type JudgeAiReceiptAggregate } from './zoorq-ai-receipts';

const receipt = { source_title: 'PIB cotton notice', source_url: 'https://www.pib.gov.in/PressReleasePage.aspx?PRID=2267065', source_as_of: '2026-05-30', input_source_id: 'pib-2267065', model_id: 'gemini-2.5-flash', model_version: '2026-10', provider_call_id: 'call-1', called_at: '2026-10-17T09:00:00Z', status: 'success' as const, output: 'English summary', evidence_refs: ['pib-2267065'], reviewed_flags: ['review_source'] };
const caseView = { status: 'success' as const, inputSourceId: 'pib-2267065', originalTitle: 'कपास आयात पर सीमा शुल्क से छूट', publisher: 'Press Information Bureau', publishedAt: '2026-05-30T18:46:00+05:30', sourceUrl: 'https://www.pib.gov.in/PressReleasePage.aspx?PRID=2267065', translationSummary: 'India temporarily exempts cotton imports from specified customs duties.', nextAction: 'Review cotton sourcing exposure for LoomWorks.' };
const aggregate: JudgeAiReceiptAggregate = { schema: 1, interpreted_articles: 93, flagged_for_review: 88, receipts: [receipt, { ...receipt, provider_call_id: 'duplicate' }] };

describe('AI receipts', () => {
  it('shows source interpretation without a model-run claim when no receipts exist', () => {
    const html = renderToStaticMarkup(<ZoorqAiReceipts state="idle" aggregate={null} hindiCase={{ ...caseView, status: 'unavailable' }} />);
    expect(html).toContain('Source interpretation');
    expect(html).toContain('No verified model interpretations yet');
    expect(html).not.toContain('93 articles');
    expect(html).not.toContain(caseView.translationSummary);
  });

  it('counts distinct successful backend receipt IDs, never the supplied aggregate count', () => {
    const html = renderToStaticMarkup(<ZoorqAiReceipts state="ready" aggregate={aggregate} hindiCase={caseView} />);
    expect(html).toContain('1 source interpreted');
    expect(html).not.toContain('93 sources');
    expect(html).toContain(caseView.translationSummary);
    expect(html).toContain('gemini-2.5-flash');
    expect(html).toContain('call-1');
    expect(html).toContain('pib-2267065');
    expect(html).toContain('https://www.pib.gov.in/PressReleasePage.aspx?PRID=2267065');
  });

  it('does not show a claimed successful case without its matching receipt', () => {
    const html = renderToStaticMarkup(<ZoorqAiReceipts state="ready" aggregate={{ ...aggregate, receipts: [] }} hindiCase={caseView} />);
    expect(html).not.toContain(caseView.translationSummary);
    expect(html).toContain('Translation has no verified model receipt');
  });

  it('does not label a non-PIB destination as this case’s official source', () => {
    const html = renderToStaticMarkup(<ZoorqAiReceipts state="ready" aggregate={aggregate} hindiCase={{ ...caseView, sourceUrl: 'https://example.org/claim' }} />);
    expect(html).not.toContain('href="https://example.org/claim"');
    expect(html).not.toContain('Official source');
  });

  it('separates failed and skipped calls from successful interpretation', () => {
    for (const state of ['failed', 'skipped'] as const) {
      const html = renderToStaticMarkup(<ZoorqAiReceipts state={state} aggregate={null} hindiCase={{ ...caseView, status: state }} />);
      expect(html).not.toContain(caseView.translationSummary);
      expect(html).not.toContain('source interpreted');
    }
  });
});
