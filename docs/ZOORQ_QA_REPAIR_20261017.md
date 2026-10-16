# ZOORQ QA repair — 17 October 2026

Release status: verification in progress. This document does not yet assert deployment.

## User-visible changes

- Carbon questions invoke the existing deterministic calculator using the current company and purchase. The conversation reuses saved freight inputs, asks for missing values, and saves the resulting assessment. It does not infer freight kilometres or shipping mass from a display map.
- Preparing an unsent RFQ or simulated email creates a saved draft. Reviewed business changes retain explicit confirmation, current workspace binding and replay protection. External messages and purchase orders are not dispatched.
- Supply has separate Inventory, Routes and Shipments destinations. Routes expose all declared material itineraries, destination selection, compact controls, readable leg status and a route diagram when map geometry is unavailable.
- Layout, dark theme contrast, navigation names, loading space and mobile controls have been corrected. Maps and non-current screens load separately.
- Research voice completion receives a brief, server-derived result with the lookup date. That date is not represented as an article's publication date.

## Data coverage and boundaries

The current profiles contain 198 declared route/material pairs. Ten have an approximate mapped corridor; 188 retain schematic itineraries. A declared route is not a tracked shipment or a validated supplier facility. Unknown transport modes and onward links stay unknown.

The carbon registry contains 52 material rows: two exact factors, four proxy factors, three unit mismatches and 43 unavailable factors. Freight can be estimated separately from explicit supported inputs. Missing material factors do not become fabricated combined totals.

## Verification record

Completed local checks:

- Full backend before the final conversational grammar extension: **3,471 passed, two provider-call skips, 263 subtests passed**. A fresh full run with the final grammar is in progress.
- Final grammar and affected carbon/session/Taher/tool tests: **188 passed**. The exact browser phrase and worded negative-distance regressions are included; negative and ambiguous inputs preserve the saved assessment and pending state.
- Full frontend: **563 passed, six skipped across 45 files**. Session-race checks, TypeScript and production build passed.
- Browser route navigation: all ten companies, desktop and 390-pixel mobile viewport, all 198 choices accounted for, no page horizontal overflow. Dark route contrast and compact controls were visually inspected. A local model outage leaves text tools usable and native voice unavailable.
- Separate source reviewers checked backend boundaries, frontend result contracts, proposal restoration/cancellation, route coverage and QA utilities. Reported blocking findings were corrected.

Hosted checks, controlled speech and physical microphone use remain distinct evidence. The deployment owner will append exact-revision receipts; the local results above do not establish hosted acceptance.

Pre-change mobile Lighthouse: performance 44, accessibility 92, best practices 100, SEO 91. Final scores are pending.

## Infrastructure and recovery

The release uses the existing `closed-source-builder-261007` project and `zoorq` Cloud Run service. The intended envelope remains zero minimum instances, one maximum instance, one CPU, 1 GiB memory, concurrency eight and a 240-second timeout. Existing Firestore sessions, GCS news snapshots, Vertex AI and restricted Maps configuration are retained. No billing upgrade or new paid service is included.

The previous image digest and service configuration are retained privately before deployment for rollback. Credentials, session cookies, browser Maps key values and private QA transcripts are excluded from this repository.
