import type { NewsView } from './demo-api';
import { feedPublicationLabel, filterNewsItems, rankNewsItems } from './demo-company-news';
import { ArrowRightIcon } from './zoorq-icons';

export function ZoorqOverviewSignal({ news, loading, error, onOpenNews }: {
  news: NewsView | null; loading: boolean; error: string; onOpenNews: () => void;
}) {
  const now = Date.now();
  const latest = news && rankNewsItems(
    filterNewsItems(news.items, 'all', 'all', false, { age: '7d', now, includeUnknown: false, includeBackground: false }),
    'relevant',
  )[0];
  const newsSuccess = news?.collection?.news_success_at ? Date.parse(news.collection.news_success_at) : NaN;
  const datedCollection = Number.isFinite(newsSuccess) && now - newsSuccess > 2 * 60 * 60 * 1000;
  const newsSources = news?.sources.filter(source => source.source_id !== 'ecb_fx') ?? [];
  const fxSource = news?.sources.find(source => source.source_id === 'ecb_fx');
  const unhealthyNewsSource = newsSources.some(source => source.state === 'stale' || source.state === 'unavailable' || source.state === 'offline');
  const fxOnlyFailure = !!news && news.collection?.status === 'partial' && !datedCollection &&
    Number.isFinite(newsSuccess) && newsSources.length > 0 &&
    newsSources.every(source => source.state === 'fresh' || source.state === 'cached' || source.state === 'empty') &&
    !!fxSource && (fxSource.state === 'stale' || fxSource.state === 'unavailable' || fxSource.state === 'offline') &&
    news.cache_state !== 'unavailable' && news.cache_state !== 'offline';
  const incompleteCoverage = !!news && (
    news.cache_state === 'unavailable' || news.cache_state === 'offline' || datedCollection || unhealthyNewsSource ||
    (!fxOnlyFailure && (news.cache_state === 'partial' || news.cache_state === 'stale' ||
      news.collection?.status === 'partial' || news.collection?.status === 'stale' ||
      news.collection?.status === 'unavailable' || news.collection?.status === 'offline'))
  );
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
      {error && <p role="status">Source check unavailable. Review source status in News.</p>}
      {incompleteCoverage && <p className="demo-news-warning zoorq-signal-warning" role="status">Coverage may be incomplete or dated. Review source and collection times in News.</p>}
      {fxOnlyFailure && <p className="demo-news-warning zoorq-signal-warning" role="status">FX reference unavailable or dated. Review FX source status in News.</p>}
    </div>
  </article>;
}
