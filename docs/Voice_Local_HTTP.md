# Local authenticated structured voice HTTP

This opt-in loopback adapter connects the gateway's existing development authentication to process-local voice sessions, the existing Go Manager/Runner and the fixed Python read-only recovery tool. Inputs are complete caller-supplied synthetic snapshots and structured comparison arguments. There is no speech/model/provider call, UI, durable state, plan application or supplier action. An authenticated local identity and internally consistent snapshot do not prove a current saved plan or production authentication.

## Host setup and ownership

The existing local identity file, loopback listener and configured Origin policy remain authoritative. Both viewers and editors may compare synthetic scenarios and manage their own ephemeral session. Signed-out callers are rejected. Principal/workspace/role come only from existing server authentication; headers, queries or model arguments cannot override them. A foreign principal in the same workspace has no access to another session.

The host opts in with `-voice-local-python /absolute/trusted/python` and `-voice-local-source /absolute/trusted/procurement-engine/src`. These are host command-line flags, never HTTP/model fields or new environment settings. The executable/module is fixed; absent configuration or an unavailable fixed executor returns explicit `voice_unavailable`, with no network/provider fallback. Malformed/incomplete/nonabsolute host CLI flags fail startup with a generic configuration error. Direct host configuration is validated before constructing the adapter. Private-machine mode cannot enable this local executor. Preserve existing reference servers; a test harness may start its own ephemeral loopback gateway without a second Python API server.

## HTTP flow

All contracts use `contract_version:"0.2.0"`, strict JSON and `Cache-Control:no-store`. Authoritative definitions and typed helpers are generated from `contracts/openapi.yaml`.

| Operation | Request | Response |
| --- | --- | --- |
| POST `/v2/voice/sessions` | supplied synthetic `ProductionSnapshot` | 201 session/context IDs, snapshot/plan/scenario identity and fingerprint |
| POST `/v2/voice/sessions/{session_id}/turns` | returned context version and recovery function call | 202 server turn ID; replaces prior current turn |
| POST `/v2/voice/sessions/{session_id}/turns/{turn_id}/cancel` | empty body | 200 current turn cancelled |
| GET `/v2/voice/sessions/{session_id}/turns/{turn_id}/result` | empty body | 200 exact pending, ready or failed state |
| DELETE `/v2/voice/sessions/{session_id}` | empty body | 200 session closed |

Session creation captures an owned immutable snapshot. The server issues session/context/scenario identity and a canonical-snapshot SHA-256 fingerprint. Supplied snapshot ID, plan version and as-of retain their synthetic meaning; they are not looked up as a saved production plan. Creation also rejects malformed or offset-free `as_of` timestamps and impossible calendar dates; accepted timestamps preserve up to six fractional-second digits. Current result polls renew both idle clocks. Context cannot change between turns. A turn's transport context-version precondition must match; its model-neutral call supplies only `compare_receipt_recovery` and explicit arrival replacements/complete assignments. Critical missing fields and unknown functions/identity/context/apply/send fields fail closed.

Ready contains the actual validated public Python tool outcome. Compared results preserve exact decimal strings; unsupported/clarification/rejection/unavailable states do not fabricate a comparison. Pending has no outcome/error. Failed has only a generic `execution_unavailable` error. Repeated current-result polls may return a bounded cached outcome after another current-lease check; they create no effect. Suppressed, stale, cancelled, foreign or expired turns are unavailable and yield no result.

Uniform unknown/foreign/stale session/turn errors use 404 `voice_session_unavailable`. Owned context-version mismatch is 409 `context_mismatch`; capacity is 429 `voice_capacity`; missing safe configuration/shutdown is 503 `voice_unavailable`. Authentication/origin/workspace/method/strict-body failures retain explicit 401/403/405/413/422 error states. Error bodies carry no token, raw process stderr, traceback or supplied snapshot text.

## Acceptance, cancellation and delivery

Execution accepted by a successfully completed 202 response belongs to the session and outlives the completed POST. Before that handoff, request cancellation or response-write failure cancels the new lease and its child. After acceptance, use cancel/replacement/close; idle expiry and host shutdown also cancel work. Ordinary Go HTTP request cancellation after a handler returns must not accidentally cancel every accepted asynchronous turn.

