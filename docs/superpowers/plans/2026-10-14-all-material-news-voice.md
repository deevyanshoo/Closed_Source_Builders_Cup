# Full material coverage and session recovery implementation plan

> For agentic workers: use subagent-driven-development or executing-plans. The user has explicitly authorized parallel Antigravity implementation and deployment to the existing public demo.

**Goal:** Run reviewed quote planning, supplier-delay comparisons and unsent RFQs for all 52 materials across ten companies; repair news and conversational voice failures.

**Architecture:** Preserve the existing Python demo, Google models, React interface and company-isolated temporary sessions. Add per-material scenario metadata and immutable fixtures. The coordinator owns shared API boundaries, integration and release; workers own disjoint data, web, news and voice files.

**Tech stack:** FastAPI, React/TypeScript, Vertex Gemini extraction/operator/Live, Cloud Run.

**Spec:** Product_Design.md and the user's October 14 all-material/news/voice request.

## Constraints and review focus

- Synthetic companies, quotes, prices and FX remain labelled; actual news and real Google calls retain separate provenance.
- No supplier sends, orders, private uploads, new IAM or billing changes. Preserve the 30 original fixture bytes and prior copper behavior.
- Preserve units exactly, dated FX explicitly and current quote validity. Material switches invalidate derived UI state and stale voice/proposals.
- Expired or lost server sessions must recover visibly before microphone capture or another mutation; foreign origins still fail before acceptance.
- No-match news must remain inspectable without implying relevance. Provider throttling must be labelled and never replaced with synthetic headlines.

## Task 1: Catalogue and immutable quotes (Antigravity data worker)

Files: company.py, quote_service.py, demo_fixtures and their tests.

- [x] Add `quote_coverage.scenarios: [{material_id, fixture_ids, buyer_defaults}]`, covering every catalogue material with three documents.
- [x] Preserve the primary defaults; add coherent same-unit amounts, budgets, supplier references and dated fictional prices.
- [x] Run the 52-case review/save/plan/delay/RFQ matrix.
- [x] Check review/save on October 17 while using explicitly dated October 14 synthetic FX.

## Task 2: Material selector and news session recovery (Antigravity web worker)

Files: demo-page.tsx, demo-api.ts, demo-company-news.tsx, demo.css and web tests.

- [x] Select a material before its quote, filter accepted quotes, load its scenario defaults and invalidate stale results.
- [x] Recover expired news sessions visibly and distinguish no-match, unavailable, stale and refreshing states.
- [x] Run web tests, typecheck and production build; inspect desktop and mobile.

## Task 3: News and voice repair (bounded chat workers)

Files: news.py, live_voice.py, demo-live-voice.tsx, demo-operator.tsx and focused tests.

- [x] Reproduce GDELT HTTP 429; expose a safe rate-limit reason without automatic retry or invented articles.
- [x] Preflight the canonical session before microphone access; guard stale asynchronous recovery and provide clear failure states.
- [x] Refund failed provider startup; use bounded renewable process allowance, keeping concurrency and conversation duration bounded.
- [x] Verify actual Google audio, browser playback and cleanup after deployment.

## Task 4: Integration and release (coordinator)

Files: app.py, regression tests, deployment acceptance scripts and release evidence.

- [x] Reproduce expired news and voice handshake failures; deliver a safe same-origin WebSocket expiry error without opening Google.
- [x] Bound local operations to 4,000 requests, 2,400 replay keys and 48 active-company offers; keep extraction paid budgets unchanged.
- [x] Run independent integration review, backend and web gates, immutable package and digest deployment.
- [x] Verify 52 material scenarios/156 quotes through the hosted API in one visit, fresh observed news, and native voice plus reviewed RFQ.
- [x] Record exact source/revision/image/checks, limits and rollback to revision 00011-gnj/image 3c20c667838543c781a77bbaef313ac0b6708d2622583befba3cca8265f45b7c.

Release verified on revision `closed-source-quote-demo-00013-6cp` at 100% traffic. See `docs/All_Material_News_Voice_Release_20261014.md` for evidence and limits.
