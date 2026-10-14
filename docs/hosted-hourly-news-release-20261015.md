# Hosted hourly news and natural assistant release

Demo: https://closed-source-quote-demo-ryoeyuqvta-el.a.run.app/demo

## Delivered behavior

The existing company portal reads durable hourly public-news snapshots for ten companies. The collector runs independently of open browser tabs. A failed provider retains its earlier observations with original source clocks; failed companies do not prevent successful companies from updating. News success, FX success and recorded attempts are displayed separately. Manual refresh remains available without overwriting immutable hourly snapshots. Source failures and conditional company relevance stay visible.

Text and Gemini Live answers use natural business wording and clean supplier labels. Canonical supplier/recipient IDs, quote evidence and underlying numerical values remain unchanged. Human-facing stock-cover ratios with long decimals are shown as explicitly approximate two-decimal values. Direct provenance questions remain truthful. RFQs stay unsent; email delivery remains explicitly simulated. No real supplier message or order is sent.

## Exact release identity

- Runtime source: `d010ec46b79c513d6a07f50dde29d1575421f52e`.
- App revision: `closed-source-quote-demo-00027-hxw`, 100% traffic.
- Cloud Build: `90b07798-770f-43e5-aa80-3b9e27c6416a`, SUCCESS.
- Immutable image: `asia-south1-docker.pkg.dev/closed-source-builder-261007/closed-source-demo/quote-to-plan@sha256:4e1b8c6f82d8296caaa4ed7639fbc48867c0638c9d6357114c9a0e6d9530917c`.
- Allowlisted source package: 306 runtime files, 2,955,114 bytes; manifest SHA256 `2ce6c31b5d86d5f866da9783329c691a6c4149e1454f62340d0504c4834bf8c2`.
- Worker: `closed-source-news-refresh`, asia-south1; same immutable image, one task, one CPU, 512 MiB, 120 seconds, zero retries, no automatic Gemini calls.
- Scheduler: `closed-source-news-hourly`, us-central1; `0 * * * *` UTC, hourly at :30 IST; one OAuth target, zero retries.
- Storage: private Standard us-central1 bucket `closed-source-builder-261007-news-snapshots`; seven-day lifecycle, public access prevention and uniform access. Added storage grants are bucket-scoped creator/viewer for the worker and viewer for the app; the observed job-level scheduler grant is roles/run.invoker.

## Observed correction and verification

US worker execution `closed-source-news-refresh-vp4ms` returned Google News HTTP 503/read timeouts while identical queries through the Mumbai portal succeeded. The collector was placed beside the portal; the existing scheduler and bucket remain in the US region. Manual Mumbai acceptance returned 80 observed headlines for every company, with 27–50 relevant observations per company. This proves observed coverage at that time, not guaranteed future feed availability.

A same-hour collector race first exposed an incorrect nonzero no-op status. The CLI now exits successfully when another valid snapshot already owns that hour. A subsequent real cross-region socket-write timeout exposed the two-second upload allowance; uploads now have `(8, 5)` socket timeouts, no retries, and a 30-second publication reserve inside the unchanged two-minute job ceiling. Requests timeouts bound socket phases/inactivity; the Cloud Run 120-second task ceiling is the hard outer bound. Storage transport failures still fail. Each extra build followed a new observed runtime defect; no unknown build was blindly repeated.

