# Company intelligence and agent actions implementation plan

**Goal:** Give every fictional company relevant observed news and a voice/text operator that answers grounded questions and performs visible utilities and simulated business actions.

**Architecture:** Preserve the deployed procurement engine, 52 material scenarios and Google native voice. Company-targeted discovery complements official feeds. Deterministic tools calculate analytics and validate editable inputs; Google chooses tools and explains retrieved facts. One coordinator owns sessions, tool execution and deployment; Antigravity workers own operator logic and web presentation, and a chat worker owns news ingestion.

**Spec:** Product_Design.md, Work_Plan.md and the user's October 14 news/agent upgrade request. Implementation and existing-link deployment remain authorized; actual messaging, billing and IAM changes are excluded.

## Contracts and tasks

- [x] News worker: company-specific bounded RSS discovery, company-keyed cache, direct/sector/route/currency mappings, independent source quality and transparent relevance scores. Preserve observed publication/retrieval times and explicit unavailable states; prove real relevant coverage for all ten profiles.
- [x] Antigravity operator worker: answer the actual question using complete trusted workspace data and deterministic inventory metrics. Keep multi-agent activity and provenance; add bounded tool routing and prevent invented tool identifiers/amounts.
- [x] Coordinator: add canonical `buyer_draft`, `selected_material_id` and per-company `simulated_outbox`; validate every write, preserve current versions and idempotency. Add `/api/demo/buyer-draft` for synchronizing form inputs before operator turns.
- [x] Coordinator: execute `fill_form`, `load_quotes`, `compare` and `navigate` as explicit reversible in-app utilities. `load_quotes` prepares draft documents; reviewed quote acceptance remains a separate action. Add reviewed `simulate_email` and `review_quotes` proposals with no external delivery.
- [x] Coordinator: native voice exposes named read/analytics/form/quote/comparison/navigation/simulated-email tools, through the same deterministic execution boundaries. Send canonical session updates only to the browser; omit credentials and CSRF from Google tool responses.
- [x] Antigravity frontend worker: restore authoritative tool sessions into the live form, show changes and analytics, render the simulated outbox, allow questions with edited inputs and synchronize before tools, preserve stale-action protections and continuous voice within the current company.
- [ ] Review and verify: current company/material binding, mixed units/currencies, repeated actions, stale approval, irrelevant/injected news, failed providers, expired sessions, voice tool reads and writes, mobile/desktop layout.
- [ ] Deploy the immutable reviewed package to the existing Cloud Run service and verify company news, utility/analytics actions and actual Google voice end to end.

## Wire additions

`DemoSession` adds `buyer_draft: PlanRequest|null`, `selected_material_id: string|null`, and `simulated_outbox: SimulatedMessage[]`. A message has company/material IDs, recipient label, subject/body, created time, `status: simulated_sent`, `simulated: true`, and `external_delivery: false`.

`OperatorTurn` retains existing keys; controlled utilities additionally return `read_only: false`, canonical `session`, and `action_result: {tool, summary, fields_changed?, view?}`. Optional `analytics` contains deterministic metrics with explicit units and synthetic provenance. Read-only answers and reviewable proposals retain `read_only: true`.

News adds `relevance_kind`, `relevance_score`, `matched_operations`, ; article summaries and bodies are not fetched. The score is a disclosed term-matching heuristic, not a calibrated probability or recommendation confidence.

## Acceptance cases

Ask which material has least stock cover; ask a specific material's monthly requirement; explain a supplier comparison and a matching news implication. Fill a non-primary material form, load its quotes, review their terms, run comparison, prepare an email simulation and inspect one outbox entry. Repeat confirmation without duplicate delivery. Run the same read/form/analysis sequence through spoken input and observe the actual form update and spoken result. No external email transport is present.
