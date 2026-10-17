# Broad inventory/sourcing operator review

## Initial root cause

The exact question, “Explain the inventory position and sourcing options for Copper cathode at Solara Cables.”, falls outside `_canonical_read_template`'s recognized requests. The current recognizer handles reporting currency, a named supplier lead, shortest/stock-cover questions, and narrow monthly/daily/how-much-stock requests. It does not compose inventory plus sourcing. The evidence model must therefore produce the entire broader answer under `_render_answer`, which rejects any digits outside fact tokens—including copied correct values or numbered list markers. The reported `unverified numerical claim` fingerprint matches that path. Existing trusted facts already contain stock, daily/monthly demand, unit, cover, profile lead, supplier names/currencies, and reviewed offer fields; broad deterministic coverage is missing.

## Load-bearing acceptance boundaries

- Answer the actual composite request with material/unit-bearing stock and daily demand, grounded stock cover, matching supplier profile options and leads/currencies, and clear unavailable-data statements. Avoid claiming a price, purchase quantity, arrival commitment, or best reviewed quote when unavailable.
- Keep profile suppliers/leads distinct from reviewed saved quote evidence. Draft/extracted quotes, unrelated materials, foreign-company offers, stale plan versions, and unreviewed fields must not become selectable or recommended reviewed quotes.
- Keep `_render_answer` and existing evidence/ID/claim validation strict; no generic catch that returns success while hiding a validator error. A deterministic response must be composed from the current authorized snapshot and must substantively answer the question.
- Resolve a named current-company material exactly. Never substitute the first material, a previous company's material, or an unrelated selected material. Selected-material wording must respect the current selection/buyer state or ask for clarification when ambiguous.
- Preserve Decimal arithmetic and units. Unknown/zero-invalid consumption or missing matching lead must yield an explicit unknown comparison, without fabricated coverage or gap. Presentation rounding must not alter source facts.
- Preserve read-only behavior: no quote loading, form editing, comparison, RFQ, simulated message, proposal application, provider research, or consent side effect from an explanatory question. Shared voice `operate_workspace` must receive the same corrected bounded behavior; direct `read_workspace`/`analyze_inventory` remain authoritative read tools.
- Regression scope should include the exact failed question, another material/company/unit, missing stock-cover or lead inputs, profile-only sourcing, saved reviewed quote context versus draft/unrelated/foreign evidence, and continued rejection of unsupported raw numerical claims in model-dependent requests.

## Current status

Initial audit only. No source/provider/browser/Git changes or installations. Actual implementation review pending worker completion. Coordinator owns hosted acceptance, integration, and delivery.

## First implementation review findings

1. P2 scope: unmatched explicit material names were treated as company-wide. Reproduced “...for nickel at Solara Cables” and “...for unobtainium” returning company-wide. Unknown/foreign named material must decline canonical routing or clarify instead of returning unrelated inventory.
2. P2 selection: “current material” was treated as company-wide; “selected material” used selected_material_id despite disagreement with buyer_draft.material. Current/selected/this wording must respect the existing scope contract and never choose a conflicting material.

Both concrete findings sent directly to the implementation owner, who confirmed and is repairing them with regressions. Company-wide deterministic answers measure at most 1,070 characters in current profiles; named answers at most 812. This is an explicitly bounded server-rendered response, so the model-template 1,000-character guard does not need to be repurposed as an arbitrary truncation rule.

Audit review: company-wide hosted question's “Do not change anything” clause hit the recognizer's action blacklist, so it missed the new route; requested removing that redundant audit-only clause while retaining protected-state verification. Voice audit initially accepted tool analytics plus audio without grounding output transcript; requested factual caption validation and retained explicit audio-semantics-unverified status. Numeric set membership alone does not establish correct material/value bindings. Acceptance-script owner notified directly.

## Final core implementation review — approved

The operator implementation now substantively handles the exact composite question with deterministic, current-snapshot facts before invoking model prose. It distinguishes profile suppliers/leads/currencies from the presence or absence of owned, saved, reviewed quote documents. Saved status alone is insufficient: the helper verifies document ownership, material, positive version, and the existing blocking-reasons gate, including critical missing pricing. No quote/plan/form/action is applied by this read.

All scope findings are repaired: explicit unknown/foreign materials and foreign company names decline canonical routing; multiple named materials stay on the existing path; current/selected/this references require a valid selection and reject buyer disagreement or missing selection. Explicit provenance questions now pass through the actual user message to presentation and retain sample/not-connected disclosure. Genuine mixed action/research/news/forecast requests retain the existing operator/consent paths. Strict model-dependent fact-token/numerical/claim validation is unchanged. Company-wide responses prioritize measured gaps and keep a bounded top-three summary; named-material responses preserve their own unit and matching profile suppliers.

