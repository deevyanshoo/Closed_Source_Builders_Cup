# Taher hosted integration implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax. This is a reviewed implementation proposal; its tasks have not been implemented by this planning change.

**Goal:** Bring Taher’s useful carbon, market, programme and shipment work into current ZOORQ with correct calculations, durable company context and tested voice/UI workflows.

**Architecture:** Keep the current Python demo BFF, Cloud Run image, Firestore sessions, React shell and Gemini Live tool boundary. Reuse Taher’s numerical modules and views with corrective commits; the coordinator owns shared session/API/navigation/voice integration. Add no new cloud service or provider dependency.

**Tech stack:** Existing Python/FastAPI/Decimal, React/TypeScript/Vite/Vitest, Firestore session persistence, Gemini Live and Cloud Run.

**Spec:** [Integration review](../../Taher_Integration_Review_20261016.md), [Product design](../../Product_Design.md), [Work plan](../../Work_Plan.md).

## Global constraints

- Preserve Taher’s baseline, commits and authorship; do not force-push or bulk-merge old proposals.
- Start from reviewed `06ccd7118dba8ad6f25ad55f9f8bf5e9bd8caaa3`; recheck remote heads before using the pinned PRs in the review record. A changed head requires review of its delta.
- Keep the current company hub, five persistent destinations, Light/Dark design, Thinking Orbs and voice/research consent lifecycle.
- Money and quantities cross boundaries as decimal strings with explicit currency/unit. No invented mass, supplier price, eligibility or currentness.
- Synthetic quotes, companies, events and buyer assumptions remain labelled; published factors and observed market data retain dated provenance.
- Same session/company/context/plan-version validation for text, voice, UI and export. Critical business changes retain reviewed proposals and idempotency.
- Existing unupgraded trial credits only. Preserve current service identity, environment, 1 CPU / 1 GiB, revision max one instance, concurrency eight and timeout 240 seconds. No IAM/billing change, new database, paid dataset, supplier delivery or purchase.
- Root owns Git integration, shared browser/provider state and deployment. Module implementers do not edit shared `app.py`, `sessions.py`, `demo-page.tsx`, `demo-api.ts`, `zoorq-workspace.tsx`, `operator.py` or `live_voice.py` concurrently.

## Review focus

1. Overlapping factor boundaries or mismatched material/geography must not become a complete emissions total; Task 2 pins this.
2. Count/volume units and missing freight must not silently become mass or zero emissions; Task 2 pins this.
3. Company A→B→A, session replacement, dirty buyer inputs and a slow response must not resurrect an obsolete result; Tasks 3–5 pin this.
4. Cold Cloud Run restore/export must retain the exact current assessment without exposing another company’s record; Task 3 pins this.
5. Voice actions and programme amounts must not exceed source evidence, current plan or user approval; Tasks 6–8 pin this.

## Ownership and dependencies

Task 1 → parallel module work in Tasks 2, 4 and 6 → coordinator integration in Tasks 3, 5 and 7 → independent review and release in Task 8. Carbon persistence can proceed after its contract is fixed; reviewers do not implement their own corrections. Suggested lanes: carbon numerical worker, market/polish worker, dispatch/programme worker; coordinator handles shared files. Keep programme benefits gated if their source review outlasts the core release.

### Task 1: Pin the integration baseline and contracts

**Files:** reviewed source refs and `docs/reviews/2026-10-16-taher/{pr-heads,verification}.json`; create `docs/contracts/taher-hosted-integration.md`.

**Interfaces:** retain `GET /api/demo/carbon/factors`, `POST /api/demo/carbon/estimate`, `GET /api/demo/country-pricing`. The carbon POST keeps `plan_id`, `legs_by_offer`, `expected_context_version` and optionally adds `mass_by_offer: Record<offerId, {mass_kg: string, basis: string}>`; IDs must belong to the active plan. Add `GET /api/demo/dispatch` and `GET /api/demo/dispatch/{shipment_id}` using the current cookie/session helper. Programme endpoints remain those supplied in #115. Every returned user-specific wrapper carries `company_id`, `context_version` and relevant `plan_id`; the client captures its local session identity without sending a session token to the model.

