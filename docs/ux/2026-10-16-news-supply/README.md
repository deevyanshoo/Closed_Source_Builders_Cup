# Company supply and news experience — 16 October 2026

Approved user scope: a material-first Inventory & Demand view, company-scoped risks, stock-cover timeline and local delay slider, explicit portfolio view in the hub, broader company news with 2–5 useful stories initially, calm dated status, and relevant photographs. No Taher feature integration or new infrastructure is included.

## Delivered source

- Inventory defaults to Materials; Orders keeps all selected-company orders, including unmatched profile material IDs. Desktop rows become mobile cards without nested table scrolling. Each material has its supply window, related suppliers/routes/orders and scoped Watch signals with impact and suggested operator prompts.
- Stock-gap filtering/counts/colors use measured positive profile gaps. Conditional news is separate context. The shared accessible timeline positions stock exhaustion and profile lead correctly, including covered/no-gap and unknown cases. A local delay scenario changes its own labelled readout and resets without changing a plan or order.
- Routine risk reads request only the current company. An explicit hub portfolio view retains all-company analysis. Filter totals follow displayed company/category/severity, preserving session/context/epoch protections and the existing voice portfolio contract.
- Broader balanced material/sector queries and a bounded thin-coverage route fallback preserve relevant publication dates. Same known publisher/headline duplicates collapse before diversified first-five selection. Explicit Newest still sorts by publication time. More stories remain available.
- News and Overview share dated freshness semantics. Provider limitations and independent news/FX clocks remain under Collection details; an individual source timeout does not turn a recent successful collection into a blanket warning.
- Ten licensed Commons context photographs (1.35MB total) ship locally with author/source/license registry at `/licenses/news-industry-photos.json`. They are labelled Industry context. Exact allowed EIA/ECB publisher images take precedence when present; failed images fall through safely. No unrestricted remote-image policy or arbitrary page fetch was added.
- Existing quote review, recovery baseline, dirty-state action gates, research consent and durable snapshot clocks remain intact.

## Verified behavior

The inbox opens with company-specific news, five ranked stories (or all available when fewer), relevant industry photographs and dated freshness. Global Watch has its own tab. Material/age filters remain available, with additional sorting and diagnostics in disclosures. Broader discovery uses balanced material and sector queries plus a bounded route fallback when recent coverage is thin; old, undated and duplicate headlines do not suppress it.

A stock gap means the matching supplier profile lead exceeds measured stock cover. A conditional news alert does not create a stock gap. Solara copper has about six days of cover against a ten-day shortest profile lead: about four days uncovered. The local fourteen-day delay slider shows an eighteen-day scenario gap and resets to the original comparison. It does not change a reviewed plan or order. Material sourcing opens the matching procurement scenario; Ask ZOORQ carries company/material context into the composer.

Mobile review found and repaired a residual third grid column and a compressed timeline endpoint label. The final responsive material summary uses equal columns and both timeline labels remain readable. A real hosted text smoke also exposed unresolved optional evidence placeholders; those clauses now render through the existing trusted-fact renderer, with neutral checks for unknown tokens and a final unresolved-token guard.

## Test and review results

- Web: 479 tests passed, six existing skips; session-race checks passed. Final CSS-only refinement: 13 focused tests and production typecheck/build passed.
- Demo backend:789 tests collected and the complete run passed with the new evidence-rendering regression. The available analytical DB dependencies are incomplete, so this scoped gate used `--noconftest` with the repository's exact existing voice process-quota reset fixture loaded as a temporary plugin. An initial run omitting that fixture exhausted the fake-provider process budget; this was a test-runtime issue and no voice runtime limit was changed.
- Independent source/security-boundary review approved the combined change, manual freshness follow-up, mobile CSS and grounded evidence formatting. The reviewer inspected the corrected 390px capture. Root owns full gates, provider tests and delivery verification.
- Existing Three.js chunk-size and Python deprecation warnings remain; no full-repository analytical/forecast suite pass is claimed.

## Delivery evidence

`release-verification.json` records the final source, immutable image, Cloud Run revision, build and both health URLs. `hosted-company-coverage.json` records a dated ten-company audit: each company had 3–32 relevant published stories in the preceding seven days. Counts are observed coverage, not a guarantee of future publishing volume. `hosted-*-responsive.json` records the exact final-revision ten-company desktop/mobile matrix, with separate visual review of the expanded mobile timeline.

`voice-verification.json` separates the ten-company controlled-speech audit from the exact-final-revision smoke. The audit exercises actual Gemini Live, current-company inventory tools, profile arithmetic and returned audio. It does not test a physical microphone or prove human audibility. Text-to-operator context, live source refresh, the delay slider/reset and material-to-procurement navigation were exercised in the isolated browser session. Original user session data/cookies were not recovered or replaced.

Screenshots show actual hosted interfaces. Industry context photographs are credited and labelled as context; they are not evidence of the reported event. Source images take precedence only from the exact allowed publisher hosts. No fabricated news or publication dates are used.

Known operator limitation: the exact final-revision direct inventory text question completed correctly, but one broader inventory-and-sourcing explanation was rejected by the existing numerical verifier. No action ran. `operator-text-smoke.json` records both results; model-response reliability remains follow-up work. The resolved-placeholder regression passed, and existing chat history is not rewritten.

## Infrastructure and release boundaries

Only existing Cloud Run service and hourly collector images were updated. Runtime identity, environment, scaling and limits were preserved. The service remains one CPU, 1GiB, max one instance, concurrency 8, timeout 240s; collector remains one CPU, 512MiB, one task, 120s, no retries. The existing hourly UTC scheduler stays enabled. No billing upgrade, IAM grant or new paid service was introduced; existing trial credits remain the authorized cost boundary.

Existing create-only hourly snapshots are retained. The updated collector uses the new discovery pipeline on its next scheduled execution; an already-existing current-hour object was not overwritten and no duplicate manual collector execution was requested. News and FX success clocks remain independent.

Rollback image: `sha256:1327ece38c1f4106baacff2d100b13630569d03b78cfd5ff2835ba50eb3fc0ee` (previous serving revision `closed-source-quote-demo-00061-49v`). The current feature branch is pushed; hosting is verified separately from PR merge. Main consolidation and Taher/carbon integration remain separate reviewed work.