Independently verified the initial 25-case broad regression set; worker reports the final added provenance/missing-selection cases and combined 207 adjacent cases passing. Final source diff whitespace check passed. No remaining core implementation blocker found. This approval does not claim hosted/provider acceptance.

Acceptance-script review remains separate: output caption checks initially accepted unsupported spelled-out pricing and future order-execution claims despite a correct stock-cover anchor. Concrete negative cases were sent to the acceptance worker for correction before claiming caption grounding.

## Combined final source and acceptance review — approved against 546eddb

Reviewed the completed operator and app changes plus the new broad-operator and hosted-audit test files. The app computes deterministic-read eligibility from the authoritative scoped snapshot after context/idempotency reconciliation. Eligible reads bypass only model availability and phantom three-call model reservation; request/session-turn/concurrency limits, generation/epoch checks, replay markers, and action/consent handling remain intact. The eligibility helper and renderer use the same copied snapshot, so a scope-approved read does not drift into an unreserved model path. API regressions cover unavailable provider, zero model budget, unchanged protected state, replay, stale context, and unrelated watch limits.

The final recognizer supports current-company overview phrasing and explicit read-only negation without exempting a positive send/action clause. Named/current-material scope findings and explicit provenance disclosure remain repaired. Saved reviewed quote state is checked through owned document identity and the existing blocking gate; no profile supplier is represented as a live quote or confirmed availability.

Audit repairs now reject both reproduced false-positive captions: spelled-out price “fifty dollars per kilogram” and “We will place an order now.” Typed checks associate primary stock/cover/supplier lead with nearby labels and reject unsupported pricing; failed observed text/voice is retained before assertions. Voice checks separately verify owned profile analytics and grounded caption anchors; actual output-audio semantics remain explicitly unverified. There are no automatic retries or outbound research/business actions in the audit.

Independent audit checker suite: **8 passed in 0.18s**. Source diff whitespace check passed. Coordinator reports 211 focused core tests and two API regressions passing; combined full backend/build gates were still running when this source approval was issued. **No remaining actionable blocker in the reviewed combined source/audit diff.** Hosted outcomes, independent parallel reviewer collection, exact deployed revision, and delivery checks remain coordinator-owned.

## r65 affected-voice acceptance checker follow-up — approved for bounded rerun

Reviewed the audit-only diff against 6e2fb2a and the original `/private/tmp/zoorq-operator-broad-hosted-r65.json`. No production source changes are part of this checker repair.

- TerraPack's recorded “just under 20 days” is consistent with PET resin cover 19.473684… days. The new integer under-threshold exception requires actual cover < threshold <= actual cover + 1 for a named returned material; the invalid under-10 regression remains rejected.
- ForgeLine's recorded approximately 25-day cover is consistent with 25.416666… days. Whole-day spoken approximation, hyphenated 10-day leads and the unique matching Alternate Source abbreviation are recognized without adding arbitrary numeric values to the allowlist.
- Canonical voice tool results without analytics are accepted only with one completed `inventory_sourcing_read`/`provider_call=none` step and the existing grounded company-wide `check_answer` validation. Unrelated tools, action results, wrong context and unsupported answers remain rejected. Sanitized tool observations are checkpointed before assertions.

Independent checker suite: **11 passed in 0.23s**. Independently rechecked the actual recorded TerraPack and ForgeLine captions; both pass the repaired checker. Diff whitespace check passed. **No remaining blocker to rerunning the five affected voice visits.** Harvest Foods, Atlas Petrochem and StoneBridge spoken outputs remain unverified because the original run stopped on tool shape before collecting their output; the repaired script must observe new tool/caption/audio results for those visits. Caption grounding remains distinct from actual output-audio semantic verification.

## Manual actual r65 caption and reviewed-quote review — nine voice visits verified; StoneBridge unresolved

Compared successful initial rows with successful overriding recheck rows against the current company profiles and Decimal inventory analytics. The recheck receipt has completed with StoneBridge `voice_unsupported_tool_result`; it is no longer processing. That row records only a `read_risks` result (“Scanned 1 companies; 15 risk and watch signals”), with no observed spoken output. Do not infer a StoneBridge caption/audio pass or a ten-company voice result from the other rows.

The following nine successful observed captions match profile stock/cover/lead and supplier-material scope:

