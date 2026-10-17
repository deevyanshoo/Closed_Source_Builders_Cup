import type { NewsView } from './demo-api';
import { feedPublicationLabel, filterNewsItems, rankNewsItems } from './demo-company-news';
import { ArrowRightIcon } from './zoorq-icons';
import { newsPresentationStatus } from './news-presentation';

export function ZoorqOverviewSignal({ news, loading, error, onOpenNews }: {
  news: NewsView | null; loading: boolean; error: string; onOpenNews: () => void;
}) {
  const now = Date.now();
  const latest = news && rankNewsItems(
    filterNewsItems(news.items, 'all', 'all', false, { age: '7d', now, includeUnknown: false, includeBackground: false }),
    'relevant',
  )[0];
  const status = newsPresentationStatus(news, now);
  const newsSources = news?.sources.filter(source => source.source_id !== 'ecb_fx') ?? [];
  const emptyCoverageHeading = news?.cache_state === 'offline' || (newsSources.length > 0 && newsSources.every(source => source.state === 'offline'))
    ? 'News coverage offline'
    : news?.cache_state === 'unavailable' || (newsSources.length > 0 && newsSources.every(source => source.state === 'unavailable'))
      ? 'News coverage unavailable'
      : newsSources.length === 0 && news?.items.length === 0
        ? 'News source status unavailable'
        : 'News coverage';
  const unavailableCoverage = emptyCoverageHeading !== 'News coverage';

  return <article className="zoorq-signals-card">
    <div className="zoorq-card-head"><h3>Latest relevant signal</h3><button type="button" onClick={onOpenNews}>View all signals <ArrowRightIcon /></button></div>
    <div className="zoorq-signal-content">
      <strong>{latest?.title ?? (news ? emptyCoverageHeading : loading ? 'Checking public news sources' : error ? 'Source check unavailable' : 'News coverage unavailable')}</strong>
      <p>{latest ? `${latest.publisher ?? latest.provider} · Published ${feedPublicationLabel(latest.published_at)}` : news ? unavailableCoverage ? 'Public news source status needs review. Open News for details.' : 'No company story in the past 7 days under the default News filters. Check source status in News.' : loading ? 'Fetching observed headlines and source status.' : 'Open News to check source status and try again.'}</p>
      {loading && news && <p role="status">Checking public news sources. The story shown is from the previous collection.</p>}
      {error && <p role="status">Source check unavailable. Review Collection details in News.</p>}
      {news && <p className="zoorq-signal-collection" role="status">{status.label}{status.detail ? ` · ${status.detail}` : ''}</p>}
    </div>
  </article>;
}
