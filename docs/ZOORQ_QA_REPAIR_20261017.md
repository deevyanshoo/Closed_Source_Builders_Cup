# ZOORQ QA repair — 17 October 2026

Release status: local verification passed; hosted verification and deployment are pending.

## User-visible changes

- Carbon questions invoke the existing deterministic calculator using the current company and purchase. The conversation reuses saved freight inputs, asks for missing values, and saves the resulting assessment. It does not infer freight kilometres or shipping mass from a display map.
- The Emissions form reflects those saved inputs, preserves up to five freight legs and explicit shipping mass, and protects unsaved edits. Incomplete input pairs produce a clear error before a request rather than being silently discarded.
- Preparing an unsent RFQ or simulated email creates a saved draft. Reviewed business changes retain explicit confirmation, current workspace binding and replay protection. External messages and purchase orders are not dispatched.
- Supply has separate Inventory, Routes and Shipments destinations. Routes expose all declared material itineraries, destination selection, compact controls, readable leg status and a route diagram when map geometry is unavailable.
- Layout, dark theme contrast, navigation names, loading space and mobile controls have been corrected. Maps and non-current screens load separately.
- Research voice completion receives a brief, server-derived result with the lookup date. That date is not represented as an article's publication date.

## Data coverage and boundaries

The current profiles contain 198 declared route/material pairs. Ten have an approximate mapped corridor; 188 retain schematic itineraries. A declared route is not a tracked shipment or a validated supplier facility. Unknown transport modes and onward links stay unknown.

The carbon registry contains 52 material rows: two exact factors, four proxy factors, three unit mismatches and 43 unavailable factors. Freight can be estimated separately from explicit supported inputs. Missing material factors do not become fabricated combined totals.

## Verification record

Completed local checks:

- Final backend: **3,491 passed, two provider-call skips, 263 subtests passed** in 460.98 seconds.
- Final grammar and affected carbon/session/Taher/tool tests: **188 passed**. The exact browser phrase and worded negative-distance regressions are included; negative and ambiguous inputs preserve the saved assessment and pending state.
- Final frontend: **571 passed, six skipped across 45 files**. Session-race checks, TypeScript and production build passed.
- Browser route navigation: all ten companies, desktop and 390-pixel mobile viewport, all 198 choices accounted for, no page horizontal overflow. Dark route contrast and compact controls were visually inspected. A local model outage leaves text tools usable and native voice unavailable.
- Connected local browser carbon check: the bot calculated 31,720 kg CO2e for material, accepted “Use road transport for 125 kilometers,” saved 103.56 kg CO2e freight and displayed the supported 31,823.56 kg total. The form showed Road HGV / 125; an incomplete second leg was blocked without losing either row, and completing a 75 km rail leg saved both. Mobile layout had no page horizontal overflow.
- Separate source reviewers checked backend boundaries, frontend result contracts, proposal restoration/cancellation, route coverage and QA utilities. Reported blocking findings were corrected.

Hosted checks, controlled speech and physical microphone use remain distinct evidence. The deployment owner will append exact-revision receipts; the local results above do not establish hosted acceptance.

Pre-change mobile Lighthouse: performance 44, accessibility 92, best practices 100, SEO 91. Final scores are pending.

## Infrastructure and recovery

The release uses the existing `closed-source-builder-261007` project and `zoorq` Cloud Run service. The intended envelope remains zero minimum instances, one maximum instance, one CPU, 1 GiB memory, concurrency eight and a 240-second timeout. Existing Firestore sessions, GCS news snapshots, Vertex AI and restricted Maps configuration are retained. No billing upgrade or new paid service is included.

The previous image digest and service configuration are retained privately before deployment for rollback. Credentials, session cookies, browser Maps key values and private QA transcripts are excluded from this repository.

The release bundle retains seven previous content-hashed static assets from the verified `b64eaaf` package, in addition to the current build. Shared paths must have identical bytes; old HTML, theme initializer and runtime source are excluded. This avoids missing lazy modules for tabs opened before the release. The combined package receives a new manifest before upload.
