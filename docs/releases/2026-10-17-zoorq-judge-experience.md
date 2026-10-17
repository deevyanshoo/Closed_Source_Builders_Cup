# ZOORQ judge experience — 17 October 2026

Implemented and deployed with the QA exceptions below. This report supersedes earlier local-only integration notes; it does not claim every acceptance gate is green.

## Destination and scope

- Canonical demo: https://zoorq-963802031195.asia-south1.run.app
- Runtime source: `b64eaaf` on `feature/zoorq-taher-integration`.
- Cloud Build: `002bfea9-0259-46a2-9d74-47d990eac6f5`.
- Final revision: `zoorq-judge-b64eaaf`, 100% traffic; immutable image `sha256:f503076fc43bcd28b07806ca5bad52bd9db2067c1076cce3800286c715c21c80`. Both new service origins and the unchanged r74 fallback were checked healthy after deployment.
- Draft PR: https://github.com/deevyanshoo/Closed_Source_Builders_Cup/pull/127. No merge or competition submission performed.

## User-visible changes

The first visit opens a prepared Solara Cables dashboard without signup, upload, or a provider call to create its baseline. Restored company work is preserved and all ten companies remain reachable. The entrance connects the signal, current exposure, route, news and what-if paths, with readable stock cover, lead time and reviewed spend.

The Google map animates ships and trucks along curated company display corridors with playback, speed and scrubbing controls. It has an accessible itinerary fallback and explicit partial/unavailable states. Its geometry is illustrative and never becomes a planning/carbon distance input or evidence that a real shipment follows that route.

The operator first reads owned inventory, reviewed quotes, shipments, cached company news and relevant approved research. Supplier-delay hypotheses use a pure preview with the original reviewed baseline, explicit duration, owned material/offer/version/shipment and no saved recovery or action proposal. Explicit recovery preparation remains a separate reviewable action. Unsupported port or canal disruptions ask for the missing binding or assumption instead of inventing an affected shipment.

The historical Suez replay filters dated Maersk advisories by replay day and separates historical evidence from a possible planning response. LoomWorks can request an actual Gemini/Vertex Hindi cotton-policy interpretation with source, model, call/time and saved output. It distinguishes Indian regional policy from Bangladesh import duty or specific supplier availability.

Inventory evidence renders one current-company material table with units and readable numbers. Static canonical, description, Open Graph/Twitter metadata, no-script copy and a 1200×630 PNG make the shared link useful outside JavaScript clients. One top workspace disclosure remains; no claimed customer savings, real tracking or external RFQ/email delivery was introduced.

## Verification recorded

| Check | Observed result |
| --- | --- |
| New judge, map, integration, consent and native-tool focused backend suites | 130 passed; a subsequently added idempotent-preview/stale-material regression passed separately |
| Full web gate after final frontend change | 548 passed, 6 skipped; typecheck and production build passed |
| Package parity | 4 passed; final package has 355 files, 6,182,173 bytes; credential-pattern scan clear |
| Source bundle manifest | `1438a64cc68e69db77cabfb8359243d3090566baab0f7510f3fad63e19de3c87` |
| Research date/source guidance | 174 focused research/app/progress/consent tests passed; final instruction refinement also passed the 114-test research suite |
| Independent review | Runtime preview/consent hashes matched c7a08e0; final b64eaaf research/voice instruction review also passed. No P1/P2 finding in scoped reviews; UI/evidence review passed |
| Ten-company read paths | Final b64eaaf release passed 98 read-path requests across all 52 material scenarios, owned route/context checks and internal-first analytics; c7a08e0 also passed the 103-request run with spoken delay preview |
| Ten-company workflows | The c7a08e0 candidate passed all ten quote correction/save/plan, supplier-delay recovery and unsent RFQ replay/persistence paths, with zero typed model turns and no external delivery |
| Native world-news voice | The c7a08e0 candidate passed controlled speech; read_news answered from actual current feed without public research. First answer audio about 5.2 s; full spoken completion observed. Sources were compared against the company news feed |
| Browser desktop/mobile | All ten profiles checked at desktop 1200 px and mobile 390×844 in dark mode; body matched viewport. Final first-visit mobile facts, current-company evidence and keyboard replay checked; reduced-motion emulation disabled animation while retaining slider inspection, then was reset |
| Browser microphone | Chrome one-time hardware microphone permission, live Listening state and End cleanup observed; no physical human utterance exchange claimed |
| Hosted Hindi receipt | Actual gemini-3.5-flash-lite call `q4_SauPyMKvCi9sP8ZSGyAg` at `2026-10-16T20:57:17.085263+00:00`; one source interpreted, saved interpretation and receipt survived browser reload |
| Historical chronology | March 23 no advisory; March 24 one dated advisory; March 30 four dated updates, all observed through keyboard slider |
| Maps | Real Google map loaded on the new origin without auth/CSP errors; playback/speed/scrub observed, ship-to-truck leg changed at the end |
| Final native delay preview, spoken consent and revision checks | Supplier preview passed on c7a08e0: first audio 6.0 s, complete turn 24.1 s, no proposal/session/action or business-state change. Final b64eaaf consent research completed, spoke a sourced answer and persisted history; strict overall audit FAILS only because the lookup date was omitted aloud (details below) |

