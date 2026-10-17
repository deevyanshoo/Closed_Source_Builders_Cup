# ZOORQ QA repair — 17 October 2026

The reviewed code `f045ea5` is deployed to the existing [ZOORQ demo](https://zoorq-963802031195.asia-south1.run.app/demo) as `zoorq-repair-f045ea5`, serving 100% of traffic. Backend and frontend gates pass. Hosted QA found remaining voice and operator presentation defects; this is not a claim of full voice acceptance.

## Delivered changes

- Carbon requests calculate and save the current company’s reviewed purchase assessment. Follow-up freight values reuse that purchase; the Emissions form hydrates canonical inputs, preserves up to five legs and protects unsaved edits. Missing distances, masses and factors are not inferred from a map.
- Preparing an RFQ now saves the canonical current RFQ and its matching unsent ledger artifact. Its download has the exact saved content. Simulated email drafts remain unsent until a separately reviewed simulation records an outbox entry; no external supplier communication occurs.
- Explicit recovery preparation reuses an owned current shipment and exact delay. Negative, signed, decimal, qualified, ranged, zero-day and multi-material action requests clarify in chat instead of silently preparing a different scenario. What-if requests remain read-only. Accepted recovery state is preserved.
- Supply separates Inventory, Routes and Shipments. All declared route/material pairs are selectable, with readable controls, buyer selection, leg status and schematic itineraries when geometry is absent. The operator can narrow owned route reads by material, route and buyer; ambiguous selections clarify rather than silently choosing a route.
- Sidebar contrast, accessible navigation names, mobile controls and microphone-permission feedback were corrected. Quote fixture loading is deferred until Procurement. Public static assets use gzip; private APIs and WebSockets retain their existing handling and security headers.
- Research completion gives the Live model a compact, server-derived dated brief while retaining the full sourced result in the browser and durable history.

## Verification on the deployed code

| Check | Result |
| --- | --- |
| Full backend | 3,551 passed; 263 subtests passed; two provider-call skips; 435.21 seconds |
| Frontend | 574 passed; six skipped across 45 files; session-race checks, TypeScript and production build passed |
| Ten-company workflow/carbon/routes | Passed, 747 requests; one covered quote/recovery/RFQ workflow per company; carbon calculator and replay checks; all 198 route/material pairs; foreign route/material/buyer requests rejected |
| Inventory read audit | All ten companies and all 52 material rows passed; combined with world-news speech, 103 requests |
| Browser desktop/mobile | All ten company workspaces and Routes reachable at 1470px and 390px; no page horizontal overflow. News and dark mobile operator visually reviewed. Temporary viewport and theme overrides reset |
| Real Gemini carbon/RFQ speech | Current material 31,720 kg CO2e and entered-freight combined total 31,823.56 kg CO2e matched the calculator. RFQ was saved and its download matched the canonical draft and ledger body |
| Spoken public research | A diagnostic run passed fresh consent, industry-scoped query, dated answer, sources, durable history and unchanged business state. An earlier attempt stopped at query validation before consent/search; that failure is retained, not described as resolved intermittency |
| World-news speech | Passed; used `read_news`, answered from connected company news, no automatic research or business mutation |
| What-if speech | Passed; used a read-only preview and preserved the accepted plan and RFQ |
| Ten-company inventory speech | Nine passed. Atlas chose `read_risks` and answered about delays instead of inventory; this remains a real routing defect |
| Hosted asset transport | Main script 268,453 bytes identity / 86,331 bytes gzip; exact decompressed package bytes; Vary, no-store and CSP retained; company API uncompressed |

Controlled speech uses real Gemini Live input/output audio and captions. It does not prove a person spoke into the physical microphone, successful interruption, or native recovery approval. The original user workspace’s unsent message was checked and preserved.

Lighthouse 13.5, actual hosted lab runs:

| Device | Performance | Accessibility | Best practices | SEO | LCP | TBT | CLS |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Mobile | 86 | 100 | 100 | 100 | 3.6 s | 40 ms | 0.104 |
| Desktop | 98 | 100 | 100 | 100 | 0.8 s | 0 ms | 0.078 |

The observed pre-change mobile run was 44 performance / 92 accessibility. Scores are individual lab observations, not a guarantee for every device or network.

## Remaining acceptance work

The Mac locked during QA. Two bounded Codex-in-Antigravity briefs are prepared but have not been launched; implementation resumes after the user unlocks it.

1. Show a validated canonical RFQ as a saved artifact with a direct download link, without asking the user to apply an already-created draft. Keep email/outbox and consequential changes on their reviewed approval flow. Restore must work independently of proposal expiry; dirty or mismatched records must not receive a current download link.
2. Make spoken carbon results ask the server’s missing-freight question and retain component figures. Replace internal shipment IDs and repeated fixture labels in recovery readback with human company/material/supplier descriptions, preserving full review and fresh independently verified approval.
3. Route the Atlas broad inventory question through the existing deterministic company-wide reader even when the model chooses the risk tool. Genuine risk requests, foreign-scope checks and action/consent boundaries must remain intact.
4. Run affected/full gates after those changes, verify native recovery approval against a local planner oracle, then build and deploy the replacement and verify the changed hosted paths.

The first native recovery audit stopped before approval because it incorrectly required a seven-day delay to change supplier. The actual planner correctly returned unchanged supplier, 82,000 USD and zero cost delta. A separate private audit now compares the proposed and accepted scenario with `recovery.compare`, preserving all audio, ownership, pre-approval-state and fresh-consent checks; its seven offline checks and independent review passed. It has not yet established hosted recovery approval.

## Coverage, infrastructure and recovery

There are 198 declared route/material pairs: ten have approximate mapped corridors and 188 have schematic itineraries. A profile route is not a tracked shipment or validated vendor facility. Missing modes and onward connections remain explicit. The carbon registry has 52 material rows: two exact factors, four proxy factors, three unit mismatches and 43 unavailable factors; no factors were fabricated to fill coverage.

The existing `closed-source-builder-261007` / `zoorq` infrastructure is retained: minimum zero, maximum one instance, one CPU, 1 GiB, concurrency eight, timeout 240 seconds. Service identity and environment were compared and preserved. Existing Firestore, GCS news snapshots, Vertex AI and restricted Maps configuration remain. No new managed service, billing upgrade, access expansion or real external mail is included.

Cloud Build `3dc8b82b-1644-461d-a800-d699f736bafc` succeeded. Deployed image digest: `sha256:80aef623d12280b9332ecd9f12faf41507f20cd1b7fa10a51c05f6e1ea231739`. The allowlisted bundle contains 389 files / 8,984,687 bytes, manifest `ba356d64cf2b2619850afc249f6156b2fea052943a9b7f22add90a3ed7b8114b`. Nineteen previous content-hashed static assets were retained with byte parity to protect already-open tabs. Private configuration and previous image/revision receipts are retained for rollback; credentials, cookies and private QA transcripts are excluded from Git.
