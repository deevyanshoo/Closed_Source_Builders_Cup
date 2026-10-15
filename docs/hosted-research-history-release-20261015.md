# Hosted consented research and conversation history — 15 October 2026

Live workspace: https://closed-source-quote-demo-ryoeyuqvta-el.a.run.app/demo

## Deployed identity

- Runtime source: `53072e6b3e1f8765464eef53d22a2e8fe810e706`, branch `feature/hosted-quote-to-plan`.
- Build: `ee463220-3c70-477d-854c-c24dcd062cf2`, successful.
- Immutable image: `asia-south1-docker.pkg.dev/closed-source-builder-261007/closed-source-demo/quote-to-plan@sha256:c5ae4b0e218eb8b92d12e60077ee3ce24de027cfd3112e9efc7122fbd04be389`.
- Source manifest: 309 runtime files, 3,026,450 bytes; SHA-256 `51ad5ceb4218f4d27de78526a90d6b60b2f04afaeda7521df5dc7b85777c7203`.
- Service: `closed-source-quote-demo`, Mumbai `asia-south1`, project `closed-source-builder-261007`.
- Final revision: `closed-source-quote-demo-00029-rch`, 100% traffic, maximum one instance. Revision 28 deployed the feature; revision 29 corrected the source label and provided a controlled fresh-instance recovery test using the same image.
- Existing service identity remains `closed-source-demo@closed-source-builder-261007.iam.gserviceaccount.com`; `DEMO_SESSION_BACKEND=firestore` is enabled. The existing news snapshot configuration is preserved.

## Behavior

Typed and spoken requests prepare a research card containing the exact public query and Google Search via Vertex AI destination. A checked review box and explicit Research web action are required. Speech cannot grant consent. Company context, expiry, workspace changes, cancellation and proposal replacement invalidate stale approvals. A durable claim precedes the provider request; replay never initiates another search.

Research returns a dated answer, source links, qualitative confidence and possible next steps. Only the reviewed public query and fixed research instructions reach the research provider. Private workspace details and contact/credential patterns are rejected or require a public reformulation. The provider makes one bounded request with no automatic retry. Limits are six attempts per session, one active search per session and 30 shared research requests per UTC day. These are request limits, not a guarantee of zero provider charges.

The same browser resumes its private session and per-company recent history for seven days. Workspace state and finalized voice text are persisted; raw audio and unfinished captions are not. History is bounded to 60 exchanges per company and a total byte limit, so older exchanges can rotate out. Cross-device or account-based recovery is not implemented. Pending consent is deliberately absent after a cold restoration.

Google Search Suggestions markup is displayed unchanged only in the initial result, within a restricted sandbox. It is excluded from persistence, history, voice recall, idempotency responses and evidence receipts. Stored answers and citations remain available. Existing sample-data disclosures, simulated delivery and review gates remain in place.

## Infrastructure and cost boundary

The existing unupgraded Free Trial was inspected before provisioning: INR 28,335.81 credit remaining and 64 days at that inspection. No billing upgrade or new trial was initiated. Later spend is not inferred from that earlier balance.

The default Firestore Native Standard database in Mumbai reported `freeTier: true`; deletion protection is enabled. PITR, paid TTL and backups were not enabled. The app identity received `roles/datastore.user` only under a condition matching `projects/closed-source-builder-261007/databases/(default)`. Anonymous access to an existing test document returned 403. Database access remains server-side.

`closed-source-chat-cleanup` uses the same immutable image and restricted app identity: one task, one CPU, 512 MiB, 60-second timeout, zero retries, at most 128 expired records per execution. The existing scheduler identity has invocation permission on this job only. `closed-source-chat-cleanup-hourly` runs at `15 * * * *` UTC, which is hourly at :45 IST. Both the direct execution `closed-source-chat-cleanup-8w275` and Scheduler-invoked `closed-source-chat-cleanup-7wrdj` succeeded. The latter completed in 8.33 seconds; the Scheduler receipt was successful. The existing news schedule remains hourly at :30 IST.

