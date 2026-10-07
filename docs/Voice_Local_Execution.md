# Local lifecycle-to-recovery execution

This package joins the existing process-local Go voice lifecycle to the existing Python read-only recovery tool. It is a local execution prerequisite, with no public routes, authentication, speech/model/provider calls, UI, persistent plans or consequential actions. The caller supplies an already authorized owner and complete synthetic snapshot. Internal consistency is not current persisted-plan or authentication proof.

## Host-owned context and model arguments

The host creates a session/lease with the existing `Manager`, then calls `CaptureRecoveryContext(owner, lease, metadata, snapshotJSON)`. `RecoveryMetadata` contains snapshot ID, plan version and scenario ID. The capture owns bounded snapshot bytes and binds private context to that exact lease and owner; session/turn IDs come from the lease. Caller mutation cannot change a captured execution. No mutable context registry is added.

The model supplies only a raw `{name, arguments}` function call. The only name is `compare_receipt_recovery`; arguments are explicit arrival replacements and complete production assignments. Workspace, principal, session, turn, snapshot, version and scenario are host state. Unknown context/apply/send/computed-result fields are rejected. Missing critical model data produces clarification or rejection, never a guessed number/date or fabricated comparison. Python remains the numerical and full schema authority.

`NewRunner(manager, executor)` creates a host-owned runner. `Run(owner, lease, captured, callJSON)` verifies current ownership before dispatch, captures the call and executes once outside manager locks. It bounds work to four concurrent calls per runner and one per lease; busy calls reject immediately without dispatch. The host owns that runner budget and must not multiply instances to bypass it. A 30-second context inherits lease cancellation. A custom executor must cooperate; an arbitrary blocking Go function cannot be forcibly stopped. The runner itself starts no goroutine to abandon such work.

## Fixed local Python bridge

`NewLocalPythonExecutor(LocalPythonConfig{PythonPath, SourcePath})` accepts only trusted absolute host configuration. It invokes the fixed `procure.agents.voice_local_bridge` module directly, with no shell or model-selected executable/argv/provider. One bounded stdin request produces one bounded stdout response. A minimal environment supplies no provider credentials; this fixed bridge calls only the pure local recovery tool. Excess output or cancellation stops the child, and the executor waits/reaps it before returning. This is one local child per accepted execution, with no long-lived process reuse or deployment claim.

The private local protocol is version 1, separate from shared/public API contracts. Request fields are exactly `bridge_version`, `host_context_json` and `call_json`. The captured host JSON includes owner/context/lease identity and the complete synthetic snapshot. Its exact UTF-8 bytes are SHA-256 bound to the response; this avoids cross-language number/canonicalization loss. The model call remains a string so duplicate keys cannot disappear in an intermediate decode.

The Python bridge constructs the existing `TrustedRecoveryContext` and calls public `execute_recovery_tool` once. Its injectable guard is used in local tests; the CLI guard establishes process-local liveness and identity, not a synchronous query of the Go manager. Go cancels the child through the inherited context and rechecks/guards output afterward. No general callback service or cross-language cancellation network is implied.

## Returned identity and guarded output

The response contains version, host binding and the actual tool outcome. The runner strictly validates bounded JSON, duplicate/unknown/trailing/nonfinite data, allowed status, `read_only:true`, tool name, owner/context digest and session/turn identity. Compared results must preserve synthetic status and matching workspace/snapshot/version/scenario/as-of identity and known order/reference sets. Existing generated Go types are reused read-only; arithmetic stays in Python.

Compared/unsupported outcomes preserve the public numerical result and its limitations. Clarification/rejection/unavailable outcomes carry no numerical result and may be delivered as safe local status. Suppressed, stale, foreign or invalid-protocol executions enqueue nothing. Errors expose generic lifecycle/execution codes rather than raw subprocess exceptions or input text. The runner does not establish numeric correctness for a malicious host-selected executor.

After execution, the existing manager performs atomic current-lease validation and bounded enqueue of the exact full validated response bytes (`bridge_version`, `binding`, `outcome`). The queued public result is `payload.outcome.result`; it is not rebuilt in Go. Replacement, cancellation, expiry, close and shutdown suppress late output even from a deliberately noncooperative executor. Dequeue remains local: a future socket/audio writer must recheck ownership at its actual send/playback point. No atomic external delivery, persistent reconnect, approval/idempotency or operational promise is implemented.

## Bounds and checks

Calls are at most 128KiB, captured host context 1MiB, encoded request 3MiB and response 2MiB. Depth is at most 16, each array 256 entries and aggregate array entries 4096 per boundary. IDs are bounded strings; quantities retain exact decimal strings with explicit units. Existing manager payload/queue limits can reject a response below the runner maximum when configured more tightly. These are computational bounds, not catalog/country/currency limits.

Use the existing compatible Go/Python environments. The package tests cover the actual local runner, deterministic late/cancel/foreign-context cases and the fixed child executor. The connected harness must be explicitly exercised against the real local Python module for recovery, no-op, precision and unsupported cases; a skipped harness or a synthetic blocking-child cancellation probe is not numerical equality evidence. Local child/bridge elapsed time is recorded separately from provider latency.

Run focused Go normal/race/vet checks, new Python bridge tests and the affected voice-tool tests as recorded in the task report. Existing numerical, gateway/UI/cloud and provider suites remain unchanged. These local tests prove no microphone, STT, Gemini, TTS, public route, production authorization or distributed/current-plan behavior. Root owns separately scoped transport/provider integration and final main integration.