Result selection validates the same authenticated owner, session and exact current turn. A completed cancel/replacement before a subsequent poll prevents stale selection. The adapter checks currentness again before writing and holds no global lock across network writes or Python execution. Concurrent cancellation cannot retract bytes already sent; this is not atomic external delivery or an audio playback guarantee. A future transport must enforce its own actual send/playback boundary.

## Bounds and ephemeral lifetime

The host retains at most eight live sessions and two per owner, with a five-minute idle lifetime. One sweep timer cleans expired records, including sessions that never started a turn. Close/shutdown releases registry snapshots/cached outcomes and cancels leases. Restart loses state; there is no persistent reconnect, approval or idempotent action store.

At most four worker executions run per adapter, reserved before replacement; capacity rejects immediately. Cancelled noncooperative work holds its slot until it returns, preventing unlimited replacement goroutines. The fixed local child follows inherited cancellation and is waited/reaped. Arbitrary custom Go executors cannot be forcibly stopped, but returned late output is rejected by the existing Runner.

Create bodies are at most 1MiB; turn bodies 128KiB. Captured canonical snapshot reserves 4096 bytes of the Runner's 1MiB host-context bound. Depth is at most16, each array256 and aggregate entries4096 across frozen context and arguments. Numbers/null, malformed UTF-8, lone surrogates, duplicate/unknown/trailing fields and nonfinite input fail closed; decimal quantities remain strings. Each session queues/caches at most one 2MiB outcome. These are computational request limits, not country/currency/catalog coverage caps.

## Evidence boundaries

Focused adapter/auth/lifecycle tests and normal/race checks must distinguish deterministic injected executors from actual loopback HTTP→Runner→fixed Python→poll evidence. The four connected hand/no-op/precision/unsupported cases compare against independent public numerical calculations with server scenario identity; record local process latency separately from provider latency. Generated-client transport-stub serialization checks prove string/state preservation only, not authentication or connected execution.

No accepted broad numerical/Python/UI/provider suites are repeated for this adapter. The global40-call pilot remains exhausted, SQL activation held and provider/account state root-owned. This local prerequisite does not complete issue4 or demonstrate microphone/STT/Gemini/TTS, hosted routes, production identity, persisted-plan freshness or operational outcomes.


## Server-owned offline proposal seam

The public session/turn/result contracts stay unchanged. The normal constructor
uses the existing typed synthetic comparison path. An explicit internal
`newVoiceHTTPAdapterWithProposal` constructor is available for a server-owned
local test source and fixed `LocalPythonProposalMapper`; no public request
field accepts SDK/provider output and no new runtime flag selects a fake
provider. Existing viewer/editor read-only policy and same-origin local
authentication remain authoritative.

Proposal mode authenticates and captures the host snapshot and current lease
before requesting a source result. A source receives an owned copy of declared
semantic argument JSON only, never the trusted snapshot/reference/revision,
principal/session/turn identity or computed result. Its private projection is
mapped through the pinned SDK bridge. Current owner, lease and saved-context
revision are checked before numerical dispatch and before selecting a result.
Saved synthetic production contexts are authoritative server records; a legacy
saved recommendation cannot supply their graph.

The leased proposal phase has a five-second deadline and shares the existing
four-worker budget with numerical execution. Sources must honor cancellation.
Noncooperative injected work holds its slot until it returns, and late output
cannot execute or publish after replacement, cancellation or expiry. This
avoids unlimited detached timeout/replacement workers; arbitrary callbacks
cannot be forcibly terminated. Explicit cancel/close remain usable. A source
error, invalid projection, unavailable mapper or timeout produces the existing
sanitized failed `execution_unavailable` state; no typed/provider fallback.
Accepted async turns retain existing request handoff semantics.

The fixed mapper child uses trusted absolute Python/source paths, a clean
environment, bounded stdin/stdout/stderr and inherited cancellation; it is
waited and reaped. The mapper does not execute tools. The unchanged Runner and
fixed Python numerical child retain host binding/currentness and strict result
validation. Database checks use bounded existing revision guards without
holding the voice registry mutex across database waits or child execution.

Evidence for this increment must separately identify authenticated loopback
HTTP, injected server proposals, actual installed SDK mapping and actual fixed
Python numerical equality. Local PostgreSQL 17 tests establish revision
behavior only, not Cloud SQL 18 or deployment. Global 40-call pilot remains
exhausted; SQL activation HOLD and all provider/account actions remain root-owned.
