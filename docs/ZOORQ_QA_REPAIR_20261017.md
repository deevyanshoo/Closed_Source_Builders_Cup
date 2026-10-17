# ZOORQ QA repair — 17 October 2026

Reviewed source `cfdec187c995aa57981079b3cb475ad92d6fda58` is deployed to the existing [ZOORQ demo](https://zoorq-963802031195.asia-south1.run.app/demo) as `zoorq-riskvoice-63b86ce`, serving 100% of traffic with Firestore persistence. The final backend gate, ten-company risk and inventory reads, exact Atlas risk speech, native carbon/RFQ/recovery approval and world-news speech passed. Coverage and remaining checks are separated below.

## Delivered changes

- Explicit questions about the active company's inventory and sourcing risks now read its existing risk assessment directly. Answers include owned impacts, conditional timing and recovery options, with readable numbers and human source labels. Text and native voice share the same structured evidence. Plain operational risk reads do not call an operator model or refresh public news. Explicit news, external research, actions, cancellations, what-if and foreign-company requests retain their separate routes and guards.
- Native voice receives the full required risk brief only for a completed, deterministic, current-company/context, read-only result without a proposal or action. Mixed model steps, stale or foreign results and malformed projections do not receive that authority. The exact Atlas question now audibly includes the conditional 11-day supplier-option lateness and the faster-alternatives review. Original structured provenance remains intact; repeated fixture qualifiers are removed only from known generated display phrases.
- Carbon requests calculate and save the current company's reviewed purchase assessment. Follow-up freight values reuse that purchase. Voice asks for missing freight inputs and states material, freight and supported total figures. The Emissions form hydrates canonical inputs, preserves up to five legs and protects unsaved edits. Missing distances, masses and factors are not inferred from a map.
- Preparing an RFQ saves the canonical RFQ and matching unsent ledger artifact. The operator restores a validated saved RFQ independently of pending-proposal expiry, with recipient, subject, full preview, direct download and navigation to Drafts. Dirty, foreign or mismatched records do not acquire a current download link. Reviewed email simulation remains separate; no external communication occurs.
- Explicit recovery preparation reuses an owned shipment and exact delay. Spoken review uses the actual planned purchase, human supplier/material names and source-derived baseline/result/cost delta, including unchanged, infeasible and no-purchase outcomes. Full review, current plan/offer binding, expiry and fresh independently verified approval remain required. What-if requests remain read-only.
- Pure inventory overviews use the deterministic current-company reader, including when Live selects `read_risks` for an inventory overview. Native risk calls require the exact nonempty question; malformed arguments clarify without scanning.
- Supply separates Inventory, Routes and Shipments. All declared route/material pairs are selectable, with buyer selection, leg status and schematic itineraries when geometry is absent. Owned route reads can narrow material, route and buyer; ambiguous selections clarify. Sidebar contrast, navigation names, mobile controls and microphone-permission feedback were corrected. Dark operator preview summaries have measured 7.03:1 text contrast.
- Quote fixtures load on Procurement; public static assets use gzip. Private API/WebSocket handling and security headers remain. Research completion supplies Live with a compact dated server brief while retaining full sources in the browser and durable history.

## Final verification

| Check | Observed result and scope |
| --- | --- |
| Full backend | **3,656 passed, 263 subtests passed, two provider-call skips**, 425.85 seconds, on the final approved source committed as `cfdec187`. Ten existing dependency/archive warnings; zero failures. |
| Full frontend | **578 passed, six skipped** across 45 files on the preceding `4995a08` frontend. Session-race checks, TypeScript and production build passed. This risk increment changes no frontend source; all deployed frontend files are byte-identical. |
| Package | Three package-parity checks passed. Actual prepared bundle source/dist bytes and manifest hashes verified; no symlinks, credential files or key patterns. All previous frontend files, including 66 hashed JavaScript/CSS assets, are retained byte-for-byte. |
| Ten-company operational risk text | **Ten of ten passed on `zoorq-riskvoice-63b86ce`** for “Analyze our inventory and sourcing risks across all materials.” Each answer contains owned alert and recovery text, one company projection, no operator provider call, exact idempotent replay and unchanged protected business state. Measured request times: 121–269 ms in this run. |
| Atlas risk speech and typed parity | **Passed on `zoorq-riskvoice-63b86ce`**. Exact spoken question recognized; actual output audio/caption states the conditional naphtha option would be 11 days late and suggests reviewing faster alternatives. Native/typed alert identities, metrics, evidence, actions and counts match. No business mutation or public research. Full spoken turn: 37.8 seconds, including the complete answer; this is not a first-response latency measure. |
| Ten-company inventory/sourcing text | **Ten of ten passed on `zoorq-riskvoice-63b86ce`** for the broad inventory/sourcing overview. Structured checks cover **all 52 material rows**, including ownership, stock, unit and computed cover. Foreign route request rejected. |
| Native carbon → freight → RFQ → reviewed recovery → fresh approval | **All five turns passed on `zoorq-riskvoice-63b86ce`**. Material 31,720 kg CO2e; entered road freight 103.56 kg CO2e; supported total 31,823.56 kg CO2e matched the local calculator. RFQ download exactly matched canonical draft and ledger. A seven-day scenario correctly kept Europa Copper SA, 82,000 USD and zero cost delta; fresh spoken “Confirm action” applied the independently checked scenario once. No mutation before approval. |
| World-news speech | **Passed on `zoorq-riskvoice-63b86ce`** for “What's happening in the world that can affect my business procurement?” Used connected company news through `read_news`, grounded the spoken implication, and performed no automatic external research or business mutation. |
| Risk-answer browser | Atlas answer reviewed at desktop and 390×844 mobile on `6a1d8ae`: readable owned timing/review suggestion, no horizontal overflow, primary controls at least 44px high. Saved answer restored after reload on final deployed source. Temporary viewport override reset; user workspace was not used for these checks. |

The two offline backend skips are the explicitly opt-in real-Gemini extraction tests. Actual hosted Gemini Live speech above is a separate provider check. Native acceptance sends controlled audio through the real service and requires recognized input, qualifying owned tools, meaningful output captions, output audio and completion. It does not prove a person speaking through the physical microphone or successful interruption.

Independent reviews approved the final intent guards, source-copy handling, voice-result contract, QA-client caption reconstruction and hosted receipts. Regression failures were observed before the corresponding corrections. The intermediate `6a1d8ae` Atlas voice run correctly failed because speech omitted the timing and recovery option; the final deployed run passed those same semantic assertions. Failed receipts remain retained privately.

## Earlier coverage retained separately

- All-company inventory speech passed ten of ten on `zoorq-repair-c853e56`, including Atlas. This does not mean every numeric material row was spoken aloud, and is not a fresh all-company voice run on the final revision.
- Spoken public research passed on `zoorq-repair-c853e56`: exact industry-scoped public query, separate fresh spoken consent, dated sourced answer, durable history and unchanged business state. This increment did not repeat that external-research provider scenario.
- Saved RFQ browser restoration, reload, company navigation and actual Chrome download passed on `zoorq-repair-4995a08`; desktop/mobile controls and dark preview contrast were reviewed. The final revision separately passed canonical RFQ creation/download in the five-turn native test.
- On `f045ea5`, a 747-request audit passed one quote/recovery/RFQ workflow per company, carbon/replay checks, all 198 declared route/material pairs and foreign route/material/buyer rejection. All ten desktop/mobile workspaces and Routes were reachable. These are historical coverage receipts, not fresh exhaustive execution on the final revision.
- The independently reviewed native harness compares delay scenarios with the local recovery planner rather than demanding a supplier change. It normalizes caption deltas/replacements, requires public active plan equality with the accepted scenario, and preserves distinct scenario identity, offer versions, full oracle fields, preapproval nonmutation and fresh spoken approval checks.

## Lighthouse and remaining checks

Lighthouse 13.5, quiet sequential hosted lab runs on `zoorq-risk-6a1d8ae`. Its frontend is byte-identical to the final deployed frontend:

| Device | Performance | Accessibility | Best practices | SEO | LCP | TBT | CLS |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Mobile | 88 | 100 | 100 | 100 | 3.5 s | 50 ms | 0.104 |
| Desktop | 96 | 100 | 100 | 100 | 1.1 s | 0 ms | 0.078 |

Earlier `4995a08` runs measured mobile 79 / desktop 98 performance. These are individual lab observations, not a proven performance trend or guarantees across devices and networks. Mobile load speed and layout stability still have room to improve. A physical human microphone/interruption walkthrough remains unverified; the earlier browser microphone check reached Chrome's permission request and was ended.

## Coverage, infrastructure and rollback

There are 198 declared route/material pairs: ten approximate mapped corridors and 188 schematic itineraries. A profile route is not a tracked shipment or validated vendor facility. Partial/unavailable geometry remains represented in structured results; catalogue reads are not model-call receipts. Missing modes and onward connections remain explicit. The carbon registry has 52 material rows: two exact factors, four proxy factors, three unit mismatches and 43 unavailable factors; none were fabricated to fill coverage.

Existing `closed-source-builder-261007` / `zoorq` resources are retained: minimum zero, maximum one instance, one CPU, 1 GiB, concurrency eight, timeout 240 seconds. Service identity, environment and resource settings were compared and preserved. Existing Firestore, GCS news snapshots, Vertex AI and restricted Maps configuration remain. No new managed service, billing upgrade, access expansion or external supplier mail was included.

Regional Cloud Build `8b8c35c0-5b50-43e1-9352-3972f7e61430` succeeded. Deployed image digest: `sha256:ddb1b3c53670385ab85f835110a216ea095319172e35e653f9dae964b0679064`. The allowlisted final bundle contains 413 files / 11,490,504 bytes, manifest `3bd14cd38550cda10bc36e65e2c858693bf65540a4be4e4ac237c33f4dfaf502`. The previous `zoorq-risk-6a1d8ae` and `zoorq-repair-4995a08` revisions/images are retained for rollback. Private configuration, cookies and QA transcripts are excluded from Git.
