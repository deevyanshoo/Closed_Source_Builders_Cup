# Company and Live News Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Follow disjoint ownership and coordinator contracts below.

**Goal:** Deploy ten meaningful fictional company workspaces and relevant live public news on the existing judge demo before adding the operator/voice.

**Architecture:** Extend the existing isolated FastAPI/React hosted demo. Server-owned company catalogue and versioned workspace selection isolate state; shared bounded news adapters supply observed metadata and explicit conditional exposure. Preserve current synthetic quote coverage and numerical planner.

**Tech Stack:** Python, FastAPI, requests, React, TypeScript; existing Cloud Run and Vertex access.

**Spec:** docs/superpowers/specs/2026-10-14-company-news-operator.md

## Global constraints

- Synthetic company data; observed news; no automatic real supplier messages or forecast/savings claims.
- Decimal-string quantities with explicit units; current kg-only quote coverage disclosed.
- Server-owned HTTPS sources; time/byte/item bounds; 15-minute shared cache; no silent fallback.
- Company/context versions, CSRF, ownership and stale-operation rejection preserve existing gates.
- Existing feature checkout is clean and exclusively owned by this task; disjoint writes permit reuse.

## Interfaces

GET /api/demo/companies -> {companies: CompanyProfile[]}. CompanyProfile = {company_id,name,sector,size,synthetic:true,as_of,description,locations:[{country,city}],reporting_currency,materials:[{material_id,name,quantity_unit,stock,consumption_per_day,monthly_requirement,criticality,keywords:string[]}],suppliers:[{supplier_id,name,country,settlement_currency,materials:string[],lead_time_days}],orders:[{order_id,material_id,quantity,unit,need_by,status}],routes:[{route_id,origin,destination,via:string[],materials:string[],direction:'import'|'export',currency}],currencies:string[],news_keywords:string[],quote_coverage:{materials:string[],fixture_ids:string[],note:string},buyer_defaults:PlanRequest|null}.

GET session additionally returns company:CompanyProfile and context_version:integer. Default solara-cables preserves copper demo. POST /company {company_id,expected_context_version,idempotency_key} -> full session. Frontend passes expected_context_version on every mutation; backend permits omitted legacy context only in default context for old tests and guards all async completion against company changes.

GET /api/demo/news -> NewsView. POST /api/demo/news/refresh {expected_context_version,idempotency_key} -> NewsView, bounded shared cache remains authoritative. NewsView={company_id,context_version,checked_at,cache_state,sources:SourceState[],items:NewsItem[],fx:FxObservation[],limitations:string[]}. SourceState={source_id,name,state,last_checked_at,reason}. NewsItem={item_id,title,url,publisher,provider,published_at,first_seen_at,retrieved_at,source_tier,source_reliability,reliability_reason,category,matched_materials:string[],matched_routes:string[],matched_currencies:string[],relevance_reason,implication,implication_confidence,next_steps:string[],synthetic:false}. FxObservation={base,quote,rate,observation_date,provider,retrieved_at,informational:true}. Nullable fields remain explicit.

News worker owns demo/news.py and tests/test_demo_news.py. Exposes NewsService(*,clock=None,transport=None,offline=None), .get(company:dict,force:bool=False)->dict without company/context wrapper; pure parse/matching helpers and tests. Backend owns company.py/sessions.py/app.py and test_demo_companies.py; calls NewsService in threadpool and rechecks context before responding. Frontend owns apps/web/src demo files and tests only. Root owns deploy files/docs/integration and cloud/browser.

## Tasks

### Task 1: catalogue and scoped backend (Antigravity Terminal 1)
- [ ] Add failing tests for ten diverse profiles, valid decimal units/routes and buyer defaults/quote coverage.
- [ ] Implement company catalogue; select/switch/reset behavior and context guards with in-flight/cross-company regressions.
- [ ] Add typed profile/news endpoints, existing security/resource gates, export company/context provenance.
- [ ] Run profile tests plus full hosted-demo backend regression suite; report exact changed files and evidence.

### Task 2: live news service (independent worker)
- [ ] Add failing tests for RSS/GDELT parsing, source health, relevance, duplicate lineage, malicious links, byte/time limits and stale cache.
- [ ] Implement fixed reviewed source adapters, cache/single flight, company matching and conditional explanations; FX reference observation if available.
- [ ] Run focused tests; coordinator verifies permitted sources with real responses separately.

### Task 3: company/news frontend (Antigravity Terminal 2)
- [ ] Add failing interaction tests for selection, late responses, unsupported quote coverage and company-specific news.
- [ ] Implement API/types and selector/overview/news tabs with source links/times/health, material/category filters and accessible actions.
- [ ] Preserve existing quote/correction/recovery/RFQ flow and its dirty-input safeguards.
- [ ] Run focused/full web tests, typecheck/build and record mobile/laptop expectations.

### Task 4: integration and release (coordinator)
- [ ] Review disjoint diffs/contracts and independent release findings; fix important issues.
- [ ] Run combined hosted backend/web/package gates and real-source diagnostic.
- [ ] Build explicit source allowlist package and deploy approved existing Cloud Run service; preserve prior image/revision.
- [ ] Run live ten-profile/isolation/news/source-failure and existing procurement checks in actual Chrome; save receipt/screenshots.
- [ ] Commit/push reviewed feature change and update attached PR; begin operator/voice plan without ending the task.
