# Closed Source product design

Revision 2, October 6, 2026. Closed Source is an AI manufacturing and global trade control room: connect sourcing, market risk, production, shipments, exports and sustainability around the same orders and evidence. Taher's working procurement engine is its starting point. Approved for team publication and implementation on October 6, 2026.

## Approved architecture and delivery decisions

- No fixed country, currency, supplier or material count in the product architecture. Discover provider catalogs and expose supported coverage dynamically. A catalog entry does not guarantee current data or a forecast.
- Preserve Taher's Python FastAPI backend, ingestion adapters, model interfaces, schedule comparisons, provenance, headline comparison flow, RFQ drafts, tests and audit history. Repair numerical correctness before adding predictive sophistication.
- Expand the central workflow to documents → comparable supplier offers → market exposure → inventory-feasible sourcing plan → disruption replan → human approval and exported RFQ.
- React and TypeScript become the product frontend. Use React Router with Vite in SPA mode, a Go API/workflow and voice-streaming gateway, and the preserved Python numerical/Google AI service. Rust is eligible for a measured bottleneck; it is not a required fourth language.
- Divyanshu owns platform/contracts, market forecasting, sourcing/production optimization, cloud integration and the shared workspace. Taher owns quote intelligence, conversational voice, shipment/dispatch event workflows and their interfaces and evaluations. Both own features from implementation through demonstration and review each other's integrations.
- Bring the working core forward to October 9, expand connected operational modules October 10–13, reach a release candidate October 14 and freeze October 16. These are delivery targets with daily evidence gates, not claims that all modules already exist.
- Use GPT 6.1 Sol High as the Antigravity Codex coordinator, smaller capable workers for bounded tasks, and Jev for useful advisory task routing on non-sensitive normalized inputs. Use Google models for the product's AI core; Jev is development support, not a dependency of the deployed procurement product.

## Review of the previous recommendation

Three countries and two currencies were demonstration bounds, but they would become a damaging product restriction if embedded in schemas. Replace them with a coverage registry and adaptive eligibility. Keep a reproducible demonstration scenario for evaluation while allowing unrestricted catalog search and any supported route/currency.

The full ambition remains: global procurement and FX/commodity intelligence; export opportunities; production and maintenance; dispatch and port disruption; carbon and compliance; and a conversational interface. The hackathon release connects these through one material/order graph, with sourcing and disruption recovery as the deepest workflow. Expansion modules get explicit acceptance gates and visible evidence status. A missing integration stays on the roadmap rather than appearing as a working capability.

Forecast accuracy alone is not the product's success criterion. Compare purchasing policies under identical demand, supplier, budget and information assumptions. Report total landed cost, stockout periods, unmet demand, working capital and tail cost; report forecast error and calibration separately. Synthetic scenarios demonstrate technical behavior, not customer savings or product demand.

## Starting point and required repairs

Baseline commit: `223f99b7efa3164843eed1c46530ed83c5412ba3`. GitHub tag: `baseline-2026-10-06`. This is the preserved starting revision; feature changes must retain its history and regression coverage.

| Existing capability | Preserve | Change before expansion |
|---|---|---|
| World Bank, Frankfurter, news ingestion | Adapter patterns, caching and source badges | Registry-driven series, provider attribution, release time and last complete observation |
| Naive, ETS, quantile GBM | Baselines and shared model interface | Per-origin model selection/calibration; calibrated forecast eligibility |
| Monte Carlo purchase scheduling | Path scoring and simple comparisons | Full inventory balance, actual arrival dates, supplier capacities, cash constraints and correlated scenarios |
| Landed cost | Explicit numerical calculation | Quote/settlement/reporting currencies; grade/unit; Incoterm; recoverable tax cash timing; destination/origin assumptions |
| Headline impact | Before/after comparison and deterministic fallback | Validate affected material/route; separate observed event from assumed price shock; report unchanged plans honestly |
| RFQ and audit | Draft review and stored input snapshots | Remove fixed Pune wording; atomic terminal action states and idempotency; record data/model versions |
| Streamlit interface | Six functioning reference flows | Retain as a regression/reference UI; React becomes the submitted product interface |

## Connected product scope

