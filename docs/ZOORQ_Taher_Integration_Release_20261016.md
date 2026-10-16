# Cost-carbon and connected shipment recovery — 16 October 2026

This increment adapts Taher Afsar's carbon (#114, d3259bc) and dispatch (#117, bd70102) foundations to the current hosted ZOORQ workspace. Original source history is preserved in two merge commits; the current company hub, news, operator and reviewed procurement flow remain the base. Markets, programme discovery and forecast PRs are not part of this increment.

## Connected decision flow

Procurement Compare now has Cost and Emissions views. Published factor sources, versions, production boundaries, explicit freight distances and optional entered shipping mass accompany the server calculation. Exact production-gate material and entered freight may combine; proxy or overlapping factors show separate components. Unknown material/unit/source coverage remains unavailable. No supplier-specific, certified or complete lifecycle footprint is claimed. The 8,000 kg copper / 1,000 km sea calibration is 31,720 kg production plus 128.96 kg freight. No unsupported green-finance savings are introduced.

A bounded, versioned assessment is saved per company and plan. Restored evidence is structurally checked, bound to current quote versions and buyer inputs, then compared with a fresh deterministic local calculation before being served to the browser, export or operator. The original creation context survives a company revisit; the current response wrapper carries the current context. Local transport edits hide current results, including after tab changes and delayed responses.

Supply adds Shipments alongside Inventory. Expected company orders remain separate from a current selected plan-demand scenario. The plan-linked row appears first; baseline and recovery supplier/arrival identities are explicit. Dates are planning estimates, not live carrier observations. Only a current eligible quote-plan row can hand an explicit whole-day delay to the existing recovery comparison. Edited days retain the shipment ID. An infeasible recovery keeps the baseline visible and disables the invalid next action. RFQs remain reviewed, unsent drafts, visible in Drafts.

Typed and Gemini Live operator tools read the same authoritative carbon/shipment results. Preparing shipment recovery yields the existing reviewable proposal; applying it revalidates company, shipment, plan, quote versions and consent. Reads do not clear pending proposals or send supplier messages. External news, grants and research questions retain their existing model/consent route.

## Review and validation

Independent reviewers requested and rechecked fixes for malformed restored assessments, proxy/overlap totals, contradictory provenance, infeasible recovery baseline loss, supplier/arrival mixing, forgotten carbon edits and edited shipment binding. See the attached backend and frontend review records. The complete hosted-demo and carbon backend group passed 954 tests and two subtests with nine dependency deprecation warnings. This does not claim the unrelated analytical/forecast repository suite.

Local HTTP acceptance uses all ten primary company scenarios: three reviewed sample quotes each, authoritative cost plan, carbon assessment/readback/export, company-owned shipment/detail, two deterministic typed operator reads, explicit 60-day recovery and a reviewed unsent RFQ with no simulated outbox sends. Cold persistence and all 52 material catalogue lines are separately covered by focused tests; the ten-company audit is not labelled a 52-material hosted transaction audit.

Actual local browser interaction verified quote review, comparison, emissions input, edit → Cost → Emissions suppression, shipment handoff and a changed delay retaining its shipment ID. Production build, hosted exact-revision acceptance, responsive observations and controlled Live speech receipts are recorded below when verified. Controlled speech is not physical microphone hardware or independent transcription of the output waveform.

## Execution and cost boundaries

Two Antigravity coding subagents were dispatched, then both stopped on connection errors. Its native windows were unstable; terminal recovery requested trust of the broader project root and automatic approval review rejected it. That broader access was not granted. Disjoint Codex component agents completed the UI and independent reviewers checked the integration. No claim is made that Antigravity completed those files.

The authenticated Cloud Billing console showed an active unupgraded Free Trial with remaining credits before release. This release uses the existing service, identity, Firestore and provider setup; no new service, IAM grant, paid upgrade or scaling increase is authorized. Rollback is r65 / image sha256:6fd2b16f49abf51629b721f925e1ef01be39c5a1fa08dcc5a4fe078d7adeb0e7. A console account-verification notice was observed; no verification or billing change was performed.

## Delivery status

Local integration prepared. Hosted deployment and release receipts pending; do not treat this checkpoint as hosted completion.
