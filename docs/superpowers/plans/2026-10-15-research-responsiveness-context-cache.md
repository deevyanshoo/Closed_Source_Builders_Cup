# Responsive company-aware research implementation plan

> **For agentic workers:** Use superpowers:subagent-driven-development. The user explicitly approved the previous assessment, parallel Antigravity execution, testing and deployment. Continue within existing trial credits without repeating approval.

**Goal:** Make consent-based public research responsive in voice/text, industry-specific for all ten companies, and reuse eligible context without caching Google Search-grounded answers.

**Architecture:** Keep the existing React/FastAPI/Cloud Run/Firestore/GCS boundaries. Drain Live events while bounded consent/research tasks run; authoritative progress follows durable claims and scoped completion. Prepare exact public industry-aware queries before consent. Reuse immutable prepared operator context in a bounded session-scoped memory cache and measure eligible Vertex prefix-cache tokens.

**Tech Stack:** Existing React/TypeScript, Python/FastAPI, Gemini Live, Vertex Google Search grounding, Firestore sessions, GCS news snapshots and structured Cloud Logging.

**Spec:** User-approved assessment in this conversation: responsive consent acknowledgment, industry-aware visible queries, short relevant opening, stage timings and recommended context/news caching. Memorystore is allowed only if trial-credit eligibility and a material product benefit are established.

## Global constraints

- Runtime starts at8512a4a; clean branch HEADc9e838f; rollback live00035-wdg/image1e19a7434b3508045988ef1958ecfa5da086ec8a2a8cbbf803fc400e3635a8f4. Preserve user data, source history, main and the existing PR106.
- No billing upgrade, new public service/IAM expansion, real email/order delivery, or weakening of exact-query/fresh audio/negation/expiry/replacement/one-time consent. Search is one attempt, never automatically retried.
- Public Search receives only the exact approved query and public industry terms. Private names, figures, session/auth identifiers and audio stay excluded. Do not use Google grounded results as a reusable answer cache.
- Keep existing news and FX collection clocks and per-company/session scope guards. Raw voice audio stays transient. No new dependencies or infrastructure without demonstrated need.
- Disjoint ownership; coordinator owns app.py, frontend types/integration, Git, cloud/browser sessions and release. Workers never commit/push/deploy.

## Review focus

- Delayed verifier/research must not block provider event draining or emit research-start before the durable claim.
- Fresh verified consent must start exactly one current request; negation/partial/early/expired/replaced utterances must start none. Late task completion after company change/disconnect must not appear or mutate another workspace.
- Generic Hormuz questions must gain selected-industry context without leaking private fields or silently changing a query after approval. Full event names must work.
- Context cache must separate sessions/company/revisions, detect quote/plan/news changes, bound bytes/entries/age and return recursively immutable prepared values. Never cache model answers, failures or consent decisions.
- Cache/latency logging must contain timings/counts only, with no raw query, transcript, credentials or company facts.

### Task1: Voice orchestration — worker

Files: live_voice.py, voice_consent.py, focused voice-consent tests.
Interface: add optional research_tools(args, context_version, key, on_progress) callback to run_live_voice; legacy workspace_tools four-argument contract remains valid. Progress frames: type research_progress, phase checking_consent/started/completed/unavailable/awaiting_consent, company_id/context_version; proposal_id required for UI correlation. started originates only in app after durable research claim.

- [x] Write and observe failing regressions for delayed verifier/search with continued event draining, immediate bounded consent progress, one execution, prompt arm status, cancellation and existing safety cases.
- [x] Move verifier/research awaits off the provider event-drain path with one bounded outstanding operation and authoritative context rechecks. Fresh finalized consent should reliably dispatch the current approved search without an extra user confirmation turn; avoid relying only on a second model decision when a server-verified utterance is available. Preserve legacy provider/tool compatibility and scope/byte/tool limits.
- [x] Run focused tests; report red/green counts and exact remaining integration needs.

### Task2: Industry query and provider — worker

Files: research.py and research tests.
Interface: prepare_research(message,company,now) remains compatible and returns the exact query/destination/shared_data DTO. VertexResearchProvider.research(query) remains compatible; shared adapter supports lazy reused transport, successful timing/token-cache metrics only.

- [x] Regress full Strait of Hormuz, generic event/current questions for all ten sectors, private-detail rejection, exact-query revalidation and industry-aware opening instructions.
- [x] Recognize full event aliases; append/derive only reviewed public industry context when company scope is present. Keep query concise and <=400chars; reject unsafe values before context normalization. Provider focuses the opening on the named industry, distinguishes direct/indirect exposure and avoids assuming an event or company impact.
- [x] Reuse the provider client per adapter, no retry/cache of grounded answers. Log provider elapsed_ms and eligible usage token counts without query content. Verify focused tests.

### Task3: Context caching — worker

Files: new context_cache.py, operator.py and new focused context-cache tests.
Interface: run_turn(..., context_cache=None, cache_scope=None) adds optional arguments. BoundedContextCache.get_or_build(scope, snapshot, build) returns recursively immutable prepared context; operator builder returns compact/facts/encoded/encoded_facts only. Root supplies one cache per app and an opaque session scope.

- [x] Regress hit/miss, cross-session/company/context and plan/news mutation invalidation, TTL, LRU/byte bounds, mutation protection and failure-not-cached.
- [x] Implement max64entries/2MiBtotal/60secondTTL, deterministic full snapshot fingerprint plus scope/company/context/schema version. Skip absent scope, oversize or invalid snapshot. Cache only prepared trusted context; message/proposals/model results never enter cache. Emit aggregate hit/miss/build latency/bytes; log operator provider elapsed and prompt/cached token counts for Vertex implicit caching.
- [x] Benchmark repeated immutable preparation and run focused operator tests. Return evidence; no new cache service.

### Task4: Root integration, Antigravity review and deployment

Files: app.py, demo-live-voice.tsx/tests, demo-api.ts and relevant operator UI tests.

- [x] Baseline demo/web tests. Wire a shared research provider/context cache and dedicated voice research callback; emit progress only after valid durable claim, preserve single claim and stale completion protection. Add sanitized stage timings.
- [x] Frontend validates scope-bound progress and shows Checking confirmation/Researching/Ready for confirmation without false Listening or search-start claims. Keep one socket, visible proposal, short sourced response and existing teardown/focus.
- [x] Run full demo/web/session-race/typecheck/build and independent Antigravity consent/context/cache reviews. Resolve findings before packaging.
- [x] Verify trial state, package explicit allowlist once frozen, build existing image, update existing service/job where runtime changed, verify final health and hosted text/voice/industry/cache behavior. Preserve prior revision for rollback.
- [x] Record measured timings and evidence limits, commit/push normal branch+release tag, update PR106, verify Git clean and remote/live revisions. No claim of cache hits or latency gains without measured receipts.
