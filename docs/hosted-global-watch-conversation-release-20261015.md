# Global Watch, conversational voice and operations release — 15 October 2026

The hosted workspace now includes shared public-event discovery for conflict, shipping chokepoints, ports and trade restrictions. All ten company views show the same bounded event set, with separate material, route or country exposure checks and suggested next checks. Public publisher metadata, discovery provider, publication/retrieval times and source freshness remain visible. Headlines do not establish a supplier delay, route closure or price effect.

Four fixed Google News RSS queries are collected once per portfolio refresh, with a shared bounded cache and at most 18 topic-balanced events. Failed sources retain older evidence and its original clocks. Explicit failed watch attempts mark retained evidence stale independently of company-news/FX success. Existing snapshots without the optional watch field receive a cached read fallback; subsequent hourly bundles persist it. No article bodies, paid Search or new scraping provider are required for Global Watch.

Consented voice research can play one short Charon acknowledgment after 1.2 seconds of pending research: “I am checking the public sources now.” It uses a bundled 2.071-second clip rather than another model turn. Native model acknowledgments suppress it; interruption, completion, context change and disconnect cancel playback. The final answer clears queued progress audio. Fresh spoken consent, query readout, company context and durable single-claim checks remain enforced. Ordinary tool responses have concise conversational guidance; the deterministic timed clip is specific to research.

Firestore now retains validated pending proposals, atomically reserves request counts and avoids unchanged payload writes. Payload updates preserve the separate counter; unrelated sessions do not wait behind a global remote-work lock. Read-only readiness probes validate the existing database with a bounded reserved-document lookup. New diagnostics use fixed route/status/outcome labels without private prompts, cookies, session IDs or audio. Production remains capped at one instance: these repairs do not certify horizontal scaling of all voice/provider limits.

## Deployment

- Runtime source: `bdcb765368c97065fd9fddf96bcb81eb0f20ca82`.
- Cloud Build: `43f6217e-a9ba-4894-8f32-7f4f08fba322`, SUCCESS.
- Image: `asia-south1-docker.pkg.dev/closed-source-builder-261007/closed-source-demo/quote-to-plan@sha256:e97b0f896aeebab5946d0a76311ca74cad76d74e49f291496b38bb478749209b`.
- Serving revision: `closed-source-quote-demo-00040-n4g`, 100% traffic. Both existing addresses report ready/Firestore; `/-/ready` passed.
- Bundle: 317 allowlisted files, 3,306,291 bytes; manifest hash `c1ac912b424159851aa71103a67a1cfe14204af1ad95b39958728977321f50fa`.
- Deployed cue PCM matched all 99,402 bytes of the bundled asset during hosted voice acceptance.
- Service identity and caps retained: maximum 1 instance, 1 CPU/1 GiB, concurrency 8, timeout 240 seconds.
- Existing hourly news job uses the same immutable image, retains its worker identity, 1 CPU/512 MiB, timeout 120 seconds and zero retries. Execution `closed-source-news-refresh-8g84b` completed successfully in 40.29 seconds. It collected with partial source availability but did not replace the already-existing immutable current-hour bundle (`published=false`); hourly scheduling remains unchanged. Immediate hosted watch visibility was verified through the read fallback. Publication of a later scheduled bundle is not claimed here.

## Validation

Final backend demo suite: **671 passed**, no failures/errors/skips. Web: **361 passed, 6 existing skips**; session-race, typecheck and production build passed. Independent scoped reviews and a final release review approved the source after a regression repair for total-outage watch freshness. These are demo-specific checks, not a passing claim for every repository test.

Hosted acceptance checked all ten company feeds: 18 shared events, four topics, valid source clocks, company-specific conditional exposure and next actions. Atlas petrochemical text research required review, completed in 5,454 ms with five sources, and a stock-cover follow-up returned 26.76 days for naphtha.

Native Gemini Live acceptance used a generic Hormuz question in Meridian Pharma, the spoken query readout, then one controlled “Confirmed” recording on the same socket. No HTTP confirmation was sent. One research result returned five sources. After confirmation audio ended: checking at 490 ms, research start at 1,908 ms, progress speech at 3,086 ms, result at 5,480 ms and first answer audio at 6,365 ms. Exactly one bundled acknowledgment played and matched its PCM; the answer completed aloud after it.

Chrome acceptance supplied a controlled inventory question through the ordinary browser audio-capture path. Atlas inventory analysis returned one read-only tool result, captions and 113 scheduled playback chunks. Mute worked; End closed the socket and audio track. The text composer then answered naphtha stock cover. Text and voice history restored after reload. Temporary test instrumentation was removed and the user's original anonymous workspace cookie was restored and verified. These are controlled-speech/browser-path checks, not a physical microphone-quality test or a universal latency guarantee.

Receipts and screenshots are in project `output/evidence/`, prefixed `conversation-global-watch-`: backend release XML, hosted text/all-company JSON, native voice JSON/WAV, browser voice JSON/JPEG, browser history JSON, build/service/job receipts and fixed-label telemetry. The frozen source manifest and review reports are in ignored `.superpowers/sdd/2026-10-15-conversation-global-watch-resilience/`.

## Monitoring, credits and rollback

[The operations dashboard](https://console.cloud.google.com/monitoring/dashboards/builder/4e8e0eba-76f3-4525-b0fa-ed351a109172?project=closed-source-builder-261007) and three enabled policies were created and read back: more than five 5xx responses per five-minute window, p95 HTTP latency above 60 seconds for five minutes, and failed news-job executions. WebSocket upgrades are excluded from HTTP latency. Policies create console incidents; no notification recipients/channels were configured. This does not include a missing-scheduled-execution alert. Actual new fixed-label request/freshness telemetry was observed on revision 40.

Authenticated billing remained an **unupgraded Free Trial**, with about INR 28,226 credits and displayed total cost INR 0.00 before release. Existing credits fund normal build, model, storage, Cloud Run and monitoring usage. No billing upgrade, new IAM grant, new public service, Memorystore instance or new schedule was added. The console also warns that an administrator must verify the account to avoid losing access; that account action belongs to the owner.

Counter-compatible rollback was built and tested before release: build `c519a5ad-f758-4f84-8331-6d996736cd0c`, image `asia-south1-docker.pkg.dev/closed-source-builder-261007/closed-source-demo/quote-to-plan@sha256:5703418d48197e21422cb18daff8083730cf36164b8855cfaa5ed55180495d03`, 66 focused checks passed. It overlays current persistence/counter admission on the prior app. Deploy this image to the existing service if rollback is needed; directly routing to an older writer can reset the separate counter. The job can return to prior image `sha256:af0ae98959467f57bbfb80ed2f5928283301785b7b298c7842655b08c717d3f2`. Old strict news readers may reject new optional-field bundles and fall back to older compatible snapshots/live collection. Neither rollback was executed.

[Live portal](https://closed-source-quote-demo-ryoeyuqvta-el.a.run.app/demo) · [Existing draft PR 106](https://github.com/deevyanshoo/Closed_Source_Builders_Cup/pull/106). The draft remains available for broader branch integration review; main and published history are preserved. The previous release is recorded in `docs/hosted-research-responsiveness-release-20261015.md`.
