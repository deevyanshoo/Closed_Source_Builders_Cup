# Voice research and visible drafts repair plan

> **For agentic workers:** Use superpowers:subagent-driven-development. The user explicitly requested implementation, parallel Antigravity agents, text/voice tests and deployment in this turn.

**Goal:** Make spoken research consent work, show proposals and durable drafts, and deliver brief readable research with diagnosable provider failures.

**Architecture:** Retain the hosted FastAPI/React/Gemini Live/Firestore service and existing review/idempotency boundaries. Add a bridge-verified spoken research confirmation that invokes the existing durable research claim. Store bounded non-actionable draft artifacts independently of pending approvals; pending approval remains invalid after cold restore. Use safe React formatting and brief-first research instructions.

**Tech stack:** Existing Python, React/TypeScript, Gemini Live and Vertex Google Search; no new service or billing upgrade.

**Spec:** This turn's user request and existing Product_Design.md/Work_Plan.md; this plan supersedes the prior research design's visual-only confirmation restriction only for current explicitly spoken public research consent.

## Constraints and review focus

- Spoken confirmation is based on new actual user input transcription after the query prompt, never model-generated confirmation arguments or output speech. Bind current proposal, company, session, context, expiry and workspace marker. It cannot authorize RFQs, simulated sends or other business changes.
- A repeat event, replaced/expired proposal, cold restore or company change cannot repeat Search. Keep the existing durable claim, six attempts/session and 30 shared requests/day. No automatic paid retries.
- Safe bounded source validation may accept 24 cited sources; never accept missing grounding or unsafe links. Log failure categories/counts, never queries, private transcripts, credentials or provider response bodies.
- Brief answer first with expandable detail and sources; escape raw HTML. Preserve Google attribution requirements and do not persist Suggestions markup.
- Drafts are unsent immutable previews, company-scoped and bounded to 24 entries/company. Restoring an artifact does not restore approval or make an old procurement plan current. Existing RFQ and simulated outbox statuses stay truthful.
- Reuse the dedicated feature checkout with disjoint file ownership. Root owns app/session integration, shared browser, provider mutations and release. Preserve main and contributor work.

## Parallel tasks

- [ ] Voice bridge: reproduce the consent gap; add fake-provider regressions for actual input consent, rejection, replacement/replay and brief readback. Implement in live_voice.py only with root callback confirm_research.
- [ ] Research adapter: reproduce deterministic false-negative validation cases; add sanitized diagnostics; accept bounded valid extra sources and optional attribution behavior per current primary Google guidance; request a brief structured answer. Keep one call and bounded deadline.
- [ ] Frontend: regress invisible/long-history voice proposals and confirmed research results; show active proposal prominently; render safe formatted brief answers with details; add a Drafts workspace with pending previews and persisted artifacts.
- [ ] Root integration: persist drafts with backward-compatible optional session field; register email/RFQ previews on prepare; return workspace.drafts without replacing voice scope. Add guarded research confirmation callback using existing durable claim. Test persistence, company isolation, deduplication and old-session compatibility.
- [ ] Review in parallel Antigravity Codex terminals, integrate actionable findings, then run full relevant backend/web gates and a production build.
- [ ] Package an allowlisted source image, deploy once to the existing service and verify hosted text, spoken confirmation/research/recall, RFQ/email visibility, reload and mobile behavior. Preserve private Firestore, hourly news/cleanup and existing trial-credit limits.
- [ ] Commit/push source and release evidence, update draft PR106/tag, verify live revision and clean Git. Record limitations instead of treating offline/mock checks as hosted proof.
