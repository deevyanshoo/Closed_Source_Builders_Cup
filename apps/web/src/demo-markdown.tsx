import type { ReactNode } from 'react';

function safeAnswerHref(value: string): string | null {
  try {
    const url = new URL(value);
    return url.protocol === 'https:' && !url.username && !url.password ? url.href : null;
  } catch { return null; }
}

function readableDurations(text: string): string {
  const round = (part: string) => part.replace(
    /(?<![\w.,/?=&:%+$-])(\d{1,4}\.\d{6,})[ \t]*(days?)\b/g,
    (original, raw: string, unit: string) => {
      const days = Number(raw);
      if (!Number.isFinite(days)) return original;
      if (days > 0 && days < 0.1) return `less than 0.1 ${unit}`;
      return `${days.toFixed(1)} ${unit}`;
    },
  );
  const protectedSpans = /`+[^`]*`+|https?:\/\/[^\s<>]+/g;
  let output = '';
  let offset = 0;
  for (const match of text.matchAll(protectedSpans)) {
    const index = match.index ?? 0;
    output += round(text.slice(offset, index)) + match[0];
    offset = index + match[0].length;
  }
  return output + round(text.slice(offset));
}

function inline(text: string): ReactNode[] {
  const pieces: ReactNode[] = [];
  const pattern = /\[([^\]\n]+)\]\(([^)\s]+)\)|\*\*([^*\n]+)\*\*/g;
  let offset = 0;
  for (const match of text.matchAll(pattern)) {
    const index = match.index ?? 0;
    if (index > offset) pieces.push(readableDurations(text.slice(offset, index)));
    if (match[3] !== undefined) pieces.push(<strong key={index}>{readableDurations(match[3])}</strong>);
    else {
      const href = safeAnswerHref(match[2]);
      pieces.push(href ? <a key={index} href={href} target="_blank" rel="noopener noreferrer">{match[1]}</a> : match[1]);
    }
    offset = index + match[0].length;
  }
  if (offset < text.length) pieces.push(readableDurations(text.slice(offset)));
  return pieces;
}

export function MarkdownAnswer({ text }: { text: string }) {
  const lines = text.replace(/\r\n?/g, '\n').split('\n');
  const blocks: ReactNode[] = [];
  let paragraph: string[] = [];
  let list: string[] = [];
  let listType: 'ul' | 'ol' = 'ul';
  const flushParagraph = () => {
    if (paragraph.length) blocks.push(<p key={blocks.length}>{inline(paragraph.join(' '))}</p>);
    paragraph = [];
  };
  const flushList = () => {
    if (list.length) {
      const children = list.map((item, index) => <li key={index}>{inline(item)}</li>);
      blocks.push(listType === 'ol' ? <ol key={blocks.length}>{children}</ol> : <ul key={blocks.length}>{children}</ul>);
    }
    list = [];
  };
  for (const raw of lines) {
    const line = raw.trim();
    if (!line) { flushParagraph(); flushList(); continue; }
    const heading = /^(#{1,3})\s+(.+)$/.exec(line);
    const bullet = /^[-*]\s+(.+)$/.exec(line);
    const number = /^\d+[.)]\s+(.+)$/.exec(line);
    if (heading) {
      flushParagraph(); flushList();
      const content = inline(heading[2]);
      blocks.push(heading[1].length === 1 ? <h2 key={blocks.length}>{content}</h2> : <h3 key={blocks.length}>{content}</h3>);
    } else if (bullet || number) {
      flushParagraph();
      const nextType = bullet ? 'ul' : 'ol';
      if (list.length && listType !== nextType) flushList();
      listType = nextType;
      list.push((bullet ?? number)![1]);
    } else { flushList(); paragraph.push(line); }
  }
  flushParagraph(); flushList();
  return <div className="demo-markdown-answer">{blocks}</div>;
}
