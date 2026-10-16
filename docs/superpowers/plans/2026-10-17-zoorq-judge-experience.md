# ZOORQ Judge Experience Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. This approved scope has now been implemented and deployed; see the execution status and release evidence below.

**Goal:** Let a judge open a prepared ZOORQ company, inspect grounded evidence, preview a route-matched disruption and compare a read-only recovery before any reviewed action.

**Architecture:** Keep the current FastAPI demo BFF, Firestore session boundary, React SPA, Gemini Live bridge and numerical engines. A shared internal-first resolver selects current workspace/cached evidence before consented public research; pure impact/replay and display-route modules feed the existing reviewed recovery flow. The coordinator integrates shared state, voice, API and a new `zoorq` Cloud Run service; three disjoint Codex lanes produce reviewable modules in parallel.

**Tech stack:** Existing Python 3.10+/FastAPI/Decimal/pytest, React 19/TypeScript/Vite/Vitest, Gemini via Vertex AI, Gemini Live, Cloud Run/Firestore; optional map visualization using the already enabled Maps JavaScript API and existing restricted browser key. No new runtime TTS or numerical engine.

**Spec:** [Judge experience design](../specs/2026-10-17-zoorq-judge-experience-design.md).


## Execution status — 17 October 2026

Implementation and deployment are delivered at `zoorq-judge-b64eaaf`. The [reconciled release report](../../releases/2026-10-17-zoorq-judge-experience.md) is the authoritative per-revision evidence. All ten companies and 52 material read paths passed on the final revision; the earlier c7a08e0 runtime passed the ten-company workflow and native news/delay checks. Only the research/voice provenance instructions changed between these two images.

Formal acceptance remains partial: spoken research completed consent, research, answer audio and durable-history checks, but its strict final quality gate is red because it omitted the displayed lookup date aloud. Physical human-microphone/interruption QA and Lighthouse scoring remain unverified, and the full backend repository suite is not green. These are recorded exceptions, not completed gates. Original task checklists below record scope; the release report distinguishes executed checks from passing checks.

## Global constraints

- Base from clean `feature/zoorq-taher-integration` at `40fcc553aead39a3f3951fc741ba48c052a901d4`; reconcile remote and user changes before execution. Preserve Taher's history and r65/old service rollback.
- This document is a plan only. Do not treat r74 targeted/inherited QA as new-feature acceptance. Video recording, outreach and final submission are out of scope. Arbitrary-company onboarding is an approved-feedback item deferred to a separate milestone; keep all ten existing profiles discoverable.
- Keep one shared top disclosure. Expose actual missing data, source dates, planned status and action/consent gates where needed; never invent supplier news, vessel tracking, delay probability, loss or customer savings.
- All authoritative money uses Decimal strings with currency/unit/as-of; visual map distance never becomes an input to carbon or planning.
- Text and native Live voice share company/session/context/material/plan/offer-version ownership; preserve generations, expiry, cancellation, research claims and idempotency. No scenario preview applies a recovery or sends an RFQ.
- Existing active trial credits only. No new paid subscription, billing upgrade, enable-all API setting or key in source/docs/logs. Only the coordinator touches Maps Console, Cloud Run, credentials and shared browser/provider state.
- **Storage preflight:** the earlier 136–159 MiB condition has cleared; the coordinator measured about 4 GiB, and a 17 October check in this checkout measured 3.1 GiB available. Before install/build/package, remeasure available and peak required space; stop only if inadequate. Do not clear unrelated files to make a gate pass.
- Maps JavaScript API is enabled in `closed-source-builder-261007`. The GCP Console key named `ZOORQ browser maps` already has Maps JavaScript API and two existing demo-origin restrictions. The release owner may load its existing value into private runtime configuration; never print it or place it in source, bundles, reports or public receipts. Add the exact future `zoorq` origin to its website restrictions and verify those restrictions before loading Maps there; retain the list fallback until then.
- The final canonical URL, origin cookie/CSRF rules, Maps referrers and public invocation scope must be verified on the eventual `zoorq` URL. No unowned custom-domain purchase.

