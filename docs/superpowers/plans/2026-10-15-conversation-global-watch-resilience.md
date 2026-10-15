# Conversational voice, Global Watch and resilience implementation plan

> **For agentic workers:** Use superpowers:subagent-driven-development for the disjoint implementation lanes and integration verification below.

**Goal:** Ship the owner's approved conversational voice cues, automatic global disruption coverage, and measured GCP monitoring/shared-state repairs.

**Architecture:** Extend the existing Gemini Live/FastAPI/React product. Share bounded global news collection across company views; retain private Firestore state and GCS hourly snapshots. Instrument actual operational outcomes without logging private conversations.

**Tech Stack:** Python/FastAPI, React/TypeScript, Gemini Live, Firestore, Cloud Run, Cloud Storage, Cloud Monitoring/Logging.

**Spec:** Owner-approved design in this conversation, plus `docs/Product_Design.md` and `docs/Work_Plan.md`. This plan concretizes that approval; no renewed approval loop is needed.

## Global Constraints

- Preserve Gemini product AI, reviewed-query consent, fresh finalized caller speech, company/context binding, single execution claims and unsent/simulated business actions.
- No billing upgrade, new paid plan, external speech provider, public database or broadened IAM. Existing unupgraded trial credits only; verify trial before cloud mutations.
- Preserve original news publication/retrieval/success clocks on failure, bounded requests, 2 MiB snapshot limit and backward-compatible snapshot reads.
- Work on existing clean feature branch with disjoint files; coordinator alone commits, pushes, changes cloud resources and releases. Parallel lanes are explicitly requested by the owner.
- Preserve existing one-instance production cap until every cross-instance acceptance prerequisite passes; distinguish shared-state repairs from full horizontal-scale certification.
- Tests first for substantive behavior. Workers report exact RED/GREEN checks and risks, no recursive agents.

## Review Focus

1. Spoken cues must never enter or approve the exact-query consent window.
2. Interruptions, quick results, company switches and disconnects must suppress stale queued speech.
3. Important global stories stay visible without overstating company exposure or treating reports as confirmed blockages.
4. Concurrent/restarted sessions retain proposals, reject conflicting writes and cannot repeat authorized research.
5. Monitoring excludes raw audio, queries, cookies and private business state; dependency checks must be bounded.

### Task 1: Conversational voice cues

**Files:** `procurement-engine/src/procure/demo/live_voice.py`, new voice_cues.py and bounded demo_fixtures/voice-cues WAV, related voice tests; `apps/web/src/demo-live-voice.tsx` and its test only if necessary.

**Interfaces:** Existing Gemini Live provider event loop and `research_progress` phases. Preserve all tool and consent interfaces. Do not edit app.py or persistence files.

- [ ] Add failing behavioral tests: slow operation receives at most one useful spoken acknowledgment; fast completion suppresses cue; cancellation/company switch/disconnect suppress queued cue; no cue can approve or contaminate consent.
- [ ] Implement bounded, interruptible, state-bound speech from one bundled same-Charon PCM acknowledgment generated once with Gemini Live. Runtime cues add no provider/tool generation; omit missing/invalid audio safely. No repeated mouth sounds or invented success. Read the current event loop before choosing scheduling, and preserve independent provider-event draining.
- [ ] Run focused live-voice/consent tests; report exact behavior and limitations. Coordinator runs full gates.

### Task 2: Global Watch

**Files:** `news.py`, `news_snapshots.py`, `news_refresh.py`, new `global_watch.py`; matching news tests; `apps/web/src/demo-company-news.tsx`, news types and matching tests/CSS. Do not edit app.py or voice/session files.

**Interfaces:** Optional `global_watch` on company news payload, holding collection metadata and public events with company exposure. Snapshot schema reads previous version; new fields are strictly validated. Coordinate any shared frontend type change before editing.

- [ ] Add failing tests for one shared global collection across ten companies, bounded deduplication, direct/indirect/context-only exposure, unknown/old dates, source outage clocks, snapshot compatibility and an always-visible UI section despite company filters.
- [ ] Collect fixed bounded public conflict/chokepoint/port/trade-restriction queries through existing public adapters. Reuse shared service/cache; no user-specific external Search or paid sources. Official incident pages are source links unless a supported fetch endpoint is verified. Limit public events and payload sizes.
- [ ] Persist global data in hourly snapshots and carry failures forward as stale with original evidence times. Render source/date/status/exposure/next action clearly. No generic war headline establishes a confirmed route closure or supplier delay.
- [ ] Run focused backend/web news tests and typecheck; hand off changed contracts and migration/rollback implications.

### Task 3: Shared state and request persistence

**Files:** `sessions.py`, `session_persistence.py`, matching persistence/session/research tests. Propose minimal app.py middleware edits as a patch file for coordinator integration; do not edit app.py/live_voice.py directly.

**Interfaces:** Existing conditional persistence writes and durable research claims. Persist pending proposals with validated safe state, preserving expiry/company/context. SessionStore remains backwards compatible.

- [ ] Add failing tests for proposal prepared by one store and confirmed/read by another, expiry/context invalidation, concurrent stale writes and duplicate research claims.
- [ ] Remove remote backend I/O from the store-wide lock without weakening per-session correctness. Introduce dirty-aware persistence so unchanged reads avoid full writes, retaining request-budget integrity and never acknowledging a successful mutation before durability.
- [ ] Test shared-backend independent stores and concurrent callers. Inventory remaining process-local admission limits; do not claim full scale safety or raise deployment cap if any remain.
- [ ] Report exact middleware integration and focused test evidence.

### Task 4: Monitoring and release (coordinator)

**Files:** New redacted operational diagnostics module/tests; `app.py` integration; bounded dependency-readiness endpoint; `deploy/judge-demo` monitoring configuration/script/tests and release documentation.

- [ ] Add failing tests for redacted request outcomes, bounded readiness failure and no repeated write on unchanged reads; integrate Task 3 safely.
- [ ] Add low-cardinality duration/error/consent/research/news-freshness telemetry. Create a Cloud Monitoring dashboard and alerts for errors, latency and collector failures with no new notification recipients or IAM grants. Reuse native metrics where possible.
- [ ] Inspect Antigravity workspace/terminal; use it for visible release checks and independent review. Run full backend demo gates, web tests/session-race/typecheck/build and focused cross-store concurrency tests.
- [ ] Independent review each lane and integrated diff; resolve substantive findings and retain rollback artifacts.
- [ ] Verify actual trial status, package explicit source allowlist, build once, verify receipt, deploy existing service/job using immutable digest and existing resource limits.
- [ ] Verify hosted health/readiness, all ten Global Watch views, text research, controlled spoken consent/cues/interruption/cleanup, draft visibility, monitoring configuration and clean synchronized Git. Record observed limits honestly.

## Dependency and ownership scan

| Tasks | Interface / result |
|---|---|
| 1 / 3 | Voice uses Session; no overlapping files. Existing proposal shape remains stable. |
| 2 / 4 | News payload adds optional global_watch; coordinator inspects operator context propagation. |
| 3 / 4 | Worker proposes middleware patch; coordinator applies only after persistence API review. |
| 1 / 4 | Coordinator does not edit voice worker files while lane runs. |
| 1 | Cues cannot weaken consent; tests include speech lifecycle. |
| 2 | Collection, persistence and UI acceptance are in one lane to preserve clocks/contracts. |
| 3 | Read optimization cannot bypass rate budgets or CAS; all costs count as real trial usage. |
| 4 | Single coordinator owns providers, review and Git integration. |
