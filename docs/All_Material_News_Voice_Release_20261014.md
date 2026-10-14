# All-material procurement, news and voice release

The existing demo now supports all **52 materials across ten fictional companies**, using **156 immutable quote documents**. The material picker filters quotes and accepted offers, applies matching quantities/units/currencies/budgets, and invalidates stale derived results. Every scenario passed the hosted quote review, plan, supplier-delay recovery and unsent RFQ workflow.

News and voice failures were traced to lost or expired temporary sessions. News also had a loading-effect cancellation defect and hid all observations for companies with zero matches. The release restores only canonical sessions, retains visible recovery guidance, shows honestly unmatched observations, labels provider throttling, and refunds failed voice startup allowances. Voice remains native bidirectional Google audio with interruption and the same reviewed action boundary as chat.

## Observed release

- Hosted URL: https://closed-source-quote-demo-ryoeyuqvta-el.a.run.app/demo
- Application source: `75c70b4aebe77a18200543c0eb3406686b10b7e6`.
- Revision: `closed-source-quote-demo-00013-6cp`, 100% traffic.
- Image: `sha256:dec83624980ae361cdbc650673ba95ab5db407489b646daed0c4c6db35c4836e`.
- Build: `97d75e33-be41-4a5b-9b29-93796e8ed733`.
- Allowlisted package: 300 files; manifest hash `b4c18750d3ab89e46b541a44842c0ab0bf20f96854ede055b0c138a8a9553299`.

## Acceptance evidence

Local checks passed: 202 demo backend tests, 268 web tests (six existing skips), typecheck and production build. The persistent recovery-notice follow-up passed 32 focused page tests and a fresh typecheck/build. October 17 checks review/save all 156 documents and plan/recover all 52 scenarios using explicitly dated October 14 synthetic FX while checking current quote validity.

The final hosted rules matrix passed all 52 scenarios and 156 immutable document hashes in one cookie session: 2,486 requests, zero transport retries, 2,262 replay keys, at most 36 active-company offers. It verified price arithmetic, wrong-unit rejection, changed supplier selection, unsent RFQ replay/download and final export. A prior urllib run stopped on a transport error; its failed receipt was preserved and the acceptance client now pools HTTPS connections with one exact-request transient retry.

Ten-company primary scenarios passed actual Gemini extraction (30 documents) and three real specialist calls per company on revision 12. All 288 backend/config/fixture files are byte-identical in revision 13. The final revision additionally passed actual Gemini quote-to-plan/recovery/RFQ checks for litre, roll and cylinder materials.

Official EIA/ECB/FX sources returned fresh observations on revision 13: 31 headlines, with distinct company projections. Atlas had eight relevant observations. Other companies can inspect unmatched observations without a claimed implication. GDELT remained unavailable; its reason is preserved instead of fabricating coverage.

Chrome voice QA on revision 13 proved expired-session preflight creates no microphone stream or WebSocket, retains a visible restart notice, then resumes a real Google conversation. Owned synthetic speech exercised input captions, a spoken acknowledgement, interruption (two playback nodes stopped), a later verified inventory answer with three real specialists, and cleanup of every track and audio context. Native voice also prepared an Atlas 213,000-barrel RFQ proposal, delivered the verified spoken answer, and applied it only after review; replay returned the same unsent draft.

Desktop (1440) and mobile (390) layouts had no horizontal overflow, and Start conversation was enabled in the final ready workspace. Synthetic QA overrides were removed and the viewport reset.

Receipts and screenshots are in the task's `output/evidence/`, especially `all-material-news-voice-release-20261014.json`, `all-material-hosted-workflows-20261014.json`, `all-material-live-news-final-20261014.json`, `all-material-browser-voice-final-20261014.json` and `all-material-native-voice-rfq-final-20261014.json`.

## Operating limits and rollback

Companies, quotes, rates and disruption scenarios are synthetic. The all-document matrix used rules; real model evidence is separately recorded. The physical microphone was not recorded or tested. Official feeds are cached on demand for up to 15 minutes; broad discovery is unavailable and no social feed is integrated. Sessions remain temporary for up to one hour or a service restart. Each voice conversation lasts at most three minutes; six starts per company/session, 24 starts/hour/process, two concurrent connections and bounded model quotas apply. Edited buyer/material inputs pause the operator until a fresh comparison. No RFQ is sent and no order is placed.

Existing service identity and IAM were preserved. Rollback remains revision `closed-source-quote-demo-00011-gnj`, image `sha256:3c20c667838543c781a77bbaef313ac0b6708d2622583befba3cca8265f45b7c`. The private feature branch and draft PR106 retain the implementation; this release does not claim a main-branch merge or hackathon submission.
