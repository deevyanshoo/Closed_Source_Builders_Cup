# Closed Source Daily Delivery Plan

> **For agentic workers:** For implementation, use superpowers:subagent-driven-development or superpowers:executing-plans for implementation tickets. This document is the delivery roadmap; each feature ticket must carry its contract, allowed files and acceptance checks before execution.

**Goal:** Build a connected manufacturing and global trade control room with evidence-backed sourcing, production and shipment recovery, market intelligence and conversational voice.

**Architecture:** Preserve Taher's Python procurement engine and history. Add a React workspace and Go API/workflow gateway, with shared contracts, Google AI and persistent state. Operational modules act on the same material/order/event graph.

**Tech stack:** React, TypeScript, React Router/Vite; Go; Python/FastAPI and Google ADK; Gemini, Chirp 3 STT and Google TTS; Cloud Run, Firebase Hosting, Storage, BigQuery and PostgreSQL subject to verified cost/quota.

**Spec:** [Product design, revision 2](Product_Design.md).

Revision 2, October 6, 2026. Approved for team publication and implementation on October 6, 2026. Ownership describes product domains, with both engineers responsible for implementation, tests, UI and documentation. Availability is not confirmed; these are aggressive integration targets. Start the bounded packages below; coordinate actual work blocks in their issues and adjust expansion breadth when evidence gates slip.

## Global constraints

- No fixed country, currency, supplier or material count in schemas. Catalog availability, observations, forecast eligibility and supplier quotes are distinct.
- Preserve baseline commit `223f99b7efa3164843eed1c46530ed83c5412ba3` and tag `baseline-2026-10-06`; retain existing endpoint/reference UI compatibility.
- React is the submitted product interface. Python owns numerical logic; Go owns public workflow/session boundaries. Money crosses service boundaries as decimal strings.
- Synthetic quotes, companies, plant and port events are labeled. Observed market history and actual Google model calls retain provenance.
- Voice and typed commands share validation, permissions and plan-version checks. No automated supplier communications or purchases in the demo.
- Working core October 9; expansion October 10–13; release candidate October 14; freeze October 16; early submission target October 17.
- Previously observed portal deadlines: team formation October 11 and prototype October 18, both 11:59 PM IST. Recheck before acting.
- This roadmap and domain ownership are approved. Feature work uses branches and reviewable pull requests. Final submission and repository visibility changes require separate authorization and verified receipts.

## Review focus

1. Decimal amounts, units and currencies survive browser/Go/Python round trips without loss or guessed defaults.
2. Replayed events, interrupted voice turns and repeated approvals cannot duplicate actions or apply to superseded plans.
3. Historical selection/calibration and covariates must be available before each evaluation origin.
4. Delivery delays and maintenance windows propagate through every affected inventory, production and dispatch period.
5. Missing evidence, noisy speech and provider failures produce correction/unavailable states rather than invented values.

## Domain ownership

| Domain | Lead | Responsibility |
|---|---|---|
| Platform and workspace | Divyanshu | Shared contracts, Go core, identity/persistence, React shell, GCP delivery and integration |
| Market and planning intelligence | Divyanshu | Currency/coverage, dated forecasting, sourcing optimization, production planning, export economics and carbon comparisons |
| Quote intelligence | Taher | Document/spreadsheet ingestion, Gemini extraction, evidence/conflict review, offer lifecycle and React workflow |
| Conversational operations | Taher | Speech integration, voice sessions, intent/tool bridge, transcripts, spoken results and evaluation |
| Shipment and dispatch intelligence | Taher | Event ingestion/replay, disruption mapping, dispatch/recovery interface and route/document evidence |
| Release and demonstration | Both | Cross-review, integrated scenarios, benchmark claims, rehearsals and submission checks |

Each lead owns features from implementation through demonstration. Pair on shared contracts and blocked integrations; cross-review feature branches. Do not edit shared contracts or migrations concurrently.

## Daily outcomes

