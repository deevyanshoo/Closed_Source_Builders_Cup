# ZOORQ workflow UX review, 16 October 2026

This release focuses News, Procurement and the Operator on the next business decision. It preserves the existing Light/Dark themes, persistent navigation, custom identity and Thinking Orbs. The final live voice audit also found and corrected an RFQ-to-email-simulation routing error. Service identity, provider configuration, resource caps and billing tier remain unchanged.

## Independent review and separate implementation

Two read-only reviewers audited News/Operator and Procurement. Different implementation agents owned the three disjoint lanes. Reviewers then inspected the resulting code and actual rendered captures; the coordinator integrated, tested and owned browser/cloud/Git operations.

News selection now stays tied to an explicit story ID. If that story disappears under filters or a refreshed collection, the screen explains the missing selection and withholds actions until a new story is chosen. Global Watch uses the same rule. Age and material remain primary filters; additional filters disclose their active count. Mobile chrome is smaller while warnings, 44 px filter controls and absolute source publication times remain available. Story details show source facts once and place the recommended next check ahead of the narrative. Identical summary/implication copy is deduplicated without generating a new impact claim.

Procurement uses the same four stages throughout. Accepted current comparison/recovery results move focus to a heading with an offset for the persistent header; stale, dirty or other-company results cannot take focus. A feasible selected purchase offers Review RFQ draft; stock-covered and infeasible states explain why an RFQ is unavailable. Delay copy describes the complete accepted scenario and labels the reset Remove all delays. Drafts separates the current RFQ and its exact download from earlier unsent drafts and simulated sent records.

The Operator keeps exact research query/destination visible and puts full draft/quote previews before confirmation in expandable sections. Draft/quote review requires opening the preview for that exact proposal and company scope. Missing or blank RFQ/email previews fail closed with a request-new-proposal instruction. Voice offers a bounded recent-current-company conversation disclosure alongside current activity, proposal and consent. Existing voice lifecycle, critical review, context and stale-response protection remain intact.

The first correction was not rubber-stamped. Independent review sent back hidden draft previews that could be confirmed unread, malformed RFQ preview handling, repeated News fallback text, a shrunken filter target and a white-on-white dark-mode next-step panel. Coordinator browser testing also found a focused heading hidden beneath the 58 px persistent header; a separate implementer corrected the scroll offset to 82 px.

## Verified local acceptance

Final web JavaScript: **448 passed, six existing skips**, 33 files; session-race gate passed. Typecheck and production build passed. The native RFQ route, existing voice bridge and exact tool catalog checks pass **28 focused backend tests**. The broader repository run was interrupted after missing PDF/forecast dependencies and failures outside this change; it is not a passing full-suite claim. The entire hosted-demo backend group passes **772 tests**, including RFQ and agent actions. Read-only triage identified missing `statsmodels`, `scikit-learn` and `fpdf`, plus a pre-existing forecast consumer hash/size mismatch in unchanged files; these remain separate follow-up work.

Independent review approved local News Light/Dark desktop and mobile list/detail captures. At 390 × 844, the first story moved from y=672 to y=456 and its bottom is y=581; More filters remains 44 px and the page has no horizontal overflow. Selecting a story then filtering it away displayed the missing-selection notice and removed Ask actions.

Independent review approved eight local Procurement captures. A rule-extracted Swift quote was accepted, compared at 8,000 kg/86,000 USD, delayed ten days to an explicit infeasible state, reset, and followed through review to an unsent RFQ appearing as Current RFQ in Drafts. The focused comparison heading measured y=82.08 on mobile and y=81.99 on desktop; recovery y=82.17 on mobile, all below the 58 px header. Current RFQ download is inside the matching record. No supplier communication occurred.

## Runtime and hosted acceptance

Initial source `3e3a259b6505e028d645c97755c1050a5ccbeef1` was built and deployed as r58. Its actual hosted Operator review exposed remaining flex-shrink clipping, so a separate implementer replaced the competing scroll panes with one continuous review flow, compacted the active header/risk line and formatted the raw ISO timestamp. The independent reviewer approved the code.

The subsequent r59 hosted voice test correctly answered Solara procurement news with named sources, dates and conditional impact, but a spoken RFQ request selected `simulate_email`. That proposal was cancelled without applying it. A separate backend implementer added native `prepare_rfq` using the authoritative current plan and saved quote, preserving the review/confirm gate. Five new WebSocket regressions cover valid purchase, active recovery plan, missing plan, no eligible purchase and stale plan rejection. Independent review approved this route and the human-readable proposal labels.

RFQ release runtime source: `7c1d81f`. The explicit package contains 325 files / 4,359,644 bytes, manifest `7206e74ecf3195ebfb427cce3d4412c9f65334bfbe0179a6c29ae25917432f53`. Cloud Build `36e0f267-99f3-4758-aaf0-260375446977` succeeded. Final hosted acceptance and restoration receipts follow below.

Before the build, the authenticated Console showed an unupgraded Free Trial, INR 27,800.41 credit and 64 days remaining. This is a point-in-time balance, not a measured task cost. The existing Cloud Run caps remain 1 CPU / 1 GiB / max one instance / concurrency eight / 240 seconds. No IAM grant, new service, billing upgrade or actual supplier delivery is authorized as part of this release. Retain r59 and r57 for rollback.

