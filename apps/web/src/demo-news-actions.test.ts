import { describe, expect, it } from 'vitest';
import { newsActionMessage } from './demo-news-actions';
import type { CompanyProfile, NewsItem, NewsView } from './demo-api';

const company = { company_id: 'atlas', name: 'Atlas', materials: [{ material_id: 'naphtha', name: 'Naphtha' }] } as CompanyProfile;
const item = { item_id: 'story-1', title: 'Cracker restart improves naphtha supply', published_at: '2026-10-15T08:00:00Z', matched_materials: ['naphtha'], relevance_strength: 'strong' } as NewsItem;
const view = { company_id: 'atlas', context_version: 4, items: [item] } as NewsView;
describe('reviewed news action entry', () => {
  it('prepares a specific unsent draft without treating news as a confirmed delay', () => {
    const message = newsActionMessage('draft', item, company, view, 4);
    expect(message).toContain('naphtha');
    expect(message).toContain('unsent');
    expect(message).toContain('does not establish a supplier delay');
    expect(message).toContain('story-1');
  });
  it('rejects old company context and stories outside the displayed snapshot', () => {
    expect(newsActionMessage('ask', item, company, view, 5)).toBeNull();
    expect(newsActionMessage('ask', item, { ...company, company_id: 'other' }, view, 4)).toBeNull();
    expect(newsActionMessage('ask', { ...item, item_id: 'forged' }, company, view, 4)).toBeNull();
  });
  it('gates material actions while retaining consent based research for broader evidence', () => {
    const unrelated = { ...item, matched_materials: ['copper'], relevance_strength: 'background' as const };
    const broader = { ...view, items: [unrelated] };
    expect(newsActionMessage('inventory', unrelated, company, broader, 4)).toBeNull();
    expect(newsActionMessage('draft', unrelated, company, broader, 4)).toBeNull();
    expect(newsActionMessage('research', unrelated, company, broader, 4)).toContain('confirmation');
  });
});
