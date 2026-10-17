# News inbox and operator release — 15 October 2026

The ten-company workspace now opens news as a ranked inbox with a selected-story reading pane. Relevant operational stories lead; publication age, material and category filters make the reading order explicit. Background and unknown-date stories remain available behind controls. The selected story separates source facts, conditional business exposure, confidence limits and next checks, with actions that prepare a company-scoped operator message for review. They never send a message or start external research automatically.

The right-side operator has a fixed header and composer, independently scrolling conversation, compact source links and a microphone beside Send. Voice opens a dedicated Thinking Orbs surface with captions, current consent controls, mute and End/Back/Escape. One controller remains mounted across research proposals and results. The desktop dock is complementary; the mobile dock is a dismissible dialog with focus restoration. Short screens can scroll stacked review panels without clipping the composer.

Selective article evidence uses Scrapling Selector with a bounded static HTTP transport. Exact EIA Today in Energy numeric article paths and English ECB press releases are allowlisted. There is no browser impersonation, redirect following or arbitrary URL fetch. Each Python process permits at most two new article GETs per hour, caches bounded metadata for six hours, transfers at most350 KB per article and retains an excerpt of at most320 characters. Other publishers keep headline metadata and an explicit unsupported/unavailable state. This is selective enrichment, not unrestricted scraping. Background enrichment makes no model calls.

Publication, first seen, article retrieval, news collection and FX collection clocks remain distinct. Re-ranking snapshots does not advance collection success or manufacture publication dates. News reports indicate conditional exposure, never a confirmed supplier delay. Research still requires the exact public query to be reviewed; voice confirmation retains the server's fresh finalized-audio gate, context/expiry checks and one-time claim. RFQs remain unsent and emails simulated unless separately authorized and implemented.

## Final deployment

- Runtime source: `8512a4a9ea091e095532a1aba0c91865ff93660e`.
- Build: `9a2861e2-f01d-4729-b7a5-fed2e8214289`, SUCCESS.
- Image digest: `sha256:1e19a7434b3508045988ef1958ecfa5da086ec8a2a8cbbf803fc400e3635a8f4`.
- Serving revision: `closed-source-quote-demo-00035-wdg`, 100% traffic on both existing addresses; Firestore ready.
- Source package: 312 files, 3,122,911 bytes; manifest `4048f561d57a6aab83bf79a4091ff21faccb760f19b607ca0ea1779811498b0c`.
- Existing hourly collector updated to the same digest. Existing UTC hourly news and cleanup schedules remain enabled and unchanged.

Final hosted acceptance passed all ten companies, scope/ranking/clock checks and two enriched EIA stories. Both metaphorical catalyst stories are background; physical catalyst and material-price coverage remains related or strong. The only runtime file changed from the browser-tested revision 34 is `news_ranking.py`; all web and voice files are byte-identical. Final evidence: `news-inbox-release-company-acceptance-v2-20261015.json`, `news-inbox-final-package-20261015.json`, service/job/health receipts and `news-inbox-final-hosted-20261015.png`.

Live portal: https://closed-source-quote-demo-ryoeyuqvta-el.a.run.app/demo

## Validation and evidence boundaries

- Backend: 538 demo tests passed on final runtime source, including reader identifier and metaphorical catalyst regressions; 101 focused news/snapshot tests also passed.
- Final web: 354 passed, 6 existing skips across 29 files; session race, typecheck and production build passed.
- Revision 00032-k9p: hosted text consent/research and source citations, email artifact visibility/isolation, three reviewed rules quotes, plan, 20-day supplier-delay recovery, operator/direct RFQ and draft retention passed. Outbox remained empty; no external delivery.
- Revision 00032-k9p: controlled spoken confirmation after completed query readout ran public research; an early confirmation was denied without a search.
- Revision 00033-2rt: mobile 390×844 visible query confirmation completed research with 4 sources, kept the same single socket open, then a spoken inventory follow-up returned a stock-cover answer. 986,883 PCM output bytes, zero interrupts; Escape stopped the one microphone track/socket and returned focus. Native microphone capture separately reached Listening, Mute and End; no human speech-quality walkthrough was claimed.
- Latest local 390×667 stress check: expanded company attention, risk summary and a long review panel left the composer within the viewport. Header Close restored focus to the now-visible Open operator chat button. The temporary layout probe was removed by reload.
- Parallel Antigravity terminal reviews covered backend boundaries and the UI acceptance plan. Independent final source reviews covered consent/replay, transfer bounds and layout; review findings were fixed.

Sanitized receipts are in the project output/evidence directory: news-inbox-final-release-backend-20261015.xml, news-inbox-text-hosted-20261015.json, news-inbox-voice-browser-20261015.json, news-inbox-final-voice-browser-20261015.json and news-inbox-transport-diagnostic-20261015.json. The latter records exactly three metadata-only public GETs in the existing job: EIA with the former identifier timed out, descriptive Scrapling reader succeeded 200 / 55,674 bytes / 838 ms, and ECB succeeded 200 / 106,935 bytes / 2,631 ms. No model or persistence call ran in that diagnostic.

## Cost and rollback

Only the existing trial-credit project, service, image repository and hourly news job were used. Authenticated console Free Trial status was observed unupgraded with INR 28,282.16 credits and 64 days remaining. Service max 1 instance, 1 CPU/1 GiB, concurrency 8 and existing identity remain unchanged; worker remains 1 task/1 CPU/512 MiB/120 seconds/zero retries. No billing upgrade, new IAM grant, public service or schedule was created. Normal Cloud Run/build/storage/model usage consumes existing credits; this release does not promise zero resource consumption.

Rollback can route the existing service to closed-source-quote-demo-00033-2rt (image 14d7df2e5627595f78fe06b753d975b1754d6b0742b0d3407544f357ba366d67), with the hourly job restored to the same digest. The pre-task 00031-79n image 43ba79e7e9c457899e5655b7f66b2b78d64764fd83fc36dab2d4ac5f6604d9cb is also retained. No IAM or schema migration is needed.

Revision 00034-hjd hosted acceptance passed all ten company scope, ranking, excerpt bounds and independent clock checks. Two Atlas EIA stories returned real excerpts of 175 and 172 characters. The selected article view showed the excerpt, primary publisher, original link, feed date and separate retrieval time. Native microphone controls on GridSpring reached Listening, muted, ended and restored focus correctly at 390×667. The heavily exercised Atlas QA workspace reached its existing voice-start limit; it was left intact and no user history was deleted. Evidence: news-inbox-release-company-acceptance-20261015.json, news-inbox-final-ui-lifecycle-20261015.json and news-inbox-mobile-listening-20261015.png.
