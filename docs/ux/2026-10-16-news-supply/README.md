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

## Verification before delivery

Independent source review approved after fixing diversity, publication eligibility, timeline positions and stock/news semantics. Coordinator checks: 477 web tests passed, six existing skips; session-race script passed; 788 demo backend tests passed with two subtests; production typecheck/build and diff check passed. The retained Three.js bundle size warning is unchanged in purpose.

A direct public-feed diagnostic returned at least two relevant dated stories in every company (TerraPack3; others11–31) before final duplicate collapse. This is a dated provider observation, not guaranteed future coverage. Exact hosted counts and collector receipt are recorded after release.

Local browser observations: desktop inventory had no horizontal page overflow; the keyboard delay slider changed a four-day baseline gap to an eighteen-day scenario with a fourteen-day delay and reset to zero. Mobile news reported 390px width, five rows and five successfully loaded bundled photographs with no page overflow. Browser automation later reset under machine resource pressure; no original hosted session cookie was recovered or replaced.

Release state: source prepared; hosted deployment and collector update pending. Previous serving revision is `closed-source-quote-demo-00061-49v`, immutable image `sha256:1327ece38c1f4106baacff2d100b13630569d03b78cfd5ff2835ba50eb3fc0ee`. Rollback retains this image. Existing service identity, environment, scaling, concurrency, resource and timeout limits will be compared after deployment. The existing hourly collector keeps one task, one CPU,512MiB,120s,no retries. No billing or IAM changes are authorized or performed.
