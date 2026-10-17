# Independent focused release re-review

Reviewed the combined working changes after the three original findings were repaired, including image/CSP changes, duplicate-headline handling, material/order views, sourcing availability, positioned timeline markers, and collapsed session options. Visually inspected `output/evidence/news-supply-release-20261016/local-inventory-desktop.png` through the image viewer. No browser/provider actions or application edits.

## Remaining actionable finding

**P2 — Conditional news alerts color fully covered stock positions as at risk.** `apps/web/src/zoorq-inventory.tsx:58` sets `atRisk` for any matched alert; lines 63, 67 and 73 then use that value for the count/filter, while `apps/web/src/zoorq-inventory.css:45` makes every such row's gap cell amber. The supplied screenshot confirms the failure: aluminium has 29.7 days of cover and a 10-day lead, polymer has 46.4 days of cover and a 10-day lead, yet both display amber “No gap” and join Copper in “At risk 3.” News evidence describes conditional exposure and does not establish either stock shortfall or a changed lead. Keep its monitor context visible, but separate it from stock coverage status. Either (a) base the stock At risk filter/count/color on positive gap and retain monitor context separately, or (b) rename the broad filter/count to “With review signals” and derive gap color solely from positive gap. A general “Needs review” summary may legitimately include all signals if its meaning is explicit. Regression: add only an observed_news alert to a fully covered material; No gap must stay neutral, conditional context must remain visible, and stock-risk semantics must stay distinct.

The primary Copper selection and its approximately four-day gap arithmetic are correct. Optional wording refinement, not a separate defect: “About a 4-day gap between 6 days of stock cover and the 10-day profile lead.”

## Re-review result

- Original frontend diversity reset, fallback publication eligibility, and timeline order/position findings are repaired in current source.
- Remote publisher images now match the exact EIA/ECB hosts permitted by CSP. Script/connect policy remains self-only. No new arbitrary server fetch exists.
- Ten locally bundled context photos match their SHA-256 registry entries and each has author, source page, license, and license URL. Detail rendering labels the photo as industry context, includes attribution, and states it is not evidence of the reported event. This is a local registry/rendering consistency check, not independent legal verification of upstream metadata.
- Same normalized headline plus known publisher is deduplicated before diversity/fallback; different/unknown publisher records remain distinct. Current-source handling preserves snapshot validation, copied read-time ranking, collection clocks, company scope/epoch guards, consent and dirty-action boundaries.
- Material-specific sourcing is disabled when unavailable; all orders including unmatched material IDs remain reachable. Export controls retain dirty/busy gates inside the disclosure.
- No additional actionable image-security, persistence-clock, consent, scope, or source-contract defect found. Root retains full gates, mobile/news browser QA, deployment, and live-provider evidence ownership.

## Final source re-review — approved

The remaining P2 is repaired in current source: row color, Stock gaps filter/count, and primary gap selection now depend only on positive profile gap. Covered materials retain neutral No gap and separate scoped Watch signals. The new regression exercises a covered material with a news-only alert and preserves its impact/action context without creating stock risk. Scenario detail labels now use the changing scenario assessment and explicitly distinguish Profile from Scenario; supplier display formatting does not mutate canonical records.

Legacy news presentation now derives freshness and saved dates from dated healthy/trusted news source checks, excluding failed attempts and the FX clock. No additional load-bearing source defect found. `git diff --check` passed during this final review. Coordinator-reported combined gates are 477 web passes with six skips plus session-race checks, 788 backend passes with two subtests, and production build success; these full gates were not rerun by this reviewer.

**Approval for the reviewed combined source increment.** Browser/mobile/news QA, exact-revision delivery checks, cloud limits, and deployment receipt remain coordinator-owned. This approval does not claim a deployment or live-provider outcome.

## Follow-up patch after 5c4263b — approved

Read-only review of the four-file manual/live-fallback freshness and error-presentation diff against 5c4263b found no actionable defect. Source-clock fallback is restricted to absent/invalid success timestamps in legacy/manual/live-fallback views; a valid scheduled success clock remains authoritative. Healthy/trusted source selection excludes failed checks and FX. Request failures are retained under Collection details, and a settled first failure renders a retryable empty state without an active loading indicator. Diff whitespace check passed. Coordinator reports 57 focused tests and typecheck passing; full build remains coordinator-owned.

## Mobile inventory/timeline CSS follow-up — approved

Reviewed the two-file CSS diff and visually inspected `/private/tmp/zoorq-news-supply-mobile-timeline-polish.png` at 390px. The five material metrics now occupy readable two-column cells; Stock cover no longer collapses into the chevron track. The 10-day lead label remains readable at the right endpoint, with cover/lead positions and scenario controls intact. No overflow or additional actionable defect visible in the supplied capture. Source diff whitespace check passed. This is local-preview visual approval; hosted verification remains coordinator-owned.

## Operator evidence-placeholder follow-up — approved

Reviewed the bounded `operator.py` diff and new `test_demo_operator_placeholders.py`. Optional explanation prose containing fact tokens now uses the existing fact/citation renderer; unverifiable or malformed token clauses receive fixed nonnumeric fallbacks. The final answer guard rejects any surviving double-brace token, including tokens introduced by untrusted fact text. Existing evidence relevance, numerical/claim validation, proposal creation, and research-consent paths remain in place. No actionable regression found in the assigned diff. Independently ran the new focused regression with the existing runtime (`pytest --noconftest -p no:cacheprovider`): one passed. Full backend gates and hosted text smoke remain coordinator-owned.

## Backend gate runtime diagnosis

Read-only inspection confirms that `--noconftest` also removes `isolated_live_voice_process_budget` from repository conftest. Successful fake sessions increment the shared process-start counter, which permits only 24 starts/hour; later examples then fail waiting for readiness. This is a test-isolation/runtime problem and does not require a product change. The Google SDK directory supplies a real importable `google.genai` 2.27.0 with Content/Part/Blob/FunctionResponse; omitting it produces ModuleNotFoundError even though separate distribution metadata reports another version. The coordinator's full-SDK path plus `/private/tmp/zoorq_demo_gate_fixture.py`, reproducing the exact existing autouse counter/window reset, is the appropriate gate for this constrained runtime. Pre-importing the real SDK also avoids the RFQ test's conditional minimal message-container stub. Full gate completion remains coordinator-owned.
