# Hosted proactive company risk release

The existing demo automatically flags inventory exposure, potential supplier lateness and relevant public news across all ten fictional companies and all 52 catalogue materials. Each alert explains its impact, confidence rationale, evidence and recovery options. The operator and native voice can read current-company or portfolio risks; recovery suggestions enter the existing reviewed workflow.

Demo: https://closed-source-quote-demo-ryoeyuqvta-el.a.run.app/demo

## Immutable runtime identity

- Application source: `24103f773ac40b88f6bc0a91547953a348ba9716`. Later evidence/documentation commits do not change this deployed application identity.
- Cloud Run: `closed-source-quote-demo-00021-6j6`, 100% traffic.
- Cloud Build: `701c1bc8-6bd0-499b-9742-d887a6018ae9`, SUCCESS.
- Image: `sha256:714e73c34256599253562761977c8e2cc452284609fe420f703ea0ceedd21644`.
- Allowlisted package manifest: `feef50c326b43689fc747d01792307ae934b7f7701a9e9ca54352fe5341a30da`; 302 runtime files, 2,868,602 bytes. Tests, private files, browser QA speech and evidence are excluded from the runtime package.
- Existing service account and public invocation scope unchanged. No IAM or billing change.

## Observed acceptance

- 277 demo Python tests passed; frontend gates passed with 288 tests and six existing skips, typecheck and production build. Independent backend and comparison review clear. This is demo-scope verification, not a repository-wide passing-suite claim.
- Revision21: all ten company risk reports and 52 materials checked, with current eligible observed news for every company in this run. Repeated scans returned stable IDs; ten company switches retained current context and restored the original workspace. Risk reads preserved business state. Twenty-nine HTTP requests, zero transport retries.
- Revision21: nine real Gemini typed turns across Solara, TerraPack and Atlas passed canonical analytics, editable buyer inputs, three reviewed quotes, comparison and reviewed simulated-email apply/replay. Comparison before review was rejected. Twenty HTTP requests, zero transport retries.
- Revision21: actual `gemini-3.8-live` voice read portfolio risks for all ten companies, included supplier and news alerts, distinguished synthetic scenarios from observed news, and produced 1,209,122 provider audio bytes in 48.4 seconds. Workspace unchanged; no action result. Test speech was synthetic; no physical microphone used.
- Revision21 Chrome: an inventory recovery suggestion seeded the copper quote-loading request. Explicit Send invoked three real Gemini specialist calls and the deterministic verifier, then loaded three immutable synthetic quotes for review without accepting them.
- Revision21 Chrome: real Google voice invoked `read_risks` for all ten companies and played 1,672,320 provider audio bytes in 212 chunks. The spoken answer identified Solara copper cover versus lead time and an observed Chile copper strike headline, while distinguishing synthetic assumptions and conditional news exposure. No action result; test input was owned synthetic speech. Voice stopped and temporary microphone/audio/socket overrides were removed.
- Chrome automatic monitor timestamps advanced from `19:14:17` to `19:18:17` UTC without a manual refresh. All-company summaries are collapsed by default. The mobile operator drawer at 390px had no horizontal overflow; the temporary viewport was reset.

## Operating boundaries and rollback

Automatic monitoring runs every 60 seconds while the workspace is visible and resumes when visible again. News uses a 15-minute cache. Closing the workspace stops monitoring; this is not a background scheduler or external-notification service. Automatic scans are deterministic reads and do not call paid agent models or execute business actions.

Inventory, suppliers, orders, stock overrides and delay scenarios are explicitly synthetic. News is observed public headline metadata, mapped with disclosed heuristics and source-health/freshness checks. Source quality is separate from impact confidence. Headline-only alerts are at most medium priority; resolved or easing disruption headlines are contextual low-priority watches. No headline confirms an actual shipment delay, stockout or price forecast.

Alerts include source outages, missing inputs and unreviewed-plan gaps. Other-company recovery actions first switch company. Current-company recovery messages only seed the composer; the user sends the request and reviews any resulting proposal. Quote acceptance, simulated email apply and unsent RFQ preparation retain their existing safeguards. No real message, order or private upload is sent.

Rollback: route the existing service to the prior stable `closed-source-quote-demo-00018-j89`. Its immutable image is `sha256:7c697a7d4055c195b88ebac4a3d19c9dbd8a12ed39f241f306ded56bfbe1e092`; preserve the current source package and serving receipt before rollback. No permission changes are needed.

## Evidence

Local receipts are under `../../output/evidence/` relative to this checkout: `proactive-risk-release-20261014.json`, `proactive-risk-final-service-20261014.json`, `proactive-risk-hosted-20261014.json`, `proactive-risk-existing-agent-workflow-20261014.json`, `proactive-risk-native-voice-20261014.json`, `proactive-risk-browser-voice-20261014.json`, `proactive-risk-browser-acceptance-20261014.json` and the backend test output. Screenshots include `proactive-risk-final-desktop-20261014.png`, `proactive-risk-recovery-browser-20261014.png` and `proactive-risk-mobile-20261014.png`. Earlier revisions and failed attempts are retained separately and are not substituted for final acceptance.
