# ZOORQ Decision Closure Implementation Plan

> For agentic workers: use test-first development and independent review. Application implementation runs only through Codex CLI agents in Antigravity terminals. The coordinator owns integration, Git and hosted changes.

**Goal:** Connect inventory, accepted quote arrivals, recovery and operator actions so users can distinguish meeting an order deadline from maintaining production supply.

**Architecture:** One pure Decimal-based supply assessment feeds planning, recovery, API snapshots, typed/native voice resolution and the React workspace. Existing accepted quotes, action validation, durable drafts, provenance and confirmation gates remain authoritative.

**Tech stack:** Existing Python/FastAPI, React/TypeScript, Vertex Gemini Live, Cloud Run and Firestore; no new dependency or service.

**Spec:** User-approved remediation plan in the conversation, starting with inventory-aware planning and operator actions. This document makes its first release contract explicit. The approved later workstreams are unfamiliar-document evaluation/intake, actionable evidence-ranked news, bound route views and reproducible outcome evidence; their integration follows the shared decision contract.

## Global constraints

- Deploy only to the `zoorq` Cloud Run service in `closed-source-builder-261007`, region `asia-south1`. The user's latest scope supersedes the prior both-address release default. Record the original service's revision before/after and do not mutate it.
- Existing trial credits only; no billing, IAM, new services, dependency installation or infrastructure scaling changes. Reuse the existing service envelope and immutable rollback image.
- Preserve contributor history and user workspaces. No cookie resets, provider tokens in logs, external supplier mail, purchase or RFQ delivery.
- Workers have disjoint write ownership, no recursive workers, no Git mutations or provider calls. Coordinator runs final suites once on frozen source and verifies actual hosted outcomes.
- Keep the current white/ink/cobalt design, dark theme, Thinking Orbs and single prominent data disclosure. No new font/palette/framework or repeated synthetic qualifiers.
- Calculate with Decimal strings and explicit units. Never sum unlike units, infer live inventory from an old snapshot, or invent prices, supplier capacity, routes or confirmed earlier delivery.
- Preserve old `feasible` fields as order/quote-constraint feasibility. New explicit fields communicate supply coverage; generic user-facing feasible/success claims must be replaced where they imply continuous supply.

## Shared contract and rulings

Create `procure.demo.supply_coverage.assess_supply_coverage(company, plan, *, offers=None) -> dict` and `with_supply_coverage(company, plan, *, offers=None) -> dict | None`, the latter returning an enriched deep copy with `supply_coverage` and never changing stored accepted state.

The assessment has `schema: 1`, `company_id`, `plan_id`, `material_id`, `quantity_unit`, `decision_date`, `horizon_end`, `inventory_as_of`, `consumption_as_of`, `stock`, `consumption_per_day`, `basis: constant_daily_consumption`, `status: covered|gap|unknown`, `order_deadline_met: bool|null`, `continuous_supply_covered: bool|null`, `stock_cover_days`, `stock_depletion_date`, `selected_arrival_date`, `uncovered_days`, `uncovered_quantity`, `bridge_quantity`, `bridge_needed_by`, `assumptions: string[]`, `missing_inputs: string[]`, `evidence_ids: string[]`, and `alternatives` with per-offer `offer_id`, arrival, status, deadline/continuity booleans and gap/bridge fields. Numeric results are decimal strings or null; dates are ISO or null. Extra additive explanatory fields are permitted; mandatory names must stay stable.

Use the plan's reviewed opening stock as of its decision date, and the owned material's dated daily-consumption assumption. A different reviewed opening stock is a valid buyer input, not automatically a profile conflict. Preserve both source dates. Do not silently age the stock snapshot to wall-clock today. Validate ownership, material/unit, current accepted versions when offers are supplied, nonnegative finite inputs and coherent date bounds. Missing or stale facts produce `unknown` with a precise missing input.