## Review focus

1. An utterance mixing “our stock” with “latest Suez news” must return local stock and identify the external evidence gap before proposing a public query; Task 1 tests this.
2. A previously approved research result for company A or an old context must not answer company B or an explicit fresh-search request; Tasks 1 and 5 test this.
3. Suez or generic port/shipping disruption without an owned material/route/location match and explicit duration must not produce a recovery preview; Tasks 2 and 5 test missing-input and unsupported behavior in text and voice across all ten companies.
4. Approximate ports or missing facility coordinates must not become authoritative road/sea distances or a fake full route; Task 3 tests this.
5. A bounded real Hindi Gemini/Vertex call must yield a sanitized persisted source/model/time/output/evidence receipt. A failed, skipped or deterministic-only run must not count; Tasks 2, 4 and 5 check this.

## Ownership and dependency graph

Coordinator reserves existing shared files: `demo/app.py`, `demo/sessions.py`, `demo/live_voice.py`, `demo/operator.py`, `demo/agent_tools.py`, `apps/web/src/demo-page.tsx`, `demo-api.ts`, `main.tsx`, `apps/web/index.html`, `deploy/judge-demo/prepare_package.py`, cloud/service and Git integration. Workers do not edit these concurrently. Independent reviewer is read-only and checks each handoff plus the combined branch. Start three **Codex-in-Antigravity** lanes after Task 0; no recursive teams.

| Lane | Disjoint write ownership | Dependency |
| --- | --- | --- |
| 1 — decision/evidence | New `demo/judge_{resolver,impact,replay,ai_receipts}.py`, curated public-evidence JSON under `procurement-engine/config/`, matching new Python tests | Task 0; resolver before impact/replay |
| 2 — map | New `demo/route_display.py`, `config/route_display_v1.json`, new `apps/web/src/zoorq-route-*` files and tests | Task 0 and contract; no shared page/API edits |
| 3 — entrance/presentation | New `apps/web/src/zoorq-judge-*` components/CSS/tests and share-card asset | Task 0 and contract; no main/index/demo-page edits |
| Coordinator | Shared contracts, integration, final metadata URL, release, receipts | Integrate reviewed outputs from Lanes 1–3 |

Task 0 → Tasks 1, 3 and 4 in parallel → Task 2 after Task 1 → Task 5 integration → Task 6 independent local/browser gate → Task 7 service/Maps/canonical setup → Task 8 final exact-revision QA and handoff. The optional Company Explorer polish follows the flagship entrance, never delays a truthful working baseline. Each lane stops at its file boundary and hands off diff, focused red/green output and unresolved gaps; the coordinator checks it before integration.

### Task 0: Pin contracts, evidence and storage preflight (coordinator)

**Files:** Modify only planning/contract fixtures during this task: `docs/contracts/taher-hosted-integration.md` if a new response is needed; create small versioned fixtures under `procurement-engine/tests/fixtures/` and `apps/web/src/` test fixtures after review. Read current `app.py:operator_snapshot/active_plan/execute_operator_turn/workspace_tools`, `operator.py:run_turn`, `research.py:prepare_research`, `recovery.py:compare`, `demo-page.tsx`, `demo-api.ts`, `main.tsx` and `index.html`.

**Interfaces:** Agree `resolve_judge_turn(snapshot: dict, message: str, *, now: datetime, fresh_requested: bool) -> dict` with primary `kind`, `company_id`, `context_version`, `evidence_refs`, `answer|missing_inputs|research_parameters|preview_request`, plus optional separately consentable `follow_up_research` when a mixed question has a local answer and a fresh public-evidence gap. Agree `build_judge_impact(snapshot: dict, recovery: dict, affected_order_ids: list[str]) -> dict`; `build_route_display(company: dict, route_id: str, material_id: str, buyer_location_index: int) -> dict`; and a bounded `JudgeReceipt` with actual model ID/version, provider call ID when returned, public input source ID, timestamp, sanitized output, status and evidence refs. Returned IDs are current-company only; no session token enters model or public evidence.

