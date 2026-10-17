# Original hosted address voice repair — 17 October 2026

## Cause

The user's active tab was the original [shared address](https://closed-source-quote-demo-ryoeyuqvta-el.a.run.app/demo). It still served `closed-source-quote-demo-00074-sjn`, which enforced the lifetime six-start company cap and returned “This company has reached its voice start limit for this demo session. Refresh the session to continue.” The newer [ZOORQ address](https://zoorq-963802031195.asia-south1.run.app/demo) already served the repaired image. Testing that newer address did not verify the independently deployed original service.

The original tab also showed the previous interface. Its stored Solara Cables workspace had four reviewed quotes and its historical conversation. Resetting that session would have hidden the deployment mismatch and discarded useful context.

## Repair

The original service now serves `closed-source-quote-demo-voice-2285048` at 100% traffic, using the same already built and tested image as `zoorq-feedback-2285048`:

`sha256:d623f833753d8818354996d1bd511020cdc2c7c8adcfe52594fce592eb810c15`

Runtime source remains `2285048f10d105d0513e466f6c6ef07ac948fc18`. No application code or dependency changed, and no duplicate Cloud Build was needed. The original service's transport timeout increased from 240 to 3,600 seconds to match the approved continuous-voice envelope. Authentication, service identity, environment hash, CPU/memory, concurrency, maximum instances and security-related annotations stayed unchanged. No IAM, billing, new service or paid-plan change was made. Existing trial-credit authorization applies.

The service-specific rollback target is `closed-source-quote-demo-00074-sjn`. Reverting traffic to it restores the prior application behavior, including its known voice limitation; it is an operational recovery option, not the desired fix. The canonical service's revision is not a valid rollback target for this separate service.

## Verification

- Original-address health reports the exact repaired revision, `ready: true` and Firestore persistence. Cloud Run traffic is 100% on that revision. The immutable image and hosted HTML/entry assets match the verified release package.
- Seven sequential starts in one disposable company session reached actual provider readiness, including starts six and seven. No audio, business action, automatic retry or workspace reset was involved; protected records stayed unchanged.
- The previously blocked **existing** Solara browser session was reloaded without clearing cookies or resetting data. Its four reviewed quotes and historical conversation remained visible. Voice connected successfully and reached the speaking/muted state without the old cap error; the verification connection was ended.
- A separate controlled-speech Hindi check on the original address returned the correct owned six-day copper cover and ten-day supplier profile lead through read-only company tools, with nonempty provider speech output, no external research and unchanged protected records. This uses actual Vertex captions/PCM, not independent output transcription or an audio-listening evaluation.

The lifetime company cap is removed, while shared service usage and concurrency safeguards remain. The transport timeout is not a promise of unlimited provider quota; healthy voice uses the existing renewal/reconnection behavior.

The [deployment guide](../deploy/judge-demo/README.md) now treats both public addresses as active release targets. Release acceptance must check each service's exact image, traffic, runtime configuration and actual address-specific browser/voice path. Optional settings, including the restricted Maps browser key, remain service-specific; this voice repair preserves the original environment rather than copying credentials or changing API restrictions.

Private operational and QA receipts are under `/private/tmp/zoorq-legacy-voice-20261017/`. They contain safe configuration hashes and bounded test results, not exported session cookies or credentials. The broader application checks and controlled-speech limitations remain in the [application release report](ZOORQ_RECOVERY_AND_FEEDBACK_20261017.md).
