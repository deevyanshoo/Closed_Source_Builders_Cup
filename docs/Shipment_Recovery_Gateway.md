# Local authenticated shipment recovery

The local Go gateway compares complete host-configured synthetic shipment/propagation/production contexts through a fixed Python child and the unchanged private shipment adapter. This is read-only: it does not apply a plan, create receipts, infer BOM/capacity, verify external source authenticity or save workflow state. Every eligible source scenario is an independent comparison, not a simultaneous recovery plan.

## Host setup

Use an existing trusted interpreter and procurement-engine/src directory; no SDK/provider/dependency setup is needed. Supply all three absolute local flags together: `-shipment-local-contexts`, `-shipment-local-python`, `-shipment-local-source`. The configuration is unavailable in private_machine mode. Existing voice flags remain compatible. The operator-supplied synthetic catalog contains exactly `{catalog_version:"1", contexts:[{workspace_id,plan_version,propagation_snapshot_id,production_snapshot_id,as_of,request}]}`. Each request is the complete existing six-field shipment adapter request documented in Shipment_Production_Recovery.md. The host cutoff is explicit UTC. Snapshots retain their own supplied IDs and complete raw records, graphs, receipts, baseline/BOM/capacity and assignments; no legacy saved plan can fill missing provenance.

The catalog is read-only configuration. Up to four contexts live only in process memory. Invalid catalog disables this feature atomically; empty enabled catalog returns an empty list. Server-issued opaque IDs are new after restart; host-only replacement checks the current revision and cancels old executions. These are ephemeral context revisions, not persisted saved-plan freshness. No public create/replace/apply endpoint exists.

## Read-only API

Existing local loopback/Origin/Bearer membership applies to both viewer and editor. HTTP identity assertions and duplicate critical headers are forbidden. Path workspace must match the authenticated workspace before context availability/disclosure.

- GET `/v2/workspaces/{workspace_id}/shipment-recovery/contexts` returns metadata only; no query or body. Metadata labels synthetic, host_configured_synthetic, ephemeral and source_authenticity:not_verified. The request digest identifies complete host-owned bytes, not tenant membership or source rights.
- POST `/v2/workspaces/{workspace_id}/shipment-recovery/comparisons` accepts exactly contract_version:"0.2.0", context_id and expected_revision. Clients supply no raw source, snapshots, hashes, cutoff or model proposals.

A complete adapter response is transported as `outcome_json`, strict JSON text in the validated versioned envelope. Preserve it unchanged and decode strictly when needed. Decimal values are strings; legitimate factory record_index values are integer metadata. All factory evidence, projections and public numerical results are retained. The accepted receipt-only voice runner and public Python service are unchanged; no UI/model/speech proof follows from this endpoint.

## Availability, validation and execution

HTTP200 retains evaluated rows, including numerical unsupported, or no_eligible_scenario with complete evidence and no comparisons. No eligible evidence does not prove full graph or numerical support. Adapter input/join/projection rejections are422; binding/source/runtime failures503; output failures502. Those valid adapter rejections keep their complete empty-comparison outcome. Transport/authority failures use fixed StateErrorResponse without outcome/exception/stderr leakage:401 identity,403 membership/role,404 foreign/unknown context,409 stale,413 body limit,429 admission,502 invalid child,503 unavailable,504 deadline. See the authoritative OpenAPI for exact envelopes.

Duplicate/trailing/unknown keys, invalid Unicode, numbers in adapter inputs and excess bounds reject before ownership copying/dispatch. Null evidence values remain valid. Input2MiB, raw/propagation1MiB each, depth16, array256, aggregate16384, strings128KiB; existing factory/exact amount/time limits apply. Eligible scenarios16; complete outcome4MiB. Derived output lists are bounded by output bytes/aggregate rather than the input array cap. Actual escaped stdin is bounded4MiB+32KiB and stdout/HTTP8MiB+32KiB.

Maximum two children globally and one per principal; no waiting queue. A10second computation budget reserves1second for child reaping; input read deadline≤5seconds and response-write deadline≤2seconds. Fixed argv is `-m procure.production.shipment_local_bridge`, with operator-owned absolute interpreter/source, sanitized environment and no request-supplied commands/module. Overflow/cancellation kills and reaps before releasing admission. Shutdown closes admission and drains within existing gateway grace; failure to reap is an error. Context publication is guarded after complete validation/buffering; replacement/disconnect/shutdown cannot turn a late result into a current outcome.

## Focused local verification

Run new bridge tests with an existing interpreter, shared conftest/plugin/cache disabled; run new TestShipment Go checks with offline existing module cache, then race checks. The connected acceptance harness uses in-memory synthetic identities with actual stateServer.Authenticate, task-owned loopback HTTP, actual catalog loading and fixed Python module. Four entire results and evidence match independent public math: delayed, no-op, precision and numerical unsupported. Additional connected availability cases preserve excluded evidence and atomic join/projection failures. The harness runs guards inside the actual child and direct comparison subprocesses. No provider, model, credential, DB or reference server action is needed.

Mocks cover race/guard behavior only; actual connected subprocess checks establish local numerical transport. They do not prove external shipment truth, source authenticity, persisted operational state, cloud deployment, Google behavior or production tenant access. Root owns final integration.