| Date | Divyanshu | Taher | End-of-day acceptance |
|---|---|---|---|
| Oct 6 | Run/preserve baseline; revise architecture, stack and roadmap | Existing prototype provides the baseline | Design and roadmap approved for team publication; implementation kickoff follows. Existing evidence: 62 tests pass; offline smoke passes; live smoke unchanged-plan failure documented |
| Oct 7 | Repair inventory/evaluation/date defects; agree `/v2` contracts; build Go/React foundation and action boundaries | Build quote extraction vertical with page/cell evidence; speech access/quality spike | React reads baseline recommendations through Go; money/currency round trips pass; reviewable extracted offer; real Google AI/speech calls verified or explicit fallback recorded |
| Oct 8 | Global currencies/coverage and multi-supplier allocation with cash/arrival constraints; first hosted React workflow | Complete offer correction/save and RFQ flow; microphone → transcript → scenario tool → spoken result | Hosted upload → reviewed offers → feasible sourcing plan → draft RFQ. Push-to-talk works through validated commands; unavailable inputs explicit |
| Oct 9 | Dated history and managed TimesFM/baselines; delay-to-inventory/production replan; persistent plan state | Streamed voice, interruption/reconnect, critical-field confirmation; shipment event ingestion/affected-order view | Hosted core: quote → plan → spoken disruption → changed plan or infeasibility → evidence → draft. Real model/tool traces and measured voice latency |
| Oct 10 | Production/BOM, capacity and maintenance-window scheduling; joint commodity/FX risk | Dispatch milestones, alternative delivery scenarios and port-delay replay | Delay/downtime consistently changes purchase, stock, production and dispatch; duplicate event cannot mutate twice |
| Oct 11 | Broad catalog discovery; Comtrade export ranking and attainable margin/capacity scenarios; verify team formation | Route/document evidence review, expiry/payment scenarios and discovery/sourcing integration | Dated export evidence distinguished from confirmed demand; uncapped catalog with explicit eligibility; team completion verified before cutoff |
| Oct 12 | Carbon comparison with sourced factors; historical forecast/policy evaluation | Route document checklist; multilingual/noisy voice refinement; held-out extraction evaluation | Auditable carbon assumptions, missing/conflicting requirements, measured voice/extraction results; no synthetic customer claims |
| Oct 13 | Integrate accepted modules; profile performance; optional model/Rust experiment only for a measured bottleneck | Cross-module walkthrough; action/event/voice concurrency and provider failure checks | Close expansion. Connected sourcing/production/dispatch/markets/evidence flow works; incomplete branches excluded from release claims |
| Oct 14 | Release candidate: identity/state/reliability review, benchmark ledger, architecture/cost evidence | Independent complete workflow, report exports, accessibility and laptop/mobile review | New tester completes hero flow unaided; no critical correctness/security blockers; revision and rollback recorded |
| Oct 15 | Fix candidate findings; business positioning, deck and brief from measured results | Rehearsals/capture; reproducible scenarios and README/setup | Claims match verified capabilities; Google AI contribution clear; signed-out demo and private-workspace checks pass |
| Oct 16 | Feature freeze; final checks and public-source readiness | Finish walkthrough and independent artifact/link review | PDF ≤5 MB; video <3 minutes, target 2:45; brief within portal limit; URLs accessible as intended |
| Oct 17 | Review artifacts; publish approved source and submit with final authorization; verify receipt/state | Independent demo/link check and judging-period triage | Submission confirmed in destination; source/deployment revisions recorded; required links work for judges |
| Oct 18 | Deadline contingency and uptime | Resolve verified release/submission blockers | Cutoff rechecked; demo remains available |

## Feature boundaries

Paths below are proposed relative to the repository root; new routes/files do not exist yet. `contracts/openapi.yaml` is the authoritative interface with generated clients. Workers receive exact schemas and reference examples before execution.