The final package differs from c7a08e0 in only `research.py` and `live_voice.py`, for the dated research instruction and shared spoken provenance guidance. Web assets, numerical engines, workflow handlers and route data are byte-identical, so the earlier scoped workflow/browser evidence is identified by revision rather than relabelled as a new run.

The final native repairs have failing regression evidence before implementation: the old direct recovery tool returned a proposal for a hypothetical question, and the independent consent verifier's recognized explicit phrase was absent from the strict affirmative list. The new read-only tool, unique owned binding and bounded phrase additions retain independent verification, negation, completed-query readback, current context, cancellation and replay protections. The controlled hosted failures remain preserved privately rather than overwritten.

## Spoken research: completed protocol, one red quality gate

On final `zoorq-judge-b64eaaf`, the controlled two-turn exchange read the exact public LME/copper/cable-manufacturing query before asking for consent. The new spoken affirmative was independently verified; progress proceeded `checking_consent → started → completed`. Research began about 6.1 seconds after consent input started and completed about 2.6 seconds later. The answer was dated in writing, referenced two returned public publishers, and the brief spoken response named Crux Investor and Mining.com.au and suggested verifying supplier premiums and lead times. The consent answer produced 138 audio frames (1,031,522 bytes); the full second turn completed in about 32.8 seconds, including input, verification, research, spoken answer and caption grace.

The same-query research result and sources were re-read from durable history; protected plan/recovery/RFQ/draft/business state remained unchanged. **The audit's overall result remains FAIL:** its sole final failure was `consent:turn / spoken_answer_undated`, because the short voice answer did not say the lookup date aloud. Both protocol turn rows passed. This is a recorded presentation exception, not an all-green voice-acceptance claim. The public audit is retained at `deploy/judge-demo/voice_research_consent_audit.py`; it requires an explicit research flag, creates private evidence, has no retries and preserves this strict final date check.

The current source instruction supplies the trusted UTC date, distinguishes lookup time from publication time, asks for actual publishers and conditional verification steps, and discourages treating secondary reporting as official exchange data. This is guidance, not semantic proof for every model response. Source links were resolved to their actual publishers. The Crux article was observed as dated 16 October 2026; the Mining.com.au article returned HTTP 403 to direct metadata inspection, so its publication date and quoted price were not independently verified. No protection was bypassed. The research result is news context; its figures do not replace reviewed quote inputs in the planner.

An earlier r4 research call returned Google HTTP 504 after about 12.8 seconds and was safely marked unavailable. No automatic retry, fabricated answer, wider consent or budget increase was introduced. Raw failures and the successful protocol traces remain separate private receipts.

## Infrastructure and cost boundary

Existing GCP project and active trial credits only; no billing upgrade or subscription was added. The last authenticated Console observation showed ₹27,504.69 credit and 63 days remaining. This is a point-in-time balance, not a spending forecast or guarantee. Cloud Run retains min 0/max 1, 1 CPU/1 GiB, concurrency 8 and 240-second timeout using the existing demo identity. Firestore and shared GCS hourly news snapshots are reused; no duplicate schedule, Cloud SQL or Memorystore was created.

The browser Maps key is runtime-configured and client-visible by design, restricted to Maps JavaScript and the exact old/new service origins. Its value is excluded from source, build assets, reports and receipts. The old `closed-source-quote-demo` r74 and earlier ZOORQ revisions remain rollback options.

One Antigravity IDE instance and at most two Codex CLI workers were used. Heavy checks were serialized and cloud image builds kept local memory load low. No dependency installation was needed; the latest local check showed about 53% free memory and 10 GiB disk available.

## Limits and follow-up

The full backend repository gate is **not green**: 3,233 passed, 153 failed, 21 errors, 2 skipped and 251 subtests. No `test_demo_*` failure was observed in that run; failures included forecast/evaluation pins and unavailable forecasting/document dependencies. A partial old-HEAD comparison reproduced one pin issue, but does not establish that every failure is inherited. These failures were not hidden or “fixed” by changing forecast behavior in this UI/voice release.

The keyless PageSpeed/Lighthouse API returned 429; no Lighthouse score is claimed. Controlled WAVs and a browser Listening state are distinct from a human physical-microphone/interrupt/company-switch walkthrough. Full physical voice acceptance remains unverified. Arbitrary-company onboarding, user interviews and video recording are separate milestones; no real-user quote or three-week warning claim was invented.

Private create-only audit/build receipts and failed runs are retained outside Git in the release owner's private temporary directory. They contain no shared session token in this report. Use the current deployment guide for exact audit commands and the active health revision, not older local checkpoints.
