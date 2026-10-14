# Company procurement and conversational voice implementation plan

**Goal:** Run reviewed quote planning, delay recovery and unsent RFQs in all ten fictional companies; expose a persistent operator side panel and native bidirectional Google voice.

**Architecture:** Keep the existing Python numerical and session authority. Add explicit generic quantities and units with legacy kg request compatibility, immutable company/material fixture coverage, and a server-authenticated Gemini 3.8 Live WebSocket relay. Voice prepares the same verified proposals as typed chat; applying a change still uses visible reviewed confirmation.

**Authorization:** User explicitly requested implementation, parallel Antigravity agents and deployment to the existing public synthetic GCP demo. Preserve runtime identity and IAM; no real supplier messages, orders or private document ingestion.

## Contracts and acceptance

- New plan requests/results use quantity, current_stock, additional_cost_per_unit, quantity_unit; legacy kg aliases remain readable for existing copper tests and clients. Unit mismatch fails explicitly. No density or package conversions are invented.
- Each company has at least one complete three-supplier scenario, with catalogue coverage expanded where coherent fixtures can be validated. Exact synthetic document hashes, company/material coverage, evidence review and original prices remain authoritative.
- Demo quantities, prices, dated FX and deadlines are labelled synthetic. Default baseline is feasible; delaying selected supplier selects another feasible offer or reports infeasibility. RFQ derives material, amount, unit, company and offer-version from the reviewed plan.
- Persistent right-hand operator on desktop; collapsible accessible panel on mobile. Company switch terminates voice and clears prior context. Existing reviewed proposal controls remain available.
- `/api/demo/voice/live` WebSocket same-origin session authentication, initial CSRF/context handshake. Input PCM16 16kHz mono, output PCM16 24kHz mono. Events ready/audio/transcript/interrupted/turn_complete/tool_result/error. Browser sends start/audio/audio_end/stop. 180s maximum; at most two simultaneous live sessions; no credentials or retained raw audio in browser/server state.
- Original composed, concise, lightly witty voice personality. Gemini native voice, automatic responses, silence detection and barge-in; no manual record/transcribe/send/speak stages in the primary voice flow.
- Live tool `operate_workspace(message)` calls existing validated multi-role operator. No voice-only mutation or confirmation bypass.

## Disjoint implementation lanes

- [x] Antigravity backend: company coverage, immutable fixtures, generic units, planning/recovery/RFQ, corresponding backend tests. Own company.py/quote_service.py/planner.py/recovery.py/rfq.py, synthetic fixtures; app.py procurement edits only after coordinator agreement.
- [x] Antigravity frontend: generic quantity UI/contracts, persistent chat panel and native streaming voice browser component. Own apps/web/src/demo* and associated tests.
- [x] Chat worker: new live_voice.py relay and focused transport tests. Coordinator integrates thin app route and validated operator callback.
- [x] Coordinator: provider preflight, shared route/contracts integration, packaging and independent review.
- [x] Run all focused/backend/frontend gates; exercise all ten complete workflows, unit mismatch, stale context/version and idempotent approval.
- [ ] Verify actual runtime identity opens Live provider and receives native audio; browser synthetic microphone test proves audible response and interruption. Record measured latency, distinguish physical microphone verification.
- [ ] Commit verified increment with existing author identity, push branch/update draft PR, build allowlisted package, deploy existing service and verify hosted workflows and voice. Preserve previous live revision for rollback.

## Release evidence

Coordinator records exact commit, image/revision, checks, all-ten workflow receipt, real provider/browser outcomes and remaining limitations in output/evidence and local task checkpoint. Deployment is not complete until the actual hosted destination is checked.