- [ ] Recheck current deployed revision, remote base/heads and clean working state. Create an isolated integration branch from the reviewed current release, retaining r61 rollback metadata.
- [ ] Record exact request/response fixtures for component emissions, market reference, scoped shipments and programme discovery before dispatching workers. Schema additions are backward compatible; old persisted sessions default new fields safely.
- [ ] Define a persisted `carbon_assessment` record containing company/plan/offer versions, factor/calculation versions, entered legs and mass basis, result and timestamps. Define a transport/production boundary compatibility result with `combined_status` and nullable `total_kg_co2e`.
- [ ] Define client request ownership as captured session identity + company + context version + generation + plan ID when applicable. Define aborted/superseded results as ignored, never displayed.
- [ ] Integrate the reviewed source commits in bounded increments with explicit conflict resolution into current components. Preserve their authorship and use separate corrective commits. Do not merge #106/#118 into main merely to make a feature branch possible; main consolidation has its own combined gate.

### Task 2: Repair the carbon calculation and coverage model

**Module worker files:** `procurement-engine/src/procure/carbon/calculator.py`, `config/carbon_factors_v1.json`, `tests/test_carbon_calculator.py`; new `tests/test_carbon_boundaries.py`.

**Interfaces:** extend the existing entry points as `estimate(*, registry, material_id, quantity, unit, legs, mass_override_kg: str | None = None, mass_basis: str | None = None) -> dict` and `compare_plan(registry, plan, legs_by_offer, mass_by_offer: dict | None = None) -> dict`. The optional mass is explicit shipping mass for non-mass quantities; never infer it. Return component values and applicability, separate transport and material boundaries, and a combined total only when compatibility is established. Shipping mass does not by itself establish the composition needed for a material-production factor.

- [ ] Add failing tests `test_overlapping_point_of_sale_transport_has_no_combined_total`, `test_pet_including_forming_is_not_exact_resin`, `test_partial_or_missing_transport_has_no_whole_total`, `test_non_mass_quantity_requires_explicit_mass_basis`, and `test_each_catalogue_material_has_an_explicit_coverage_state`.
- [ ] Pin arithmetic: 8 tonnes × 1,000 km × `0.01612` = `128.96` kg CO2e freight; with the existing PET category, its `30892.66008` material proxy must remain separate and combined total null. Zero-entered-distance is distinguishable from missing distance.
- [ ] Add registry metadata for source geography, product match and included life-cycle stages. Retain verified values/cells/hash and attribution. Classify the current PET factor as a formed-product proxy, not exact raw resin. Do not add broad material mappings without appropriate evidence.
- [ ] Validate positive/finite mass inputs and bounded leg counts/decimal text using existing service limits; preserve unsupported-factor and unit-mismatch outcomes. A user-entered shipping mass is labelled and bound to its offer/quantity snapshot.
- [ ] Run the new tests red before the change, then all carbon tests green. Hand off the exact response examples and uncovered material states; no shared-file edits.

### Task 3: Persist and scope carbon in the hosted BFF

**Coordinator files:** `demo/carbon_api.py`, `demo/sessions.py`, `demo/app.py`, `demo/session_persistence.py` only if validation requires it; tests `test_demo_carbon_app.py`, new `test_demo_carbon_persistence.py` and `test_demo_carbon_context.py`.

**Interfaces:** the Task 1 `carbon_assessment` belongs to the company workspace and active authoritative plan. UI, Operator and export all read this same record; do not maintain independent computed copies. Add `current_carbon_assessment(value, active_plan) -> dict | None` and use it consistently.

- [ ] Add failing cold-restore/export and company-switch/revisit cases using `MemorySessionPersistence` through a fresh `SessionStore`, plus old-session compatibility and malformed/oversized stored-record rejection.
- [ ] Add failing cases for replaced quote versions, changed/recovery plan, other-company plan, invalid context and cancelled pending request. No current carbon result may survive a mismatched plan or versions as an actionable result.
- [ ] Persist only the bounded latest assessment per company/material plan, using the existing session serialization/CAS/budget rules; do not add an unlimited history array. Reset clears it; a revisit can expose valid saved evidence through a fresh current-context wrapper.
- [ ] Return whole-total null/incomplete status and reasons through API and JSON export. A missing factor file disables only carbon routes, with a clear unavailable response.
- [ ] Run carbon API, context, persistence, export and session regressions green. Independently review state changes before proceeding.

### Task 4: Correct market references and small polish changes

**Module worker files:** `demo/country_pricing.py`, `config/commodity_benchmarks_v1.json`, `apps/web/src/demo-country-pricing.{tsx,css,test.tsx}`, `demo-display.ts`, `demo-polish.test.tsx`; Python `test_demo_country_pricing.py`, `test_demo_polish.py`. Coordinator owns call sites and shared types.

**Interfaces:** `build_country_pricing(...)` continues to return dated global benchmarks and separate accepted quote items. Add company/context provenance from the coordinator wrapper. The panel receives the Task 1 request ownership token, selected material and a currentness guard.

