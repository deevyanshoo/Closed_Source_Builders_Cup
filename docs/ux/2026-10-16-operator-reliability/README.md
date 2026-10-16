# Broad inventory and sourcing operator repair — 16 October 2026

The reported question was “Explain the inventory position and sourcing options for Copper cathode at Solara Cables.” It missed the narrow canonical fact reader and depended on model prose obeying a strict fact-token format. Ordinary digits in that prose could trigger the unverified-numerical-claim guard. The preceding release also exposed an unresolved optional evidence placeholder, repaired separately in e395a0d.

## Resulting behavior

Clearly read-only inventory-and-sourcing questions now use current workspace facts directly, with no model calls. A named material gets stock, daily use and units, rounded stock cover, the shortest listed lead, a conditional timing assessment and up to three matching supplier profiles. Company-wide requests show the three priority materials with separate units and sourcing leads. Answers use readable paragraphs/bullets and distinguish supplier directory entries from owned saved reviewed quote documents. No invented prices, shipment delays, guaranteed availability or certain stockouts are inferred.

Scope comes from the request and authoritative company snapshot. Unknown/foreign materials and companies do not silently become an unrelated company overview. Current/selected material requires a valid selection consistent with the buyer form. Precise “do not change anything” wording remains read-only; genuine mixed action/research requests retain the existing action and consent flow. Explicit provenance questions retain truthful record-source disclosure.

The API recognizes this same deterministic scope before checking model availability or reserving three AI calls. Session request/turn limits, concurrency, company/context versions, idempotency, generation markers, stale-result protection and human action approval remain intact. This does not loosen numerical/citation validation on model-generated responses.

## Tests and review

- Root ran all demo backend tests plus the initial audit checker suite: 829 passed, 8 warnings, two subtests passed. The final audit-only refinements passed 12 focused checks. Production web typecheck/build passed; no frontend source changed. Existing Three.js size and Python deprecation warnings remain. Unrelated analytical DB/forecast tests were not run.
- The scoped backend gate uses existing SDK/runtime dependencies, `--noconftest`, and the repository's exact autouse voice process-quota reset fixture as a temporary plugin; this excludes unrelated analytical DB initialization without changing production limits.
- Regression cases cover all ten profiles, nonprimary materials, company-wide and current-company phrases, missing demand/lead, unknown/foreign scope, selected/buyer disagreement, reviewed/draft/foreign/incomplete quotes, read-only negation, explicit provenance, mixed actions, unavailable AI, saturated model quota, context and replay protections.
- Separate implementation and acceptance workers were reviewed independently. Antigravity also ran two actual parallel read-only reviewers for procurement usefulness and voice/tool parity. They independently identified the narrow-read/numerical-format problem. Their report was advisory, not proof of a deployment or an immutable final-code review.
- Coordinator disposition of advisory language: a cover-versus-profile-lead gap is conditional planning context, not proof a stockout will occur. Reviewed quote fields are document evidence, not a binding contract or confirmed supplier availability. Those stronger interpretations were not adopted.

## Hosted acceptance and evidence boundaries

The exact application source, build digest, ready revision, health aliases, runtime comparison and sanitized text/voice acceptance are recorded in `release-verification.json` and `hosted-acceptance.json` after completion. All 20 unique typed cases passed, and all 10 companies passed controlled voice with grounded captions, returned audio and unchanged protected state. The before/after saved reviewed quote case also passed. Independent review found no remaining defect in this scope. Both the original and corrected audit observations are retained: initial voice checks rejected valid “just under twenty”, whole-day rounding, hyphenated day labels and the new canonical tool-result shape. Those checker defects received negative regressions and independent review before only the affected cases were rerun. No product guard was relaxed to pass the audit.

`reviewed-quote-hosted.json` exercises a fresh isolated workspace before and after explicitly reviewing a bundled quote using rules extraction. The answer recognizes the saved reviewed document without inventing price/capacity or producing a plan, draft or outgoing message. The actual hosted desktop/mobile chat was checked with the reported material question and a company-wide “do not change anything” request. Original user browser session data was not recovered or changed.

Voice acceptance uses owned Google-generated speech, real Gemini Live tools, current-company facts, output captions and returned audio. Caption grounding is checked independently against profile values, including valid rounding. This does not test a physical microphone or independently transcribe/audit the waveform. No email or supplier communication is sent.

## Delivery and recovery

App source: 6e2fb2a. Cloud Build: f4aa0b1a-c378-40ac-b152-9c2dcfb90d38. Immutable image: `sha256:6fd2b16f49abf51629b721f925e1ef01be39c5a1fa08dcc5a4fe078d7adeb0e7`. Serving revision: `closed-source-quote-demo-00065-vst`, 100% traffic. Existing hourly collector and scheduler were left unchanged because news collection code did not change. Service identity, environment, resources and scaling were compared independently: only the service image changed. No billing upgrade, IAM grant, paid subscription or new infrastructure was added; existing trial credits remain the cost boundary.

Rollback is the preceding r64 image: `sha256:0b6e2fead22a3c1ecf98a0998da8aa982b5865e4b44230fc61a7599e1edb7a29`. Deployment is separate from Git merge state. The existing feature branch/PR is updated without rewriting history or merging unrelated work.
