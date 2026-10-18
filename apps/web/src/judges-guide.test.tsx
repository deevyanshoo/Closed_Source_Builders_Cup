import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { JudgesGuide, claimFirstGuideVisit, guideVisibility, GUIDE_SESSION_KEY } from './judges-guide';

describe('judge guide', () => {
  it('auto-opens only once per tab and version, with a safe storage fallback', () => {
    const values = new Map<string, string>();
    const storage = {
      getItem: (key: string) => values.get(key) ?? null,
      setItem: (key: string, value: string) => { values.set(key, value); },
    };
    expect(claimFirstGuideVisit(storage)).toBe(true);
    expect(values.get(GUIDE_SESSION_KEY)).toBe('seen');
    expect(claimFirstGuideVisit(storage)).toBe(false);
    expect(claimFirstGuideVisit({ getItem: () => { throw Error('blocked'); }, setItem: () => {} })).toBe(true);
  });

  it('closes and reopens without changing the first-visit marker', () => {
    const values = new Map<string, string>();
    const storage = { getItem: (key: string) => values.get(key) ?? null, setItem: (key: string, value: string) => { values.set(key, value); } };
    let open = false;
    if (claimFirstGuideVisit(storage)) open = guideVisibility(open, 'firstVisit');
    expect(open).toBe(true);
    open = guideVisibility(open, 'close');
    expect(open).toBe(false);
    expect(claimFirstGuideVisit(storage)).toBe(false);
    open = guideVisibility(open, 'reopen');
    expect(open).toBe(true);
    expect(values.get(GUIDE_SESSION_KEY)).toBe('seen');
  });

  it('renders the approved news, voice and multilingual prompts with a persistent reopen control', () => {
    const html = renderToStaticMarkup(<JudgesGuide />);
    expect(html).toContain('Judge guide');
    expect(html).toContain('<dialog');
    expect(html).toContain('News');
    expect(html).toContain('Global Watch');
    expect(html).toContain('Gemini Live operator is multilingual');
    expect(html).toContain('मेरी कंपनी की खरीद पर कौन-सी खबरें असर डाल सकती हैं?');
    expect(html).toContain('Explain that in Hindi.');
    expect(html).toContain('Let’s explore');
  });
});
