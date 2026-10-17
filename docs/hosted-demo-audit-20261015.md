# Hosted demo audit — 15 October 2026

Completed hosted acceptance on 15 October 2026. The final serving revision is `closed-source-quote-demo-00043-n5f`; the detailed backend matrix ran on its byte-identical inherited r41 backend. See the phase-specific evidence below.

## Scope and delivery

Audit all ten company workspaces, with real Gemini Live voice/tool calls, twenty real typed agent turns, scheduled news and Global Watch, and the 52-material / 156-quote deterministic workflow. Parallel workers used Antigravity and the shared checkout with disjoint file ownership. The coordinator controls browser, cloud, integration and Git.

The authorized destination is the existing `closed-source-quote-demo` Cloud Run service in `closed-source-builder-261007`, region `asia-south1`. Only existing unupgraded trial credits are authorized. No new IAM, billing upgrade, service, external notification or real supplier delivery is part of this audit.

## Demonstrated defects repaired

1. A long multi-company visit exhausted the original 8 MiB session limit because historical idempotency results repeatedly copied the growing workspace. At the first hosted failure, those copies accounted for 7,480,027 of 8,214,097 raw bytes. Keep every retry key, digest and operation metadata; retain exact responses for the newest 128 insertion-ordered actions within 2 MiB. Retired keys return `409 retry_result_expired` after digest-conflict checking and never silently repeat an action. In-flight/failed `result=None` records, business data, proposals and research claims remain intact. Original 8 MiB JSON / 700,000-byte encoded limits and schema/codec are unchanged. A full local catalogue saves at 5,705,130 raw / 247,924 encoded bytes. The expanded-codec prototype was rejected in review and never deployed.
2. The operator disabled a second recovery proposal after the first recovery changed the displayed active plan. Bind recovery review to the original server baseline only when the active scenario ID and quote versions match the current recovery view. RFQs continue to bind to the active scenario; dirty/stale inputs remain blocked.
3. JSON exports reported ephemeral storage while the actual session used Firestore. Exports now reflect the store's real persistence mode.
4. Sector-only stock analyst price-target headlines were promoted for Meridian Pharma and CircuitPeak without physical material or route exposure. Demote those financial-rating matches while preserving commodity-price and supplier-production news. Stored snapshots are reclassified on read without changing provenance or collection clocks.
5. The voice orb's intended circular dimensions lost to a more specific direct-child `width:100%` selector. Exclude the orb from that rule, preserving its desktop/mobile dimensions and full-width controls/status. A further phone-width check reproduced an active voice screen hidden by the closed mobile chat drawer; the mobile dock must stay visible whenever its voice screen is active so Back/End remain accessible.

## Verification completed so far

- Frozen backend/UI source release review approved the revised bounded journal; independent cold-restart probes for retired operator, research and voice requests returned 409 with zero provider calls. A compatible rollback retains the retirement guard.
- Final backend/demo/audit gate: 706 passed, 8 dependency warnings. The later final harness gate passed 20 tests. Web: 362 passed, 6 existing skips; session-race, typecheck and production build passed after the orb correction.
- Backend release `closed-source-quote-demo-00041-hjf` serves image `sha256:919f6d160d5119dfe17422d6f1deb86d0561d19b90d89ee4585e50440d37a90b`; both existing URLs report ready / Firestore.
- All ten companies passed 20 typed model-backed inventory/form turns, with 60 real model-role calls, plus their direct quote/recovery/RFQ checks. The original sandbox-only preflight failed before any provider call; its receipt remains separate.
- All ten companies passed native continuous voice inventory facts, unsent email preparation, visible draft inspection and reviewed simulated-send/replay checks. One initial Forgeline test lacked a selected material and correctly elicited clarification; the corrected harness mirrors browser buyer/material synchronization, uses one transport attempt and passed in a fresh session. Original failed evidence is retained. No real email was sent.
- Meridian Pharma voice research waited for spoken consent and performed one industry-aware Search on the same socket. Checking-consent began 517 ms after approval audio ended; result arrived at 6.563 seconds and first final-answer audio at 7.517 seconds. A bounded progress cue played. These are measured observations for one run, not universal latency guarantees.
- All ten company news/risk feeds passed the r41 audit with no findings. Shared Global Watch contained 18 events with company-specific conditional exposure. The normal 16:00 UTC hourly job completed using the new image and published a 1,405,908-byte snapshot with ten views and Global Watch; SHA-256 `55b26d0dbdcc6b42c7189380630c1a847fc5bcf150858d603e6980229d07016f`.
- Native Chrome r41: reviewed Swift quote → baseline arrival 28 October → first 3-day scenario 31 October → second operator-proposed 2-day replacement 30 October → reviewed 8,000 kg RFQ visible in Drafts. Microphone permission/connection, listening, mute, End and editable text composer after End were observed. Controlled factual voice testing is separate from microphone connection testing.
- Earlier browser audit also exercised real Gemini PDF extraction, AI loading state, infeasible delay/RFQ gates, company draft isolation, real RFQ download, news-to-composer context/focus and 390px mobile overflow checks.

## Evidence and remaining gates

