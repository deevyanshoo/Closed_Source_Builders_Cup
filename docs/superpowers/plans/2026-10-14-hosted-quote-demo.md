# Hosted Quote-to-Plan Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development; explicit user parallel-agent instruction overrides sequential default. Disjoint ownership fixed in spec.

**Goal:** Deploy browser-accessible synthetic quote review and feasible planning with live Gemini.
**Architecture:** Isolated React/FastAPI host reuses procurement document/numeric modules, preserving private gateway.
**Tech Stack:** React/TypeScript, Python/FastAPI, Vertex Google GenAI, Cloud Run.
**Spec:** docs/superpowers/specs/2026-10-14-hosted-quote-demo.md

## Global Constraints
Synthetic fixture allowlist; per-session state; CSRF/version/idempotency; no keys/credentials in source or client. Money decimal strings, source locators retained. Forecast action ABSTAIN; savings null. Root owns cloud/provider state. Preserve all contributor changes. No final contest submission or private-repo publication.

## Review Focus
Browser loads without machine auth; cross-session/CSRF/stale retries fail; corrected economics re-normalized; Gemini fallback never claimed live; ephemeral/cached/synthetic assumptions visible.

### Task 1: Hosted sessions/API
Files and API exactly as Host worker in spec.
- [ ] Tests first: fresh session/assets, CSRF/origin, foreign/stale offers, idempotency replay/conflict, upload allowlist, provider failure, export/reset.
- [ ] Observe expected red; implement bounded thread-safe session/host/cache routes; verify green and report.

### Task 2: Quote service/planner
Files/signatures exactly as Quote worker in spec.
- [ ] Freeze 2 labelled PDFs and 1 CSV with fixed demo assumptions; one missing settlement currency. Tests first for evidence, correction/save gates, expiry and typed rejection.
- [ ] Independently hand-compute late cheap/on-time plan, budget/MOQ/quantity/stock/unknown currency/stale tests; observe red.
- [ ] Implement existing-module wrappers and Decimal single-offer feasible comparison; verify focused green. No provider calls from worker/tests.

### Task 3: React workflow
Files exactly as Frontend worker in spec.
- [ ] Tests first for typed API/provider errors, correction/version and planning states.
- [ ] Show document selector/download/upload, real/cached/rules status, evidence/edit/accept, buyer inputs, alternatives and no-feasible state, export/reset. Keep existing visual direction.
- [ ] Typecheck, focused suite, production build; report red/green. Root visual verification.

### Task 4: Integration/deployment (root)
- [ ] Review diffs and independent security/correctness; pin minimal dependencies; build/package excluding private artifacts.
- [ ] Inspect runtime identity; bounded synthetic live Gemini diagnostic; cloud build/deploy low-resource app.
- [ ] Verify revision/URL and fresh-browser complete extraction/correction/save/plan/export; second-session refusal; laptop/narrow screenshots.
- [ ] Required integrated offline gates; reviewable commit/PR and remote confirmation separate from deployment.

User explicitly chose execution and approved delivered plan; no repeat approval loop for this refinement. Existing identity/billing/project configuration preserved unless deployment needs a narrowly reviewed permission.
