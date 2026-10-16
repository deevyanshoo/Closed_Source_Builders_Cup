# ZOORQ QA repair implementation plan

**Goal:** repair the reported backend, voice, carbon-task and route usability failures, then verify and deploy the existing product.

**Architecture:** keep the existing React/FastAPI/Gemini Live/Firestore/Cloud Run stack. Share the carbon calculation service between HTTP, typed operator and voice; keep display geometry separate from calculation inputs.

**Authorization:** user explicitly requested implementation, parallel Codex agents in Antigravity terminal, desktop/mobile/Lighthouse verification, deployment and Git updates using existing trial credits only. This is a repair of existing flows; no new billing tier, public delivery integration or hackathon submission.

## Acceptance and ownership

- [ ] Backend implementer: reproduce missing dependency, immutable-source identity, strict-mode test leakage, stale helper and fixture failures; repair their causes without removing fail-closed checks or skipping failing tests. Install only published pinned test dependencies in an isolated temporary environment. Independently review changed source before accepting a new trusted identity.
- [ ] Operator implementer: expose the existing carbon computation through text and Live, automatically use owned plan quantities, return and persist actual results, ask only missing freight/mass inputs in chat and continue the task. Bind pending work to company/context/plan/offer versions and clear stale work. Preserve reviewed business actions and internet consent.
- [ ] Operator implementer: repair spoken research completion guidance in every completion path, including consent started without a model confirmation call, and test actual dated answer delivery.
- [ ] Operator implementer: complete explicit unsent RFQ drafting without an unnecessary second approval. Share existing apply checks for explicit text and independently verified spoken approval of reviewed changes. Never let model-authored tool text masquerade as typed human approval. Keep external delivery disabled.
- [ ] UI implementer: make all declared routes discoverable and identifiable by material/origin/destination, select the matching buyer when supported, improve meaningful display coverage, and preserve truthful unavailable states. Give Routes a destination beside Inventory and Shipments so it does not bury inventory. Align controls and composer; use readable labels, 44 px controls, keyboard focus and reduced-motion behavior.
- [ ] Coordinator/reviewers: inspect code and regression evidence; serialize full backend/web/type/build gates. Browser QA all ten companies on desktop and 390 px mobile. Run actual Lighthouse mobile and desktop. Test text, native voice, consent, carbon follow-up and reviewed actions.
- [ ] Coordinator: package allowlisted sources and built assets with manifest/secret checks; verify trial and existing service caps, build one immutable image and deploy reviewed source. Verify actual hosted revision and workflows; preserve rollback, update release evidence and Git.

## Decisions and safeguards

Known kg/tonne quantities support a material-only estimate. Missing freight is a missing component, not a reason to delegate all work to the user. Distance and transport mode are never invented from illustrative maps. Provider errors never become successful research. Maps never imply a vendor facility, selected quote or tracked shipment without an actual binding. All counts and coverage are derived from the current company, including generated routes.

Actual recent Solara chat was read in the hosted operator: it instructed the user to open Procurement and enter emissions values. Hosted route selection exposed nine routes with indistinguishable duplicate labels and a buyer mismatch after changing the route. These observations are the regression targets.