Expiry is enforced on reads even before physical cleanup. No Cloud SQL, BigQuery, public database rules or additional paid plan was introduced. Existing trial credits cover allowed usage outside ongoing free allowances; provider quotas and the single-instance cap remain bounded.

## Verification and limits

Local checks before packaging: 455 backend demo tests passed; 327 web tests passed with six existing skips; session-race, type checking and production build passed. Independent persistence, consent/UI, and final release reviews were completed in parallel, including Antigravity terminal reviews. The Mac shutdown did not change source or the successful deployed image; live state was reconciled before continuing.

Fresh hosted evidence:

- Text acceptance: 36 checks, all ten companies prepare/cancel, unsafe-query rejection, unreviewed approval rejection, one actual Atlas Hormuz search with seven sources, replay, same-browser reload and independent-session/company isolation.
- Voice: controlled 16 kHz input streamed through Gemini Live, reviewable research proposal, one explicit HTTP consent, four-source approved result, read_research recall and 24 kHz spoken output. Two finalized captions per turn were saved. The test waits for spoken output after the tool result, rather than ending on an intermediate model turn.
- Browser: explicit query/review controls and research activity were observed; a company-aware Hormuz result completed and restored after reload. Sources and date remained; the prior actionable approval and transient Suggestions did not. At 390 px, page and drawer widths were both 390 px and the result was reachable by scrolling. Temporary sizing was reset. Chrome microphone start reached Listening, then was stopped. Spoken research content used controlled audio; a human utterance through the physical microphone was not part of the controlled test.
- Cold revision recovery: 11 checks across revision 28 to 29; the same owned session, company and finalized voice marker restored. Pre-restart consent returned 404 proposal_not_found and made no Search request.
- Core regression: all ten companies passed reviewed quotes, baseline comparison, wrong-unit rejection, supplier-delay recovery and unsent RFQ creation/replay/download: 456 HTTP requests, rules extraction, no extraction/operator model calls.
- A separate specific TerraPack PET resin stock question completed with three real specialist roles on revision 29 and returned the canonical 18,500 kg figure.
- Actual Firestore preflight verified cold-store restore, stale conditional-write rejection, expiry, cleanup and anonymous denial. Hosted session creation, caption writes and revision recovery subsequently verified the deployed app identity as well.

Two unavailable responses were retained in evidence: a generic TerraPack inventory operator question on revision 28 and a copper research request in Chrome. The original operator harness discarded the response body, so clarification, validation and provider failure cannot be distinguished from that receipt. The specific TerraPack probe passed on revision 29. A separate one-request copper provider diagnostic completed in 6.191 seconds with seven valid sources and safe attribution markup; it did not reproduce the hosted unavailable response. These later checks do not erase the earlier failures or establish their cause. The product fails explicitly and requires fresh consent for another search; no automatic paid retry or external business action was added.

## Evidence locations

Sanitized receipts are in the local ChatGPT project directory `output/evidence/`:

- `research-history-backend-20261015.xml`
- `research-hosted-text-20261015.json`
- `research-hosted-voice-20261015.json` and the owned test output WAVs
- `research-hosted-restart-20261015.json`
- `research-history-company-core-20261015.json`
- `research-history-company-core-20261015.operator-probe.json`
- `research-copper-diagnostic-20261015.json`
- `research-firestore-preflight-20261015.json`
- `research-history-infrastructure-20261015.json`
- `research-history-browser-20261015.json`, desktop and mobile PNGs

Receipts contain no session cookies, CSRF values, credentials or Suggestions markup. Earlier failed receipts are preserved separately. Repository review notes are under `.superpowers/sdd/consented-research-history/` in the development checkout.

## Recovery

No rebuild is needed to restore the previous release: route traffic back to revision `closed-source-quote-demo-00027-hxw` if necessary. That revision uses ephemeral sessions, so durable history would temporarily be unavailable through the app; the private Firestore database should be preserved for the next corrected release. Pause `closed-source-chat-cleanup-hourly` if cleanup itself requires investigation. Preserve the existing news job, schedule, private snapshot bucket and database. This release does not merge the feature branch into main; integration remains in draft PR 106.
