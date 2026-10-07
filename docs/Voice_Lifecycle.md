# Local voice session and turn lifecycle

`services/gateway/internal/voice` provides process-local lifecycle primitives for future conversational transport. It is a standard-library Go package, with no HTTP/WebSocket routes, authentication, providers, transcript interpretation, persistent state or effectful actions. The host supplies an already authenticated principal/workspace. Local identity comparisons do not authenticate a person or establish a current persisted plan.

## Host boundary and API

Construct `NewManager(Config, now)` with explicit limits and a concurrency-safe trusted clock. `Owner` contains `PrincipalID` and `WorkspaceID`; both must be nonempty, trimmed UTF-8 without control characters and at most 256 bytes. Never populate owner fields from model output or untrusted document text. Each session access requires that same owner. Foreign, unknown, expired and closed session/lease lookups return the same unavailable error; the manager retains no tombstones.

`Create(owner)` issues a securely generated opaque session ID. `StartTurn(parent, owner, sessionID)` creates an immutable `Lease`, exposing only `SessionID()`, `TurnID()` and `Context()`. One turn is current per session. A replacement invalidates the prior lease, drops its queued output and cancels its Go context. Private lease/manager identity and nonreused turn generation prevent old handles from becoming current after replacement or restart.

`IsCurrent(owner, lease)` checks ownership and active context; it does not renew idle lifetime. A future host can capture its trusted owner/lease in the Python tool's active-turn guard, additionally matching the expected session/turn identifiers. This package does not wire that cross-service guard or authenticate its caller. A check is only a snapshot of local state.

`Cancel(owner, lease)` invalidates only a current authorized turn and clears output. `Close(owner, sessionID)` removes the session and frees quota. Duplicate/stale close/cancel calls have no effect and return unavailable. `Shutdown()` is idempotent, cancels all active work and permanently closes that manager. A fresh manager has no prior sessions, reconnect records or approvals.

## Bounded output and cancellation

`Enqueue(owner, lease, payload)` checks the current lease and appends under the same manager lock. Payloads are nonempty opaque bytes copied on acceptance. Exceeding message, payload or byte bounds rejects without keeping the rejected data or evicting an accepted current message. The package does not understand or synthesize model/speech output.

`Dequeue(owner, sessionID)` discards superseded, cancelled or expired output and returns FIFO current-turn `Output {SessionID, TurnID, Payload}` plus a presence flag. A returned payload is caller-owned. This is local dequeue, not atomic network delivery: a turn can change immediately afterward. A future writer must recheck ownership at the actual send/playback boundary and suppress stale bytes there. No callback or network write runs inside the manager lock.

Context cancellation is cooperative. The manager cancels derived Go contexts; it cannot forcibly stop noncooperative CPU work or an external provider. Producer code must observe `lease.Context().Done()` and still use guarded enqueue. Parent cancellation also makes current-lease checks/publication/drain fail closed. Replacement, cancellation, close, expiry and shutdown discard queued output.

## Bounds, clock and ephemeral state

All config values are explicit and positive: at most 4096 live sessions, a per-owner session limit no larger than the global limit, idle lifetime at most 24h, at most 256 queued messages per session, payload at most 2MiB and queued bytes at most 8MiB per session. Payload limit cannot exceed queue-byte limit; global sessions times queue-byte limit cannot exceed 64MiB. These are process computation bounds, not country/currency/catalog restrictions.

Every operation checks expiry at its trusted clock sample; samples are taken outside locks and clamped to the highest previously observed sample. Idle expiry occurs at the deadline, not after it. Valid create/start/cancel/enqueue/dequeue activity renews idle lifetime; foreign/invalid operations and `IsCurrent` do not. Expiry removes the session and releases quota. Cancellation functions run after lock release.

There is no permanent session goroutine or internal timer. Cleanup is opportunistic on manager operations and explicit `Sweep()`. An idle host must call sweep on its own lifecycle/timer if it needs contexts cancelled without another request. Go's standard context machinery may monitor a parent; this package starts no goroutine of its own. Restart loses all state. These primitives are not a distributed session service, durable idempotency, current-plan authority or a persistent reconnect implementation.

## Local checks

From `services/gateway`, using the approved compatible Go toolchain:

```sh
GOTOOLCHAIN=local GOPROXY=off GOSUMDB=off go test -count=1 ./internal/voice
GOTOOLCHAIN=local GOPROXY=off GOSUMDB=off go test -race -count=1 ./internal/voice
GOTOOLCHAIN=local GOPROXY=off GOSUMDB=off go vet ./internal/voice
```

Focused tests exercise the actual local manager with coordinated producers, channels and an injected clock. They distinguish current delivery from suppression, verify ownership/turn/cancellation/expiry/overload/restart behavior and run under the race detector. They provide no microphone, speech, Gemini, TTS, provider, route, authentication or production delivery evidence. Existing Python/UI/provider suites are unchanged and are not repeated for this isolated package.
