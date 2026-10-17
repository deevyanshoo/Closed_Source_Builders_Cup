import type { CompanyProfile, NewsItem, NewsView } from './demo-api';

export type NewsAction = 'ask' | 'inventory' | 'draft' | 'watch' | 'research';

/** Prepare a composer prompt only; the operator still owns consent and reviewed actions. */
export function newsActionMessage(action: NewsAction, item: NewsItem, company: CompanyProfile, view: NewsView | null, contextVersion: number): string | null {
  if (!view || view.company_id !== company.company_id || view.context_version !== contextVersion || !view.items.includes(item)) return null;
  const materials = item.matched_materials.filter(id => company.materials.some(material => material.material_id === id));
  if ((action === 'inventory' || action === 'draft') && (!materials.length || item.relevance_strength === 'background' || item.relevance_strength === 'unmatched')) return null;
  const evidence = `For ${company.name}, news ${item.item_id}: "${item.title.slice(0, 320)}" (published ${item.published_at ?? 'date unknown'}).`;
  const requests: Record<NewsAction, string> = {
    ask: 'Explain the business impact briefly using the current news evidence. Separate reported facts from possible implications and suggest one next step.',
    inventory: `Check actual inventory and stock cover for ${materials.join(', ')} against this news. Explain any exposure using our workspace data. News alone does not establish a supplier delay.`,
    draft: `Prepare an unsent supplier email draft for ${materials.join(', ')} asking about availability and lead time in light of this news. This news does not establish a supplier delay. Keep any draft subject to review.`,
    watch: materials.length ? `Prepare a material watch proposal for ${materials.join(', ')} with the existing watch tool. Explain its scope and request review before applying.` : 'Suggest how to monitor this topic using the available tools. Explain any monitoring limits; do not claim a watch has been saved.',
    research: 'Prepare a focused external internet research request to verify this story and its business implications. Show the exact query and wait for my confirmation before searching.',
  };
  return `${evidence} ${requests[action]}`.slice(0, 1200);
}
