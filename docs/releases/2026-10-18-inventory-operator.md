# Inventory-aware planning and operator actions — 18 October 2026

This is the first decision-closure release from the approved remediation plan. It connects reviewed inventory, quote arrivals, supplier-delay recovery and saved RFQ actions. Unfamiliar-document intake, evidence-ranked news, decision-bound routes and reproducible outcome evidence remain separate follow-on workstreams.

## User-visible behavior

An order arriving before its commercial deadline can still leave production without stock. Overview, sourcing comparison, recovery and the operator now separate order feasibility from continuous production supply. The assessment includes inventory and consumption dates, first shortage date, uncovered duration and quantity. A new comparison prefers the cheapest commercially feasible continuously supplied option when one exists. Reading a saved comparison never silently changes the selected supplier.

For Solara's reviewed 14 October inventory, the selected 24 October arrival meets the order deadline, but opening stock runs out around 20 October: approximately four days and 1,333.3 kg uncovered. The primary sourcing action prepares an earlier-delivery RFQ for this shortfall. It requests fresh capacity, price and terms, rather than treating the original quote as an earlier-delivery promise. The accepted purchase plan stays unchanged.

Typed and native voice requests share owned-workspace facts. An explicit earlier-delivery quotation request creates an unsent draft visible in chat, Drafts & activity and its exact download. Repeating the request reuses the same artifact. Conditional, negated, foreign-material, stale or ambiguous requests fail closed or ask for clarification without changing business state. Missing or inconsistent inventory inputs remain unknown. Whole-item bridge quantities round up; continuous material quantities retain the calculated amount.

A safe voice clarification is spoken before a fresh complete action request. Local inventory answers use the current company's plan without public research. The broker accepts the provider's optional transcription-finality field after a valid settled speech boundary; it does not reuse closed utterances. Ordinary commands have their own bounded duration. The separate eight-second research/action-consent guard and authorization checks remain unchanged. Eligible local clarification reviews wait through an entirely empty completion within their original deadline; stale or contradictory audio remains blocked. This fixes locally reproduced completion-boundary failures; the precise packet ordering of the earlier hosted ForgeLine failure was not observed.

The existing light/dark design, Thinking Orbs, multilingual Gemini on Vertex AI label and single workspace disclosure remain. Comparison actions precede detailed buyer inputs. QA corrected contrast, deferred 3D shader preparation and stabilized first-visit overview/navigation/comparison geometry across ready and unavailable states.

## Release identity and limits

- Canonical workspace: [ZOORQ](https://zoorq-963802031195.asia-south1.run.app/demo).
- Runtime source: `575ba48f20d616b15d113f5b4cdf7517dc70dfc1`.
- Reviewed change: [PR #136](https://github.com/deevyanshoo/Closed_Source_Builders_Cup/pull/136).
- Cloud Build: `2590cdc8-4a70-4c56-9b48-ac34edb478ad`.
- Active revision: `zoorq-decision-575ba48`, 100% traffic.
- Immutable image digest: `sha256:b60317a7c1b0b1e7bd4233201ddbcc796fda5b77c4659dfe12c28eaa89530f3b`.
- Source bundle SHA-256: `c6e577a1d8845515efcf26797be5a6e880aef3728f70b061781699ac1b169140`.

Only the existing `zoorq` service changed. The legacy service remained at `closed-source-quote-demo-voice-2285048`. Exact packaged JS/CSS, Firestore readiness and the active image were verified after activation. Existing minimum 0 / maximum 1 instance, 1 CPU, 1 GiB, concurrency 8, 3,600-second request timeout, service account and environment fingerprint were preserved. Existing trial resources were used without billing, IAM or service expansion. Rollback revisions and immutable images remain available. Product code was implemented by parallel Codex CLI agents launched in Antigravity terminals; independent reviewers assessed frozen changes.

## Acceptance evidence

| Check | Result and evidence boundary |
| --- | --- |
| Final full backend suite | 4,209 passed, 2 skipped, 263 subtests passed; 10 warnings; 609.81 seconds. |
| Full frontend suite | 632 passed, 6 skipped across 48 files; session-race, typecheck and production build passed. Frontend bytes are identical between `1eb0de2` and final `575ba48`. |
| Independent source review | Numerical, action, UX and final immutable clarification-boundary reviews completed; no unresolved load-bearing source finding at `575ba48`. |
| Hosted business workflows | All 52 materials across all 10 companies; 156 bundled quote fixtures, 2,749 HTTP requests, zero transport retries. Independent Decimal coverage checks, conditional/foreign refusal checks, recovery/RFQ, exact downloads and durable Firestore exports passed on `d97afa6`. Those core paths are unchanged; later native-voice and frontend corrections were separately checked. This is carried-forward hosted evidence, not a new 52-material run on `575ba48`. |
| Final live voice negative case | Actual hosted Gemini received a controlled spoken proposal request and spoke the quotation-confirmation question without changing business state. One start and one turn, 15.36 seconds; returned PCM retained. |
| Final ten-company text and voice actions | Pending final protocol and independent semantic acceptance. |
| Final covered-plan voice read | Pending final protocol and independent semantic acceptance. |
| Responsive UI | Forty local overview states across ten companies, light/dark and desktop/mobile; additional 360/1440 sourcing, recovery, draft and operator inspections. Hosted desktop/mobile comparison buttons aligned, with no page overflow. Ready/unavailable geometry checked; 360-pixel factual wrapping retained a 6-pixel difference. |
| Hosted Lighthouse with normal graphics | Desktop performance 99; mobile 96. Accessibility, best practices and SEO 100 on both. Measured `1eb0de2` frontend, byte-identical to final. Desktop/mobile CLS 0.0485 / 0.0194; LCP 818 / 2,780 ms. |

The 156 quote extractions use the bundled rules path, not 156 live Gemini document extractions. Controlled voice acceptance sends generated speech through the actual hosted Gemini Live socket, checks tools and returned captions, and retains returned 24 kHz PCM. It does not establish physical-microphone conversation, human listening or independent transcription of the returned audio. Private QA workspaces preserve the original user workspace. The nine non-Solara voice action scenarios use reviewed zero opening stock while retaining the accepted purchase quantity; source company profiles are unchanged.

Failed and superseded runs are retained privately. In particular, earlier voice tests exposed ambiguous acronym handling, stale utterances, optional provider finality, an ordinary command exceeding the consent-duration limit and an empty clarification completion. The final test harness permits one fresh, complete follow-up request only after a verified local unsaved clarification. It does not infer authorization from a bare yes or join fragments into a command. An intentionally stopped two-company run retains its overall incomplete status; its fully completed Solara case can contribute to a separate final acceptance receipt, together with the nine-company run on the same runtime.

## Remaining work

Unfamiliar supplier-document evaluation/intake, evidence-ranked actionable news, routes bound to the current sourcing decision and reproducible before/after outcome evidence remain approved follow-on workstreams. This release does not claim those are complete or establish forecasting accuracy, confirmed supplier availability or realized customer savings. Existing research-specific presentation exceptions in earlier reports are not revalidated by this inventory/operator acceptance.
