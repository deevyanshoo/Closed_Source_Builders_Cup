# Taher’s work: integration review, 16 October 2026

**Decision: reuse the work, with corrective integration before deployment.** The data and numerical foundations are useful. The current PRs are not ready to merge unchanged into ZOORQ: there are emissions-boundary, persistence, financial-benefit, stale-result and navigation defects that existing tests do not catch.

This review makes no product, billing, IAM or deployment change. The serving baseline was freshly checked as `closed-source-quote-demo-00061-49v`, source `0485463`, at 100% traffic. The integration base is `06ccd7118dba8ad6f25ad55f9f8bf5e9bd8caaa3`, which includes the release evidence. Deployment and merge state remain separate.

## What was reviewed

Three independent reviewers examined the five newest feature PRs. A fourth inventoried ten older proposals for relevance; that inventory is not a full approval of those older patches. The coordinator verified fetched heads, tested isolated snapshots, downloaded authoritative workbooks and probed merge conflicts without changing the working branch.

| PR and pinned head | Quality assessment | Integration decision |
|---|---|---|
| [#113](https://github.com/deevyanshoo/Closed_Source_Builders_Cup/pull/113), `11c82c1` | Useful formatting, but a converted comparison price is labelled as the supplier’s quoted price; its legacy title would regress ZOORQ branding. | Bring selected formatting changes into current components; correct the price label and retain the new shell. |
| [#114](https://github.com/deevyanshoo/Closed_Source_Builders_Cup/pull/114), `2eb31e7` | Sound Decimal foundation and accurately transcribed factors. Incorrect combined boundary and missing durable result storage are release blockers. | First priority: integrate corrected emissions scenarios with the active sourcing plan, persisted evidence and Operator. |
| [#115](https://github.com/deevyanshoo/Closed_Source_Builders_Cup/pull/115), `4abc75b` | Useful programme discovery; monetary benefit formulas lack necessary constraints or evidence. Depends on #114. | Integrate verified discovery first. Enable each benefit calculator only after its own rules and test gate pass. |
| [#116](https://github.com/deevyanshoo/Closed_Source_Builders_Cup/pull/116), `cd595a2` | The source values and Decimal conversions are useful; the heading overstates country-specific price coverage and response ownership is too weak. | Adapt as dated market reference, with current session/company guards and explicit coverage. |
| [#117](https://github.com/deevyanshoo/Closed_Source_Builders_Cup/pull/117), `8ee55f3` | Useful read-only shipment/replay UI. It serves one fixed fixture set, loses company context and exposes dead links on a bare hosted URL. | Reuse views/read models inside current Supply navigation; connect company/order/plan IDs before claiming an operational workflow. |

All five three-way merge probes conflict in `apps/web/src/demo-page.tsx`; #113 and #117 also conflict in `demo.css`. Automatic conflict-free portions are not combined acceptance. Preserve Taher’s commits/authorship, then resolve into the current ZOORQ components and add corrective commits. Do not restore the old masthead or replace current voice/session logic.

## Verification completed

| Pinned standalone snapshot | Focused web tests | Focused Python tests, including packaging where present | Typecheck / production build |
|---|---:|---:|---|
| #113 | 5 | 2 | Passed |
| #114 | 5 | 19 | Passed |
| #115, schemes delta | 4 | 11 | Passed |
| #116 | 3 | 9 | Passed |
| #117 | 23 | 1 | Passed |
| **Total across snapshots** | **40** | **42** | **5 passed** |

These are focused tests of separate PR snapshots, not a combined branch, full repository suite, browser audit or hosted feature test. Packaging initially failed because the source snapshots lacked built web assets; after normal builds, those package tests passed. The published reports preserve that chronology. Existing node dependencies and a temporary offline Python test runtime were reused; no new project or cloud dependency was installed.

The coordinator and reviewers checked the official [DESNZ workbook](https://www.gov.uk/government/publications/greenhouse-gas-reporting-conversion-factors-2026): its 2,109,334-byte file checksum matches the registry, and all ten factor cells, relevant CO2e columns and units match. The official [World Bank monthly workbook](https://www.worldbank.org/en/research/commodity-markets) also matches its recorded checksum: all 18 July–September values across six series, headers and units match. This establishes transcription accuracy, not applicability to every material, geography or commercial transaction. Keep attribution and the dataset-specific usage terms with the packaged source evidence.

## Findings that must be resolved

1. **Carbon boundary, high priority.** `carbon/calculator.py:109–116` adds entered freight to material factors that already include transport to point of sale, then calls the result complete. The registry itself discloses the overlap. The DESNZ methodology confirms that boundary; its PET category also includes forming, so it is not an exact raw-resin factor. Keep component estimates separate unless non-overlap and product applicability are established. Do not claim a complete or certified footprint. [Methodology](https://assets.publishing.service.gov.uk/media/6a2940543b15d05a7ce3202e/2026-GHG-conversion-factors-methodology-report.pdf).
2. **Carbon persistence, high priority.** `demo/carbon_api.py:53–54` dynamically assigns `carbon_estimate`, but `demo/sessions.py` omits it from its persisted fields and company workspace restoration. A fresh store restores the session without the result, so export loses it. Add a validated, bounded, versioned field or recompute from persisted inputs; test process restart and company revisit.
3. **Carbon currentness and incomplete totals.** Dirty buyer inputs leave the old-plan calculator actionable. Incomplete freight can still produce a numeric `total_kg_co2e` in JSON. Gate both UI and tools on the accepted current plan and use null for an unsupported whole-scenario total. Keep legitimate partial component figures explicitly named.
4. **Programme benefit claims, high priority.** ZED accepts an arbitrary certification cost; its own test turns ₹250,000 into a ₹150,000 subsidy. That is not an eligible-fee calculation. Its women-owned rule also conflicts with the official [amendment](https://loginzed.msme.gov.in/Uploads/Incentives/146File1.pdf). The indexed Ministry fee references require a retrievable current schedule before enabling the formula: direct annual-report/booklet downloads failed in this review. CBAM uses a correctly copied Q3 2026 price outside a quarter-specific calculation, without the required product/importer and certificate adjustments. The [Commission price schedule](https://taxation-customs.ec.europa.eu/carbon-border-adjustment-mechanism/price-cbam-certificates_en) explicitly binds quarterly prices to imports in that quarter. SIDBI’s cited landing page does not substantiate the precise loan terms or a rate concession. Scheme matches are leads to investigate, not entitlement, issued credit or secured savings.
5. **Pricing ownership and meaning, high priority.** `demo-country-pricing.tsx:21–28` accepts delayed fetches, and its parent checks only `company_id`. A→B→A or session replacement can display obsolete offers/FX for A. Bind to session, context, generation and request identity. The dataset is a global benchmark in converted currencies; supplier country belongs only to the separate synthetic quotes. Six of ten companies have no matched benchmark in this registry. Iterate priced quote items rather than silently showing only the first item. Validate positive finite FX and matching provider/date.
6. **Dispatch navigation and scope, high priority.** `dispatch-page.tsx:349–351` infers hosted capability from `?from=demo`. The server also serves bare `/dispatch`, which exposes links to unserved local pages. Host configuration and supported destinations must determine navigation. The same fixture shipments currently appear for every company; no selected shipment changes the current hosted recovery plan. A visible page is not connected dispatch.
7. **Polish accuracy.** #113 calls the planner’s FX/unit-normalized field “Quoted unit price.” Use “Comparison unit price” with reporting currency, while keeping original quote evidence separately. Retain ZOORQ’s title. Rounded stock cover must not alter exact threshold logic or conceal a near-threshold risk.

## How the integrated product should work

Keep the five persistent destinations. In Procurement’s existing Compare stage, use compact **Cost / Emissions / Market reference** views; do not append several long panels with new step numbers. Retain the RFQ review step. Under Inventory & supply, use **Inventory / Shipments** with the same company and selected material. Programmes are contextual research within Emissions, with official sources and missing eligibility inputs visible.

The text and voice Operator should read these same server results, explain their limitations briefly, navigate to them and prepare reviewable recovery scenarios. It must not calculate emissions/benefits in a prompt, invent distance/mass, treat hazard exposure as a confirmed delay, or change a sourcing plan without the existing approval boundary. External research still requires consent.

The 52 catalogue material scenarios remain available. Only two catalogue lines currently match a mass-compatible material factor; one is PET with the product-boundary caveat above. Other lines must show a reasoned unavailable/proxy status. Freight can be estimated for stated mass; count, roll, litre or other non-mass quantities need an explicit evidenced or reviewed mass basis. Do not claim universal material-footprint coverage.

## Recommended sequence

1. Correct carbon semantics, persistence and current-plan gating; integrate the current UI, export and Operator. Demonstrate source-backed component comparisons first.
2. Bring in accurate small polish changes and scope-safe market references in parallel, preserving current navigation.
3. Adapt dispatch to company/order/offer identities and connect an explicit delay proposal to the existing recovery/RFQ path. Keep synthetic events and real news evidence distinct.
4. Add verified programme discovery. Release a monetary calculation only when its eligibility, effective dates, required inputs, eligible base and output meaning are supported and tested. Unsupported calculations remain unavailable at the API as well as the UI.
5. Run combined gates and independent desktop/mobile, persistence and real-provider voice acceptance; build one immutable image and deploy to the existing Cloud Run service with rollback. No Cloud SQL, BigQuery, Memorystore, new IAM or paid feed is needed for this integration.

The [implementation plan](superpowers/plans/2026-10-16-taher-hosted-integration.md) defines files, ownership, failure cases and release gates. The [independent reports and verification record](reviews/2026-10-16-taher/verification.json) contain the exact heads and evidence. Older proposals are catalogued [separately](reviews/2026-10-16-taher/older-pr-inventory.md); they should not be bulk-merged to make Git appear current. The existing broad forecast-suite dependency and integrity-pin issues remain separate from this focused approval process.
