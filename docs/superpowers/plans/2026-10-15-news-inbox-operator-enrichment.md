# News inbox, operator and selective enrichment implementation plan

> **For agentic workers:** Use superpowers:subagent-driven-development. The user approved this design and parallel implementation, tests and deployment; execute continuously within existing trial credits.

**Goal:** Turn the ten-company news workspace into a ranked reading/action inbox and the operator into a compact chat with composer-triggered dedicated voice, with selective official-source article enrichment.

**Architecture:** Preserve React/FastAPI/session/action boundaries. Rank company news before caps, present cached snapshots using current ranking, and attach bounded optional Scrapling evidence from allowlisted EIA/ECB pages. Keep one voice controller/socket mounted across voice UI changes, with current proposals visible inside voice mode.

**Tech stack:** Existing React/TypeScript/Vitest, Python/FastAPI/pytest, Thinking Orbs, Scrapling0.4.15 static Fetcher, existing Cloud Run/Firestore/GCS/hourly jobs.

**Spec:** User-approved news/operator concept at local project `output/news-operator-ux-concept-20261015/index.html` and previous turn's design proposal. The concept is illustrative; product uses actual company/news data and existing validated tools.

## Global constraints

- Existing trial credits only; no billing/IAM/public-service expansion, real supplier delivery, browser downloads, paywall/CAPTCHA evasion or new schedule.
- Preserve exact company/session/context guards, dirty/stale-plan checks, explicit public research consent and one-time claims, unsent/simulated artifacts and raw-audio exclusion.
- Distinguish publication date, first seen, article retrieval and collection success. Unknown dates remain unknown; source assurance and company fit are separate from impact confidence.
- Existing source failures retain dated stale evidence; old snapshots remain readable. No paid model calls for background ranking/enrichment.
- Disjoint ownership; root owns shared API types, page integration, dependencies, cloud sessions, Git and release. Workers do not commit/push/deploy.

## Review focus

Incidental material mention must not outrank direct operational events. Old/unknown dates must not appear newly published. Company switches and late callbacks must not expose prior company results. Voice mode/closing the dock must not duplicate sockets or leave microphones active. Scraping must not follow user URLs, redirects or unsafe targets, and failures must not erase existing news or advance success clocks.

### Task 1 — News inbox frontend

Own `apps/web/src/demo-company-news.tsx`, related test file and new dedicated news stylesheet. Preserve CompanyOverview/Outbox exports. Consume existing NewsView plus optional article/fit fields below. Produce `NewsWorkspace` with existing props plus `onAction?: (action: 'ask'|'inventory'|'draft'|'watch'|'research', item: NewsItem) => void`.

- [ ] Add failing ranking/age/selection tests: default relevant/7days, newest toggle, missing/future dates, source staleness, company scope reset, empty result and material controls.
- [ ] Implement compact inbox/selected-story detail; early next-step actions, source/freshness disclosure and optional enrichment status/excerpt. Keep background matches available behind explicit broader-coverage toggle.
- [ ] Verify focused tests/typecheck; report checks and stop.

### Task 2 — Operator/voice frontend

Own `demo-operator.tsx`, `demo-live-voice.tsx`, their tests and `demo-assistant.css`; create focused subcomponents if needed. Root owns `demo-page.tsx` and global CSS. Add optional `closeSignal?: number` on OperatorPanel, so parent dock close ends voice. Keep LiveVoice mounted when switching UI modes.

- [ ] Add failing tests for composer-adjacent mic, dedicated voice surface, visible current proposal and cleanup/focus behavior.
- [ ] Implement compact chat, a composer voice button and dedicated responsive orb interface. Keep one active controller/socket; move the single proposal card into voice UI while voice is open; preserve same-connection spoken research confirmation. End/Escape/dock close tears down audio exactly once and returns focus.
- [ ] Verify existing live/session/proposal tests and focused new checks; report and stop.

### Task 3 — Ranking and selective Scrapling backend

Own `news.py`, new `news_enrichment.py`/`news_ranking.py` as useful, `news_snapshots.py` and related focused tests. Optional NewsItem fields: `relevance_strength` in strong/related/background/unmatched; `ranking_reason` string; `article` object with status enriched/unavailable/unsupported, method scrapling-static, fetched_at timestamp, title/published_at/excerpt nullable strings, content_chars integer and reason nullable string. Excerpt maximum320chars, no raw full article persistence. Root adds TypeScript types and pinned dependency.

- [ ] Failing regressions for operational vs incidental matches, ranking before truncation, old snapshots, unknown dates and bounded enrichment/unsafe URLs.
- [ ] Rank on company material+sector/route+event fit and known publication recency. Preserve source provenance and separate confidence. Re-rank old snapshot views at read time without rewriting collection clocks.
- [ ] Enrich only verified HTTPS EIA todayinenergy numeric article paths and ECB press English release paths, no redirects, no browsers, short deadline/zero retries, bounded process cache and at most two article fetches per refresh/hour budget. Exclude injected/offline test transports. Robots/usage-policy handling must be bounded; unavailable enrichment retains the headline.
- [ ] Support publisher date selectors/fallback without footer-date guessing. Reader enrichment must not alter news/FX success clocks; apply bounded enrichment consistently to live and snapshot views, with cache/budget guards.
- [ ] Validate optional nested fields in stored snapshots while accepting prior schema1 bundles. Verify focused tests and stop.

### Task 4 — Root integration and release

- [ ] Add shared optional types and Scrapling pinned fetchers dependency; integrate news actions as current-company operator seeds, compact workspace header and operator close signal/focus behavior in demo-page/global styles.
- [ ] Run relevant backend/web suites, session race/typecheck/build; parallel Antigravity UI/backend reviews and independent final consent/enrichment review. Fix evidenced findings.
- [ ] Commit source, package explicit allowlist, build immutable image once per actual source change, update existing service and hourly news job image with existing identity/config/caps. Verify news snapshot freshness/ranking/enrichment on hosted service; preserve generation-zero writes and hourly schedules.
- [ ] Hosted text/voice research, quote/plan/recovery/RFQ regression, draft visibility, dedicated voice lifecycle and desktop/mobile visual checks. Save sanitized evidence, release record and checkpoint; push clean branch/tag/update existing PR106.
