export type JudgeAiReceipt = { source_title: string; source_url: string; source_as_of: string; input_source_id: string; model_id: string; model_version: string; provider_call_id: string | null; called_at: string; status: 'success'; output: string; evidence_refs: string[]; reviewed_flags: string[] };
export type JudgeAiReceiptAggregate = { schema: 1; interpreted_articles: number; flagged_for_review: number; receipts: JudgeAiReceipt[] };
export type JudgeHindiCase = { status: 'success' | 'unavailable' | 'failed' | 'loading' | 'skipped'; inputSourceId: string; originalTitle: string; publisher: string; publishedAt: string; sourceUrl: string; translationSummary?: string; nextAction?: string };
export type JudgeAiReceiptsProps = { state: 'idle' | 'loading' | 'ready' | 'failed' | 'skipped'; aggregate: JudgeAiReceiptAggregate | null; hindiCase?: JudgeHindiCase | null; onRequestTranslation?: () => void; translationBusy?: boolean };

function verifiedReceipts(aggregate: JudgeAiReceiptAggregate | null): JudgeAiReceipt[] {
  if (aggregate?.schema !== 1 || !Array.isArray(aggregate.receipts)) return [];
  const seen = new Set<string>();
  return aggregate.receipts.filter(receipt => {
    if (!safePublicUrl(receipt.source_url) || !receipt.source_title?.trim() || !/^\d{4}-\d{2}-\d{2}/.test(receipt.source_as_of ?? '') || !receipt.model_version?.trim() || !Number.isFinite(Date.parse(receipt.called_at)) || receipt.status !== 'success' || !receipt.input_source_id || !receipt.model_id.startsWith('gemini-') || !receipt.evidence_refs?.includes(receipt.input_source_id) || seen.has(receipt.input_source_id)) return false;
    seen.add(receipt.input_source_id);
    return true;
  });
}

export function safePublicUrl(url: string): string | undefined {
  try {
    const p = new URL(url);
    if (!['https:', 'http:'].includes(p.protocol) || p.username || p.password || p.port || /[\\\s]/.test(url)) return undefined;
    const host = p.hostname.toLowerCase();
    if (!host.includes('.') || host === 'localhost' || /(?:\.local|\.internal|\.localhost)$/.test(host) || /^[\d.]+$/.test(host) || host.includes(':')) return undefined;
    return p.href;
  } catch { return undefined; }
}
function safeSourceUrl(url: string): string | undefined {
  try { const parsed = new URL(url); return safePublicUrl(url) && parsed.protocol === 'https:' && ['www.pib.gov.in', 'static.pib.gov.in'].includes(parsed.hostname) ? parsed.href : undefined; }
  catch { return undefined; }
}

export function ZoorqAiReceipts({ state, aggregate, hindiCase, onRequestTranslation, translationBusy = false }: JudgeAiReceiptsProps) {
  const receipts = state === 'ready' ? verifiedReceipts(aggregate) : [];
  const matched = hindiCase && receipts.find(receipt => receipt.input_source_id === hindiCase.inputSourceId);
  const translated = hindiCase?.status === 'success' && matched && hindiCase.translationSummary && hindiCase.nextAction;
  const sourceUrl = hindiCase ? safeSourceUrl(hindiCase.sourceUrl) : undefined;
  return <section className="zoorq-ai-receipts" aria-label="AI receipts">
    <div className="zoorq-ai-receipts-head"><div><span className="zoorq-ai-receipts-kicker">Verified AI activity</span><h2>Source interpretation</h2></div>
      <strong>{receipts.length ? `${receipts.length} source${receipts.length === 1 ? '' : 's'} interpreted` : 'No verified model interpretations yet'}</strong></div>
    {state === 'loading' && <p role="status" className="zoorq-ai-receipts-state">Checking model receipts…</p>}
    {state === 'failed' && <p role="status" className="zoorq-ai-receipts-state">Receipt check failed. No model result is claimed.</p>}
    {state === 'skipped' && <p role="status" className="zoorq-ai-receipts-state">Model interpretation was skipped.</p>}
    {hindiCase && <div className="zoorq-ai-receipts-case"><div className="zoorq-ai-receipts-case-title"><span>LoomWorks · cotton sourcing</span><h3>Hindi source → English decision context</h3></div>
      <p className="zoorq-ai-receipts-original"><span lang="hi">{hindiCase.originalTitle}</span> <span>· {hindiCase.publisher} · <time dateTime={hindiCase.publishedAt}>{hindiCase.publishedAt.slice(0, 10)}</time></span></p>
      {sourceUrl && <a href={sourceUrl} target="_blank" rel="noopener noreferrer">Official source ↗</a>}
      {translated ? <><p><strong>Translation summary:</strong> {hindiCase.translationSummary}</p><p><strong>Next action:</strong> {hindiCase.nextAction}</p></> : <p className="zoorq-ai-receipts-state">{hindiCase.status === 'success' ? 'Translation has no verified model receipt.' : 'Translation is not verified yet.'}</p>}
      <p className="zoorq-ai-receipts-limit">India’s cotton import-duty exemption does not establish supplier price or stock, or Bangladesh import duty.</p>
      {onRequestTranslation && !translated && <button type="button" disabled={translationBusy} onClick={onRequestTranslation}>{translationBusy ? 'Requesting translation…' : 'Request verified translation'}</button>}
    </div>}
    {receipts.length > 0 && <details className="zoorq-ai-receipts-evidence"><summary>Model receipt details</summary><ul>{receipts.map(receipt => <li key={receipt.input_source_id}><strong>{receipt.input_source_id}</strong><a href={safePublicUrl(receipt.source_url)} target="_blank" rel="noopener noreferrer">{receipt.source_title}</a><time dateTime={receipt.source_as_of}>{receipt.source_as_of}</time><span>{receipt.model_id} · {receipt.model_version} · <time dateTime={receipt.called_at}>{receipt.called_at}</time></span>{receipt.provider_call_id && <span>Call {receipt.provider_call_id}</span>}<span>Evidence: {receipt.evidence_refs.join(', ')}</span></li>)}</ul></details>}
  </section>;
}
