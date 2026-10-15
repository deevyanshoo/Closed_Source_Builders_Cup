# ZOORQ workspace redesign implementation plan

> **For agentic workers:** Use parallel bounded implementation followed by independent review and coordinator integration.

**Goal:** Ship the approved ZOORQ company-first workspace, focused news inbox, procurement steps and collapsible conversational operator on the existing hosted demo.
**Architecture:** Keep the React/Vite application and existing authoritative API/state guards. A persistent workspace controller owns navigation and operations; company hub, inbox and operator are bounded views. URL search parameters encode destination; business state remains server authoritative.
**Tech Stack:** React 19, TypeScript, React Router 7, existing CSS and Thinking Orbs.
**Spec:** User-approved redesign in this conversation, captured below.

## Approved design

- Exact brand: ZOORQ - A product by ClosedSource. Meaning: An ecosystem of resilience intelligence.
- /demo opens company hub with ten company profiles, searchable by company/sector, direct Open workspace and Continue current company.
- Stable workspace navigation: Overview, News, Procurement, Inventory & supply, Drafts & activity. All companies returns to hub without resetting saved work.
- Company news is default. Global Watch is an adjacent view, never a long section before company stories. List/detail inbox has immediately visible filters and publication/source labels; details progressively disclose evidence. Preserve ranking/provenance clocks and reviewed actions.
- Overview contains current company priorities, short next actions and workflow links. Full operational tables belong to Inventory & supply. Portfolio risk is optional, not repeated before quote intake.
- Procurement is Add quotes / Review / Compare / Draft RFQ with one material selector. Recovery available from comparison; drafts remain discoverable. Do not change economics or authoritative validation.
- Operator is collapsible at every viewport. Microphone beside composer opens voice orb; text/voice preserve scope, current consent, tool semantics and draft links. Pending review remains visible. Keep answers/source content intact.
- Light neutral workspace, graphite navigation, restrained coral accent, readable sentence case, keyboard focus, reduced motion and responsive layouts. Existing synthetic provenance remains available via concise Demo data disclosure; never relabel simulations as external sends.

## Global constraints

- Preserve company/session/context-version/epoch guards, dirty-state blocking, review gates, stale-result rejection, buyer-draft synchronization and all server contracts.
- No backend/provider, billing, IAM, schedule, private-data, real email or supplier-order change. Existing unupgraded trial credits only; verify current status before deployment.
- One coordinator owns shared demo-page.tsx, global CSS, Git, browser and release. Workers have disjoint files, no commits/push/deployment/browser use.
- Work on feature/zoorq-workspace-redesign from 066921c; preserve history and existing contributor authorship.
- Existing Thinking Orbs remains. No decorative dependency is necessary; add libraries only if justified and tested.

## Review focus

1. Browser Back/refresh restore destination without mutating a different company.
2. Switching company or returning to hub stops voice and invalidates old approvals; ordinary destination changes preserve in-progress text/workflow state.
3. Hidden procurement steps remain mounted only when needed for state; controls are not exposed outside their destination.
4. Mobile keyboard, operator focus, Escape, microphone End and consent review remain usable.
5. Retained stale news keeps original clocks; missing news remains explicit; no invented impact or calibrated-confidence claim.

### Task 1: Company hub and navigation (coordinator)
Files: demo-page.tsx, new zoorq-workspace.tsx/.css/.test.tsx, demo.css, index.html.
- [ ] Add tests for hub search/ten companies, navigation parse/serialization and overview links.
- [ ] Implement hub and five destinations with persistent controller and URL navigation.
- [ ] Gate full forms/tables to their destination; preserve old controller/session contracts.
- [ ] Run focused and existing web tests; inspect desktop/mobile locally.

### Task 2: News inbox (Antigravity worker news)
Files: demo-company-news.tsx, demo-company-news.css, demo-company-news.test.tsx only.
- [ ] Add failing company-first / Global Watch toggle and condensed actionable detail tests.
- [ ] Implement company-first inbox, explicit global mode, compact source status, secondary actions/evidence disclosure and responsive reading behavior.
- [ ] Preserve exports/contracts/ranking/source clocks; run focused tests and typecheck.

### Task 3: Procurement stages (coordinator, after Task 1)
Files: demo-page.tsx, zoorq-workspace.* and tests.
- [ ] Test stage gating, draft discovery and buyer/quote guards.
- [ ] Implement guided sections and recovery from comparison with one material context.
- [ ] Verify extraction/review/plan/recovery/RFQ on hosted acceptance later.

### Task 4: Operator polish (Antigravity worker operator)
Files: demo-operator.tsx, demo-assistant.css, demo-operator.test.tsx only.
- [ ] Add failing assertions for branded compact header, concise guidance, persistent current approval and voice controls.
- [ ] Polish panel/composer/context actions and activity disclosure without changing voice/consent lifecycle or backend calls.
- [ ] Keep header onClose available; coordinator owns outer drawer behavior.
- [ ] Run focused tests and typecheck; report exact failures from concurrent edits separately.

### Task 5: Integration, independent review and release (coordinator + read-only reviewer)
- [ ] Review diffs for guard/state regressions; run all web tests/typecheck/build and backend demo gates.
- [ ] Local desktop/mobile walkthrough including text and controlled voice; fix demonstrated issues.
- [ ] Verify current trial status, freeze allowlisted package and immutable image, deploy existing service only; preserve caps and rollback image.
- [ ] Hosted all-ten-company news/procurement/text checks plus native/browser voice and spoken approval; record exact evidence boundaries.
- [ ] Commit verified source with correct identity; push branch/update review artifact; clean status and final deployed revision receipt.
