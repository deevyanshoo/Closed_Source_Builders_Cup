# Local structured recovery tool

This is a read-only, provider-neutral prerequisite for conversational recovery. It accepts an exact structured function call and uses the existing Python receipt-recovery comparison. It does not recognize speech, interpret natural language, call Gemini, synthesize audio, authenticate a person, apply a plan, persist approvals or contact suppliers. Local synthetic checks are not model, microphone, IAM or speech evidence.

## Trusted caller and model boundary

The future authenticated gateway/session caller owns `TrustedRecoveryContext`: complete synthetic snapshot, workspace, snapshot ID, plan version, scenario ID, session ID and turn ID. These fields must be selected from authorized caller state, not supplied by the model or copied from untrusted browser/document text. The adapter verifies internal consistency; it cannot establish who authenticated or whether an ephemeral snapshot is the current persisted production plan. A future caller must separately enforce those guarantees.

The only tool name is `compare_receipt_recovery`. `function_declaration()` returns a fresh standalone JSON-schema declaration with only two arguments:

- `arrival_replacements`: explicit receipt ID, expected current arrival and target arrival timestamp. Empty means no receipt changes; missing fields do not cause a guessed delay.
- `assignments`: an explicit production period for every trusted order, exactly once. Empty/incomplete/foreign/duplicate assignments cannot form a comparison.

The local raw JSON envelope is `{name, arguments}`. A future provider adapter must deliberately map its SDK function-call format to this envelope. Workspace/version/snapshot/session/turn fields, computed outcomes, apply/send actions and other names are forbidden in model arguments. The declaration is model-neutral; its export and later Gemini schema support require actual provider validation. No SDK is installed or exercised here. A later Google Cloud adapter can map the standalone `parameters` object to `parametersJsonSchema`, which is documented as mutually exclusive with the provider's `parameters` field. That mapping and a real call remain unimplemented. [Official FunctionDeclaration reference](https://docs.cloud.google.com/gemini-enterprise-agent-platform/reference/rest/Shared.Types/FunctionDeclaration).

## Execution and result ownership

`execute_recovery_tool(call_json, context, *, is_active)` requires an injected trusted `is_active(session_id, turn_id) -> bool`. Missing/exceptional/nonboolean/false guards fail closed. The adapter checks ownership before numerical invocation and before returning a publishable result. Cancellation or replacement during calculation suppresses the late result. It keeps no persistent session registry, and it does not interrupt numerical CPU work already running. The future transport must guard actual delivery atomically against current session/turn ownership; a returned result is not an authorization token.

Input data is captured as an execution-owned JSON clone so later caller mutation cannot alter the captured numerical request. The adapter builds scenario/recovery scope only from context and delegates to the existing public `compare_receipt_recovery`, using the current strict HTTP-comparison schema authority without modifying it. Numerical calculations remain in the reviewed production modules. Existing unsupported reasons, exact decimal strings, units, timestamps, synthetic status and unmodeled limits survive unchanged.

The result is explicitly `read_only:true`. Compared/unsupported outcomes carry trusted session/turn ownership and the unchanged public numerical result. Missing critical fields produce structured clarification; invalid/forbidden calls produce rejection; execution failure produces generic unavailable; cancelled/superseded turns produce suppression without partial numerical data. No status proves persisted approval, durable idempotency, customer commitment or operational feasibility. Repeated valid comparisons can recompute because they have no durable effects.

Local computation bounds are 128KiB for the call, 1MiB for captured context/envelope, 2MiB for returned output, depth 16, 256 entries per array and 4096 aggregate array entries. These bounds are not country/currency/catalog caps. Dates, exact decimal quantities, unit rules and numerical eligibility come from the current strict schema/public numerical authority. Unsupported data is not silently converted or fabricated.

## Local use and checks

The local harness constructs trusted synthetic context from the existing recovery examples, submits explicit structured arguments and compares the returned result to the actual public numerical tool:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=procurement-engine/src python scripts/check_voice_recovery_tool.py
```

Use the project's existing compatible Python environment; no new dependencies or provider credentials are required. The harness's trusted active-turn guard is synthetic test state, not authentication evidence. Tests additionally exercise cancellation/replacement before and during calculation, malformed/duplicate/trailing/nonfinite JSON, bounds, context/model spoofing and stale/ambiguous inputs.

Only new tool tests and the affected existing HTTP adapter/recovery suites are required for this increment. Prior React/Go/cloud suites are unchanged and are not repeated. The task report identifies actual commands, red-to-green checks, final frozen artifact/review and draft PR. Future work remains real supported-region STT/Gemini/TTS access, authenticated transport integration and measured transcript/tool/audio lifecycle; the owner controls that provider pilot. SQL activation remains held; model-region choice is pending; the INR 10000 total existing-credit allowance is root-owned. This module causes no account/resource/API/deployment or supplier action.