- [x] Reconfirm clean branch, current exact deployed image and protected configuration; record no new provider state in this step.
- [x] Remeasure free space immediately before build/package and estimate peak need; record a blocked build only if inadequate, without cleanup. Make the gate explicit before any worker runs tests that generate large artifacts.
- [x] Write representative contract fixtures for internal answer, missing input, research consent proposal, feasible/infeasible preview, generic port/shipping matched and unsupported route, map supported/partial/unavailable, AI receipt success/skipped, and stale context. Include stable IDs and Decimal string examples without real customer data.
- [x] Have the independent reviewer approve ownership/contract coherence before parallel code work.

### Task 1: Shared internal-first resolver (Lane 1)

**Files:** Create `procurement-engine/src/procure/demo/judge_resolver.py`; create `procurement-engine/tests/test_demo_judge_resolver.py`. Existing `operator.py`, `app.py` and `live_voice.py` are coordinator-owned and remain untouched here.

**Interfaces:** `resolve_judge_turn` consumes a server-owned `operator_snapshot` (including current news and approved completed research records supplied by coordinator), `message`, `now`, and `fresh_requested`; produces the Task 0 resolution. It may call current deterministic readers (`integration_tools.integration_outcome`, inventory scope/answer, `news_brief`) through narrow adapters, but may not call external research or mutate session. A `research_proposal` contains only the existing `research.prepare_research` validated public parameters.

- [x] Add failing tests for current stock/quote/carbon/shipment questions, mixed local-plus-latest-Suez question, cached News/Global Watch, relevant completed research, explicit fresh search, missing material/route/delay/location for a generic port/shipping disruption, company A→B and stale research, and provider-offline local answers.
- [x] Run `python -m pytest procurement-engine/tests/test_demo_judge_resolver.py -q` and record the expected failures before implementation.
- [x] Implement the smallest resolver with explicit ordered evidence categories; do not use a prompt to override the research-first bug. Reuse validated public query preparation only after evidence gap/fresh intent.
- [x] Re-run focused tests green, send diff and facts/consent limitations to coordinator and reviewer.

### Task 2: Read-only impact, dated replay and truthful AI receipt (Lane 1, after Task 1)

**Files:** Create `demo/judge_impact.py`, `judge_replay.py`, `judge_ai_receipts.py`; create `procurement-engine/config/judge_suez_2021_v1.json` and, only after a verified source is selected, `judge_hindi_case_v1.json`; create `procurement-engine/tests/test_demo_judge_{impact,replay,ai_receipts}.py`. Do not copy full copyrighted articles into fixtures.

**Interfaces:** `build_judge_impact(snapshot, recovery, affected_order_ids)` reads owned order IDs, compatible selected reviewed prices, baseline/scenario IDs and authoritative carbon; returns `gap_days`, `planned_spend_exposed`, `additional_recovery_cost`, `carbon`, each with unit/currency/as-of/status/reason. `replay_at(replay, as_of: datetime) -> dict` emits only evidence published by that instant and separates documented advisory from simulated alert/delay. `summarize_gemini_receipts(records: list[dict]) -> dict` counts distinct real input article IDs with successful recorded Gemini calls and retained evidence; it counts flagged IDs only from model-reviewed flags.

