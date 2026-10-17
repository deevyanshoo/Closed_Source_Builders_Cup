# ZOORQ hosted judge demo: deployment and operator guide

**Current destination:** https://zoorq-963802031195.asia-south1.run.app (`/demo`). Identify the exact active ZOORQ revision from `/-/health` and the Cloud Run release record before testing; this guide does not pin a future revision. Current verification and remaining voice acceptance are recorded in the [latest QA report](../../docs/ZOORQ_QA_REPAIR_20261017.md). The [earlier judge release report](../../docs/releases/2026-10-17-zoorq-judge-experience.md) retains its historical results and failures. The older `closed-source-quote-demo` service is a retained rollback option; check its health before using it.

## What the demo does

- Ten fictional company profiles span 52 synthetic material scenarios. Every company has rules-based reviewed quote, comparison/plan, delay-recovery and **unsent** RFQ coverage. Financial and operational values in these workspaces are demo assumptions, not live customer records or market offers. Company switches carry an owned context version; stale work is rejected.
- Typed Operator and native Gemini Live voice share validated company, session and action boundaries. Internal inventory, shipment and current news reads come first. An explicit material delay can return a pure, read-only profile or reviewed-baseline preview; it does not save a scenario, apply recovery or draft an RFQ. Solara's `judge/start` seeds a deterministic reviewed baseline once in an untouched flagship workspace; other profiles are not overwritten.
- Fresh external research requires a bounded public proposal for the current company and context and explicit confirmation of the exact reviewed query. In voice, the query must be read aloud and a separate affirmative spoken response independently verified before execution. Source receipts distinguish actual successful model/provider work from rules-based results. Failed or unavailable sources remain visible.
- Judge routes and maps depict owned, declared display corridors. A route line does **not** prove a supplier, offer, carrier, shipment event or chosen recovery path. Actual shipment facts come from the owned shipment workspace and its evidence. The dated Suez replay and Hindi case are labelled historical or simulated where appropriate. A browser Maps key only enables display.
- Email actions populate a **simulated outbox** (`external_delivery: false`); RFQs remain drafts. The demo has no external supplier message or purchase delivery path.

## State and public information

The hosted configuration uses Firestore for anonymous session/workspace persistence across instance restarts, with a seven-day maximum session lifetime and shared request/model/research budgets. A fresh browser gets its own session; stored company state and consent remain bound to that session. Check `/-/health` for `ready: true`, `persistence: firestore` and the exact revision before using a hosted audit. Persistence does not turn a hypothetical delay into an observed event.

The public-news path reads bounded hourly GCS snapshots when `NEWS_SNAPSHOT_BUCKET` is configured. News and FX collection have separate success times and explicit fresh/partial/stale/unavailable states; a failed refresh may retain a dated stale result. Global Watch can inspect current company exposure while the workspace is open. Hourly snapshot collection is durable; it is not continuous background monitoring of each company's private business events. Source headlines indicate possible exposure, never a confirmed shipment delay or price forecast. Check source dates and collection health in the workspace.

## Release packaging and runtime

Build the reviewed web assets first. `prepare_package.py DESTINATION` creates a new directory only, with an SHA-256 `source-manifest.json`. Its exact allowlist is `procurement-engine/src`, `procurement-engine/demo_fixtures`, `apps/web/dist`, the deployment Dockerfile/requirements, and these config files: `materials.yaml`, `assumptions.yaml`, `iso3166_alpha2.csv`, `carbon_factors_v1.json`, `route_display_v1.json`, `judge_suez_2021_v1.json`, `judge_hindi_case_v1.json`, `locations_v1.json`, `news_sources_v1.json`. Review the manifest and package contents before Cloud Build; do not add local data, credentials, logs or repository metadata.

The intended Cloud Run envelope is **min instances 0, max instances 1, 1 CPU, 1 GiB memory, concurrency 8, timeout 3,600 seconds**. Use trial credits only; no paid upgrade is authorized. Set provider project/location and Vertex settings through runtime configuration, with `DEMO_SESSION_BACKEND=firestore` and the approved GCS snapshot bucket. Keep the Maps browser key in runtime configuration only, with Maps API and HTTP referrer restrictions; never place its value in source, receipts or this guide. `/api/demo/judge/config` supplies the restricted browser key to the current browser session, along with the enabled state; the key is intentionally client-visible and its API/referrer restrictions remain the protection boundary. Reconcile these intended settings with the actual Cloud Run revision before declaring deployment complete.

## Continuous voice

Voice has no fixed application conversation duration. A healthy conversation keeps its microphone while Gemini connections renew using fresh model sessions and context compression. Bounded completed current-company history is supplied as passive configuration context, never queued again as a user message. Provider resumption tokens are not reused. Handoffs clear pending consent and require a fresh utterance before new tools can run; actions with uncertain results are never automatically replayed.

Cloud Run WebSockets still have a per-request timeout. The 3,600-second runtime envelope is paired with bounded browser reconnection, preserving the open voice interface. End, company changes, expired authentication, terminal provider errors and exhausted usage budgets still stop voice safely. Startup and reconnection deadlines remain bounded; they are not conversation timers. Rolling audio/tool allowances and the existing company/process connection-start budgets remain in place. Provider renewals inside one socket do not create another visitor start. A persistent outage is reported instead of retried forever.

## Verification by the release owner

Each output below must be a **new, private path**. These commands are examples for the release owner after the exact hosted revision is known; they have not been run by this documentation update.

```sh
BASE=https://zoorq-963802031195.asia-south1.run.app
REV=replace-with-observed-revision
python deploy/judge-demo/ten_company_agent_audit.py --base "$BASE" --expected-revision "$REV" --output /private/tmp/zoorq-ten-company-new.json --max-typed-turns 0
python deploy/judge-demo/judge_experience_audit.py --base "$BASE" --expected-revision "$REV" --output /private/tmp/zoorq-judge-new.json
```

The first command exercises all ten rules-based quote/recovery/RFQ paths without typed model turns. The second checks exact health/Firestore readiness, catalogue, company-owned judge routes/evidence, context isolation, internal-first typed inventory and pure what-if previews. Both write bounded create-only receipts and fail on mismatched revisions. They do not prove a spoken Live exchange or fresh public research.

For one **controlled-speech** native Live turn, pass an owned mono PCM16, 16 kHz, 1–20 second WAV to the judge audit; use `--voice-kind what-if` with the corresponding owned WAV for the other path. This uses real hosted Gemini Live and existing trial credits:

```sh
python deploy/judge-demo/judge_experience_audit.py --base "$BASE" --expected-revision "$REV" --output /private/tmp/zoorq-world-voice-new.json --voice-wav .superpowers/sdd/2026-10-17-zoorq-judge-experience/voice-world-news.wav --voice-kind world-news
```

The separate `deploy/judge-demo/voice_research_consent_audit.py` utility is the explicit, chargeable two-turn public-research check. It requires `--execute-public-research`, `--research-wav`, `--consent-wav`, `--base`, `--expected-revision` and a new `--output`; only the release owner should run it after review. It must observe a real proposal, spoken confirmation, research progress/completion and a sourced answer. Do not use an HTTP seed or a text confirmation as spoken-consent evidence.

Controlled WAV receipts prove only the submitted audio and hosted tool/answer path. A physical microphone, browser permission, playback and user-visible interruption check require separate hands-on browser evidence. The old recorded push-to-talk STT/TTS walkthrough is legacy evidence and does not establish acceptance of native Gemini Live. Provider calls, fresh source timestamps, consent and final answers require real hosted receipts and human review; local deterministic checks cannot establish them. Keep exact release receipts and history in the release report, not in this guide.
