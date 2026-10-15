# Responsive company-aware research release — 15 October 2026

Public research now prepares the selected industry in the exact query before approval. A generic Strait of Hormuz question in Meridian Pharma asks about pharmaceutical manufacturing; Atlas asks about petrochemicals. All ten company query paths passed hosted acceptance. Explicit questions about another industry retain that topic. Private names, contacts and quantities are rejected before reformulation; public dates and model numbers remain usable. The Search adapter receives only the reviewed query, reuses its connection client and makes one attempt.

Voice drains Gemini Live events while consent verification and research run independently. Progress distinguishes checking approval, a durably claimed search, and completion. The current exact query must finish its readout before fresh finalized positive speech can approve it. Expiry, company/context, negation, malformed activity, replacement and single-claim checks remain enforced. Duplicate or empty completion captions within the same speech segment preserve the pending verifier; new speech supersedes it. The verifier receives at most 250 ms leading audio after the prompt and previous speech, within the eight-second clip cap. Raw audio stays transient. Replayed model calls receive read-only results instead of repeating effects, and a delayed research result settles the orb without another model turn.

Prepared operator context uses an immutable per-process LRU with an opaque session scope, company/revision/business-state fingerprint, 64-entry cap, 2 MiB serialized payload cap and 60-second TTL. Only derived risk-check timestamps are excluded from both fingerprint and prompt. News, risk, quote and plan changes still invalidate it. Model answers, Search results and consent decisions are excluded. Existing GCS hourly news snapshots and Firestore private chat history continue. Hosted logs observed a 276,223-byte context built in 8 ms, then reused on a cache hit without rebuilding. Vertex token-cache metadata was unavailable; no token-cache saving is claimed. Memorystore is deferred because this single-instance workload showed no material need for an additional running service.

## Deployment

- Runtime source: `f2e3c88039ad6cbe47c326eca30f56c69521356b`.
- Build: `eae041ec-4ca6-40b4-9774-387aedfdbb0b`, SUCCESS.
- Image: `sha256:af0ae98959467f57bbfb80ed2f5928283301785b7b298c7842655b08c717d3f2`.
- Serving revision: `closed-source-quote-demo-00039-mg4`, 100% traffic; both existing URLs report ready with Firestore.
- Bundle: 313 files, 3,165,038 bytes; manifest `6f9c54de7ddfdc747617f3f7ae2f32e88c1c56e6945b81a899c2a27b262446e1`.
- Existing hourly collector uses the same image. Service identity, max 1 instance, 1 CPU/1 GiB, concurrency 8, 240-second timeout, worker limits and hourly schedules remain unchanged.

## Validation and limits

Final source passed **602 backend demo tests**. Web **359 passed / 6 existing skips**; session-race, typecheck and production build passed. Independent Antigravity reviews covered frontend completion/progress, consent/replay, industry privacy and cache bounds, with follow-up regressions for actual hosted failures.

The final Chrome test used the controlled “Confirmed” recording after the exact pharmaceutical query readout. One research result returned four sources, and the bot spoke three brief industry-specific sentences. Feedback appeared 1,050 ms after the recording ended, Search started at 2,952 ms, the result arrived at 9,391 ms, and first answer audio at 10,070 ms. A subsequent spoken inventory question returned active ingredient, sterile vial and medical ethanol stock-cover facts on the same socket. End closed the one socket and microphone track. Test instrumentation was removed, and the research result remained in ordinary chat after reload. These are controlled-speech and browser-pipeline checks, not a human microphone-quality walkthrough or a universal latency guarantee.

On the otherwise byte-identical revision 36 runtime, all ten company preparations took 192–453 ms; approved petrochemical text research took 6,453 ms and returned four sources. Revision 37 native voice passed with checking at 458 ms, start at 2,153 ms, result at 5,180 ms and audio at 5,888 ms. Only `live_voice.py` changed in subsequent runtime packages. Failed intermediate voice receipts remain retained as failures; they are not represented as successful acceptance.

Sanitized evidence is under project `output/evidence`: `research-context-final-preroll-20261015.xml`, `research-context-text-hosted-20261015.json`, `research-context-voice-hosted-v2-20261015.json`, `research-context-final-browser-20261015.json`, `research-context-final-r39-stage-metrics-20261015.json`, health/service/job receipts and UI screenshots. Research accuracy still depends on dated public sources and provider availability; observed headline exposure is not a confirmed supplier delay. RFQs remain unsent and emails simulated.

## Cost and rollback

Authenticated unupgraded Free Trial was verified with INR 28,259.95 credits and 64 days remaining before deployment. Existing credits fund normal build, model, storage and Cloud Run usage. No billing upgrade, new IAM grant, public service, schedule or Memorystore instance was created.

Rollback: route the service to `closed-source-quote-demo-00035-wdg` and restore the hourly job image to `sha256:1e19a7434b3508045988ef1958ecfa5da086ec8a2a8cbbf803fc400e3635a8f4`. No IAM or schema migration is required.

[Live portal](https://closed-source-quote-demo-ryoeyuqvta-el.a.run.app/demo) · [Existing draft PR 106](https://github.com/deevyanshoo/Closed_Source_Builders_Cup/pull/106). Main and published history are preserved for the broader branch review.
