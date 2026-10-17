import type { ResearchResult } from './demo-api';
import { MarkdownAnswer } from './demo-markdown';

export function safeResearchHref(value: string): string | null {
  try {
    const url = new URL(value);
    return url.protocol === 'https:' && !url.username && !url.password ? url.href : null;
  } catch { return null; }
}

export function validatedResearchResult(value: unknown): ResearchResult | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  const result = value as Record<string, unknown>;
  if (typeof result.research_id !== 'string' || !result.research_id || result.research_id.length > 128 ||
      typeof result.query !== 'string' || !result.query || result.query.length > 400 ||
      (result.status !== 'completed' && result.status !== 'unavailable') ||
      typeof result.checked_at !== 'string' || !Number.isFinite(Date.parse(result.checked_at)) ||
      typeof result.answer !== 'string' || result.answer.length > 8000 ||
      !Array.isArray(result.sources) || result.sources.length > 24 ||
      !result.sources.every(source => source && typeof source === 'object' && !Array.isArray(source) &&
        typeof source.source_id === 'string' && !!source.source_id && source.source_id.length <= 200 &&
        typeof source.title === 'string' && !!source.title && source.title.length <= 200 &&
        typeof source.url === 'string' && source.url.length <= 1200 && !!safeResearchHref(source.url)) ||
      (result.search_suggestions_html !== undefined && result.search_suggestions_html !== null &&
        (typeof result.search_suggestions_html !== 'string' || result.search_suggestions_html.length > 12000))) return null;
  return result as ResearchResult;
}

export function researchPreview(answer: string): string {
  const lines = answer.trim().split(/\r?\n/);
  const content: string[] = [];
  for (const line of lines) {
    const trimmed = line.trim();
    if (/^#{1,6}\s/.test(trimmed)) {
      if (content.length) break;
      continue;
    }
    if (!trimmed) {
      if (content.length) break;
      continue;
    }
    content.push(trimmed);
  }
  const paragraph = (content.join(' ') || answer.trim())
    .replace(/\[([^\]\n]+)\]\([^\s)]+\)/g, '$1')
    .replace(/\*\*([^*]+)\*\*/g, '$1')
    .replace(/\s+/g, ' ').trim();
  const sentences = paragraph.match(/[^.!?]+[.!?]+(?:\s|$)|[^.!?]+$/g) ?? [];
  const firstTwo = sentences.slice(0, 2).join('').trim();
  if (firstTwo.length <= 360) return firstTwo;
  const prefix = firstTwo.slice(0, 361);
  const cut = prefix.lastIndexOf(' ');
  return `${cut > 0 ? prefix.slice(0, cut) : ''}…`;
}

export function ResearchOutcome({ result }: { result: ResearchResult }) {
  const checked = Date.parse(result.checked_at);
  const summary = researchPreview(result.answer);
  const hasDetails = result.answer.trim() !== summary;
  return <div className="demo-research-outcome">
    <span className="demo-kicker">Public web research · {result.status}</span>
    <h3>{result.query}</h3>
    <p className="demo-muted">Checked {Number.isFinite(checked) ? new Date(checked).toISOString().replace('T', ' ').slice(0, 16) + ' UTC' : 'time unavailable'}</p>
    <div className="demo-operator-answer"><MarkdownAnswer text={summary || 'A sourced answer is unavailable.'} /></div>
    {hasDetails && <details className="demo-research-details"><summary>Show full research answer</summary><MarkdownAnswer text={result.answer} /></details>}
    <h4>Sources</h4>
    {result.sources.length ? <ol className="demo-operator-evidence">{result.sources.map(source => {
      const href = safeResearchHref(source.url);
      return <li key={source.source_id}>{href ? <a href={href} target="_blank" rel="noopener noreferrer">{source.title || href} ↗</a> : <span>Source link unavailable</span>}</li>;
    })}</ol> : <p>No verifiable source links returned.</p>}
    {result.search_suggestions_html && <div className="demo-research-suggestions"><h4>Google Search Suggestions</h4><iframe title="Google Search Suggestions" sandbox="allow-popups allow-popups-to-escape-sandbox" referrerPolicy="no-referrer" srcDoc={result.search_suggestions_html} /></div>}
  </div>;
}
