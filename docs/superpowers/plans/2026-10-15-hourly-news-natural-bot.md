# Hourly News and Natural Bot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver durable hourly public news for ten companies within verified free allowances, and natural text/voice answers without changing analytical quality or action truth.

**Architecture:** Cloud Scheduler invokes a bounded Cloud Run Job; the job reuses current feed adapters and publishes immutable hourly public-news bundles into private Storage. The existing application reads compatible bundles and retains its live fallback. Scheduling activation fails closed unless free-tier headroom is established.

**Tech Stack:** Python/FastAPI, requests/google-auth already available, React/TypeScript, existing Google Gemini Live tools, Cloud Run Jobs, Cloud Scheduler, Cloud Storage.

**Spec:** ../specs/2026-10-15-scheduled-news-and-demo-presentation-design.md

**Cost clarification:** The user subsequently approved spending existing trial credits with no out-of-pocket charge. Task 4's activation gate therefore accepts a freshly verified active, unupgraded Free Trial with credit and time remaining. Unknown permanent-free quota does not block this explicitly approved mode. No billing upgrade is permitted; all worker/storage bounds remain.

## Global Constraints

- Existing portal stays in `asia-south1`; collector uses `asia-south1` after the observed Google RSS region failure; Standard Storage and the single Scheduler stay in `us-central1`. Mumbai requires the approved trial-credits gate.
- Schedule hourly `0 * * * *` UTC (every hour at :30 IST); one task, 1 vCPU, 512 MiB, timeout 120 seconds, zero retries.
- No automatic paid model calls, no billing upgrade, public bucket, private uploads or actual email delivery.
- At most 2 MiB per bundle, seven-day retention, generation-zero immutable publication.
- Preserve canonical company/supplier IDs and names, currencies, numerical calculations, context guards, evidence and confidence.
- Keep one product-level demo disclosure, truthful provenance on request and explicit simulated/unsent action outcomes.
- Coordinator alone owns `app.py`, deployment files, Git commits/pushes, release state and provider mutations.
- Native Antigravity workers write disjoint files on the existing dedicated feature checkout. No duplicate checkout, dependency installation or redundant baseline rebuild is needed.
- User has explicitly selected execution: prepare plan then parallel Antigravity implementation/test/deploy. Proceed in that order; no further execution-method question is needed.

## Review Focus

- On-time failed news attempts with successful FX must not renew news freshness: Task 1 tests independent news/FX timestamps.
- Same-slot racing writers and delayed older runs must not regress results: Task 1 tests conditional-create and slot selection.
- Canonical recipients containing `[SYNTHETIC]` must still validate after display cleanup: Task 2 tests raw identity preservation and actions.
- A changed company while news or bot work is pending must reject stale results: Task 3 retains and runs existing context-race tests.
- Unknown shared free allowance must not cause automatic paid activation: Task 4 tests a deny-by-default cost gate and reports missing evidence.

---

### Task 1: Durable public-news bundles (Antigravity Terminal 1)

**Files:** create `procurement-engine/src/procure/demo/news_snapshots.py`, `procurement-engine/src/procure/demo/news_refresh.py`; modify `procurement-engine/src/procure/demo/news.py`; create `procurement-engine/tests/test_demo_news_snapshots.py`, `procurement-engine/tests/test_demo_news_refresh.py`.

**Interfaces:**
- `SnapshotNewsService(*, live: NewsService, store, clock)` exposes `get(company: dict, force: bool=False) -> dict`, matching current news contract with additional `collection` metadata.
- `GcsSnapshotStore(bucket: str)` exposes `read(slot: datetime) -> dict | None`, `create(slot: datetime, bundle: dict) -> bool`; all calls bounded, credentials use ADC and are never logged.
- `refresh_portfolio(*, live, store, clock, company_profiles=None) -> dict` collects ten profiles, validates bounded bundles, preserves previous observations, publishes an attempt receipt and independent success/health state.
- CLI `python -m procure.demo.news_refresh` reads `NEWS_SNAPSHOT_BUCKET`; no environment means explicit configuration error without network/model calls.

- [ ] Write failing tests for ten-company coverage, global fetch once, company query once, current news versus FX success, stale carry-forward timestamps, malformed/oversized/catalog-incompatible bundles, no private fields, duplicate generation-zero winner and delayed slots. Assert old `published_at` and source-check timestamps remain unchanged after failure, and news success remains unchanged when only FX succeeds.
- [ ] Run `python -m pytest tests/test_demo_news_snapshots.py tests/test_demo_news_refresh.py -q` from the engine with `PYTHONPATH=src`; capture the failing behavior before implementation.
- [ ] Implement the interfaces with existing feed adapter semantics, bound portfolio concurrency to three, respect the 120-second worker deadline, and use deterministic hourly object names. Read at most eight recent hourly slots, memoize reader checks for 15 minutes, and retain last valid in-process bundle when quota/unavailability blocks reads.
- [ ] Run new suites plus `tests/test_demo_news.py`; record counts and exact commands in the lane report. Do not change tests merely to bypass freshness errors.
- [ ] Return diff and report to coordinator; coordinator reviews and commits only this lane's files.

### Task 2: Natural wording and display labels (Antigravity Terminal 2)

**Files:** create `procurement-engine/src/procure/demo/presentation.py`, `procurement-engine/tests/test_demo_presentation.py`; modify `procurement-engine/src/procure/demo/operator.py`, `procurement-engine/src/procure/demo/live_voice.py`, `apps/web/src/demo-company-news.tsx`, `apps/web/src/demo-operator.tsx`, `apps/web/src/demo-page.tsx`; create `apps/web/src/demo-display.ts` and focused presentation tests.