- [ ] Add failing A→B→A/session-replacement and superseded-fetch tests; only the current request may publish a result. Clear stale parent results when the captured scope changes.
- [ ] Add failing tests for multiple priced items in a saved quote, zero/negative/non-finite FX, mixed providers/dates and unavailable company coverage. Exercise all ten company profiles without guessing benchmark mappings.
- [ ] Label the view **Market reference**; show global series definition, month, original unit, conversion date and source. Supplier origin belongs to separate synthetic quote rows. Currency conversion must not imply a local observed price or live intraday feed.
- [ ] Adopt order-ID wrapping, accurate total-cost copy and decimal formatting from #113. Use **Comparison unit price** for normalized/reporting-currency values; original quote values keep their document currency/unit. Retain ZOORQ’s title and exact threshold arithmetic.
- [ ] Preserve source checksums and attribution; record applicable dataset terms and any exceptions before redistribution. Run focused web/backend tests and build green.

### Task 5: Fit emissions and markets into the current UX

**Coordinator files:** `apps/web/src/demo-page.tsx`, `demo-api.ts`, `zoorq-workspace.tsx`, `demo-carbon.tsx`, `demo-carbon.css`; tests `demo-page.test.tsx`, `demo-carbon.test.tsx`, new `demo-comparison-context.test.tsx`.

**Interfaces:** within the existing Compare stage, introduce `ProcurementResultTab = 'cost' | 'carbon' | 'market'`, defaulting to Cost. Keep the four procurement stages and five primary destinations. The Carbon panel consumes server results and full currentness/dirty status, never performs authoritative math in JavaScript.

- [ ] Add failing tests that editing buyer inputs blocks estimation and labels prior evidence stale; a delayed carbon result cannot replace the active plan’s result. Gate native/typed entry points consistently through the coordinator state.
- [ ] Render Cost / Emissions / Market reference as compact result views; keep source/formula detail in accessible disclosures. Do not append legacy `04b` sections or replace the current header/Operator.
- [ ] Show component estimates, coverage reasons and explicit missing mass/route inputs. Keep partials and proxies visibly distinct from a complete footprint. Export must match the exact displayed assessment.
- [ ] Test Light/Dark, keyboard focus, 390 × 844 and 1440 × 900; primary controls remain reachable and headings clear the 58 px persistent header.

### Task 6: Adapt dispatch and programmes as current company capabilities

**Module worker files:** reuse `dispatch/{read_models,replay,summary}.py`, create `demo/dispatch_view.py`, `apps/web/src/demo-dispatch.tsx` and tests; adapt `carbon/schemes.py`, `config/carbon_schemes_v1.json`, `demo-schemes.tsx` and their tests. Coordinator supplies session/API/navigation bindings.

**Interfaces:** `build_dispatch_view(company: dict, offers: list[dict], plan: dict | None, recovery: dict | None) -> dict` returns company/order/offer-linked synthetic shipments with evidence status, using immutable declared fixture milestones and the current persisted recovery scenario; it does not change arrivals or accept model-generated IDs. Do not introduce a live event-ingestion service in this increment. `find_schemes(...)` returns source-linked possible matches with calculation availability per programme, effective/source dates and missing inputs.

- [ ] Add failing all-ten-company tests showing no foreign shipment/order IDs. Map synthetic fixtures to current company/order/offer IDs and preserve their declared scenario provenance. A missing link remains unavailable rather than receiving an arbitrary shipment.
- [ ] Add failing direct `/dispatch` versus query-entry navigation tests. Determine supported destinations from the deployed app; never infer capability from `?from=demo`. The coordinator redirects the legacy hosted entry to the current Supply/Shipments view.
- [ ] Connect an explicitly selected, current shipment delay to the existing reviewed recovery proposal using a validated shipment→offer mapping. Hazard/news exposure can suggest checking a route; it cannot become a confirmed delay. Duplicate/reordered events and repeated approvals must not duplicate changes.
- [ ] If reusing route/hazard evidence, independently verify #72’s registry claims and update consumers/fixtures together. The authority page was inaccessible during this review; a mirror-labelled port code is not a new independent approval. Do not start another live feed to satisfy a fixture demo.
- [ ] Add failing programme cases for ineligible/unknown enterprise status, missing programme validity, arbitrary ZED fee, CBAM unknown quarter/CN/importer inputs, unsupported SIDBI terms, unissued credits and negative interest difference. Correct ZED’s women-owned rule against the official amendment, but do not activate fee-based benefits until a current schedule is archived.
- [ ] Release verified programme discovery with benefit status unavailable wherever rules are incomplete. Disable unsupported numeric calculations on the backend as well as in the UI. Credit-value sensitivity is not realised savings, and loan interest difference is not an approved offer or amortised benefit.
- [ ] Run new and existing module tests green; hand off only supported data/interaction states.