Project constant daily consumption from decision date through the end of the need-by date, with the selected quote quantity received on its planned arrival date. Arrival is the start of that calendar day; demand is spread over elapsed calendar days. The projection interval is `[decision_date 00:00, need_by + 1 day 00:00)`, while `horizon_end` displays the original inclusive need-by date. Guard an unrepresentable endpoint rather than overflowing. Account for uncovered intervals before and after receipt; never double-count the order quantity as additional consumption. An earlier-than-horizon replenishment does not prove the remaining horizon is covered unless its quantity suffices. For no-purchase plans, assess the stock over the same horizon. Dates/coverage are planning assumptions, not observed factory events.

Validate the saved-plan algebra: positive finite `quantity`, nonnegative `current_stock`, and `required_purchase == max(quantity - current_stock, 0)`, including generic/kg alias agreement when both are present. Inconsistent selected-offer/receipt quantities, units or versions return unknown. `bridge_needed_by` is the first actual uncovered date after accounting for any receipt, not unconditionally the initial stock depletion date: 2,000 units at 100/day plus 1,000 received on day 5 first runs short on day 30, not day 20. Preserve exact arithmetic gap while rounding only orderable bridge requests for discrete units. Without a current selected supplier the factual assessment may identify a gap, but cannot offer or create a supplier-specific bridge draft.

The audited Solara case must report order deadline met and approximately four uncovered days / 1333.30 kg before the October 24 receipt, from the October 14 stock snapshot. Keep full Decimal arithmetic; UI may round with “about”. Boundary arrival at depletion has zero gap. Unknown coverage must never become covered through truthiness or missing fields.

New comparisons choose the cheapest accepted quote satisfying existing commercial constraints **and** continuous supply when such an offer exists. Otherwise retain a deadline-feasible comparison option while clearly reporting `gap` or `unknown`; it is not an uninterrupted-supply recommendation. Do not silently reselect an existing saved plan on a read. Recovery exposes baseline/scenario coverage and remaining gap independently from its existing cost/supplier outcome enum.

A bridge RFQ is a new unsent request for the calculated gap quantity and earlier required date to a known current selected supplier. It requests new capacity/price/terms; it does not reuse the original quote as proof the bridge is available, nor overwrite the accepted plan quantity. Discrete units may require upward rounding for an orderable request, with the arithmetic quantity preserved. It uses the existing durable RFQ/draft ledger and download boundary, canonical equal body bytes, context/version ownership and idempotency. The draft stays visibly unsent. An explicit user drafting request can prepare it; a factual question remains read-only. No bridge draft exists when coverage is unknown or no positive gap is supported.

Native and typed bridge requests must bind any explicitly named material or plan ID to the current selected plan, including all-letter URL-safe plan IDs. A stale or ambiguous target returns clarification before any draft is saved. Generic requests for the current plan remain allowed. Drafting negation differs from delivery negation: “prepare a bridge RFQ; do not send it” prepares an unsent draft; “do not draft” and hypothetical discussion remain read-only.

Coordinator native-wiring ruling: the narrowly scoped voice implementation may update `live_voice.py` and its RFQ/coverage tests. A bridge/earlier-delivery request uses the shared `operate_workspace` resolver, even if the model chooses the generic RFQ tool. Action routing waits for settled current-utterance evidence and never substitutes a regular full-purchase RFQ for a bridge request. Existing stale-input, renewal, replay, consent and recovery-approval guards stay intact.

## Tasks

### 1. Shared supply assessment and planning/recovery integration — Engine lane

Own new `supply_coverage.py`, existing `planner.py`, `recovery.py`, and their focused tests only. Read company/quote modules without changing fixtures or commercial values. Write failing Solara/boundary/all-late/non-kg/stale/missing/insufficient-receipt tests, run them red, implement, run focused planner/recovery/coverage checks green. Attach coverage to new plans; expose additive baseline/scenario coverage in recovery. Do not run the full suite or commit. Freeze writes and report exact files/tests/limitations.

### 2. Grounded reads and usable operator actions — Operator lane

Own `app.py`, `judge_resolver.py`, `experience_service.py`, `agent_tools.py`, `rfq.py`, new focused bridge helper if needed and their focused tests. Do not edit `live_voice.py` without coordinator ruling; use its existing shared resolver/operate-workspace boundary. Enrich current API/operator plan copies from the shared helper. Route stockout/arrival/continuous-supply questions before generic stock retrieval. Give a brief direct answer, dated quantities and references, then the concrete next tool/action; ask one necessary question for unknown facts. Typed/native results must agree and never default to public research for local planning facts.

