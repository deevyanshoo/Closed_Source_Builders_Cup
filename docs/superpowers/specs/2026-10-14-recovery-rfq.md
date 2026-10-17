# Hosted supplier-delay comparison and unsent RFQ

User approved these two bounded extensions and parallel Antigravity execution on14Oct2026. Existing isolated checkout and public synthetic host are reused; no repeat approval gate. Goal: the buyer compares original and delayed supplier feasibility, reviews the current selected plan, and downloads an unsent RFQ with auditable facts. No Gemini assistant, forecast, split sourcing, real shipments, private uploads, supplier sending or durable storage in this increment.

## Frozen shared API

Existing PlanRequest/PlanView and /api/demo/plan stay compatible. Session fields appended: plan (active PlanView|null), plan_request (original PlanRequest|null), recovery (RecoveryView|null), rfq (RfqView|null). Existing clients tolerate extra fields. Reload restores these only within the ephemeral session.

POST /api/demo/recovery body: {baseline_plan_id:string, expected_versions:Record<string,number>, delay_days_by_offer:Record<string,number>, idempotency_key:string}. Same CSRF/origin/owned-session gates. Baseline is the current original last_plan, not caller-supplied calculations. Versions must match both baseline and current accepted offers. Delay keys are a subset of baseline offerIDs; integer0..3650 only. Server copies stored original request and runs the existing planner with entered delays. Empty delays restore the original scenario. Atomically stores the comparison and invalidates RFQ. Repeated exact idempotency returns replayed true only while this comparison is current; old plan/comparison replay must fail stale409, never revive a draft.

RecoveryView: {comparison_id:string,synthetic:true,baseline:PlanView,scenario:PlanView,baseline_request:PlanRequest,scenario_request:PlanRequest,cost_delta:string|null,selection_changed:boolean,outcome:'changed'|'unchanged'|'infeasible'|'stock_covers_need',replayed:boolean}. Decimal cost_delta = scenario.total - baseline.total; null if either has no selection. It is an illustrative cost change, never savings. Quote evidence/commercial terms unchanged. Baseline last_plan stays stored; current active plan is recovery.scenario when present.

POST /api/demo/rfq body: {plan_id:string,expected_versions:Record<string,number>,reviewed:true,idempotency_key:string}. Server validates the current active plan, owned accepted quotes, exact plan/offer versions and current quote validity. A missing selection or zero purchase blocks409 draft_unavailable; reviewed not exactly true422 review_required; old active plan409 stale_plan. Draft generated deterministically from selected reviewed offer and active plan. No recipient/email input or send endpoint.

RfqView: {draft_id:string,plan_id:string,offer_versions:Record<string,number>,synthetic:true,status:'draft_unsent',supplier:string,material:string,quantity_kg:string,need_by:string,reporting_currency:string,total_cost:string,created_at:string,text:string}. text clearly says synthetic/unsent/request-for-quotation/no order commitment. Include requested quantity/date/product, original quoted price/currency/unit, settlement currency, named Incoterm/payment terms if usable, separate reviewer corrections, original document locators, hypothetical delay where applicable and separate buyer extra-cost/synthetic FX assumptions. Reporting total is a scenario comparison, not a supplier commercial quote. Unknown terms remain explicit; never invented.

GET /api/demo/rfq/{draft_id}/download returns owned/current UTF8 text attachment synthetic-rfq-draft.txt. Stale/foreign/reset/expired draft404 draft_not_found. Revalidate date/version/state before any cached generation or download.

GET /api/demo/export retains offers and plan=active plan; append baseline_plan,plan_request,recovery,rfq. Both baseline/scenario input snapshots preserved by RecoveryView. Correction/save clears all plan/recovery/RFQ state. New /plan invalidates recovery/RFQ. Extraction of a new offer clears derived state for UI/server consistency. Reset/expiry clears all new fields. Failed computations do not overwrite a previously valid comparison/draft. Idempotency scope includes mutation identity and exact body; stale replays cannot recreate old derived state.

