# Hosted ten-company audit and repair plan

> **For agentic workers:** Use systematic debugging and test-driven development for reproducible defects; the coordinator integrates, reviews and releases. The user explicitly requested parallel agents and execution.

**Goal:** Audit the live procurement demo across all ten companies, repair demonstrated failures, and verify the delivered revision using existing trial credits only.

**Architecture:** Keep the existing dedicated feature checkout and disjoint write ownership. Use separate anonymous QA sessions per lane, bounded real Google calls and deterministic fixture checks. One coordinator owns browser sessions, shared app contracts, Git and cloud mutations.

**Tech stack:** Existing React/Vite, Python/FastAPI, Gemini Live/Vertex AI, Firestore, GCS and Cloud Run. No new provider, dependency, IAM, billing upgrade or service is planned.

## Review focus

- Company/material changes must invalidate stale actions and prevent state leakage.
- Quote review, delay recovery and RFQ preparation must retain decimal/unit correctness and server-authoritative baselines.
- Spoken consent and replay must dispatch one current industry-aware research action, with no pre-consent Search.
- Voice interruption, mute, end and delayed results must release resources and leave the composer usable.
- News/source failures and persistent drafts/history must retain provenance, clocks and review boundaries.

## Task 1 — Quote/recovery/RFQ audit

Files: `deploy/judge-demo/all_material_workflow_smoke.py`, focused smoke tests and, only after demonstrated product failure, `procurement-engine/src/procure/demo/quote_service.py` or `planning.py` with their tests (coordinate before any other product file).

- [x] Reconcile the 52-material/156-fixture rules-only audit with current Firestore sessions without weakening fresh-session, context, idempotency or unit checks.
- [x] Run it once against the exact hosted revision, saving incremental redacted receipts. Test every company/material through review/save, baseline, delay recovery and unsent RFQ/download.
- [x] Reproduce defects locally, add a failing regression and repair only the cause. Report exact files, counts, failures and stop before commits/deployments.

## Task 2 — Typed agent action audit

Files: new `deploy/judge-demo/ten_company_agent_audit.py`, `procurement-engine/src/procure/demo/operator.py`, `agent_tools.py`, and focused operator/action tests. `app.py` is coordinator-owned.

- [x] Prepare an isolated-session matrix across all ten companies for inventory, explicit form edits, quote loading/review, comparison, recovery, RFQ drafts, simulated email and persisted drafts/outbox.
- [x] Confirm model-backed turns, review gates, replay behavior, saved records and company isolation. Preserve the existing 90-model-call hourly limit: 20 typed turns (60 role calls) across ten companies, plus direct rules workflows and native voice actions. No external delivery or Internet research.
- [x] For each genuine failure, reproduce, add a failing regression, repair and verify the affected tests. Request coordinator integration for shared app changes.

## Task 3 — News/voice source audit and UI checks

Files: coordinator/browser; Antigravity worker owns the new `deploy/judge-demo/ten_company_news_audit.py` and news-specific audit tests. No existing production edits without a handoff.

- [x] Validate news/Global Watch coverage, ranking, publication timestamps, provenance, failures and per-company mapping across ten profiles; inspect the next scheduled persistent watch snapshot.
- [x] Run native continuous voice with one generic inventory sample for all ten companies; record tool context, captions, factual answer, audio and completion. Separately exercise research consent and a reversible action on selected companies.
- [x] Inspect laptop/mobile UI, voice entry/orb, news actions/composer, drafts, saved history and End cleanup using a preserved original browser session.
- [x] Route demonstrated failures to the single owner of the affected files. Keep native/provider, browser-controlled audio and physical microphone evidence distinct.

## Task 4 — Integration and delivery

Files: coordinator-owned `app.py`, frontend repair files if needed, and release audit record.

- [x] Review all patches and affected regression evidence; run demo backend, web, session-race, typecheck/build gates. Record unrelated broader-suite failures separately.
- [x] Verify fresh unupgraded trial, preserve service/job identities and resource caps, build an allowlisted immutable package and deploy demonstrated fixes with a compatible rollback.
- [x] Re-run failed hosted rows and focused voice/action acceptance against the new exact revision; verify health/readiness, telemetry, scheduled collection and current image.
- [x] Commit/push the verified release and audit record, update existing draft PR, and confirm clean/synchronized Git. Report measured coverage and every remaining limitation.

Acceptance phases and evidence boundaries are recorded in `docs/hosted-demo-audit-20261015.md`. Final image changes static files only after the r41 52-material audit; backend source remains identical.
