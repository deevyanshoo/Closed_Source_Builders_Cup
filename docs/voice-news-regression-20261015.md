# Voice news grounding regression — 15 October 2026

## Observed failure

The exact controlled spoken question “What's happening in the world that can affect my business procurement?” failed on hosted revision `closed-source-quote-demo-00043-n5f`: News Workspace contained 80 company items and 18 Global Watch events, but Gemini selected `prepare_research`. That request was rejected as an invalid external-research question and the failure-speech guard correctly suppressed its model audio. The tool returned a clarification instead of the requested briefing. HTTP and WebSocket success logs alone did not reveal this answer-quality failure.

The earlier catalogue audit covered inventory and draft actions, but did not establish acceptance for this broad news question.

## Change

- Added a dedicated `read_news` tool over the same cached company feed as News Workspace, including Global Watch. It loads on first use, even if the news page has not been opened.
- Recognized read-only company/world-news briefings separately from explicit external research. A mistaken research or general workspace tool selection for a clear briefing now returns connected news. Explicit web requests retain query review and fresh consent.
- Projected at most six company headlines and four Global Watch events, retaining publication/retrieval dates, publisher/source, observed reliability/confidence metadata and conditional exposure. The model projection is capped at 24,000 ASCII-encoded JSON bytes; large unrelated workspace details cannot discard this brief.
- Typed three-role answers use observed company headline IDs and fact tokens. Inventory-only answers cannot silently substitute for a news briefing. Global Watch is separate context, not proof of company exposure or a verified shipment delay.
- Added content-free voice tool diagnostics: tool/status, projected byte count, headline/watch counts and availability. No transcripts, queries, cookies, session IDs or source text enter those events.

## Verification

- The exact reported phrase failed in the new route tests before implementation.
- 747 backend demo tests passed, including both direct and mistaken research routes in all ten companies, missing news, source failure, credential stripping and oversized context retention.
- Web TypeScript/production build passed; frontend source is unchanged.
- Independent frozen review found no blocking issue. A delayed old-company read was discarded before model response or audio. Explicit research consent and deterministic action boundaries remain intact.

The first hosted release, r44, passed the exact controlled spoken question for Solara Cables, Atlas Petrochem and Meridian Pharma in fresh QA sessions without news preload. All three returned real audio, dated publishers/headlines and conditional industry-specific implications. Completed answer durations were 24.569 s, 25.793 s and 34.766 s respectively; these include listening/tool work and full spoken playback, not first-audio latency. Each model result retained six company headlines and four Global Watch events (about 15–16 KB). Mocked speech was not treated as answer-quality evidence.

Native Chrome also caught a mechanical typed fallback and exposed reference IDs during r44 testing. The final operator refinement retains the validated implication/confidence/next step, renders date-only metadata and keeps opaque citation IDs in structured evidence. The final 747-test suite and separate operator review passed.

An explicit native-browser web request displayed the exact electrical-cable-industry query, unchecked review box and disabled Research Web button. It was cancelled without approving external search. The original user browser session was left intact.

## Release limits

Use only the existing unupgraded trial, existing service/identity and resource caps. No new service, IAM, billing upgrade, increased provider budget or external supplier delivery is part of this change. The hourly news ingestion implementation is unchanged. The previously accepted r43 image is the compatible rollback target.

## Final hosted release

- Revision: `closed-source-quote-demo-00045-vbg`, serving 100% of traffic on both existing URLs.
- Image: `sha256:0608913176513aab1ca306e438c021b8e7f16bcedbfd0a00f002e2bb03b67166`.
- Build: `87a4a4ae-efd2-4605-aacb-a18dc212dbe2`.
- Allowlisted source bundle: `e4b45f89cf8a779a7272876d363127827889da07123d6d012e517be709eed4d5`.
- Exact spoken question passed again on r45 in a fresh Solara session without news preload: real audio, two dated publishers/developments, conditional volatility and supplier-verification next step. Completed answer 24.600 s; 932,162 audio bytes. App/live/helper are byte-identical to the three-company r44 acceptance.
- Explicit spoken research on r45 for Meridian returned an audible pharmaceutical-industry public query and “Say confirm research to start”; no confirmation was sent and no external research executed.
- Native Chrome r45 text answer rendered dated sources, current owned material facts, conditional implication and a supplier-pricing/lead-time check. All three references resolved to actual news links with publisher/retrieval metadata. No opaque IDs appeared in the new answer prose. Historical saved answers were preserved.
- Both URL health/readiness checks passed; Firestore readiness is true. Resource caps remain one instance, one CPU, 1 GiB, concurrency eight, timeout 240 s. The hourly ingestion job is unchanged.

Private QA receipts and screenshots remain excluded from Git under the project `output/evidence` folder, including `voice-news-acceptance-r45-solara-20261015.json`, `voice-news-acceptance-r44-atlas-20261015.json`, `voice-news-acceptance-r44-meridian-20261015.json`, `voice-research-proposal-r45-meridian-20261015.json` and `voice-news-r45-text-qa-20261015.jpg`. Controlled speech tests verify the provider/tool/audio path; this change does not constitute a new human-accent/noise microphone certification.