- [x] Add failing tests: supplier seven-day preview bound to selected shipment; Suez fourteen-day preview requires route/material match and explicit delay; generic port/shipping preview for each company requires owned location or route segment, material and duration and selects only matched shipments; missing/foreign/unknown/unsupported routes return questions or unavailable without guessing; duplicate orders do not double count; mismatched units/currency or absent price yield unavailable exposure; `cost_delta` never labelled loss; infeasible plan has no feasible purchase/total.
- [x] Add failing point-in-time replay tests using curated dated Maersk advisory sections: the living March 24 URL contains later updates, so each fact must be pinned to its own update date; future facts stay invisible, documented update/reaction dates remain distinct from the synthetic alert, and no three-week warning is claimed.
- [x] Add failing receipt tests for successful real Gemini input IDs/model/time/sanitized output/evidence, durable retrieval, duplicate IDs, skipped/error/deterministic-only runs and unsupported flags. Use the [30 May 2026 Hindi PIB cotton release](https://www.pib.gov.in/PressReleasePage.aspx?PRID=2267065&lang=2&reg=48) and [Gazette No. 19/2026-Customs](https://static.pib.gov.in/WriteReadData/specificdocs/documents/2026/may/doc2026530879601.pdf) as candidate source fixtures: cotton heading 5201 imported into India, stated duty and cess exemption 1 June–31 October 2026. Test original/publisher/date, English translation provenance and regional exposure versus supplier-specific claim; do not infer supplier stock/price or a Bangladesh import-duty change. If the source or real call is unusable, preserve an unavailable state and defer the AI moment.
- [x] Run focused new Python tests red, implement pure modules/curated metadata, then run them green. This lane uses recorded test doubles only; the coordinator owns the bounded real provider call and durable sanitized receipt in Task 5. Reviewer checks source chronology, Decimal/unit handling and missing states.

### Task 3: Versioned display routes and responsive map (Lane 2)

**Files:** Create `procurement-engine/config/route_display_v1.json`, `demo/route_display.py`, `procurement-engine/tests/test_demo_route_display.py`; create `apps/web/src/zoorq-route-map.tsx`, `zoorq-route-map.css`, `zoorq-route-map.test.tsx`. No edit to `locations_v1.json` or shared `demo-page.tsx` without coordinator review.

**Interfaces:** `build_route_display(company, route_id, material_id, buyer_location_index)` returns a versioned view with owned IDs, ordered `vendor/road/origin_port/sea/destination_port/road/buyer` segments, each `supported|approximate|unavailable`, source and precision flags; never emits planner/carbon distance inputs. `<ZoorqRouteMap view={...} reducedMotion={...} />` consumes that read-only view and renders an accessible list without Maps.

- [x] Add failing all-ten overview tests, multiple buyer-location selection, unknown material/route/foreign company, unverified port, missing vendor/facility coordinates, no invented exact location, and no visual distance in scenario inputs. Review eight approximate port records and mark any use as approximate.
- [x] Add failing UI tests for play/pause, speed, scrub, disruption/recovery overlay, `prefers-reduced-motion`, keyboard/mobile list fallback, Maps loading failure and context-switch cleanup. Animation must never mutate a plan or call a numerical endpoint.
- [x] Run focused Python/Vitest tests red, implement curated registry and map adapter, then run green. Use Maps JavaScript only in the first version; defer Routes API unless a separate endpoint/cost/coordinate review approves it. Hand off required loader/key configuration to coordinator without a key value.

### Task 4: Flagship entrance, receipts and sharing presentation (Lane 3)

**Files:** Create `apps/web/src/zoorq-judge-entry.tsx`, `zoorq-judge-entry.css`, `zoorq-judge-entry.test.tsx`, `zoorq-ai-receipts.tsx`, `zoorq-ai-receipts.test.tsx`, optional `zoorq-company-explorer.tsx` and test; create one real `apps/web/public/assets/zoorq-share-card` image with truthful branding and verified format. Coordinator owns `main.tsx`, `index.html`, `demo-page.tsx` and the final URL.

**Interfaces:** Entrance consumes only current `DemoSession`/`CompanyProfile` and a prepared baseline view from coordinator; receipts consume the Task 2 aggregate. All ten companies remain reachable. Company Explorer is profile navigation with consented public research through existing operator controls, not arbitrary onboarding.

- [x] Add failing component tests for a first visit with prepared flagship baseline, restored nonflagship session, no signup/upload prerequisite, one top disclosure, ten-company access, empty/failed AI receipts and no hard-coded article count or deterministic-as-AI claim.
- [x] Implement accessible quick path and truthful “Built with Gemini, Vertex AI and Cloud Run” roles; show model/ID/time/evidence only when server receipt exists. Create/inspect share-card dimensions/MIME/size and check no private session URL.
- [x] Run focused Vitest/typecheck after storage gate; hand off the component and asset to coordinator. Explorer polish is deferred if it threatens the primary journey.

### Task 5: Coordinator integration of typed, voice and browser state

**Files:** Modify `procurement-engine/src/procure/demo/{app.py,operator.py,live_voice.py,sessions.py,agent_tools.py}`, related existing native tests (`test_demo_operator_app.py`, `test_demo_live_voice_app.py`, `test_demo_recovery.py`); modify `apps/web/src/{demo-page.tsx,demo-api.ts,main.tsx}` and corresponding tests. Review `integration_tools.py`, `news_brief.py`, `carbon_api.py`, `dispatch_view.py` before use.

**Interfaces:** Bind `resolve_judge_turn` before existing `prepare_research` in both `execute_operator_turn` and `run_turn`; native `workspace_tools('prepare_research')` must ask the same resolver and return an internal read/preview or current consent proposal. One server-owned preview route may wrap Task 2 results and existing `recovery.compare`, with explicit current `shipment_id`, `baseline_plan_id`, offer versions, material/route segment/location/duration and idempotency key. Read-only result includes `company_id`, `context_version`, baseline/scenario IDs and no applied state. Add read-only route/AI-receipt endpoints only if existing session projection cannot carry bounded payloads. Browser captures session/company/context/plan/epoch and discards stale results.

- [x] Add failing native and typed app tests for identical local-first results; generic port/shipping location, duration and material prompts; unmatched/unsupported routes across all ten companies; repeated/expires/cancelled research approval; stale turn during company switch; native interruption; read-only preview versus reviewed apply/RFQ draft; invalid/foreign shipment; actual research only after newly confirmed exact public query.
- [x] Run one bounded real Gemini call through Vertex AI on the public Hindi PIB excerpt after source review. Persist a sanitized server-owned receipt in the existing durable session/evidence boundary with source/input ID, actual model ID/version, provider call ID when returned, call time, status, translation/summary output and evidence links; exclude raw provider payloads, secrets and private workspace data. Re-read it after persistence and show it in text/UI/voice only when successful. Count distinct successful model-reviewed input IDs and flags, never deterministic ranking or failed/skipped calls.
- [x] Add failing browser tests for default `/`→flagship `/demo`, idempotent fresh-session fixture bootstrap, restored user company, route-list fallback and stale response discard. Do not auto-approve a recovery, research or RFQ.
- [x] Integrate the three reviewed lane outputs through owned shared files, keeping existing action handlers and model-visible projections bounded. Run focused app, voice, session, recovery, page and API tests red then green.
- [ ] Run manual text/native parity on one supplier delay, one route-matched Suez assumption and one generic port/shipping disruption with explicit and missing inputs. Record actual tool/action state; do not use a provider retry to make a checker happy.

### Task 6: Combined review and local acceptance (coordinator + independent reviewer)

**Files:** Add scoped audit tests under `deploy/judge-demo/`; update `deploy/judge-demo/prepare_package.py` allowlist for route/replay/Hindi metadata and share-card asset only after source review. No raw provider receipt or credential in package.

- [x] Independent reviewer checks the combined diff for company isolation, consent, no action-on-read, chronology, route precision, money/carbon truth and Maps key handling. Fix findings before the full gate.
- [x] With storage gate satisfied, run new focused suites, then existing `python -m pytest procurement-engine/tests -q`, `npm test`, `npm run typecheck`, production build and package parity once on the integrated head. Record exact counts/commands and avoid repeating a passing full suite without a changed input.
- [ ] Check all ten route overviews and generic port/shipping matched, missing-input and unsupported behavior in text and voice; 52 material states where relevant, the flagged Hindi source case with a real sanitized Gemini/Vertex receipt or explicit unavailable result, Suez historical replay, reduced-motion/mobile/list states, keyboard access, simulated map errors and deliberate no-data outcomes.
- [ ] Measure first-ten-second entrance under documented device/network conditions and run Lighthouse/performance review. Obtain manual physical-microphone QA for any spoken claim, including interruption/company switch; controlled WAV is separately labelled. User interviews are planned only; a real user quote requires an actual interview and consent to quote, and no outreach occurs here.

### Task 7: New origin, Maps restrictions and canonical metadata (coordinator only)

**Files:** Modify `apps/web/index.html`, `apps/web/src/main.tsx` as needed; use final reviewed `apps/web/public/assets/zoorq-share-card.*`; update release runbook/receipts only. Console and Cloud Run state are coordinator-owned.

- [x] Reconcile current old service image/config and active trial-credit budget. Create separate Cloud Run service `zoorq` from the reviewed immutable image with the same service account, persistence, resource, concurrency, timeout and max-instance caps; keep old service/link untouched. Verify new URL and public invocation scope explicitly; do not assume a renamed service.
- [x] Check Maps JavaScript API quota/cost under existing trial credits. The API is already enabled and `ZOORQ browser maps` already has API and two existing demo-origin restrictions; add only the exact new `zoorq` origin to its website restrictions. Verify all origin/API restrictions and map behavior on the new URL before enabling the map there. Load the existing restricted key only through private runtime configuration; never print or commit it; retain the map list fallback until verified.
- [x] Put the *actual absolute new URL* in static canonical/OG/Twitter metadata and produce the correctly typed share card/noscript copy. Test raw built and hosted HTML/image without JavaScript, including content type, bytes, dimensions, no session query and no placeholder.
- [x] Test cookies, CSRF/origin, consent proposal/confirmation and Maps referrers on the new origin. Keep the old link healthy. A custom domain is not part of the critical path and is used only if already owned and separately authorized.

### Task 8: Exact-revision release and honest handoff (coordinator + reviewer)

**Files:** Existing `deploy/judge-demo/` smoke/audit runners and a new sanitized release report under `docs/`; private raw receipts outside Git.

- [x] After review and local gates, build one create-only allowlisted immutable package/image for the final source and deploy it to `zoorq` with reviewed caps. Verify Cloud Build receipt and immutable digest before any retry. Preserve previous `zoorq` revision and the old r74 service/r65 rollback.
- [ ] On the exact live revision, verify both old/new health, new traffic, ten company discovery, flagship first-ten-second journey, one real typed and one physical-mic voice scenario, one explicit research consent, one route-matched Suez preview, generic port/shipping matched and unsupported cases, map failure fallback, successful persisted Hindi Gemini receipt if enabled, static metadata and mobile/reduced-motion/light/dark. Preserve raw failures and separate manual interpretation.
- [x] Review and sanitize evidence, check image/package parity, secrets, source roles, origin restrictions and unchanged caps. Push only reviewed source/evidence on the feature branch and update its PR after authorization/review; do not infer a main merge, customer outcome, public repository change or final submission.

## Completion gate

The experience is complete only when a judge can reach a populated synthetic flagship without signup/upload, see dated evidence and honest AI roles, preview a matched and explicitly assumed disruption without action, compare sourced Decimal outcomes, use an accessible route visualization or list fallback, and separately review any change. New origin and Maps protections, all-ten discovery, exact hosted revision, physical-mic/manual QA and source/receipt integrity must be observed. If a fresh storage measurement, source licensing, new-origin Maps restriction or hosted origin blocks a step, report that exact gate and keep the last healthy service live.