| Domain | Submission target | Evidence required to count it as implemented |
|---|---|---|
| Sourcing and market risk | Evidence-linked quote ingestion, multi-supplier landed cost, commodity/FX forecasts, cash and inventory constraints | Successful Google model call; held-out extraction evaluation; dated forecast backtest; hand-solvable allocation checks |
| Production and maintenance | Material/BOM demand propagation, capacity/maintenance windows and feasible production rescheduling | Synthetic plant scenario where a shortage or downtime moves production and shipment promises; correct capacity and material balances |
| Dispatch and ports | Shipment milestones, delay/capacity events, affected orders, alternative dispatch and recovery plans | Timestamped event trace that propagates to arrivals, inventory and production; replay/deduplication tests; synthetic port events labeled |
| Export opportunities | Country/HS market discovery from trade data, source dates, route/offer cost and attainable contribution-margin scenarios | Observed trade evidence distinguished from prospective buyer demand; margin scenario uses explicit cost/FX assumptions and available capacity |
| Carbon and compliance | Scenario emissions comparisons and evidence-linked document/requirement review for a selected route | Factor source/version, transport/unit boundaries and missing-data states; requirements linked to current authoritative sources; no certification claim |
| Voice operations | Spoken questions, scenario changes, evidence explanations and RFQ preparation with visible transcripts | Real microphone → recognized intent → validated tool call → updated plan → spoken answer; critical numbers confirmed; interruption/reconnect demonstrated |

Production/maintenance is a constrained planning demonstration, not a claim of a trained equipment-failure predictor. Export discovery is not a claim of confirmed buyers. Carbon estimates and compliance review are decision support with stated boundaries. The business roadmap retains sensor-driven maintenance, deeper market-demand models, ERP integrations, optimization at scale and additional verified trade coverage after the hackathon.

The hero demonstration follows one disruption across the product: upload synthetic international offers and production demand; choose a feasible sourcing plan; report a shipment delay by voice; recompute inventory, production and dispatch; compare cash, cost and emissions assumptions; inspect source evidence; prepare an RFQ for review. Export-market discovery is an adjacent scenario using the same available capacity and landed-cost graph.

Priority defects: the stockout penalty only considers the first delivery; historical replay uses present-day selection/calibration; forecast plots are anchored to the last observation while schedule labels use the current month; an explicit material can make unrelated headlines relevant; sample news can contribute to confidence; configured Gemini key is not proof of a successful model call. Also make offline mode block Gemini calls even if a key exists, and enforce RFQ state transitions in one transaction. Detailed evidence is in the accompanying baseline code review.

## Global coverage contract

Each coverage record identifies material/grade/HS revision, origin, destination, route or transport mode, currency pair, provider and observation interval. It stores retrieval time, source publication time when known, last non-null period, unit basis, estimation flags, usage rights and eligibility reasons.

Four separate states must be visible: catalog available; observations available; forecast eligible; supplier quote available. Unsupported inputs remain discoverable with reasons and an option to enter an explicitly labeled assumption or quote. Do not silently generate synthetic data in an ordinary workspace; synthetic data belongs to an explicitly selected demo workspace.

Discover all current Frankfurter currencies/providers; derive cross rates only through consistent dated providers and disclose the path. Store origin country independently of supplier invoice/settlement currency and buyer reporting currency. Handle invertible pairs, stale rates, missing days, incompatible providers, currencies with different minor-unit conventions and negative/malformed inputs. Use decimal money arithmetic and explicit unit conversion.

Use exact World Bank commodity series descriptions rather than treating indexes as physical product prices. Comtrade can rank observed historical sourcing routes; value divided by valid quantity is a customs unit-value proxy. Keep HS revision, quantity reliability and CIF/FOB valuation so freight is not counted twice. Tariffs require a national line, destination, origin eligibility and effective date. Freight indexes are lane context; supplier quotes or user assumptions supply transaction costs.

Provider breadth is researched, not integrated: Frankfurter advertises 223 currencies from 104 sources; Comtrade nearly 200 reporter areas; WTO tariff schedules cover over 150 economies. These are qualified source/catalog counts, not promises of fresh forecasts or executable prices. [Source research](Global_Coverage_Research.md)

## Components and data flow

Keep `procurement-engine` as the Python numerical/Google AI service. Add `apps/web/` for React and `services/gateway/` for Go at the repository root. Go owns authenticated public API routes, workflow state, action authorization/idempotency, job coordination and voice-session transport. Python owns extraction/ADK tools, forecasts, optimization and authoritative numerical calculations. Preserve existing `/recommend` and current reference UI behavior; new functionality uses versioned `/v2` contracts. Do not duplicate numerical or currency logic across Go and Python.

Use a single versioned OpenAPI/JSON schema source under `contracts/`, with generated TypeScript/Go clients and Python validation. Represent monetary values as decimal strings on the wire. The Go gateway calls a private Python service; browser clients receive no service credentials. Existing endpoints remain available to baseline regression checks.

Proposed shared contracts:

