# Local synthetic shipment-to-production comparison

`procure.production.shipment_recovery.compare_shipment_production_recovery(request, *, expected_binding)` is a private, pure local composition. It accepts independently supplied complete propagation and production snapshots, raw synthetic milestones, explicit joins and complete recovery assignments. It calls the existing milestone factory, propagation projector and public `compare_receipt_recovery` without replacing their numerical calculations or editing their results.

Each eligible shipment scenario is compared independently against the original production snapshot and the same supplied assignments. Rows do not form a simultaneous multi-shipment recovery plan. The adapter does not select a schedule, create receipts, infer BOM/capacity, aggregate arrivals, apply changes or promise operational feasibility. This interface adds no route, persistence, authorization or provider integration.

## Caller-owned inputs

The already-decoded plain JSON request has exactly these fields:

| Field | Value |
| --- | --- |
| `adapter_version` | `"1"` |
| `raw_records` | Raw milestone array accepted by the existing synthetic factory; no client-normalized evidence or scenario |
| `propagation_snapshot` | Complete declared graph, inventory, arrivals and commitments; optional string `notice` is hash-bound |
| `production_snapshot` | Complete existing `ProductionSnapshot`, including all unrelated rows |
| `joins` | Exact `{line_id, receipt_id}` rows covering every unique propagation arrival line |
| `recovery_assignments` | Complete existing `{order_id, period_id}` rows, exactly one per unique production order |

The separate `expected_binding` contains exactly `adapter_version`, `synthetic`, `workspace_id`, `plan_version`, `propagation_snapshot_id`, `production_snapshot_id`, `as_of`, `raw_sha256`, `propagation_sha256`, `production_sha256`, `joins_sha256` and `assignments_sha256`. Version is `"1"`; synthetic must be literal `true`. IDs are nonempty trimmed strings of at most256 characters; hashes are lowercase64-digit SHA256 values. Both snapshots must match the asserted workspace/version and common aware-UTC cutoff. Each snapshot retains its own asserted ID. Production `stock_as_of` must match the cutoff.

Hash the entire corresponding original value, including excluded raw records, duplicate rows, unrelated production rows, optional notice, exact decimal/timestamp spellings and array order. Canonical JSON uses UTF-8, sorted object keys, `separators=(",", ":")`, `ensure_ascii=False` and `allow_nan=False`. For already validated plain JSON values:

```python
import hashlib
import json

def input_hash(value):
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False, allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
```

The adapter validates bounds before copying/hashing. A caller asserting hashes is not proof of tenant authorization, source authenticity or external freshness. Strict transport decoding, including duplicate JSON-key rejection, remains the caller's responsibility; a decoded dictionary cannot retain duplicate keys.

## Explicit consistency boundary

Joins are one-to-one from every unique propagation arrival line into an existing production receipt. Production may contain additional receipts. No missing/foreign/duplicate/ambiguous join is accepted. Each pair must match component, unit, exact decimal quantity and original arrival instant. Its component must exist in both inventories with identical unit and exact starting stock at the common cutoff. Other inventory components and graph commitments are not claimed equivalent to the production BOM/orders.

Identical repeated logical arrival or receipt rows may coalesce for lookup, but all originals remain hash-bound. Conflicting copies reject. Duplicate assignment rows reject; every assigned order/period must exist. These checks finish before any projection or production comparison. Complete baseline/BOM/routing/capacity semantics remain the public numerical tool's responsibility.

For each factory scenario, the adapter validates its declared graph using the unchanged projector. It maps accepted changes through the supplied joins, taking the expected arrival from the original production receipt. Same-instant replacements remain explicit. Every projection must succeed before any production comparison starts. Composition IDs bind the factory scenario ID and the full asserted binding; factory source IDs and evidence are preserved separately. Array reordering needs refreshed guards and can change provenance IDs even when numerical outcomes are equivalent. The factory normalizes its own cutoff representation, so its snapshot hash can differ from the exact-input guard.

## Results and availability

`evaluated` contains the binding plus its hash, the full unchanged factory evidence, and comparison rows sorted by shipment/source scenario. Each row contains `shipment_id`, `source_scenario_id`, `receipt_scenario`, `recovery`, `projection` and the entire unchanged public `result`. A public numerical `unsupported` result remains an honest result row; adapter/projection evidence is separate.

`no_eligible_scenario` retains full factory evidence and an empty comparison list. Neither numerical tool runs. This confirms structural binding/join checks and absence of eligible source evidence; it does not prove full graph or numerical support. Partial deliveries, holds without ETA, future evidence and expired evidence follow the factory's existing rejection/exclusion rules rather than becoming assumed delays.

`rejected` contains a fixed stage/reason, empty details/comparisons and factory evidence only if already built and within bounds. No raw exception text or partial numerical rows escape. Unknown raw/nested fields reject at the adapter boundary; malformed known milestone fields remain subject to factory reporting. Input mutation, stale guards, unsupported propagation, unavailable calculations, malformed output and overflow reject atomically.

## Computational limits and local checks

Plain JSON means exact dictionaries/lists, strings, booleans and null; all numerical Python types and custom containers reject. Decimal quantities remain strings. Cycles and invalid Unicode reject before copy. Limits are request2MiB, raw and propagation each1MiB, binding8KiB, depth16, arrays256, aggregate entries16384, strings128KiB, eligible scenarios16 and final output4MiB. Existing source-receipt128KiB and exact numeric/timestamp bounds remain authoritative. Overflows reject rather than truncate or select an initial subset.

With an existing project interpreter and no dependency installation, focused checks can run as:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
PYTHONPATH=procurement-engine/src "$PYTHON" -m pytest \
  --noconftest -p no:cacheprovider -q \
  procurement-engine/tests/test_shipment_production_recovery.py
```

Use an offline execution guard for acceptance evidence; do not load shared conftest, service startup, settings, credentials or provider clients. Tests exercise actual factory/projector/public numerical composition and complete-result equality, plus rejection/provenance/bounds behavior. Synthetic local checks establish neither provider/model/speech behavior nor production deployment, source rights, persisted plan application or tenant permissions.