| Feature | Files and owner | Contract and acceptance |
|---|---|---|
| Platform | Divyanshu: `contracts/`, `services/gateway/cmd/server/`, `services/gateway/internal/api/`, `internal/workflow/`, `apps/web/app/root.tsx`, `routes.ts`, `components/` | Existing `/recommend` plus `/v2`; decimal strings and explicit units/currencies. Verify round trips, permissions, stale-plan rejection, repeated-action idempotency |
| Numerical/markets | Divyanshu: `procurement-engine/src/procure/decision.py`, `backtest.py`, `forecast/`, new currency/inventory/production/markets/evaluation modules; `apps/web/app/features/planning/`, `markets/`, `production/` | PlanningRequest → PlanResult with inventory/cash/arrivals/production. Hand-solvable cases, MOQ/capacity/payment limits, late-arrival infeasibility, past-only model selection |
| Quote intelligence | Taher: `procurement-engine/src/procure/offers/`, `documents/`, `tests/fixtures/offers/`, `tests/test_offer_extraction.py`, `apps/web/app/features/offers/` | `/v2/offers/extractions` → evidence/conflicts → reviewed SupplierOffer. PDF/CSV/XLSX page/cell references, exact unit normalization, expiry/conflicts blocked, document instructions ignored, held-out evaluation |
| Voice | Taher: `services/gateway/internal/voice/`, `procurement-engine/src/procure/agents/voice_tools.py`, `apps/web/app/features/voice/`, `tests/fixtures/voice/` | `/v2/voice/sessions` and stream; VoiceTurn; tools inspect/explain/propose scenario/prepare RFQ. Real speech, number/currency clarification, cancelled playback, stale result rejection, reconnect without duplicate effects |
| Shipment/dispatch | Taher: `procurement-engine/src/procure/events/`, `dispatch/`, `tests/test_event_replay.py`, `apps/web/app/features/dispatch/` | `/v2/events` → affected orders → revised planning constraints. IDs deduplicated, observed/occurred times distinct, correct impacted orders and infeasible shipping commitments |
| Sustainability/evidence | Divyanshu: `procurement-engine/src/procure/sustainability/`; Taher: `route_evidence/`, `tests/test_route_evidence.py`, `apps/web/app/features/evidence/` | Source-versioned emissions scenarios and route/material/date requirements. Unit/factor boundaries, authoritative dated links, missing/conflicting evidence, no certification claim |

Every feature ticket specifies its failing behavioral case, exact interface, allowed files and verification command. Use focused Python tests, Go tests and browser workflow checks; preserve baseline tests as an integration gate. Mocked contracts enable parallel development but never count as connected evidence.

## First parallel packages

- [ ] **Platform/planning:** Divyanshu agrees contracts with Taher, repairs inventory/evaluation, connects React → Go → Python recommendations, and supplies a global plan fixture plus an infeasible alternative.
- [ ] **Quote intelligence:** Taher builds extraction through review/save and evidence UI. Include international fixed/index-linked offers, mixed units/currencies, partial shipments, capacity/MOQ, validity and payment terms.
- [ ] **Voice operations:** Taher selects the speech path and proves a spoken delay invokes validated tools and is explained aloud. Pair on stream/authorization contracts before effects are implemented.

The primary speech path is Google Chirp 3 STT → Gemini reasoning/tools → Google TTS. Start with push-to-talk; add streaming and interruption. If streaming TTS Preview is unavailable, use ordinary Cloud TTS responses while keeping STT and tool behavior. Gemini Live and Fish Audio are alternatives behind adapters, not parallel required implementations. [Voice research](Voice_Architecture.md).

## Gates and coordination

October 7: choose speech/storage using actual access, quota and cost. October 9: working React core with voice, evidence and disruption recovery. If late, both engineers close the failing integration before expanding; React remains the frontend. October 13: stop expansion. October 14: candidate. October 16: freeze.

The full business roadmap remains intact: global trade, procurement/FX, production and predictive maintenance, dispatch/ports, carbon/compliance, mobile/conversational access and integrations. This release demonstrates constrained production/maintenance planning; a trained equipment-failure predictor needs suitable data and validation. Export discovery does not prove buyers; carbon/compliance support does not certify compliance. Only verified modules enter submission claims.

Reconcile branches and deployed revision each work block. Record checks, failures, evidence and next dependency daily. Use deterministic tools first, smaller capable agents for scoped work and Jev when advisory routing replaces meaningful effort. Keep sensitive documents/source out of advisory requests; one coordinator owns integration and provider mutations.

Publication and implementation were approved on October 6. This does not change billing or authorize final submission. Continue the existing Antigravity Codex session from this reviewed revision; one coordinator owns integration, while Taher works in separate feature branches.

## Source integration priorities

Preserve existing FX, World Bank and news adapters. First connect one hazard/weather signal to affected sample orders; then verified calendars and a suitably licensed land router, supplier identity, Comtrade product-level trade ranking and explicit carbon scenarios. See the [API integration guide](API_Integration_Guide.md). Missing data must remain visible; every synthetic operational input must be labelled.