- `Money(amount: Decimal, currency: str)`; `Quantity(amount: Decimal, unit: str)`.
- `EvidenceRef(document_id, page_or_sheet, locator, source_text_hash)`.
- `SupplierOffer(id, supplier_id, material_id, grade, origin, destination, quantity_unit, price, settlement_currency, incoterm, min_order, capacity, lead_time_days, valid_until, payment_terms, evidence_refs, synthetic)`.
- `PlanningRequest(as_of, reporting_currency, destination, demand_schedule, inventory, supplier_offer_ids, budget_schedule, risk_policy, assumptions)`.
- `ForecastResult(series_id, origin, observation_end, horizon, model_version, quantiles, evaluation, provenance, eligibility)`.
- `PlanResult(status, purchase_lines, arrival_lines, inventory_by_period, cash_by_period, cost_breakdown, risk_metrics, alternatives, warnings, evidence_refs, versions)`.
- `OperationalEvent(id, occurred_at, observed_at, event_type, affected_entities, source, evidence_refs, synthetic)` and `ProductionOrder(id, material_requirements, quantity, deadline, capacity_requirements)`.
- `VoiceTurn(session_id, turn_id, transcript, proposed_intent, validated_arguments, tool_result_refs, confirmation_state, interrupted)`; commands bind to a specific workspace and plan version.

Exact API JSON fixtures are the first implementation deliverable after approval. Contracts must include errors and unavailable-data states, not only happy paths. Do not migrate existing results destructively; maintain read compatibility or a versioned migration with a recoverable snapshot.

Gemini extracts structured fields from text, scans and spreadsheet cells with evidence references. Missing or conflicting critical fields require human correction. Document text cannot authorize tools or change instructions. ADK coordinates narrow extraction, research, impact assessment and explanation tools; numerical services own calculations and recommendations. Arbitrary agent SQL or generated code must not execute in the production planning path.

The inventory engine updates stock at each arrival/consumption period and rejects or explicitly reports unmet demand. It enforces minimum orders, available supplier capacity, validity windows, cash/payment dates and production deadlines. Quantity cannot be moved to an earlier date merely to make an infeasible plan look valid. Infeasible requests return a reason and possible relaxations requiring user choice. CVaR or another stated tail metric and expected cost are compared consistently. No performance or optimality claim without a reproducible reference problem and solver evidence.

## Forecasting and evidence

Start with existing naive/ETS/GBM and BigQuery TimesFM 2.5. Evaluate TimesFM 3.0 multivariate Preview separately behind a fallback; its failure cannot disable planning. Add ARIMA_PLUS where useful. AutoML/fine-tuning are optional experiments only after an adequate dataset and measurable baseline gain exist. BigQuery managed TimesFM avoids an unnecessary always-on GPU endpoint and TimesFM 3.0 self-host licensing restrictions.

Evaluate each model using data available at each origin, including source publication lag. Calibration and selection must use training/validation periods before the test origin. Foundation-model pretraining overlap can make retrospective tests less independent; retain a forward shadow evaluation and disclose the limit. Future covariates require genuinely known future inputs or explicitly assumed scenario paths. Realized future FX, prices or news are forbidden.

Use joint commodity/FX residual scenarios or a validated dependency model; marginal quantiles alone do not define a joint risk model. A news event can support an affected-entity claim; its numerical price effect remains an assumption unless estimated and validated on timestamped history. Test unchanged-plan outcomes as legitimate.

Show extraction accuracy on a held-out synthetic document set, numerical constraint correctness on hand-solvable cases, forecast metrics on observed historical data, scenario outcomes on synthetic inputs and cloud response latency separately. Keep baseline comparison tables even when the simple model wins.

## Deployment and interface

Proposed GCP split: Cloud Run Go gateway and private Python service; Firebase Hosting for the React SPA; Cloud Storage for documents and exports; BigQuery for dated market observations and managed forecasting; PostgreSQL for mutable offers, plans and audit. Evaluate Cloud SQL cost/region/quota before provisioning. DuckDB remains the local baseline/research store; it is not the shared writable database for horizontally scaled Cloud Run. Use service identity and secrets management, never committed keys. Validate the chosen speech APIs and model quota against the actual billing project before relying on them.

React is a committed frontend choice. React Router/Vite SPA mode fits an interactive operations workspace with a separate Go backend, route-level loading/error states and browser audio. Next.js remains a valid future option if server rendering or a separate public content product becomes necessary; Angular offers no established advantage for this team. React's official guidance recommends frameworks, and React Router provides a documented SPA mode. Browser audio initializes after client mounting, including in a build-time rendered shell. [React guidance](https://react.dev/learn/creating-a-react-app), [React Router SPA mode](https://reactrouter.com/how-to/spa).

Go has a concrete role in request concurrency, workflow/action boundaries and bidirectional voice transport. Preserve Python's existing tested forecasting and scientific stack. A Rust simulation kernel may be added only after profiling identifies a bottleneck and a benchmark proves matching outputs and worthwhile improvement within the expansion window. The published rubric scores technical/GenAI merit, impact, innovation and UX; it does not specify a Go/Rust bonus. [Official judging criteria](https://aibuildercup.com/themes.html).