Support explicit bridge/earlier-delivery RFQ requests using the shared assessment and existing saved draft/download UI contract. Test canonical body parity, replay, stale company/context/offer rejection, no plan change/no delivery, unknown gap rejection, and unchanged factual-read state. Keep existing regular RFQ behavior. Run focused app/resolver/integration/draft tests, freeze and report; no full suite/Git/provider calls.

### 3. Decision-first UI — Frontend lane

Own web TypeScript/React/CSS/tests only. Add typed optional coverage fields and a reusable compact supply assessment view. Use it in comparison, overview and recovery; show order-deadline and production-supply outcomes separately, source dates, uncovered days/quantity, and before/after recovery impact. Avoid displaying old generic “is feasible” or “stock covers this need” as an assurance about production supply. Existing old sessions without coverage should show an honest “Check supply coverage” state rather than a green success.

Give a meaningful bridge/earlier-delivery draft action through existing operator API and drafts view; show prepared draft location and unsent status. The backend owns arithmetic; do not recreate calculations in JavaScript. Keep buttons aligned, keyboard targets/focus visible, layout within 360/390/1280/1440 widths, both themes, Thinking Orb and multilingual attribution. Tests first for gap/covered/unknown/recovery/draft states. Run focused web tests/typecheck; no full suite/build/Git/provider calls until coordinator requests.

### 4. Integration, independent review and release — Coordinator

Integrate against exact shared contract; inspect disjoint diffs. Use independent numerical/action review and visual review, with separate fixer dispatches for concrete findings. Run final backend/frontend suites and production build on frozen source. Verify all ten companies and non-kg units, unchanged read semantics, typed/native questions and explicit unsent bridge action; test ordinary recovery/RFQ/carbon regressions and stale contexts. A same-plan repeated command must not duplicate actions.

Capture exact existing `zoorq` config, prior revision/image and original-host health before release. Package only allowlisted runtime files, scan for secrets, build once, deploy with no traffic and compare envelope. Activate only the `zoorq` revision after required checks. Verify exact active image/assets/Firestore health and hosted text/voice outcomes, desktop/mobile usability and appropriate Lighthouse checks. Confirm the original service stayed unchanged. Use a reviewed normal PR merge without bypass/force, verify remote main and clean Git. Record actual acceptance boundaries, including controlled-speech versus physical-microphone evidence.

## Review focus

- Date anchors, zero use and no-purchase cases cannot turn unknown inventory into a guarantee.
- Receipt quantity can be insufficient after arrival; cover must consider the horizon, with no double-counted demand.
- Existing saved plans can lack assessment or have stale versions; reads must not replace accepted state.
- Bridge RFQ must not inherit a price/capacity promise or silently change an accepted purchase; ledger and downloads must agree.
- Company switches, late voice ASR, interrupted consent, replay and retries must retain existing isolation/approval rules.

## Dependency and ownership scan

| Pair/task | Shared interface or self-check | Ruling |
|---|---|---|
| Engine / Operator | `assess_supply_coverage`, `with_supply_coverage`, mandatory JSON names | Engine owns implementation; operator imports stable contract and waits for focused engine green before integration acceptance. |
| Engine / UI | `plan.supply_coverage`; additive recovery coverage | Frontend writes optional types/states first; arithmetic is backend-only. |
| Operator / UI | Existing operator turn, saved RFQ and drafts/download | Keep current response shape; additive bridge fields only. No frontend reconstruction of canonical draft body. |
| Engine | Changing new-plan selection versus saved state | New build may prefer continuity; read enrichment never changes existing selection or request. |
| Operator | Factual answers versus requested draft | Factual question read-only; explicit draft request only creates an unsent artifact, with existing context/idempotency. |
| UI | Current design versus new information | Targeted hierarchy/labels/components, no redesign of fonts/navigation/palette. |
| Release | Prior both-host guide versus user's latest scope | Only `zoorq` may be mutated; original host only read-checked for unchanged state. |