### Task 7: Connect Supply and the text/voice Operator

**Coordinator files:** `demo/app.py`, `demo/agent_tools.py`, `demo/operator.py`, `demo/live_voice.py`, `demo/sessions.py`; `apps/web/src/demo-page.tsx`, `demo-api.ts`, `zoorq-workspace.tsx`, `demo-operator.tsx`; focused voice/action/context tests.

**Interfaces:** Supply gets `SupplyTab = 'inventory' | 'shipments'`. Add bounded read-only native tools `read_carbon`, `read_market_reference`, `read_shipments`, `read_programmes` with no caller-selected session/company/plan IDs; each resolves the current server context. Add `estimate_carbon` with only `legs_by_offer` and optional `mass_by_offer` from Task 1, resolving plan/context server-side and requiring explicitly supplied or referenced recorded inputs. Reuse existing recovery proposal/confirm machinery for changes. Numerical requests use the same calculation service and current-plan rules as UI; the model does not compute authoritative results.

- [ ] Add failing tool tests for missing assessment, unsupported material, other-company shipment, stale context and changed plan. Model-visible results exclude credentials and session tokens.
- [ ] Add failing estimation-tool cases for guessed distance/mass and unknown offer IDs; accept only inputs explicitly stated by the user or selected from the current recorded scenario. Update the exact bounded tool-catalog tests alongside each declaration.
- [ ] Typed and spoken requests read the same evidence, briefly explain impact/limits and navigate to the matching view. Unknown distances, mass or programme eligibility prompt for clarification; they are not filled by the model.
- [ ] Any proposed delay/replan uses current accepted quotes and human review. Preserve external-research consent and RFQ-vs-simulated-email routing. New read tools must not bypass or weaken those boundaries.
- [ ] Verify cancellation, context switch during a tool call, duplicate approval and failed-tool speech suppression. Complete read/action/voice suites and independent review before release.

### Task 8: Verify the combined release, deploy and reconcile Git

**Files:** `deploy/judge-demo/prepare_package.py`, existing smoke/audit scripts, new source/provenance regression receipts, release documentation and isolated QA evidence.

- [ ] Build the actual combined web assets, then run package tests for all carbon/scheme/benchmark config and Python modules. Run all web tests, session-race/typecheck/build and all `test_demo*.py`, new state tests and relevant dispatch numerical tests. Do not count standalone PR tests as combined acceptance.
- [ ] Reconcile the broader repository dependency and forecast-integrity failures separately. Any required combined gate must pass or have an explicit reviewed scope exception; do not silently skip red tests or claim a full-suite pass.
- [ ] Independently audit all ten companies and 52 material availability states, desktop/mobile and both themes. Exercise one correct mass calculation, one counted-unit clarification, one missing/proxy factor, a stale/dirty plan, company A→B→A, persisted cold restore/export, direct dispatch entry and a connected delay→recovery→unsent RFQ case.
- [ ] Test actual Gemini text and completed voice turns: ask about emissions, market sources and shipment exposure; propose and review a delay; confirm research aloud only after its query is disclosed. Record first audio and terminal completion separately, orb states and End cleanup. Controlled audio does not prove physical microphone acceptance; obtain that evidence separately where possible.
- [ ] Record independent findings and have separate implementers correct them. Inspect final exact-revision screenshots and source/export matches before promotion.
- [ ] Build one allowlisted immutable image, retain r61 rollback, deploy to the same service with unchanged cost/security settings, and verify both health addresses, Firestore restore, exact revision/traffic and representative hosted transactions. If a blocker remains, retain the known-good image or disable the affected feature at the backend.
- [ ] Push the reviewed integration branch with a clean working tree and update its PR/evidence. Consolidate main through reviewed, combined-tested increments, preserving authorship and branch dependencies. Do not close Taher’s issues until hosted acceptance exists. Final submission/publicity remain separate actions.

## Completion criteria

All integrated views share the company/material/plan context; authoritative calculations and observed data have explicit provenance and limits; state survives hosted restore; Operator reads and proposes through the same contracts; unsafe/unsupported programme amounts are unavailable; no current UX/voice regressions; exact hosted acceptance and clean Git are recorded. Broad ten-company material coverage, real tracking, certified footprints and realised financial savings are not implied by a working demo screen.