The main workspace shows buyer demand and stock, searchable sourcing offers, market exposure, recommended allocation, alternative plans and evidence. A disruption drawer changes delivery or cost assumptions and displays cost, cash and inventory differences. Every number shows its basis and freshness; exports preserve this evidence.

## Conversational voice

Build speech-to-text (STT) and text-to-speech (TTS), natural turn-taking and a visible transcript as a product workflow, not just a microphone beside chat. Voice and typed input share the same validated commands and tool result cards. The primary path is Chirp 3 streaming STT → Gemini reasoning/tools on Google Cloud → Chirp 3 HD TTS. Confirm account/region/quota at the October 7 spike; do not build two complete voice stacks. [Voice research](Voice_Architecture.md).

Google-native speech integrates with Gemini and GCP deployment. Streaming Cloud TTS is Preview; ordinary Cloud TTS audio responses are the fallback if streaming is unavailable. Gemini Live native audio with transcriptions and tool calls is a later alternative behind the same session/command interface, using the Google Cloud deployment path when relying on GCP credits. Fish Audio can sit behind the speech-output adapter if a listening comparison justifies it; its documented STT endpoint processes uploaded recordings synchronously rather than live streams, and external Fish charges are not covered by GCP credits. Expressive speech is welcome, but it must read verified tool outputs rather than invent prices or forecasts. These are documented capabilities and design choices, not measured voice-quality or latency results. [Cloud TTS streaming](https://docs.cloud.google.com/text-to-speech/docs/create-audio-text-streaming), [Google Cloud Live API](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/live-api), [Fish ASR](https://docs.fish.audio/api-reference/endpoint/openapi-v1/speech-to-text).

Start with push-to-talk and spoken results, then add streamed turn-taking, barge-in and reconnect. Use utterance/turn IDs to discard interrupted audio and stale tool results. Voice requests may inspect data, propose scenarios and prepare draft RFQs. A displayed confirmation card binds quantity, unit, currency, destination and plan version before a consequential state change; speech alone cannot send a supplier message or place an order. Ambiguous numbers/currencies require clarification before the scenario runs.

Measure recognition/intent accuracy on synthetic spoken commands, time to first transcript, tool response and first audible answer, interruption cancellation and reconnect behavior. Include accented English, Hindi/English mixed input as an evaluated target, background noise, corrections and unavailable-data responses. Language support must be verified for the selected model rather than assumed. Cloud Run supports WebSockets, but timeout/reconnect, cross-instance state and active-connection billing need explicit handling. Disconnect idle sessions and report actual latency/cost results after measurement. [Cloud Run WebSockets](https://docs.cloud.google.com/run/docs/triggering/websockets).

## Submission and business gates

Target a hosted React/global-input vertical slice by October 8; a working core including voice and disruption recovery by October 9; connected expansion October 10–13; release candidate October 14; freeze October 16; verify and submit early October 17. Portal cutoff observed earlier: October 18, 11:59 PM IST. Recheck before final submission. Team formation cutoff observed: October 11, 11:59 PM IST. Keep the demo available throughout judging.

Required submission preparation: deployed URL, public repository, PDF deck within 5 MB, video strictly under three minutes (target 2:45), and brief within portal limit. Review secrets, source provenance and dependency/data rights before changing repository visibility. Current GitHub repo is private and the baseline tag is published there; the team plan is approved for publication here; public repository visibility remains a separate release action.

The business hypothesis is an accessible manufacturing/global trade decision layer, with sourcing and disruption recovery as the first paid workflow. Validate onboarding effort, recurring decision frequency, useful budget/stockout outcomes and willingness to pay after the prototype; do not claim customer traction from generated examples. Defensibility can develop through permissioned quote/delivery/outcome history and workflow integrations. The longer roadmap includes mobile access, smaller domain models and adaptive planning policies only after suitable data, offline evaluation and deployment benefit are established.

## Execution authority

Approval covers this architecture, the owner split and the accompanying daily plan; execution uses the requested Antigravity Codex coordinator and bounded delegation. This design and the daily plan were approved for team publication and implementation. Material scope changes require review; keep task ownership and implementation status current. Final hackathon submission, billing upgrades, paid data contracts and communications beyond the authorized GitHub invitation require their own concrete authorization.

## Source integrations

Use the [API integration guide](API_Integration_Guide.md) to extend the existing adapters. Prioritize hazard/weather-to-order effects, verified calendars, identity evidence, HS trade data and transparent carbon calculations. Keep provider access, country coverage, observations and forecast eligibility separate. No paid data service is assumed to be covered by GCP credits.