- Full backend demo suite: 349 passed, two existing deprecation warnings.
- Web: 317 passed, six existing skips; session-race gate, typecheck and production build passed. Later repairs change only Python news transport/collection and the deployment helper; web runtime is unchanged.
- Focused final news/infrastructure/app checks: 60 passed.
- Hosted revision 24: all ten companies, 52 material workflows and 156 immutable quote fixtures passed rules-mode planning, delay recovery, unsent RFQ/idempotency, context and export checks; 2,486 requests, no transport retry. Later revisions repair news transport and typed/display fact handling; the direct quote/planning/recovery/RFQ modules remain unchanged.
- Final hosted Gemini Live audio/tool turn on revision 27: 910,080 output-audio bytes and eight completed tools: one company-view navigation utility response and seven reads. Spoken inventory agrees with 2,000 kg copper/six days cover, 5,800 kg polymer/46.4 days and 4,300 kg aluminium/nearly thirty days; the tool records retain precise arithmetic. Test input was an owned recording; physical microphone hardware was not tested.
- Hosted typed turns verified clean supplier labels, truthful provenance and explicitly unsent/simulated actions. Independent review found a typed shortest-lead answer mislabeling 2,000 stock units as cover days; recognized shortest-lead and stock-cover lookups now render canonical supplier fields and inventory analytics, including tied suppliers and unavailable cover. All-ten regression tests passed. Final revision 27 hosted turns verified 2,000 kg stock, Europa Copper SA/ten-day shortest lead, explicitly approximate 6.00-day cover, combined lead/cover questions, truthful source disclosure and canonical unsent simulated-message recipient. Exact stock-cover arithmetic remains in analytics. Chrome text answer and collection/source-health display were inspected. Fresh Chrome 390px inspection passed: current hourly timestamps, mobile operator drawer, visible voice controls/orb, completed typed reply and composer; document/body width remained 390px. Escape closed the drawer and the temporary viewport override was reset. Thinking Orbs are unchanged from the separately documented release.
- Scheduler force execution `closed-source-news-refresh-9nr2t`: exit 0, successful immutable same-hour no-op, fresh news/FX collection clocks.
- Natural hourly execution `closed-source-news-refresh-4whwx`: created automatically at 22:00:04 UTC, successfully published the 22:00 snapshot and completed at 22:00:40 UTC (03:30:40 IST). All ten hosted company views read that scheduled slot, with 80 items each and 25–50 relevant matches. The collector runtime in this execution was source 473ab6f; the final display-only rounding polish leaves collection unchanged.

GDELT remained unavailable in the observed checks; Google News provided fresh company headlines; EIA/ECB showed actual fresh or cached metadata. No replacement headlines are fabricated. Individual source health remains explicit.

## Cost boundary and recovery

The user approved existing trial credits with no out-of-pocket charges. An authenticated console Free Trial dialog showed the account remains unupgraded, with credit and time remaining. No billing upgrade was performed. Regional worker use and metadata transfer may consume credits; zero metered usage is not claimed. Google states an unupgraded trial is not billed and auto-closes at credit/time expiry: https://cloud.google.com/signup-faqs. Scheduler and Run job regions may differ: https://docs.cloud.google.com/run/docs/execute/jobs-on-schedule.

Rollback app traffic to `closed-source-quote-demo-00022-wgm`, image `sha256:037d6f99d6b33cf3f3103c97a6736164182cb3bc973d0867d9ddd16e3a28fccc`, and pause the single scheduler if background collection must stop. Preserve news objects and configurations. The unused US collector was retired only after the Mumbai natural run succeeded; its configuration is saved in `unused-us-news-job-before-retirement-20261015.json`. Existing voice/provider identity and public app invocation were not expanded. The feature branch remains separate from main; PR 106 stays draft for broader integration review.

Evidence receipts are in the project `output/evidence/`: `hourly-final-provider-configuration-20261015.json`, `hourly-all-materials-revision24-20261015.json`, `hourly-hosted-product-20261015.json`, `hourly-hosted-voice-20261015.json`, `mumbai-manual-news-20261015.json`, and `hourly-natural-schedule-20261015.json`, `hourly-final-scheduled-news-20261015.json`, `hourly-final-typed-20261015.json`, `hourly-final-release-20261015.json`, `hourly-final-browser-20261015.json`, `hourly-final-backend-junit-20261015.xml`, and final desktop/mobile screenshots. Earlier failed executions remain separate from final passing evidence.
