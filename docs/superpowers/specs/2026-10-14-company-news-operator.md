# Company, news and operator expansion

User approved implementation and deployment of the proposed company/news expansion, followed by the multi-agent operator and voice, on 14 October 2026. Existing hosted base is d8b329b. Release each stage independently; preserve verified quote/recovery/RFQ behavior.

## Stage 1: company workspaces and live news

Exactly ten initial fictional profiles: solara-cables, terrapack, loomworks, harvest-foods, meridian-pharma, forgeline-components, gridspring-batteries, circuitpeak-systems, atlas-petrochem, stonebridge-materials. These are catalogue seeds, not schema caps. Profiles differ in sector, size, locations, inventory, consumption, suppliers, orders, material count/volume, currencies and import/export routes. Every value is synthetic and dated. Quantities have decimal strings and explicit units. No claim of actual customers.

Server-managed selected company and context version scope all operations. Switching saves/restores isolated per-company temporary state or explicitly starts an empty company workspace. Old in-flight responses cannot populate the new company. Do not silently use copper quotes for a company/material without matching quote coverage. Current quote fixtures remain synthetic and hash allowlisted. Company data/news exposure exploration works for all ten; runnable procurement coverage is shown honestly.

News is observed public data, separate from synthetic company exposure. Use bounded server-owned HTTPS adapters for EIA RSS, ECB RSS/reference rates, and GDELT discovery. No arbitrary URLs, private addresses, redirects to unreviewed hosts, credential forwarding, paywall bypass or social scraping. Provider timeouts, byte/item bounds, 15-minute shared cache, single-flight refresh and offline switch apply. Return explicit fresh/cached/stale/empty/unavailable states; never substitute synthetic articles in a live response. Store article source/publisher/link/publication (nullable)/first-seen/retrieval and original attribution. Display metadata and short explanation; don't republish third-party articles.

Map evidence to company materials, currencies/routes using explicit recorded matching. Relevance and source confidence are distinct; an exposure isn't a confirmed delay or price forecast. Source tiers and support reasons are explained. Wire-source republications aren't independent corroboration. FX reference rates are dated informational observations, not executable offers.

UI adds company selector, company overview/inventory/orders and a news workspace within /demo. Retain planning/recovery/RFQ UI. Show source health, times, company relevance, conditional implications and next steps; filter relevant material/category and view original sources. Public article text is inert escaped data.

## Stage 2: multi-agent operator and voice

Build real coordinator/evidence/exposure/planning/RFQ/verifier roles with structured, validated outputs and actual Vertex Gemini calls. Distinct tool permissions; numerical calculations stay in existing backend. Record an activity trail. Public news and quote text cannot authorize actions. Reads and hypothetical comparisons can run immediately; workspace writes use a visible proposal, exact company/context/offer/plan versions, expiry and idempotency. Support inspect company/news/plan, explain exposure, propose recovery, add watch item and prepare current unsent RFQ. No real supplier sending or order placement.

Push-to-talk uses browser audio, Google STT, visible editable transcript, same typed operator tools, optional Google TTS spoken response. Bound audio duration/body/model cost/concurrency and validate actual transcript/output. Cancel/company switch invalidates old responses/playback/actions. If a provider isn't enabled, show unavailable rather than browser-vendor speech masquerading as Google Cloud. New API/IAM changes require a concrete scoped request if automatic approval review rejects them; existing Vertex access can be reused.

## Acceptance

Stage 1: ten distinct profiles, meaningful inventory/route data, company isolation and async-race regression checks, live provider response seen from hosted service with source/timestamp, different company relevance, stale/outage behavior, mobile/laptop visual checks, existing quote/recovery/RFQ smoke. Stage 2: fresh actual Gemini multi-role run, tool-backed comparison/draft, rejected stale/foreign/replayed writes, synthetic speech recognized by actual Google provider, readable transcript and spoken output artifact, browser microphone path verified separately, no invented live/voice completion. Record exact code, image, build/revision, checks and rollback for each release.
