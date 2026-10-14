# Proactive risk monitor implementation plan

Goal: surface relevant news, potential supplier delays and inventory exposure for all ten fictional companies without requiring a chatbot question, then offer explained recovery steps through existing reviewed workflows.

Authorization: user explicitly requested parallel Antigravity implementation and deployment on the existing hosted link. No real emails/orders, private files, IAM or billing changes. Extend existing flows; automatic monitoring runs while the workspace is open, with cached public news and zero automatic paid model calls.

Design/contract: `docs/proactive-risk-monitor-contract-20261014.md`; stable company-owned alerts, units/provenance/freshness, no asserted real delay from headlines, current-session safeguards.

- [x] Antigravity engine worker: pure company risk assessment and meaningful regression tests.
- [x] Antigravity frontend worker: all-company proactive monitor, active polling, impacts/evidence/recovery prompts and responsive checks.
- [x] Coordinator: bounded all-company scans, snapshot ownership/context, report API and current/all-company voice tools; proactive reads preserve proposals/forms.
- [x] Parallel acceptance/review: all ten reports/materials, stale/injected news, synthetic recovery/dedup, UI isolation and native voice.
- [x] Demo/backend and web gates, independent review, immutable source package.
- [x] Deploy same service with existing runtime identity; verify hosted risk/news/voice/actions and save release/rollback evidence. Source preservation in the existing draft PR is the final release step.

Final gates: 277 demo Python tests passed; 288 web tests passed with six existing skips, typecheck and production build passed. Independent backend/comparison review clear. Revision21 serves 100% traffic. All ten/52 risk checks, nine real typed agent turns, actual native voice and Chrome playback, inventory recovery handoff and 390px responsive checks passed. Exact release/rollback identities and evidence scopes are in `docs/hosted-proactive-risk-release-20261014.md`.
