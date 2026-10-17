# Consented public research and durable demo history

Approved scope: the user's October 15 instruction approves building, parallel implementation in Antigravity, text/voice testing and deployment to the existing hosted link. Use existing Google Cloud trial credits only; no billing upgrade, paid plan, supplier delivery or public database access.

## Outcomes

- A typed or spoken question can prepare an external-research request. A visible card displays the exact public query and Google Search via Vertex AI destination. No search runs until the user explicitly reviews and confirms that card. The voice model cannot approve on the user's behalf.
- Search returns a separate read-only, dated answer with validated HTTPS source links and Google's supplied Search Suggestions. Do not assume a Hormuz closure from the question. Distinguish reported facts, uncertainty and possible business implications. Missing evidence produces an unavailable state, never invented research.
- Research is isolated from workspace tools: no private documents, inventory, supplier details, credentials or full conversation are supplied to Search. Only the displayed query and fixed public-research instructions are sent. Queries are checked for known private workspace labels and credential/contact patterns before proposal creation; rejected queries require a public reformulation.
- Approval binds the session, company, context, workspace marker, exact immutable query and five-minute expiry. Cancellation, a newer turn, company change or workspace mutation invalidates it. The server claims a proposal durably before invoking Search. Replays cannot initiate another search, including after restart or uncertain provider completion.
- Store anonymous demo sessions and per-company conversations in private Firestore Standard using server credentials. The same browser's protected session cookie resumes for seven days across reload and service restart; cross-device recovery is out of scope. Other sessions cannot retrieve that history. Expired history is inaccessible and removed through bounded explicit cleanup rather than paid Firestore TTL.
- Preserve reviewed quotes/plans and tool state sufficiently to make restored history consistent with its workspace. Pending approval cards are invalidated on restoration; historical cards are read-only. Store finalized voice text/tool outcomes, never audio or partial captions.

## Architecture

Retain the existing FastAPI and React application, Gemini Live connection and hourly news pipeline. Add a server-side research adapter using Google Search grounding, separate from ordinary function-calling requests. Add a Firestore REST persistence adapter behind SessionStore; local tests retain injected in-memory storage. Use bounded JSON serialization, optimistic concurrency and explicit failure handling. Research requests and results are persisted at their effect boundaries. Ordinary requests flush changed session state; conflicting instances cannot silently overwrite it. Do not claim persistence when a write fails.

News remains in the existing private seven-day Cloud Storage snapshot bucket. Research responses belong only to the requesting user's conversation and are not republished to the company news index or reused as a scraping corpus. Keep existing fictional-data disclosure, truthful provenance, simulated-email wording and all procurement safeguards.

Search Suggestions HTML is displayed unchanged only in the initial response and is never saved in server session state, chat history, research records, replay data or evidence logs. Stored results retain the answer and citations. Google's current service terms distinguish grounded-result chat-history storage from suggestions storage; current Gemini 3 pricing permits omitting suggestions below one million daily grounding prompts, well above this demo's 30-request cap. References: https://cloud.google.com/terms/service-terms and https://cloud.google.com/gemini-enterprise-agent-platform/generative-ai/pricing.

## Limits and release

Bound public queries to 400 characters, one active research request per session, six search attempts per session and bounded shared daily calls, one provider request per approval, no automatic retries, bounded output/source count and a provider timeout. Keep explicit unavailable states at quota/provider/storage limits. Firestore is one default Standard database beside the Mumbai service if the free-database slot is eligible; no paid TTL, PITR, backup or analytics add-ons. Recheck unupgraded trial status before provisioning. Keep the existing single-instance service bound until multi-instance tests pass; use durable conflict checks regardless.

Acceptance includes no search before confirmation; replay/stale/cancel/company isolation; private query rejection; sourced real Hormuz research without presuming closure; text and voice proposal/result display; stored finalized voice conversation; reload and cold-process recovery; persistence failures; independent sessions; existing quote/plan/recovery/RFQ regressions; desktop/mobile review; actual hosted revision evidence and rollback.
