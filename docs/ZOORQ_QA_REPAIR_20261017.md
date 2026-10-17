# ZOORQ QA repair — 17 October 2026

Reviewed source `4995a08e947fc82ef267df2d45f1f593373b9381` is deployed to the existing [ZOORQ demo](https://zoorq-963802031195.asia-south1.run.app/demo) as `zoorq-repair-4995a08`, serving 100% of traffic with Firestore persistence. The backend and frontend gates, controlled native carbon/RFQ/recovery approval, world-news speech and all-company inventory checks below pass. A separately discovered broad risk-answer quality defect remains open; this report does not claim complete operator acceptance.

## Delivered changes

- Carbon requests calculate and save the current company's reviewed purchase assessment. Follow-up freight values reuse that purchase. Voice asks the exact missing-freight question and retains material, freight and supported total figures. The Emissions form hydrates canonical inputs, preserves up to five legs and protects unsaved edits. Missing distances, masses and factors are not inferred from a map.
- Preparing an RFQ saves the canonical RFQ and its matching unsent ledger artifact. The operator restores a validated saved RFQ independently of pending-proposal expiry, with recipient, subject, full preview, a direct download and navigation to Drafts. It does not ask users to apply an already-saved RFQ. Dirty, foreign or mismatched records do not acquire a current download link. Reviewed email simulation remains separate; no external communication occurs.
- Explicit recovery preparation reuses an owned shipment and exact delay. Spoken review uses the actual planned purchase, human supplier/material names and source-derived baseline/result/cost delta, including unchanged, infeasible and no-purchase outcomes. Full review, current plan/offer binding, expiry and fresh independently verified approval remain required. What-if requests remain read-only.
- Pure inventory-overview requests use the deterministic current-company reader, including when Live selects `read_risks` for an inventory overview. New native risk calls require the exact nonempty question; missing/malformed arguments clarify without scanning. Risk/news words no longer trigger the generic stock fallback, but the remaining risk-model fallback quality gap is described below.
- Supply separates Inventory, Routes and Shipments. All declared route/material pairs are selectable, with buyer selection, leg status and schematic itineraries when geometry is absent. Owned route reads can narrow material, route and buyer; ambiguous selections clarify. Sidebar contrast, navigation names, mobile controls and microphone-permission feedback were corrected. Dark operator preview summaries now have measured 7.03:1 text contrast.
- Quote fixtures load on Procurement; public static assets use gzip. Private API/WebSocket handling and security headers remain. Research completion supplies Live with a compact dated server brief while retaining full sources in the browser and durable history.

## Verification

| Check | Observed result and scope |
| --- | --- |
| Full backend | **3,583 passed, 263 subtests passed, two provider-call skips**, 433.90 seconds, on `c853e56`. The only subsequent product change in `4995a08` is CSS; backend/config/fixtures are byte-identical. |
| Full frontend | **578 passed, six skipped** across 45 files. Session-race checks, TypeScript and production build passed, including the final CSS change. |
| Package | Three package-parity checks passed. Exact source/dist hashes, no symlinks or credential files/key patterns; previous content-hashed assets retained byte-for-byte. |
| All-company text inventory/sourcing | **Ten of ten passed on `zoorq-repair-4995a08`**, covering the primary material and company-wide overview per company, with source-matched numbers and protected business state. |
| Structured inventory | **Ten companies and all 52 material rows passed on `zoorq-repair-4995a08`** in the judge-experience audit: complete material ownership, stock, unit and computed cover checks. Foreign route request rejected. |
| All-company inventory speech | **Ten of ten passed on `zoorq-repair-c853e56`**, including Atlas. Real Gemini Live received controlled spoken input, used qualifying owned tools and returned audio/captions. The backend is identical in `4995a08`. This does not claim every numeric material row was spoken aloud. |
| Native carbon → freight → RFQ → reviewed recovery → fresh approval | **All five turns passed on `zoorq-repair-4995a08`**. Material 31,720 kg CO2e; entered road freight 103.56 kg CO2e; supported total 31,823.56 kg CO2e matched the local calculator. RFQ download exactly matched canonical draft and ledger. A seven-day scenario correctly kept Europa Copper SA, 82,000 USD and zero cost delta; fresh spoken “Confirm action” applied the independently checked scenario. No mutation before approval. |
| Spoken public research | **Passed on `zoorq-repair-c853e56`**: exact industry-scoped public query, separate fresh spoken consent, dated sourced answer, durable history and unchanged business state. No automatic retry was used. |
| World-news speech | **Passed on `zoorq-repair-4995a08`** for “What's happening in the world that can affect my business procurement?” Used connected company news through `read_news`, grounded the spoken implication, and performed no automatic external research or business mutation. |
| Saved RFQ browser | Restored after reload and company navigation; saved artifact visible without redundant Apply, actual Chrome download event succeeded. Desktop and 390×844 mobile reviewed; no horizontal overflow, primary artifact controls at least 44px high. Final dark preview text/background measured 7.03:1. Temporary theme/viewport overrides reset. |
| Earlier unchanged workflow/route coverage | On `f045ea5`, 747-request audit passed one quote/recovery/RFQ workflow per company, carbon/replay checks, all 198 declared route/material pairs and foreign route/material/buyer rejection. All ten desktop/mobile workspaces and Routes were reachable. These are historical coverage receipts, not fresh exhaustive execution on `4995a08`. |

The two offline backend skips are the explicitly opt-in real-Gemini extraction tests. Actual hosted Live speech above is separate from those tests. Independent RFQ, voice confirmation, shared routing, dark CSS and release-history reviews were completed. New regression failures were observed before the production corrections.

The native acceptance harness needed independently reviewed corrections: compare a delay scenario with the local recovery planner rather than demand a supplier change; normalize caption whitespace; and require the public active plan to equal the accepted recovery scenario, while `recovery.baseline` preserves the original. Distinct scenario identity, offer versions, full oracle fields, preapproval nonmutation and fresh spoken approval checks remain. Failed/preflight receipts are retained privately; the final five-turn run is a new successful run, not a relabeled earlier failure.

Controlled speech proves real Gemini Live input/output audio and captions, not a person speaking into the physical microphone or successful interruption. The browser microphone check reached Chrome's permission request and was ended; a physical human microphone/interruption walkthrough remains unverified. The original user workspace's unsent message was preserved.

## Lighthouse

Lighthouse 13.5, actual individual hosted lab runs on `zoorq-repair-4995a08`:

| Device | Performance | Accessibility | Best practices | SEO | LCP | TBT | CLS |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Mobile | 79 | 100 | 100 | 100 | 4.7 s | 30 ms | 0 |
| Desktop | 98 | 100 | 100 | 100 | 0.8 s | 0 ms | 0.078 |

Mobile was measured while hosted voice/API QA was active; that is context, not a proven explanation of its score. Mobile load speed needs further improvement. Previous `f045ea5` observations were mobile 86 and desktop 98; the pre-repair mobile baseline was 44 performance / 92 accessibility. Scores are lab observations, not guarantees across devices or networks.

## Open quality issue and next increment

The exact Atlas question **“Analyze our inventory and sourcing risks across all materials.”** still falls through to the model's analytics response. Direct text returned stock-cover figures with long decimals rather than alert impacts/recovery actions. A fresh observed native turn recognized the exact question, selected analytics, spoke stock figures and a generic market-watch statement, but returned no owned risk projection. This is a demonstrated text/voice answer-quality defect, despite pure inventory and world-news checks passing.

Independent review traced the gap to the fallback after risk-word exclusions: the owned `risk_report` is available as model context but is not authoritative routing. The next bounded correction is to route explicit owned inventory/sourcing risk/alert reads through the existing current-company scanner and projection, with concise grounded impacts and recovery suggestions. It must preserve foreign-company, news/research, action and fresh-approval boundaries, and use meaningful actual HTTP/native regression assertions rather than only banning a literal stock phrase.

That implementation brief is prepared but **has not been run**: the Mac relocked and Antigravity terminal access awaits user unlock. No unfinished risk-fix code is in this release. A further physical microphone/interruption check and mobile performance work also remain open.

## Coverage, infrastructure and rollback

There are 198 declared route/material pairs: ten approximate mapped corridors and 188 schematic itineraries. A profile route is not a tracked shipment or validated vendor facility. Missing modes and onward connections remain explicit. The carbon registry has 52 material rows: two exact factors, four proxy factors, three unit mismatches and 43 unavailable factors; none were fabricated to fill coverage.

Existing `closed-source-builder-261007` / `zoorq` resources are retained: minimum zero, maximum one instance, one CPU, 1 GiB, concurrency eight, timeout 240 seconds. Service identity, environment and resource settings were compared and preserved. Existing Firestore, GCS news snapshots, Vertex AI and restricted Maps configuration remain. No new managed service, billing upgrade, access expansion or external supplier mail was included.

Regional Cloud Build `1206d9c9-4166-4fd7-bbf7-89236872904f` succeeded. Deployed image digest: `sha256:ee4d14006396de3642d07cd0bb543e65d66d6bc69516095336de0166b2162890`. The allowlisted final bundle contains 412 files / 11,477,547 bytes, manifest `3f1a19281ff3a7d0e387dfa3baf14e0a6a0e3999ec75951aad4d9e8f1df31279`. Fifty-five prior hashed assets are retained for already-open tabs. Private configuration and prior revision/image receipts are retained for rollback; credentials, cookies and private QA transcripts are excluded from Git.