## Original plan and Taher's work

See the [plan gap assessment](ZOORQ_Plan_Gap_Assessment_20261016.md). Taher's carbon calculator is real implementation under open PR #114, and PR #115 separately adds schemes/green finance. Neither is part of this UX source/package. Carbon needs an independent factor/boundary/unit review and integrated hosted acceptance; the broader production/dispatch, export, forecast-policy and compliance ambitions should not be represented as proven hosted capabilities merely because modules exist locally. Git consolidation and one connected disruption demonstration precede further breadth.

## Exact hosted acceptance and evidence

[r60 deployment](ux/2026-10-16-workflow/deployment-r60.json) verified both hosted addresses ready on `closed-source-quote-demo-00060-bjq`, Firestore persistence and 100% traffic. Service account, environment, CPU/memory, concurrency, timeout and max-instance cap matched the approved baseline.

The [company matrix](ux/2026-10-16-workflow/company-screen-matrix-r60.json) passed 40 combinations: all ten companies × Light/Dark × 1440 × 900 desktop / 390 × 844 mobile. Each combination checked News, Procurement and Operator (120 screen observations) for company scope, active navigation, actual viewport width, overflow and composer/voice entry. This is a presentation/navigation matrix, not 120 full procurement transactions or 40 microphone sessions.

[Hosted interactions](ux/2026-10-16-workflow/hosted-acceptance-r60.json) separately exercised the decision path. A ten-day Swift delay changed its arrival to 7 November, leaving no feasible quote; the RFQ path was blocked, the original baseline remained distinct, and focus landed at y=82.17 below the fixed header. Filtering an explicitly selected copper story to polymer showed a missing-selection notice and zero Ask actions.

Actual controlled speech through the browser and Gemini Live selected `read_workspace` → `prepare_rfq`, kind `rfq`, from the current Solara plan. The correct 8,000 kg Swift draft stayed gated until the full preview was opened and the checkbox reviewed. Apply navigated to a Current RFQ in Drafts. The matching current draft endpoint returned HTTP 200/plain text, correct quantity and unsent status; simulated outbox count remained zero. The browser download event did not reach the automation tool, so a completed operating-system file save is not claimed.

A separate completed spoken News turn selected `read_news`, named Mining.com/Reuters with 15 October publication dates, and explained conditional copper price and supply exposure plus a supplier/route check. Provider turn completion followed 1,226,882 output audio bytes; RFQ completion followed 793,442 bytes. Orb connecting/listening/thinking/speaking states were observed. These used controlled Google TTS fixtures, not the physical microphone. End cleanup left zero live test tracks; the immediate post-Mute label probe was not awaited and is not a runtime mute assertion. The original Meridian/Dark browser cookie was restored without changing its saved data, temporary viewport/audio overrides were removed, and the local QA server stopped.

Independent reviewers approved actual News desktop/mobile and Operator RFQ/voice captures. The final Procurement reviewer found one remaining Drafts-arrival header overlap; a separate implementer added `scroll-margin-top:82px` to the existing target. This CSS-only follow-up has its own final revision/viewport receipt below; the r60 transaction/voice/matrix evidence remains explicitly bound to r60.

See [verification scope](ux/2026-10-16-workflow/verification.json), [screenshot dimensions/hashes](ux/2026-10-16-workflow/screenshots.json), and [restoration receipt](ux/2026-10-16-workflow/browser-restoration.json). Earlier r59 files with incorrect mobile filenames were excluded; final published mobile PNG dimensions are 390 × 844.

## Final CSS follow-up and current serving revision

The independent final Procurement reviewer approved both [desktop](ux/2026-10-16-workflow/hosted-drafts-arrival-dark-desktop-r61.png) and [mobile](ux/2026-10-16-workflow/hosted-drafts-arrival-dark-mobile-r61.png) Drafts-arrival captures after the separate implementation. Heading top is 103.5 px / 101.5 px against a 58 px header, with no horizontal overflow; the 82 px target scroll margin correctly clamps at the document start. The exact target scroll used by existing Operator navigation was exercised on a new isolated unsent fixture; the actual spoken RFQ/action transaction remains the completed r60 evidence. No additional voice-contract claim is inferred from this CSS-only delta.

**Currently serving: `closed-source-quote-demo-00061-49v`, runtime source `0485463`, 100% traffic.** [Final deployment receipt](ux/2026-10-16-workflow/deployment-r61.json): build `5ed8e438-26a5-4639-b3dc-c6d2ee2ee042`, image `sha256:1327ece38c1f4106baacff2d100b13630569d03b78cfd5ff2835ba50eb3fc0ee`. Package 325 files / 4,359,667 bytes, manifest `961c3dc705a944d3889c62af041eb3738a6a21dd09f635eb4e7921051f90f965`. Both hosted health addresses report this exact revision and Firestore readiness. All six baseline service-configuration comparisons pass unchanged. r60/r59/r57 remain rollback options.

The original Meridian Pharma/Dark workspace was restored again after final isolated offset QA; no original company data was changed. Temporary browser viewport/media overrides and QA tabs were removed. Release work remains on the reviewed PR #118 branch; the assessment explicitly calls out unmerged main integration and carbon correctness gates.
