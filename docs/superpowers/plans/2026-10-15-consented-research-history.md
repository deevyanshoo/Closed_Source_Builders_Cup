# Consented Research and Firestore History Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deploy explicitly approved public research and private seven-day anonymous conversation recovery on the existing demo.

**Architecture:** Three disjoint lanes build durable storage, the research adapter, and the React interface. The coordinator integrates HTTP/voice effect boundaries, provisions eligible Google resources, and owns Git and release verification.

**Tech Stack:** Existing Python FastAPI, Google Gen AI, Google-auth/requests, Firestore REST, React/TypeScript and Gemini Live.

**Spec:** `docs/superpowers/specs/2026-10-15-consented-research-history-design.md`.

## Global Constraints

- Existing Google Cloud trial credits only; no billing upgrade, paid plan, supplier delivery or public database access.
- Exact reviewed query only; no private documents, inventory, supplier details, credentials or full conversation supplied to Search.
- Five-minute research consent expiry; seven-day anonymous browser session/history; six research attempts/session; no automatic provider retry.
- Pending cards do not survive session restoration as actionable approvals; cross-device recovery is out of scope.
- Preserve the current dedicated feature checkout and other contributors' changes. Disjoint file ownership; coordinator commits/integrates and mutates providers.

## Review Focus

1. Restart between claiming a research approval and receiving its answer cannot duplicate paid search.
2. Late voice captions, cancellation and company switches cannot attach text or evidence to another company.
3. Conflicting Firestore writes never silently overwrite accepted state or falsely report durable history.
4. Untrusted source text and supplied Search Suggestions cannot invoke tools or execute scripts.
5. Expired cookies, unknown sessions and oversized state cannot bypass ownership or storage limits.

## Task 1: Durable sessions and history

**Files:** `procurement-engine/src/procure/demo/sessions.py`, new `session_persistence.py`, new `test_demo_persistence.py`.

**Interfaces:** SessionStore exposes `save(session)`, `persistence`, and optional injected backend. Persisted state restores JSON-compatible Session fields with a fresh lock and no actionable pending proposal. Session has a bounded per-company `chat_history` and research attempt/claim state. Coordinator uses explicit save at effect boundaries and final request flush.

- [ ] Write and run failing tests for independent-store restart, expiry, company/session isolation, CAS conflict, oversized data, unavailable storage and pending-proposal invalidation.
- [ ] Implement bounded Firestore REST persistence with server ADC, deterministic document IDs and conditional writes; no raw tokens in logs.
- [ ] Verify targeted session/persistence tests and report exact interfaces, residual constraints and diff for review.

## Task 2: Public research adapter and proposal routing

**Files:** new `procurement-engine/src/procure/demo/research.py`, `operator.py`, `live_voice.py`, corresponding focused new tests.

**Interfaces:** `prepare_research(message, company, now)` yields a validated public query or no proposal. `VertexResearchProvider.research(query)` yields a bounded validated result with answer, source records and Search Suggestions; no workspace object is accepted by this adapter. Typed research routing and a voice preparation tool call the coordinator's consent endpoint, never the provider directly. Coordinator owns `app.py` integration and shared API contract.

- [ ] Write and run failing tests for research routing, unsafe/private query rejection, missing citations, malformed URLs, unavailable provider and grounded result limits.
- [ ] Implement one bounded Google Search grounding request with fixed public instructions, dated uncertainty and no action tools or automatic retries.
- [ ] Add the preparation-only voice declaration/allowlist and concise spoken consent/result guidance.
- [ ] Run focused tests and return source contract for integration review.

## Task 3: Visible consent, research results and history

**Files:** `apps/web/src/demo-api.ts`, `demo-operator.tsx`, `demo-live-voice.tsx`, new research/history UI module and focused tests; minimally scoped existing styles if required.

**Interfaces:** Session `persistence` becomes `'ephemeral' | 'firestore'`. `GET /api/demo/operator/history` returns current-company accepted exchanges. OperatorProposal kind adds `'research'`; its parameters contain `query` and `destination`. Existing confirmation endpoint returns `kind: 'research'` and a read-only result. History append for finalized voice uses same-origin `POST /api/demo/operator/history` with channel, text, speaker, context and idempotency key; server validates all fields.

- [ ] Write and run failing tests for approval-only enablement, query/destination display, safe citations/sandboxed suggestions, history hydration and stale response rejection.
- [ ] Implement a distinct research review card, searching state and read-only cited result; old history never restores an Apply button.
- [ ] Hydrate same-company conversations on load and store finalized voice captions without partial duplication or automatic research approval.
- [ ] Verify relevant UI tests, type checking and production build; report changes for review.

## Task 4: Coordinator integration and hosted release

**Files:** `app.py`, focused API integration tests, deployment documentation/configuration and bounded acceptance scripts.

- [ ] Write/run failing HTTP tests for no-search-before-consent, stale/replay/cancel/company/expiry cases, durable research claims, history ownership and persistence failures.
- [ ] Integrate explicit research prepare/confirm/cancel routes with shared typed/voice result handling, durable claim-before-call and context recheck after work outside the session lock.
- [ ] Integrate request state persistence, seven-day session cookie and accepted typed/voice history; keep errors truthful and avoid transcript logging.
- [ ] Review worker diffs and run backend demo suite, frontend suite, type check and build. Independently review persistence/consent risks.
- [ ] Verify trial status, eligible Firestore configuration and least-privilege server access; create no paid add-ons. Preserve existing deployment and rollback image.
- [ ] Build once from a clean, reviewed revision, deploy to the existing service and verify real sourced research, consent rejection, history recovery and controlled live audio against that revision.
- [ ] Commit/push release evidence, update the existing draft PR and report source/deployed revisions and any remaining verification limits.
