# Hosted company intelligence and agent actions release

The existing demo now maps observed news to all ten fictional company profiles and supports grounded text/voice analytics, visible buyer-form updates, quote loading, reviewed quote acceptance, saved-quote comparisons, navigation and reviewed simulated emails. Supplier-delay recovery and unsent RFQ preparation remain available through the operator.

Demo: https://closed-source-quote-demo-ryoeyuqvta-el.a.run.app/demo

## Immutable runtime identity

- App source: `6a6f98b3d83c75fbf078d0aecfe105b028c7692c`. Later documentation commits do not change this deployed app identity.
- Cloud Run: `closed-source-quote-demo-00018-j89`, 100% traffic.
- Cloud Build: `6d5ae1b0-e6db-4949-9b54-847f1abb218c`, SUCCESS.
- Image: `sha256:7c697a7d4055c195b88ebac4a3d19c9dbd8a12ed39f241f306ded56bfbe1e092`.
- Allowlisted source package manifest: `fb8c0b4ec6453bfe59eaf5d9ad04816cf80bb92bfbcd07753f79e7908f031c6a`; 301 files, 2,813,712 bytes.
- Existing runtime service account and public invocation scope unchanged. No IAM or billing change.

## Observed acceptance

- 252 demo Python tests; 281 web tests passed with six existing skips; web typecheck and production build passed. This is the demo scope, not a claim that the repository-wide suite passed.
- Revision18: all ten companies have current relevant observed news. 421 company-headline matches across profiles, not 421 unique articles. Twenty-two HTTP attempts, zero transport retries.
- Revision18: nine actual Gemini multi-agent typed turns across Solara, TerraPack and Atlas, canonical stock/currency/lead-time answers, editable buyer form, three reviewed quotes, supplier comparison, simulation apply/replay and company isolation. Unsaved quotes were correctly rejected before comparison.
- Revision18: actual Google native voice, six synthetic spoken steps in 86 seconds: inventory, form, quote load, review, comparison and email proposal. Human review/apply and replay checks completed; no external delivery.
- Revision18 Chrome: standalone spoken polymer form set 12,000 kg and USD 50,000; real provider audio played. A second spoken request prepared an email to the first material-owned supplier; reviewed Apply produced one visible simulated outbox entry. Test speech was synthetic; no physical microphone was used. Test audio stopped and temporary browser overrides removed.
- Revision18 Chrome: Meridian news explanation cited three observed sector items, supplied conditional implications, confidence rationale and next checks, and returned canonical active-ingredient stock of 1,250 kg.
- Full 52-material / 156-quote planning, recovery and unsent-RFQ matrix passed on revision17 with 2,486 HTTP attempts and zero retries. Later changes affect native voice quote projections/default selection and recipient normalization; the underlying matrix paths remain unchanged.
- Responsive layout: 390px mobile operator drawer fits without horizontal overflow; temporary viewport override reset. Web source is identical across these final runtime revisions.

## Boundaries and recovery

News contains public headline metadata, with 15-minute caches, disclosed title-term relevance heuristics and explicit unavailable/stale source states. Source reliability is separate from relevance. No article-body or X/Meta/Reddit scraping is included. AI implications are conditional, not confirmed delays or forecasts.

Company profiles and documents are synthetic. Sessions are ephemeral. No email transport, supplier message, order or private upload is executed. Simulated emails and quote acceptance require in-app review. Native voice tools validate company/material ownership, current context, state markers and idempotency; failed actions cannot announce a successful write.

Rollback to the prior stable demo: route the existing service to `closed-source-quote-demo-00013-6cp`. Preserve the current image and source package before rollback; no permission changes are necessary.

## Evidence

Local task evidence is stored in `../../output/evidence/` relative to this checkout. The canonical reconciliation receipt is `company-intelligence-release-20261014.json`, with linked news, typed, native voice, browser voice, material matrix and serving-service receipts. Screenshots include `company-intelligence-news-final-desktop-20261014.png`, `company-intelligence-outbox-final-desktop-20261014.png`, and the mobile drawer check.

Earlier failed provider/tool and acceptance-harness attempts were retained as historical receipts. They are not substituted for final successful acceptance.
