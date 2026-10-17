# ZOORQ cost carbon and shipment integration contract

Approved delivery starts from91c9872/r65. Original Taher heads114d3259bc and117bd70102 retained through merge history. Existing company hub, five destinations, four procurement stages, voice/research/action approvals and cost caps stay in place.

## Carbon

Use Taher’s Decimal calculator and factor registry; do not infer mass/distance or add overlapping/proxy components into a whole total. A supplier-industry-average factor is not supplier-specific performance. API register_carbon_routes remains callback-based with current active_plan, session, mutate, body, shape, context.

POST /api/demo/carbon/estimate: {plan_id,legs_by_offer:{offerId:[{mode,distance_km}]},mass_by_offer?:{offerId:{mass_kg,basis}},expected_context_version}. Existing comparison response gains company_id and context_version; suppliers remains array of {offer_id,supplier,estimate}. Inputs limited to current plan/offer versions; current saved quotes and authoritative buyer draft must agree with current plan. Distances/mass are explicit buyer assumptions. No model calculation.

GET /api/demo/carbon/assessment: {company_id,context_version,assessment:null|CarbonAssessment}. Store validated bounded versioned carbon_assessment in Session and per-company workspaces. Record uses Taher’s assessment.py {schema,company_id,context_version,plan_id,offer_versions,material_id,registry_version,calculator_version,inputs,result,created_at}. Revisit returns a current-context wrapper around retained valid evidence; context at creation need not equal new revisit context. Stale quote/plan mismatch returns no current assessment. Export uses the exact same validated current result.

UI CarbonPanel receives plan, csrf, contextVersion, scope:{sessionId,companyId,contextVersion}, dirty, result,onResult. Scope+plan+generation guard results; edits invalidate displayed current result until re-estimation. Root owns callsites/types and GET restore. Backend owns sessions.py/carbon_api.py; Antigravity owns demo-carbon.tsx/css/test only.

## Shipments

build_dispatch_view(company,offers,plan,recovery) is pure read model. The validated current plan row is first in the canonical response; remaining expected-order rows retain their profile order. An unrelated expected order with an unknown arrival cannot override a known plan-linked arrival. Output company_id,plan_id,synthetic:true,source,as_of,shipments[],limitations[]. Each declared sample shipment has shipment_id,company_id,material_id,material_name,order_id,quantity,quantity_unit,need_by,status,expected_arrival,offer_id,offer_version,plan_id,recovery_available,recovery_reason,delay_days,timeline[],source. No generic PO-SYN or SHP-SYN data silently joined by name. New declared scenarios use explicit company/order/material identities; inbound purchase quantity is separate from order demand. Unknown material/unit/order/quote ownership yields unavailable or excluded row. No route inferred from arbitrary currency/name. Preserve dates/provenance; do not masquerade fixture cutoffs as live tracking.

For a current selected offer, create an explicitly declared plan-linked demonstration shipment when order demand/date/unit agree with plan; use its real company order if exactmatch, otherwise an explicitly named plan-demand reference (not an invented purchase order). Offer id/version/current-plan association must hold. Company orders without accepted offer remain read-only expected-order rows with no recovery action. Plans are not actual shipments; row labels distinguish planning scenario.

GET /api/demo/dispatch and /{shipment_id}: root resolves session/current company and stamps context_version. No caller-selected company authority. Proposed delay must include shipment_id,plan_id,offer_version,days and be validated through current read model; no mutation until existing recovery review/approval. News/hazard exposure cannot provide delay days. Repeated approvals/idempotency remain safe. Legacy /dispatch redirects into current ZOORQ Supply Shipments tab.

UI DemoDispatchPanel props {view:DispatchView|null,busy:boolean,error?:string,onRefresh:()=>void,onReviewDelay:(shipmentId:string,days:number)=>void,disabled?:boolean}; source-linked compact rows, selected shipment details/timeline, explicit whole-day input and Review recovery action, no local calculation or API write. Root owns requests/currentness/handoff.

## Ownership and verification

Backend workers have disjoint source/tests; root owns app.py, operator.py, live_voice.py, agent_tools.py, demo-page.tsx, demo-api.ts, navigation and release. Antigravity owns two component file sets only. No parallel writer on a shared file. Tests precede substantive corrections. Verify all10 companies/52 materials; persistence, dirty/stale/context, unit/proxy/unavailable paths, actual text/voice reads and reviewed actions; desktop/mobile both themes. Existing trial-credit services only, no IAM/billing/scaling change.

## Hosted r74 behavior and evidence scope

The company workspace displays one top-level demo notice for illustrative company records, supplier quotes and buyer inputs, public-source news, and published-average emissions that may use product proxy factors. Routine text and voice summaries need not repeat those generic qualifiers. A direct source or method question still exposes the recorded factor publisher, selected proxy category, entered freight distance and overlap or missing-data reason. The assessment and source fields remain inspectable. A supported selected-purchase total must be backed by its own compatible production and freight components; alternatives stay clearly labelled and do not become the current purchase.

`read_shipments` keeps current plan-linked rows first without erasing other expected orders when the question is broader or no plan exists. A reviewed recovery binds reads to its active scenario. The original baseline supplier and arrival remain identifiable beside the reviewed supplier and arrival; infeasible recovery does not turn the baseline into a feasible selected purchase. A current *planned* shipment prompt is routed to the deterministic read and may summarize the existing reviewed comparison. Planned arrivals remain planning estimates, separate from confirmed orders or live carrier tracking. Neither a read nor a proposal applies a recovery, prepares an RFQ for sending, or sends a message.

The hosted r74 evidence and limits are recorded in [the release note](../ZOORQ_Taher_Integration_Release_20261016.md) and [sanitized targeted QA](../ux/2026-10-16-taher-integration/hosted-targeted-r74.json). r73 all-company HTTP/text and controlled speech results are inherited evidence; r74 checks are targeted. The Solara ordinary speech raw checker failure is retained separately from the independent same-turn factual acceptance. Runtime voice remains Google Live. This section records observed behavior and does not change the public API schemas above.
