# Hosted voice research and draft repairs — October 15, 2026

The research request now appears above chat history. Native voice reads the exact public query and accepts a fresh spoken “confirmed” or “confirm research”; typed users can review and confirm the visible card. Research opens with a short answer and expandable details. Assistant Markdown headings, lists, bold text and safe source links render in the chat.

Prepared email and RFQ bodies appear before Apply and in the company’s Drafts workspace. Up to 24 unsent artifacts per company survive a new comparison and same-browser restoration. Simulated outbox records remain separately labeled; no external supplier message or order is sent. RFQ and recovery proposals from a replaced plan cannot be applied through the interface.

## Runtime and release

- Final runtime source: `a41efb810fb03b05ab847821ef27d5e7f111cecb` on `feature/hosted-quote-to-plan`; substantive backend source is `e8752051324fb403b550830d0065041455876c69`.
- Frozen runtime/test review patch: SHA-256 `5d60e8947755ecb0b9a7ddc695db68d843f5f2a4a89ecb2f02124ac9de4e7bbb`.
- Final package: 310 runtime files, 3,069,579 bytes; source bundle SHA-256 `ccb437631bfef377a2ac7949c4f9cd3031038c43218b2a299db1e8d867f2711f`.
- Initial successful build: `fcf6b35b-da54-4141-b835-da9aa6c01c25`; immutable image `sha256:a3b33b1a28e81b453d6afeee4c887592ef17287415d37f12250c4ce234e8bf54`; revision `closed-source-quote-demo-00030-gkk` served 100% traffic and passed the functional hosted tests below.
- Final successful presentation build: `bb5bcea5-9e87-41be-9c9f-5a9fce6b8610`, Mumbai; image `asia-south1-docker.pkg.dev/closed-source-builder-261007/closed-source-demo/quote-to-plan@sha256:43ba79e7e9c457899e5655b7f66b2b78d64764fd83fc36dab2d4ac5f6604d9cb`.
- Final revision: `closed-source-quote-demo-00031-79n`, 100% traffic; both service addresses returned this ready revision with `persistence: firestore`. The final change only collapses technical proposal details and corrects voice-consent help. All 299 non-web package files are byte-identical to the functionally tested initial image.
- Destination: existing `closed-source-quote-demo` service in `closed-source-builder-261007`, `asia-south1`.
- Previous working revision: `closed-source-quote-demo-00029-rch`; preserve it for rollback.

## Consent and reliability

Search still requires the exact current, unexpired company/session proposal and a durable one-time claim. Voice approval applies only to public research. Other proposed business actions retain their existing review gate. Replays cannot search twice or restore an older buyer form.

Actual Gemini Live probes returned input transcripts without the optional finished flag. The bridge accepts an explicit final input transcript or verifies a complete fresh voice-activity segment through the existing Google Vertex destination. Complete audio intervals are mapped using cumulative provider offsets into a ten-second memory buffer; clips are limited to eight seconds. The verifier makes one transcription-only request, with no automatic retry, a ten-second deadline and at most three checks per voice connection. Query, company data and chat history are excluded from that verification request. Raw audio is not persisted by the product. A five-second wait admits late activity events; elapsed time never grants consent.

The research adapter accepts up to 24 cited sources and bounded extra unused grounding chunks, while rejecting missing grounding, invalid referenced indices and unsafe source URLs. Optional unsafe Suggestions markup no longer discards an otherwise valid grounded answer. Failure logs contain bounded status/reason/count/timing data, excluding the public query, response and exception message. Provider failures remain possible; an unavailable result is explicit and no paid Search request is automatically retried.

## Verification

- Local backend: 512 demo tests passed, zero failures or skips.
- Web: 337 tests passed, six existing skips; session-race, TypeScript checking and production build passed.
- Final independent review: 146 focused tests passed against the frozen patch, with no remaining evidenced blocker. Partial speech, negation, late activity, expiry during verification, proposal replacement and stale-session replay were exercised.
- Parallel Antigravity backend and frontend re-reviews approved their focused scope, including a fresh-app direct RFQ restoration test.
- One actual Google verifier request recognized the owned, complete “Confirmed” audio. Separate live probes verified cumulative offsets across two audio-stream endings in one connection.
- Hosted native voice on revision 30: one WebSocket carried the public Hormuz/petrochemical question, exact spoken query, a fresh owned “Confirmed” input, one actual research call, six sources and a 32-word spoken answer (610,080 output PCM bytes after the tool). There were zero HTTP research confirmations. Controlled audio tested speech content; no human utterance through the physical microphone was used.
- Hosted text: Hormuz research completed with four sources, 111 words overall and a 23-word opening. An email artifact was visible before Apply, retained on session reread, isolated from another company and left the simulated outbox empty.
- The initial text acceptance script stopped at `/api/demo/plan` with 422 because its general POST helper added an unsupported idempotency field. The product correctly rejected that request. The failing receipt was preserved; a corrected RFQ-only continuation made no Search or email retry. It passed three rules-reviewed naphtha quotes, an operator RFQ before and after Apply, artifact survival after a new comparison, and a direct RFQ retained after reread. Both artifacts remained unsent; simulated outbox count was zero.
- Desktop Chrome additionally observed the email preview in the current proposal, opened the Drafts workspace, reloaded, and reopened the retained unsent body. On the final revision, the current research card appeared above history with exact query/destination, collapsed technical details and visible checkbox/button. At 390 pixels, the page width was 390 and the operator opened as a mobile dialog. The reviewed public copper query completed with five sources, a one-paragraph brief and expandable formatted Impact/Confidence/Next step detail. After reload, answer/citations and the unsent email remained, while Suggestions markup was absent. Chrome microphone permission was granted for this test only; startup reached Listening, End returned to idle. Temporary viewport overrides were reset to normal 1200-pixel width. A browser-control call took an unexpectedly long time during mobile setup; it made no research request, and the expired test proposal was replaced before the one copper confirmation.

## Infrastructure and cost

Existing trial credits only; no billing upgrade, new database, public service or IAM grant is part of this repair. Google Cloud’s account page was reloaded and still showed Free Trial with credits remaining. The existing service identity, private Firestore history store, bounded cleanup and hourly news jobs, and maximum-one-instance revision setting are preserved. Existing Search/session budgets remain in force. Previous infrastructure evidence remains in `docs/hosted-research-history-release-20261015.md`.

## Evidence

Sanitized receipts live outside the repository in the local ChatGPT project `output/evidence/`: `voice-research-drafts-backend-20261015-verified.xml`, `voice-consent-verifier-live-20261015.json`, `voice-transcription-finality-probe-20261015-v3.json`, `voice-consent-hosted-20261015.json`, `voice-drafts-text-hosted-20261015.json`, `voice-drafts-rfq-hosted-continuation-20261015.json`, `voice-drafts-final-package-20261015.json`, `voice-drafts-final-health-20261015.json`, `voice-drafts-browser-final-20261015.json`, and the final desktop/mobile screenshots. No cookies, CSRF values, credentials or Google Suggestions HTML belong in release receipts. Product review notes are in `.superpowers/sdd/voice-research-draft-repairs/`.

## Recovery and Git state

To recover the immediately preceding functional release, route existing service traffic to `closed-source-quote-demo-00030-gkk`; its backend and stored artifacts are compatible. Revision 29 remains an earlier fallback. Preserve the private database and existing news/cleanup jobs. Runtime commits and release evidence are pushed to the existing feature branch and draft PR 106; this repair does not change main. Tag `deploy/demo-20261015-voice-drafts` identifies the final deployed runtime source.