## UI

Retain existing design/quote flow. After original comparison, show 'Test a supplier delay', accepted supplier selector and nonnegative whole days, visible hypothetical scenario summary, 'Compare hypothetical delay' and 'Remove delay' controls. Display baseline and changed supplier/arrival/cost/feasibility side by side with cost delta and rejected alternatives. Never claim production impact or confirmed shipment delay. Original same inputs/versions stay paired. Editing buyer inputs or selected quotes immediately clears visible derived states and invalidates pending result guards; require fresh comparison. Backend rejects stale IDs even from other tabs. Epoch/request guards reject late async results after reset, form edits or newer action.

Then 'Prepare an RFQ draft': checkbox 'I reviewed this plan and understand the RFQ is an unsent draft', action 'Prepare unsent RFQ', text preview, 'Download RFQ draft'. Block absent selection/stock-only/stale result. Form/scenario edits clear checkbox/draft. Export includes both comparisons and current draft. Temporary/session/synthetic labels visible; keep tables internally scrollable on mobile. API request key injection must not duplicate server input keys. Do not use floating-point currency subtraction in UI; use server cost_delta.

## Expected synthetic example

8000kg purchase, need5Nov2026, decision14Oct2026, budgetUSD100000, buyerextra0.25/kg, EUR/USD1.25 synthetic. BaselineEuropa82000, Swift86000, Atacama77300late. Europa+20days=>13Novlate andSwift86000selected, delta4000. AddSwift+10days=>7Novlate andno selection. Removing all delays=>Europa82000, delta0. RFQ afterdelay targetsSwift, requested8000kg/by5Nov, originalUSD10.50/kg; never confuse86000 scenario total with supplier quote. No forecast recommendation.

## Review clarifications

The compatible existing /plan API can contain original delay assumptions. Recovery copies that original delay map then overlays supplied keys as absolute replacements, never added increments. Empty recovery restores the exact original baseline; a baseline already EU20days remains86000 with delta0. Normal UI baseline contains no delays. UI can accumulate hypotheses for multiple suppliers, then restore baseline with an empty recovery request.

First field correction preserves an immutable original {status,value,source,evidence,...} snapshot; further corrections retain it. RFQ labels original quoted pricing only from usable original extraction evidence, and lists current reviewed pricing separately. Original unverified/missing pricing stays unavailable; a correction is never relabelled supplier commercial evidence. Backend allowed scope expands to quote_service.py/test_demo_quotes.py solely for this provenance fix.

Buyer edits/sample-input actions stay disabled before initial session opens, or restoration must safely preserve initial session independently of derived-result cancellation. All session refresh side effects restore canonical inputs/selected IDs together with results, with epoch/request guards. Pending delay/RFQ edits cannot restore an old result. Tests pin initial-loading/edit, refresh and combined-delay behavior.

## Ownership and gates

Antigravity backend worker: procure/demo/app.py,sessions.py,newrecovery.py,newrfq.py, tests/test_demo_app.py,test_demo_sessions.py,newtest_demo_recovery.py,newtest_demo_rfq.py. Do not edit planner/quote_service/deployment/frontend.
Antigravity frontend worker: apps/web/src/demo-api.ts,demo-page.tsx,demo.css,demo-api.test.ts,demo-page.test.tsx and new demo-* UI components/tests if needed. No backend/deployment edits.
Root: shared spec/plan, deploy packaging/smoke, integration/cloud/browser, commits/PR. Read-only chat reviewer after implementation; no provider mutations from workers.
Checks: test-first API/browser-state behaviors, exact handcomputed outputs, invalid shapes/dates, expiry, cross-session/CSRF, stale/duplicate retries, edit/reset races, downloaded fact fidelity. Existing49 demo/package and full web suite/typecheck/build gates. Release only after current-image packaged offline checks, independent review, healthy revision and actual HTTPS browser review/recovery/RFQ/export observed. Same approved service identity/public invocation; no IAM/billing/repository visibility/final submission changes. Keep prior image digest for rollback.
