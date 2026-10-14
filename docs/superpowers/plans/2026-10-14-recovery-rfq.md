# Hosted Recovery and RFQ Implementation Plan

> For agentic workers: use superpowers:subagent-driven-development or executing-plans. Explicit user chose parallel Antigravity workers; root owns integration.

Goal: deploy paired supplier-delay recovery and a current-plan-bound unsent RFQ on the existing hosted link.
Architecture: append bounded state/routes to the existing synthetic FastAPI host, reuse exact Decimal planner, extend React flow. No new services/packages or external actions.
Tech stack: existing React/TypeScript, FastAPI and Cloud Run; existing Gemini extraction unchanged.
Spec: docs/superpowers/specs/2026-10-14-recovery-rfq.md (frozen contract).

## Constraints / review focus
Three synthetic fixtures, owned ephemeral state, CSRF, current versions, no order or sending; no float monetary UI arithmetic; no stale replay reviving comparison/draft; original quote evidence unchanged; reset/form-edit races guarded. Existing deployments untouched until final verification.

## Task1 Antigravity backend
- [ ] Read spec and assigned issue scope; write failing behavioral tests for paired comparison, current/replay state, RFQ facts and failure gates.
- [ ] Observe expected red; implement append-only API/state and small pure comparison/RFQ helpers under allowed paths.
- [ ] Green focused demo suites, retain existing tests; record results and unresolved risks in .superpowers/sdd/recovery-rfq/backend-report.md. No commit/cloud/provider calls.

## Task2 Antigravity frontend (parallel, shared frozen spec)
- [ ] Read current API/page/tests; write failing reducer/API/UI behavior checks for compare, clear-on-edit/reset, stale guard and reviewed/draft controls.
- [ ] Observe red; implement delay and RFQ sections, session restoration and version checks under allowed web paths.
- [ ] Typecheck, full web suite/sessionrace/build; report .superpowers/sdd/recovery-rfq/frontend-report.md. No commit/cloud/provider calls.

## Task3 root/reviewer integration
- [ ] Compare API/UI exact contracts; independent read-only correctness/security/UX review and fix any important issues with regression tests.
- [ ] Extend blackbox smoke with baseline/delay4000delta/null/revert, RFQ review gate/download, stale/foreign/replay/edit/export checks; no repeated provider calls required for deterministic changes.
- [ ] Run integrated demo/package and web gates once final changes settle; inspect production assets; record actual source revision.

## Task4 root deployment
- [ ] Freeze minimal package and manifest, Cloud Build, deploy same service/identity bounded resources. Retain previous digestbfa12b9d for rollback.
- [ ] Verify exact live revision, run synthetic-only blackbox acceptance, actual Chrome extraction/review/recovery/RFQdownload/export, laptop/mobile screenshots.
- [ ] Commit/push reviewable changes and update attachedPR106 around final scope; keep draft pending broader legacy integration. Receipt distinguishes source, deployment, merge and submission.
