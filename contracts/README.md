# Foundation contract 0.1.0

`openapi.yaml` is the authoritative OpenAPI 3.1 source (JSON syntax is valid YAML). `generate.py` derives TypeScript interfaces and Go structs from its schemas. Run `python3 contracts/generate.py`; generated files are coordinator owned. Consumers must not edit generated code.

Implemented routes: `GET /health`, `GET /v2/materials`, `POST /v2/plans/recommendations`, and read-only `GET /v2/contracts/examples/{name}` for exact synthetic contract fixtures. Recommendation calculation is stateless in the gateway. The legacy Python POST may store its existing recommendation audit record. No supplier action, approval, permission, idempotency or plan-application endpoint is implemented. Calls under `/v2/actions` return HTTP 501 `not_implemented`. Plan versions identify content snapshots, not mutable state or stale-plan enforcement.

The schema has no country/currency catalog cap. The compatibility adapter currently supports buyer destination IN, quote USD, settlement/reporting INR, and kg only. Other valid contexts return `status=unavailable`, `availability.code=unsupported_context`; mismatched units/currencies return semantic validation errors. Unknown material keys are unavailable. Source failure/invalid upstream responses never produce a fabricated plan.

Money and quantities cross browser/Go as decimal strings and are echoed exactly. Go must decode legacy numeric tokens without float conversion. Python's preserved legacy numerical engine uses floats and rounds output; converting its JSON numeric spelling to a string cannot restore precision already lost upstream. The adapter must reject quantities or budgets with more than 15 significant digits or more than 6 fractional digits as `legacy_precision_unsupported` before calling Python. Do not interpret fixture precision as numerical precision in this legacy engine.

`examples/precision.json` exercises exact browser/Go round trips with deliberately unsupported legacy precision. Offer, voice and shipment examples are synthetic proposed consumer contracts only; their domains are not implemented. The proposed action envelope binds workspace, plan ID/version and idempotency key, but no enforcement is claimed. These examples unblock development; they are not provider or permission evidence.

Main now includes issue 1's inventory/backtest corrections from PR #9 and this foundation from PR #10. Inventory shortages are scored with a soft penalty rather than forbidden, so a recommendation still does not certify stock feasibility. Every legacy plan must retain that limitation and distinguish observed market sources from synthetic/sample provenance. Publication/vintage limits remain.

Legacy price observation periods are represented by the first day of the reported month. That ISO date is a month marker, not a publication day or proof of source availability on that day. Schedule amounts and shares are rounded upstream; the gateway validates their totals with the documented per-row rounding allowance, independently of inventory feasibility.

## Additive persistent boundary 0.2.0

Legacy preview requests/responses and proposed offer/voice/shipment fixtures retain 0.1.0. New state/session/action envelopes use 0.2.0. The gateway computes saved snapshots server-side from PlanningRequest; it does not accept client-supplied recommendations. A saved plan has a resource ID, monotonic revision and the immutable computational snapshot/version. Editing inputs produces a temporary preview; saving or revising requires the development editor identity.

Confirmation persists an approval bound to workspace, saved plan, revision, snapshot version, principal and exact reviewed arguments. Execution atomically consumes that approval and creates one local prepare_rfq receipt plus audit record. Concurrent identical requests replay the same receipt; a changed request under the same workspace idempotency key conflicts. A changed plan rejects an unused old approval. Previously committed receipts can be replayed after a plan changes, with no new effect. A consumed approval cannot create another receipt under a different key.

Only local RFQ draft preparation is implemented. There is no sending, ordering, offer lifecycle, voice or shipment implementation. Unknown action kinds remain unavailable. Production identity is not implemented: temporary server-configured bearer identities authorize one workspace/role each. Browser input, actor/role headers and JSON assertions cannot grant permission. The loopback development proxy supplies its configured identity server-side; do not expose this development server or treat it as OAuth. Process health, database/schema readiness and provider/model verification remain separate.

The five new examples are synthetic protocol examples, not proof of persisted state or effects. Real PostgreSQL tests and actual browser receipts supply implementation evidence.
