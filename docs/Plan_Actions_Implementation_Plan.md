# Persistent plans and local action boundary

Approved scope: first persistence/action increment for issues2 and8. Preserve Python numerical modules, the existing temporary recommendation route and reference runtime. No supplier communications, orders, cloud/account changes or Taher domain implementation. Source base a9e10d3; feature/plan-actions. Contracts/migrations have one coordinator owner.

## Design

State envelopes use0.2.0 while nested preview requests/results retain0.1.0. Saved snapshots are computed and validated through the gateway's server-side Python adapter. Request workspace must match the server principal and route; client recommendations/actors/roles are rejected. A saved plan resource holds immutable revision snapshots and a current revision. All money/quantity amounts remain strings in JSONB and wire types.

Server-owned Authenticator/Principal interfaces precede state handlers. Temporary bearer identities come from a private server file, with editor/viewer and one authorized workspace per identity. Browser tokens never enter client bundles/storage: the loopback Vite proxy reads a private token file server-side. Gateway checks loopback transport and the configured development Origin for state requests; unknown identities, missing credentials, actor/role assertions and cross-workspace requests cannot grant authority. These are local development identities, not production authentication.

Confirmation records exact quantity/unit/currencies/destination/buyer/material/action and current revision/snapshot version. Execution consumes a confirmed approval from the same principal and atomically writes its receipt and audit event. Plan revision updates, confirmation and execution lock the same plan row. Execution additionally serializes workspace/idempotency key (transaction advisory lock or an equivalent real PostgreSQL mechanism), then checks a persisted receipt before checking current plan state. A committed identical replay returns that same receipt even after later revisions; a changed request conflicts. A changed plan rejects an unused old approval. One approval can create only one receipt regardless of keys. Required unique indexes and scoped foreign keys enforce the relational boundary.

Never make Python compute inside a long-held plan lock: compute a proposed new snapshot first, then transactionally compare expected revision again before committing it. No client paste is accepted as authoritative. Unsupported or malformed computed snapshots cannot be saved. Existing legacy arithmetic/coverage and soft shortage penalties remain explicit; merged PR9 does not certify full inventory feasibility.

Migration001 has safe reapplication and a non-destructive down path that archives its entire schema. Stop writers before rollback; up restores an existing archive before ensuring tables. No user-record DROP/TRUNCATE. Readiness checks DB/schema and identity separately from process liveness; provider remains not_verified.

## Task 1: Coordinator prerequisites

Own contracts/, migrations/*.sql and setup/check docs. Freeze additive interfaces,12synthetic fixtures, generated types and relational schema before delegation. Start a separate ignored PostgreSQL17 cluster on loopback; record source/DB identity without credentials.

## Task 2: Gateway worker

Own services/gateway except generated types and migrations/*.sql. Read contracts/README.md, generated types and migrations first. Implement pgx-backed state/store, authenticated routes, server-owned principal middleware, strict request validation, save/list/read/revise, confirmation/read history, local prepare_rfq receipt and audit, readiness. Preserve existing newHandler/stateless suite. Runtime config: DATABASE_URL_FILE, DEV_IDENTITIES_FILE, DEV_ALLOWED_ORIGIN. Keep credentials/logged connection strings out of source/reports. Bound HTTP/database timeouts and return semantic0.2.0errors on new routes. Default compatibility routes keep existing shapes.

Tests first against actual task PostgreSQL using PLAN_ACTIONS_TEST_DATABASE_URL_FILE: unauthorized/cross-workspace/actor assertion no effects; client-pasted snapshot rejected; server computation; exact strings; persisted reload/store reopen; concurrent same-key one-effect/same-receipt; changed-key payload conflict; consumed-approval/different-key rejection; changed plan after confirmation stale; concurrent revise/execute correctness; audit failure rolls back receipt and approval consumption; migration apply/reapply/archive/restore. Test-owned isolated schemas only; never delete application records. Go full suite/vet and race test where meaningful. Worker starts no shared servers, commits nothing and spawns no agents.

## Task 3: React worker

Own apps/web only. Consume frozen state types. Maintain industrial light workspace and preview flow. Fetch server session, show local identity limits, saved plans/current revision, separate temporary preview from saved snapshots. Save computes a fresh server snapshot; revise binds expected revision. Show explicit confirmation card with all action arguments, persist approval on user confirmation, execute local draft with a stable idempotency key retained for retries, and show receipt/history/audit. Never expose bearer credentials or claim sending/ordering. On conflict reload current saved plan and invalidate confirmation; pending/late responses cannot cross selected plans/edited inputs. Show denied/stale/unavailable/retry/error states. Read-only viewer has disabled writes, while server remains authority. Proxy uses server-side GATEWAY_DEV_TOKEN_FILE and GATEWAY_URL. Tests first, then typecheck/tests/build. No shared servers/browser/commits/subagents.

## Task 4: Coordinator integration, review and handoff

Use task-only gateway8817/web5317 and preserved Python8016. Verify actual PostgreSQL migration/restart/concurrency effects, prior Python70case suite, Go/web/contracts gates and actual browser save/reload/confirm/draft/stale/error desktop/narrow screenshots. Obtain one independent bounded security/correctness review; fix findings through owner workers, then verify. Commit/push/reviewable PR and exact receipt to the control-room integration owner; do not race main. Prepare next ready approved Divyanshu increment with dependencies/files/tests for root dispatch; retain coordination until integration/dispatch. No inferred Google/provider proof.
