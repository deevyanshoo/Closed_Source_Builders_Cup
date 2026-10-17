# Hosted Thinking Orbs assistant release

The hosted demo now uses the approved Thinking Orbs library in a warm charcoal assistant panel, with a prominent voice orb, clear connection/listening/thinking/speaking/mute states, readable merged speech transcripts, paired typed questions and answers, compact company attention and collapsed evidence/tool details. The composer remains accessible in the desktop sidebar and mobile drawer. Quote extraction, source fetching, company risk reads, comparisons, recovery and draft preparation have descriptive activity feedback tied to actual pending work.

Demo: https://closed-source-quote-demo-ryoeyuqvta-el.a.run.app/demo

## Exact deployed identity

- App source: `c3b86d2f5668903f137e2c981eee6357738bb67d`. Subsequent documentation commits do not change this deployed app identity.
- Cloud Run: `closed-source-quote-demo-00022-wgm`, 100% traffic.
- Cloud Build: `9bfc20e8-549f-4fa5-b85a-b51653c681ad`, SUCCESS.
- Image: `sha256:037d6f99d6b33cf3f3103c97a6736164182cb3bc973d0867d9ddd16e3a28fccc`.
- Package manifest SHA256: `35abe661f6ffb976e3355467a07c2f2973d9f8279b49de91955aac50c4e0ae1e`; 303 runtime files, 2,919,153 bytes.
- `thinking-orbs@0.3.2` is pinned from npm. MIT copyright/permission notice is copied into the runtime distribution and retained in all five distributed JS bundles. Cursor gravity is disabled. The library's reduced-motion, hidden-tab and offscreen behavior remains intact.
- Existing runtime service account, invocation scope and backend/provider configuration unchanged. No IAM or billing change.

## Observed checks

- Full web gate: 310 tests passed, six existing skips; session race check, TypeScript typecheck and production build passed. Independent review cleared the final voice, caption, loader and company-scope changes. A date-dependent fixture failure was reproduced against unchanged baseline source and repaired with a fixed test clock; production date behavior is unchanged.
- Two disjoint native Antigravity workers implemented voice lifecycle/transcripts and panel/conversation styling. The coordinator implemented shared orbs/loaders, integrated review fixes and owned cloud/browser verification.
- Hosted revision22: nine real Gemini typed turns across three companies passed buyer-form, quote review, comparison, simulated-message apply/replay and ownership checks. Unreviewed comparison was rejected. Twenty HTTP requests, zero retries.
- Hosted revision22: all ten company risk reports and 52 materials passed, including current eligible observed news for every company in this run, stable IDs, ten company switches, context checks and preserved business state. Twenty-nine HTTP requests, zero retries.
- Chrome: a real text question displayed the working orb and returned a paired question/answer confirming Solara copper stock of 2,000 kg and shortest listed lead of 10 days. Actual Gemini extraction of the synthetic Europa CSV displayed the thinking loader and returned a real-extraction result. News refresh and risk scans displayed searching/working loaders; none remained active after completion.
- Chrome native Google voice: the observer captured idle, connecting, listening, thinking, speaking and muted states. Actual provider audio played in 121 chunks (942,242 bytes), and the speaking canvas produced 58 distinct observed frames. A read-only native risk tool returned all ten companies, with no action result. The completed provider answer rendered as one assistant message and matched its transcription exactly.
- A prior voice check also explained inventory impact, conditional news and recovery options. Its observer was attached after playback began, so it is retained as partial evidence rather than substituted for the final full state check.
- Reduced motion: 168 listening samples shared one static frame under the emulated preference. The preference was restored. Microphone mute stopped input; End stopped playback/session resources. Test speech was owned synthetic audio; no physical microphone was used. Temporary microphone/audio/socket/observer overrides were removed.
- Mobile 390px: no horizontal overflow, visible voice controls/orb, focus stayed in the drawer, Escape closed it, and the temporary viewport was reset. Desktop and mobile screenshots were visually inspected.

## Boundaries and recovery

The orb is an interface state indicator, not an extra model or inference service. It does not open the microphone automatically. Typed progress describes the pending request without inventing specialist stages or percentages. Voice speaking tracks queued output playback; completion events cannot truncate queued audio. Stale scope, cancellation and session guards remain, and company switches immediately hide old questions, answers and proposals. Complete typed exchanges are bounded to 20 pairs; streamed voice fragments merge within a turn, with boundaries at completion/interruption and a 24-message history limit.

Company operations and documents remain synthetic, public news remains observed metadata with conditional implications, and reviewed actions retain their existing boundaries. No real supplier message/order or private upload occurred. The Python runtime is unchanged from the prior proactive release; the previously verified all-material numerical matrix is not claimed as rerun here.

The npm audit reported three existing development-tool advisories in Vitest and its test-runner dependencies. This change added no dependency with a reported advisory and did not force an unrelated tooling upgrade. Those npm development dependencies are excluded from the allowlisted Cloud Run runtime package.

Rollback: route the same service to `closed-source-quote-demo-00021-6j6`, image `sha256:714e73c34256599253562761977c8e2cc452284609fe420f703ea0ceedd21644`. Preserve the current immutable package and serving receipt; no permission change is required.

## Evidence

Receipts live in `../../output/evidence/` relative to this checkout: `orb-assistant-release-20261015.json`, `orb-assistant-final-service-20261015.json`, `orb-typed-agent-20261015.json`, `orb-proactive-risks-20261015.json`, `orb-browser-voice-20261015.json`, `orb-browser-loaders-20261015.json`, `orb-browser-layout-20261015.json`, and the web test/typecheck/build output. Final screenshots: `orb-assistant-final-desktop-20261015.png` and `orb-mobile-20261015.png`. Failed/partial QA attempts remain separate from final acceptance.