| Company | Verified actual caption facts |
| --- | --- |
| Solara | Copper cover approximately 6 days vs Europa Copper's listed 10 days; polymer/aluminium alternate sources also list 10 days. Copper's below-lead comparison is valid. |
| TerraPack (recheck) | PET resin cover 19.473684… days, spoken about 19.5; ColorMill is the shortest company-listed lead at 6 days for inks. Caption does not assign that 6-day lead to PET resin. |
| LoomWorks | Cotton 920 bales, dyes 4,400 kg, yarn 7,100 kg; cotton cover 28.75 days; material-specific alternate sources list 10 days. |
| Harvest (recheck) | Wheat has lowest cover, 24.324324… days spoken roughly 24; PackEast's 5-day lead is explicitly associated with food cartons. |
| Meridian | Sterile vials have lowest cover, 31.578947… days spoken about 32; PureSolvent's 8-day lead is explicitly associated with medical ethanol. |
| ForgeLine (recheck) | Steel has lowest cover, 25.416666… days spoken approximately 25; alternate source options list 10-day leads. |
| GridSpring | Lithium has lowest cover at 22 days; the material-specific alternate source options list 10-day leads. |
| CircuitPeak | Semiconductors 74,000 units, boards 42,000 units, solder paste 860 kg; boards have lowest cover at 35 days; shortest listed leads are 10 days. |
| Atlas (recheck) | Naphtha has lowest cover, 26.760563… days spoken approximately 26.8; Oman Chemicals' 7-day lead is explicitly associated with Methanol. “Delivering” is interpreted in its stated lead-time context, not as proof of an order/shipment commitment. |

No fabricated price, placed/sent/applied action, unit substitution, or foreign-company/material attribution is present in those nine recorded captions. Successful recheck tool observations are completed current-company `read_workspace` reads with analytics. Initial five successful rows predate raw tool-observation checkpointing; their exact tool payloads are not available for independent reinspection, so this review relies on the recorded pass/material IDs plus direct factual caption comparison for those rows.

The separate `zoorq-operator-broad-reviewed-quote.json` receipt is consistent: before/after answers preserve the same Copper profile values/options and change only saved reviewed quote-document presence. It states rules extraction/review, deterministic operator operation, and no resulting plan/draft/outbox. No live price or accepted delivery commitment is introduced in either answer.

Scope: manual inspection of receipts only; no new provider calls. Physical microphone hardware and speech-to-text verification of output audio are not claimed. **Current verified voice-caption count is 9/10, with StoneBridge requiring further observed evidence.**

## StoneBridge intermediate-risk audit chain — approved for StoneBridge-only rerun

Reviewed the final audit-only `check_voice_tool_result` and `voice_turn` changes. A completed, read-only, deterministic current-company `read_risks` result is permitted only as an intermediate. It must have one owned company row and only owned material references; foreign/all-company rows are rejected. This path returns no material IDs, cannot accumulate qualifying inventory audio, and cannot satisfy the final pass condition. A subsequent validated analytics or canonical inventory result is still required, and resets prior caption/audio evidence before collecting the final grounded caption and sufficient output audio. Raw sanitized tool observations are preserved in order.

Independent focused checker suite: **12 passed in 0.30s**, including risk→inventory success, foreign/all-company risk rejection, and risk-only nonpass. Diff whitespace check passed. No production source change or new provider call was made in this review. **Approved for the coordinator's single-company StoneBridge rerun; no retrospective StoneBridge pass is claimed.**

## Final actual StoneBridge and consolidated hosted acceptance — approved

Independently inspected `zoorq-operator-broad-hosted-r65-stone.json`. Its caption correctly identifies Cement clinker as the lowest-cover material: 12,500 tonnes ÷ 480 tonnes/day = 26.041666… days, appropriately spoken “approximately twenty-six days.” KilnSouth is the matching clinker supplier with the shortest listed lead, six days. No fabricated price, foreign material/supplier, or performed business action is asserted. The completed current-company/context read_workspace tool has profile analytics for all eight owned materials; the receipt records 562,562 output audio bytes, verified input transcript and caption grounding, and unchanged protected workspace state.

Combining only successful rows, with successful recheck/Stone rows overriding initial rows, exactly matches all ten current catalogue company IDs on `closed-source-quote-demo-00065-vst`. Every consolidated row records a verified input transcript, grounded output caption, sufficient output audio, and unchanged protected state. This completes **10/10 controlled Gemini Live visits plus the 20 primary/company-wide typed reads**, with the separate empty→saved reviewed-quote transition also verified in its receipt. The initial and affected-company runs remain preserved as historical evidence; failed earlier rows are not represented as passes.

**Final acceptance approval: no remaining finding in the inspected actual outputs and receipts.** This is controlled-input/provider-caption/audio-delivery acceptance. Physical microphone hardware and independent speech-to-text/semantic verification of output audio remain explicitly unclaimed. No provider rerun, source edit, or cloud mutation was performed by this review.