**Interfaces:** `supplier_display_name(name: str) -> str` and `supplierDisplayName(name: string): string` remove only a trailing fixture annotation for human-facing labels. They never replace canonical records or recipient/tool arguments.

- [ ] Write failing tests asserting normal inventory/lead-time answers omit repetitive fixture qualifiers while still reporting stock 2,000 kg and shortest Solara supplier lead 10 days, explicit provenance questions disclose sample company records/public news distinctions, supplier IDs/raw names remain intact, and simulated send results explicitly confirm no external delivery.
- [ ] Run those new tests to capture failures; preserve the existing company/action tests as identity gates.
- [ ] Implement presentation-only formatting and concise default text/voice instruction. Retain one visible demo-boundaries disclosure, detailed provenance/evidence and provider freshness. Do not transform dates, amounts, confidence, URLs or actual message status.
- [ ] Run affected Python operator/live-voice/presentation suites and focused web tests plus typecheck. Report test evidence and any necessary coordinator-only `app.py` adjustments without editing that file.
- [ ] Return diff and lane report; coordinator reviews and commits this lane's files.

### Task 3: Application integration (coordinator, after Task 1)

**Files:** modify `procurement-engine/src/procure/demo/app.py`, add tests in `procurement-engine/tests/test_demo_news_snapshots_app.py`; update `apps/web/src/demo-company-news.tsx` only after Terminal 2 finishes.

- [ ] Write/run failing tests for bucket configuration selecting the snapshot adapter, no bucket retaining live adapter, scheduled success/stale metadata passing through news and risk endpoints, same-company context guards and explicit manual-refresh distinction.
- [ ] Wire `SnapshotNewsService` in `news_adapter()` when configured, retain existing injection/test behavior, and update monitoring limitations accurately. Apply only necessary operator-facing presentation cleanup reported by Task 2.
- [ ] Show last successful news collection, last attempt and source freshness in the workspace using optional fields compatible with the existing API.
- [ ] Run new app tests, all affected demo suites, complete web tests including session-race, typecheck/build and package checks. Commit the integration with exact evidence.

### Task 4: Free-tier activation gate and infrastructure (coordinator)

**Files:** create `deploy/judge-demo/news_schedule.py`, `procurement-engine/tests/test_demo_news_schedule.py`; update `deploy/judge-demo/README.md` and runtime package inclusion only if necessary.

- [ ] Write/run failing tests for deny-by-default activation: missing account-wide Scheduler count, unknown Cloud Run headroom, unknown Storage operations/transfer headroom, ineligible bucket region or timeout above 120 seconds must block activation. Required JSON evidence records observation time, billing scope, existing usage, projected bounded usage and approval constraint; no mere `free=true` override.
- [ ] Prepare idempotent infrastructure configuration for one private bucket, worker/scheduler identities with bucket-level creator/viewer and job-level invoker only, OAuth invocation, scoped existing-app viewer, seven-day lifecycle, no retries. Do not mutate provider resources from native workers.
- [ ] Verify free allowance headroom across the billing account and available hard usage caps. Resource limits and budgets are not a universal zero-cost guarantee; leave recurring scheduling inactive if the user's strict no-new-cost requirement cannot be assured. Do not solve that constraint by counting trial-credit consumption as free usage.
- [ ] If gate passes, create resources once, inspect IAM and private access, run a manual trigger, then observe a natural scheduled execution with closed browser tabs. Record execution IDs and distinct saved results. Otherwise record exact evidence gaps and keep the code/configuration deployable but inactive.
- [ ] Commit infrastructure preparation and the cost/activation decision, with no credentials or private billing data.

### Task 5: Review, hosted delivery and Git reconciliation (coordinator + read-only reviewer)

**Files:** create `docs/hosted-hourly-news-release-20261015.md`; evidence under workspace `output/evidence/` remains untracked.

- [ ] Obtain independent review of freshness, recipient identity, action truth and free-tier activation boundaries. Resolve material findings and rerun only affected checks.
- [ ] Confirm Git identity, current remote main/feature heads, clean coherent commits and PR #106 scope. No force push, mass deletion or blind merge; unrelated open work remains intact.
- [ ] Build one release package, record its source/manifest, submit one container build, inspect its receipt before retrying, deploy the existing authorized service with immutable digest. Preserve revision 22 as rollback.
- [ ] Verify the actual hosted ten-company news/text/action behavior, natural provenance response and live Gemini voice/tool exchange. Inspect desktop/mobile and animated states; remove test overrides and stop microphone/voice sessions afterwards.
- [ ] If scheduling remained blocked, do not label background collection deployed or active. Report bot deployment and scheduling readiness separately. Save release evidence and a deployed-source Git tag, push coherent branch updates, and leave the working tree clean.

## Self-review and execution ledger

All spec sections map to a task, except hourly activation which is intentionally conditional on the user's stricter free-only requirement. Interfaces and ownership are explicit. Parallel tasks do not share write ownership. Review-focus cases are assigned above. The user explicitly requested execution after preparing this plan, so execution proceeds without an additional approval round; the strict cost gate remains binding.

Ledger: `../../.superpowers/sdd/hourly-news/progress.md` within the repository; native lane reports use the same ignored directory. Start from committed baseline `b3a453e7b31179e9e5f7b9c4a6aad5f00d819065`.
