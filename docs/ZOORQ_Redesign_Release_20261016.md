# ZOORQ redesign release — 16 October 2026

ZOORQ - A product by ClosedSource. An ecosystem of resilience intelligence.

## Delivered experience

- Searchable ten-company hub with Continue current workspace; stable Overview, News, Procurement, Inventory & supply and Drafts & activity destinations.
- Company-first news inbox, adjacent Global Watch, relevance/publication sorting and age/material filters. Mobile list/detail reading has a Back action; evidence and secondary actions progressively disclose.
- Add quotes → Review → Compare → Draft RFQ. One material selector, discoverable saved drafts and supplier/quantity/date/total recap before RFQ review. Navigation preserves work and shows running operations.
- Collapsible operator, microphone beside composer, dedicated Thinking Orbs voice surface, current exact-query consent and compact activity details. Mobile focus and desktop nonmodal behavior are preserved.
- QA-discovered voice compaction fix retains the canonical current material and bounded buyer fields. Voice reads workspace before asking which selected material; large tool responses retain no session secrets or arbitrary buyer fields.

## Deployed state

- URL: https://closed-source-quote-demo-ryoeyuqvta-el.a.run.app/demo
- Cloud Run: `closed-source-quote-demo-00047-ql7`, 100% traffic, project `closed-source-builder-261007`, `asia-south1`.
- Source: `2efdd99` (subsequent release-record commit is documentation only).
- Immutable image: `sha256:122e4992b9bb2db7af2f97364212a3a7e6f2dc1fe5afdf26a51552855e633835`.
- Cloud Build: `8244ea2b-d893-48c9-bbfb-7e931e4bbe00`, SUCCESS.
- Allowlisted package: 319 files / 3,373,727 bytes; manifest SHA256 `f481732ba9ad7a29e31d2efc70216cf8b764c75062d2784aa2f932b97e67a761`.
- Both existing service URLs report ready, exact revision and Firestore persistence.
- Existing caps preserved: max 1 instance, 1 CPU, 1GiB memory, concurrency 8, timeout 240s. No IAM, scheduler, billing tier or public-access expansion.
- Immediately before deploy, authenticated console showed unupgraded trial: INR 28,031.86 / 64 days remaining. No billing upgrade performed.

## Verification

- Final local gates: 377 web tests passed, 6 existing skips; TypeScript and production build passed. Backend demo suite: 749 passed. Focused voice regression demonstrated red then green. Independent UI/state and final backend diff reviews approved.
- Final revision: all ten companies passed news/Global Watch acceptance with zero structural findings. All ten passed real Live voice inventory analytics with stock, unit and stock-cover facts checked against the profile and real PCM output received.
- Earlier deployed revision 46: all ten passed procurement/recovery/RFQ/replay and 20 live typed model turns (analytics and form fill). The final source changes afterward were reviewed frontend navigation/recap fixes and the bounded live-voice context fix; procurement engine and typed operator semantics were unchanged.
- All ten passed voice email drafting across the two release rounds. Atlas/StoneBridge initially asked for the selected material. The final revision retested the original generic spoken request and passed both: inspected unsent draft, reviewed simulation, replay recorded once and external delivery false.
- Hosted browser: all ten hub/company/news/procurement/draft routes checked on revision 46; final revision checked saved-state continuity, RFQ recap, live typed analytics, mobile company-news reading/Back, width 390 with no horizontal overflow.
- Final browser voice: original procurement-world-news question returned company news; dedicated surface displayed exact petrochemical research query and destination. Fresh spoken “confirm research” produced checking/started/completed on the same active connection, dated result, brief preview, sources and real output audio. Mute/unmute, interruption, End and focus back to microphone verified.
- Controlled public speech fixtures were used; physical microphone hardware and subjective voice quality were not tested. Parallel QA initially occupied both voice slots and browser showed the expected busy message; it connected once a slot was released. No automatic retry logic was added.
- Original protected browser cookie restored in memory with Secure/HttpOnly verified. Original Atlas workspace was not changed by QA. Test harness removed and viewport reset.

## Evidence and limits

Sanitized receipts and screenshots are in the task output/evidence directory, outside the repository: `zoorq-final-release-20261016.json`, `zoorq-final-news-20261016.json`, `zoorq-final-voice-inventory-20261016.json`, `zoorq-final-voice-actions-corrected-20261016.json`, `zoorq-final-browser-voice-20261016.json`, `zoorq-hosted-actions-r46-20261015.json`, `zoorq-hosted-ui-r46-20261015.json`, `zoorq-final-company-hub.jpg`, `zoorq-final-voice-consent.jpg`.

Structural acceptance does not certify every news match or factual claim. An existing finance-metaphor “catalyst” false positive was observed in retained operator evidence; the spoken answer used methanol/benzene stories. Tightening source relevance remains follow-up work. Headline-only items retain explicit evidence limitations; research source quality varies. No real supplier email/order was sent.

## Recovery

Previous original runtime: `closed-source-quote-demo-00045-vbg`, image `sha256:0608913176513aab1ca306e438c021b8e7f16bcedbfd0a00f002e2bb03b67166`.
Preliminary ZOORQ runtime: `closed-source-quote-demo-00046-w9x`, image `sha256:8778ce4887e6f535ffa90cdf9f957621f4b60032b8081286187d2e3e75d79774`.
Traffic can be returned to either retained revision without changing session data. No database migration was introduced.

Git delivery is the dedicated redesign branch, based on `feature/hosted-quote-to-plan`; do not interpret this release as merging the wider draft PR106 or main.
