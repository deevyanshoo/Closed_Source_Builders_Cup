# ZOORQ continuous voice release — 17 October 2026

The fixed three-minute conversation stop is removed. The browser retains its open voice interface and microphone across bounded transport recovery; the server renews fresh Gemini connections using context compression and bounded completed history as passive configuration context. End still stops capture. Startup deadlines, authentication/company checks and rolling usage controls remain.

## Exact delivered revision

- [Hosted demo](https://zoorq-963802031195.asia-south1.run.app/demo): `zoorq-continuous-4ad06d2`, 100% traffic, Firestore ready.
- Runtime source: `4ad06d2cf63ff90d6f0dffc71a2bcd744994d649`; [PR #129](https://github.com/deevyanshoo/Closed_Source_Builders_Cup/pull/129).
- Cloud Build: `a0ba21d5-5967-42b9-8552-cbea3834d1e7`, SUCCESS.
- Image: `sha256:9b731f874bc5ecf2395b699384c37d1fc6118df42b23a0011b83c4e54140cb48`.
- Allowlisted source bundle: `f4f2b2e6736c0814d1be238a531254f48e1ce2cbbeef4dcbe4af6827df2fbd1e`, 425 files. Manifest/file parity and secret-pattern checks passed. Prior hashed assets were retained for already-open clients.

The existing service request timeout changed from 240 to 3,600 seconds. Workload comparison confirmed the image and timeout were the only workload changes. Identity, environment, CPU 1, memory 1 GiB, concurrency 8, scale annotations and network/security annotations were preserved. No new service, dependency, IAM grant or billing configuration change was made. Existing trial-credit authorization was used.

## Behavior and review

The old lifetime timer is gone from both client and server. Audio and tool controls use rolling windows rather than cumulative conversation totals. Actual Chrome callback batching reproduced a rejection at 180.224 seconds, so normal input has two seconds of bounded burst headroom; high-speed abuse remains rejected.

Provider resumption tokens are not used. Every renewal opens a fresh Gemini connection. Bounded completed current-company history is passive context, never replayed as a new user turn. Pending audio, actions and consent are not replayed. After handoff, tools wait for a fresh valid speech activity and a nonempty current transcript. A single early tool call can wait for up to three seconds for independent activity/transcription event ordering; timeout, interruption and another handoff fail closed. The provider may omit the transcription completion flag: a valid fresh speech interval and nonempty transcript suffice, while an explicit incomplete flag stays latched until completion or a new utterance. Provider connection setup has its own 15-second deadline. Separate implementer and reviewer checks approved the final source before packaging.

## Verification

| Check | Observed result |
| --- | --- |
| Full backend | 3,701 passed; 263 subtests; 2 skipped; 459.93 seconds |
| Full frontend | 586 passed; 6 skipped; session-race checks, TypeScript and production build passed |
| Focused voice/backend | 345 passed, including 45 continuity tests and speech-order, cancellation, replay and budget cases |
| Real Chrome microphone | Recovered one forced transport close, remained Listening beyond 216.212 seconds and 6,899,200 input bytes; unmute resumed capture beyond four minutes; End stopped data |
| Mobile voice controls | 390×844 dialog fit with no horizontal overflow; mute/unmute/End worked; viewport restored |
| Hosted client | Desktop/mobile voice entry visible; HTML, JavaScript and CSS exactly matched the reviewed package |
| Ten-company text risks | All 10 passed, 153–366 ms; company ownership, idempotent replay and unchanged protected business state verified; no external research |
| Hosted long voice | 691.632 seconds on one browser-facing WebSocket; 22,124,836 PCM input bytes; two provider-captioned answers with nonempty PCM, with the second fresh question after 655 seconds |
| Hosted agent actions | Five real Live turns passed: carbon, freight, saved RFQ, recovery proposal and a fresh controlled spoken approval clip; recovery matched the planner and was applied once |

An internal provider renewal was observed: the server opened a fresh Google connection and returned to ready before the new inventory question and grounded answer.

The long voice check independently compared the risk lateness against the hosted company order dates and supplier leads. The later inventory answer was checked against the current company materials and calculated values. Protected business state and exact health revision were unchanged before/after the read-only long call. The action run verified the saved RFQ's canonical text, ledger and download matched, and checked recovery against the numerical planner. RFQs remained unsent and no external message was delivered.

During earlier candidate QA, one action-test attempt used an older clip saying “apply this recovery” rather than the spoken prompt's required “confirm action.” It received an explicit denial and the success-only test timed out. The subsequent fresh-workspace run used the correct reviewed phrase and passed all five turns; assertions were not weakened. A separate initial command rejected relative audio paths before any provider request.

## Corrected integration failure

The first candidate (`5a35b44`) passed offline checks but its real late-session question returned the previous risk question/answer after provider renewal. A fresh-provider control with the same inventory audio passed, while another fresh connection seeded through queued client-content history reproduced the old question. The exact provider token/stream-end mechanism was not established. Traffic was temporarily restored to the prior stable revision while this was corrected. The second candidate (cb47a9d) kept the long connection open and recognized the later inventory question, but denied its tool request because it required a transcription-finished flag that real Google events omitted. A direct metadata trace identified that mismatch; the final gate accepts the observed None-only sequence and retains an explicit-incomplete latch. A loopback handoff reproduction then used the real Gemini adapter and canonical inventory tools on the frozen final source and passed in 41.613 seconds. The final release uses fresh connections and passive configuration history, with no history queued as a new user message. A short seeded-history control initially failed because the QA parser split correct spoken decimal words into integers. An independent review confirmed the actual Methanol cover matched the company profile; a private parser extension added exact decimal handling, retained the existing criteria and passed nine positive/negative tests. The corrected hosted control passed. A direct real Google control and the hosted seeded-history control both recognized the new inventory question and requested the correct workspace tool before the final long test.

## Recovery action consistency

During final hosted action QA, one correctly transcribed recovery request called read_workspace and received a generic ready-to-help reply. The recovery assertion timed out; no recovery preparation was observed in that turn. A single fresh isolated run with identical speech then passed all five actions and the strict numerical, persistence and approval assertions. The generic reply occurred before provider renewal; its cause was not established. It remains an observed model-routing consistency limitation outside the voice-duration correction. The failed receipt is retained as native-completion.json, while the passing run is native-completion-confirmed.json.

## Evidence boundaries and operation

Hosted tests used controlled WAV speech and verified actual PCM output plus provider captions; they did not independently transcribe or listen to speaker output. The physical microphone was exercised with a local mock that counted then discarded audio, without Google calls or saved audio. That local rate/transport fixture used backend `1ca8cfb`; final `b06568f` additionally corrected provider renewal, passive history restoration and optional transcription completion handling; the microphone/rate/client-transport paths were unchanged. The real hosted tests exercised the exact final image.

The 60-minute Cloud Run boundary was not waited out. Automatic browser recovery was exercised through a forced close; provider renewal and context behavior have the scoped evidence above and offline regressions. This removes a fixed conversation timer, not authentication expiry, network/provider outages or usage protections. Persistent failures stop with a visible explanation rather than retrying forever. Refresh an already-open demo once to load the new voice client.

Private receipts are retained under `/private/tmp/zoorq-continuous-voice-20261017/`: `backend-completion-approved.xml`, `frontend-full.log`, `local-browser-receipt.json`, `deployment-completion-verification.json`, `hosted-completion-assets.json`, `ten-company-risk-completion.json`, `native-completion-confirmed.json`, `native-evidence-completion-confirmed.json`, `direct-history-config.json`, `inventory-history-completion.json`, and `long-call-completion-final.json`. They are local release evidence, not committed workspace/session credentials. The previous ready revision `zoorq-riskvoice-63b86ce` remains the rollback destination.