Private local receipts are excluded from Git under `/output/`. Coordinator receipts are under the ChatGPT project `output/evidence`; worker receipts are under the repository `output/evidence`. Relevant basenames include `hosted-audit-procurement-20261015-00041-hjf.json`, `hosted-audit-agent-actions-00041-network-20261015.json`, `hosted-audit-news-00041-20261015.json`, `hosted-audit-voice-coverage-summary-00041-20261015.json`, `hosted-audit-research-voice-00041-20261015.json`, and `hosted-audit-browser-final-00041-20261015.json`. No cookies, CSRF tokens or credential artifacts are committed.

The full continuous material matrix passed on r41: **52 unique material scenarios, 156 fixtures, ten companies, 2,486 HTTP requests, zero transport retries and 2,262 preserved idempotency keys**, in 23 minutes 18 seconds. The final Firestore export matched session state.

| Company | Materials passed | Typed turns passed | Native voice/action passed |
|---|---:|---:|---|
| Solara Cables | 3 | 2 | Yes |
| TerraPack Films | 3 | 2 | Yes |
| LoomWorks Textiles | 3 | 2 | Yes |
| Harvest Foods | 4 | 2 | Yes |
| Meridian Pharma | 3 | 2 | Yes |
| ForgeLine Components | 3 | 2 | Yes, corrected material setup |
| GridSpring Batteries | 10 | 2 | Yes |
| CircuitPeak Systems | 3 | 2 | Yes |
| Atlas Petrochem | 12 | 2 | Yes |
| StoneBridge Materials | 8 | 2 | Yes |

Monitoring evidence from 15:58 through the latest available 16:20 UTC sample recorded 2,906 HTTP 200 responses, 13 WebSocket upgrades, expected negative-test 422 responses and no HTTP 5xx. The maximum sampled memory utilization was 42.58% of the existing 1 GiB limit. HTTP mean was 433 ms and the aggregate p95 histogram upper bound was 663 ms, excluding WebSocket 101; these are dominated by deterministic requests and are not voice/research latency claims. A later error-log read across r41/r42 also returned zero error rows.

The first static-only overlay r42 passed both URL health/readiness and exact deployed-asset checks, all ten news/risk views, a representative real voice/action flow and cross-restart browser recovery/RFQ retention. Its backend is inherited unchanged from r41. The final responsive overlay **r43** passed both URL health/readiness and exact deployed CSS checks. Native Chrome verified the circular desktop/390×844 phone orb and the desktop-to-phone transition with Back/End visible. Keyboard End closed voice, cleared the microphone recording indicator and restored the chat launcher. Temporary device emulation was reset and DevTools closed. The existing recovery and RFQ survived both static-only process restarts. No further backend changes followed the full catalogue audit.

## Operational limits

The service remains capped at one instance, one CPU, 1 GiB, concurrency eight and 240-second request timeout; the hourly worker retains its existing identity, caps, timeout and schedule. This is a bounded hackathon-demo audit, not concurrent-load or horizontal-scaling acceptance. Some upstream news providers time out; partial/stale provenance and original success clocks stay visible. End-to-end factual voice uses controlled QA speech; hardware microphone connection/controls passed, but human accent/noise quality is not comprehensively certified.

The original Atlas browser workspace was restored with explicit user approval after the test tool lost its saved handle. Its exact original cookie expiry, Secure/HttpOnly/Lax flags and visible history were verified; no stored business data was changed. The temporary private recovery cookie file was deleted. Browser automation SDK availability was intermittent; native Chrome controls provided visual checks.

## Final delivery and recovery

- Service: `closed-source-quote-demo-00043-n5f`, 100% traffic; image `sha256:9659a73ef0a2925d668209b49aa383ce0e166b5e196fd87eff32b5e646133104`. Final static build `e8cc28b8-ed33-4fa6-b23f-8c68368cb902` succeeded. Its Dockerfile inherits immutable backend image `sha256:919f6d160d5119dfe17422d6f1deb86d0561d19b90d89ee4585e50440d37a90b` and copies only `/app/web`.
- Backend build `d7ccc0fe-48cf-423f-975a-0b76b916cc2e`, source-bundle manifest `0cb4788676d6cee34ec35b607e5fec12ab149e6853b49ed28f7536b0ee984529`; review diff `44f579771341477819c04e373cda87e6bd28345af120a9d913e1df2d8307b85c`. All reviewed source hashes remained unchanged through final delivery; the two subsequent CSS-only changes received separate independent source review.
- Existing hourly job retains the accepted backend image. Its ordinary `closed-source-news-refresh-6wbkt` execution at 16:00 UTC completed successfully; no forced repeat or schedule change was needed.
- Compatible rollback image: `sha256:05773f2eed8a91c768c0eae289291abd43a7ca80efe104442024747205f1c88a`, built `4e77c937-55d9-4760-82c6-d1cdb5705f8c`. It retains the bounded journal and explicit retired-retry guard with the earlier UI; do not replace it with an unpatched reader if preserving the explicit 409 contract. Backend r41 image is also compatible. No rollback was executed.
- Final source and audit record are recorded on `feature/hosted-quote-to-plan` and the existing draft PR106; main/history are preserved. Private receipts and screenshots remain local and ignored.

[Live demo](https://closed-source-quote-demo-ryoeyuqvta-el.a.run.app/demo) · [Draft PR106](https://github.com/deevyanshoo/Closed_Source_Builders_Cup/pull/106)
