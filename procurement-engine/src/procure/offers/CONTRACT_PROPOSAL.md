# Offer contract proposal (issue #3)

From Taher (quote intelligence) to Divyanshu (contracts, gateway, persistence). Status: proposal. Nothing under
`contracts/`, `services/gateway/` or migrations has been edited. The Python routes below exist and are tested in
`procurement-engine`. The shapes here are what they return today; please adopt, rename or reject them in
`contracts/openapi.yaml`.

## 1. Shared types

Reuse the existing `Money {amount: decimal string, currency: ISO 4217}` and `Quantity {amount: decimal string,
unit}`. Units are `tonne`, `kg` and `lb`. Conversion to kg is exact (1 lb = 0.45359237 kg). Plain `ton`, `dmt` and
other units are rejected or left unverified, never converted.

```text
EvidenceRef {
  document_id: "sha256:<hex of file bytes>"
  locator: "p1:l10" | "Offers!D9" | "r3:c6"        # PDF page/line, sheet!cell, CSV row/column (1-based)
  quote: string                                     # exact text the value was read from
  text_sha256: hex                                  # hash of the whole located segment
  segment_text: string
}

FieldState<T> {
  name: string                                      # "valid_until", "items[0].pricing"
  kind: text | date | integer | quantity | currency | incoterm | pricing | shipments | material
  status: extracted | missing | conflict | unverified | corrected
  value: T | null                                   # null unless extracted or corrected
  critical: bool
  source: rules | gemini | user
  evidence: EvidenceRef[]                           # empty for missing/conflict/unverified
  candidates: [{value, verified, problems[], evidence[], cited[]}]   # kept for review, incl. rejected ones
  reasons: string[]
  # after a correction:
  evidence_status?: verified_against_document | user_entered_without_document_evidence
  corrected_by?, corrected_at?, previous?: {status, value}
}

Pricing = Fixed | IndexLinked
  Fixed       {type: "fixed", unit_price: Money, per_unit: unit}
  IndexLinked {type: "index_linked", index: nonblank string, averaging_period: nonblank string,
               premium: signed finite decimal Money, per_unit: unit,
               fx_basis?: nonblank string, fx_terms?: nonblank string,
               fx_conversion?: nonblank string, quotational_period?: nonblank string}
Incoterm    {code: one of EXW FCA FAS FOB CFR CIF CPT CIP DAP DPU DDP, place: string | null}
Capacity    Quantity + {period: month | quarter | year | week | null}
ShipmentLot {quantity: Quantity, window: "YYYY-MM" | "YYYY-MM-DD/YYYY-MM-DD"}
```

## 2. SupplierOffer

```text
SupplierOffer {
  offer_id, version: int, status: OfferStatus, synthetic: bool, extraction_id
  document: {document_id, filename, media_type, byte_size, trust: "untrusted_document_content", warnings[]}
  extractor: rules | gemini
  extractor_detail: {requested, call: real | mocked | none, model?, latency_ms?, fallback_reason?}
  fields: {
    supplier_name: FieldState<string>          # critical
    origin_country: FieldState<string>         # as written; ISO code in normalized.origin_country_code
    incoterm: FieldState<Incoterm>             # critical
    settlement_currency: FieldState<ISO 4217>  # critical; see section 6
    payment_terms: FieldState<string>
    valid_until: FieldState<date>              # critical
    lead_time_days: FieldState<int>
    min_order: FieldState<Quantity>            # MOQ
    capacity: FieldState<Capacity>
    shipments: FieldState<ShipmentLot[]>       # split arrivals
  }
  items: [{
    material: FieldState<catalogue key>        # critical; derived from the verified description, not by a model
    description: FieldState<string>
    grade: FieldState<string>
    quantity: FieldState<Quantity>             # critical
    pricing: FieldState<Pricing>               # critical (price, currency and price unit)
  }]
  review_flags: [{flag: hidden_content | embedded_instruction | formula_without_cached_value | expired |
                  price_conflict | field_conflict | no_text_layer | ..., locator, text}]
  normalized: {
    as_of, reporting_currency, origin_country_code, expiry: valid | expired | unknown,
    min_order: {kg}, capacity: {kg},
    payment_terms: {status: parsed | partial | inconsistent | unrecognised | not_stated | unavailable, text,
                    instalments: [{percent: decimal text | null, percent_basis: stated | single_term | not_stated,
                                   instrument: telegraphic_transfer | letter_of_credit | cash_against_documents |
                                               open_account | not_stated,
                                   anchor: order | shipment | bill_of_lading | documents | invoice | delivery |
                                           arrival | not_stated,
                                   days: int | null, timing: on | on_or_before, clause}],
                    unrecognised: string[], evidence: [{locator, quote}], reasons: string[], source}
    items: [{quantity: {kg}, pricing: {per_kg: Money, reporting: {currency, status, per_kg: Money | null,
             fx: {base, quote, rate, rate_date, source, retrieved_at, status} | null}}}]
  }
  blocking_reasons: [{code, field, message}]
  allowed_actions: string[]
  created_at, updated_at
}

OfferStatus: extracted | needs_review | corrected | saved | used_in_plan | rfq_drafted | rejected | expired
Moves:   correct     extracted|needs_review|corrected      -> corrected
         save        extracted|needs_review|corrected      -> saved        (blocked by blocking_reasons)
         reject      extracted|needs_review|corrected|saved -> rejected
         use_in_plan saved|used_in_plan                    -> used_in_plan
         draft_rfq   saved|used_in_plan                    -> rfq_drafted
         expire      any active status (system)            -> expired
Save is blocked while expired, while any critical field is missing/conflict/unverified, or while a usable
critical quantity/price cannot be normalized. Unavailable reporting-currency FX alone does not invalidate
an otherwise valid original price.
```

## 3. Examples (real output, trimmed)

Success: a saved offer's price and a reviewer-confirmed currency.

```json
{"status": "saved", "version": 3,
 "items": [{"pricing": {"name": "items[0].pricing", "status": "extracted", "source": "rules", "critical": true,
   "value": {"type": "fixed", "unit_price": {"amount": "9412.50", "currency": "USD"}, "per_unit": "tonne"},
   "evidence": [{"document_id": "sha256:5032b401...", "locator": "p1:l10", "quote": "USD 9,412.50 per metric tonne",
                 "text_sha256": "681e5277...", "segment_text": "Unit price: USD 9,412.50 per metric tonne"}]}}],
 "fields": {"settlement_currency": {"status": "corrected", "value": "USD", "source": "user",
   "evidence_status": "verified_against_document", "corrected_by": "reviewer",
   "previous": {"status": "missing", "value": null},
   "evidence": [{"locator": "p1:l10", "quote": "USD", "text_sha256": "681e5277..."}]}}}
```

Conflict: two verified prices; no value is chosen.

```json
{"name": "items[0].pricing", "status": "conflict", "value": null, "reasons": ["2 different values are stated"],
 "candidates": [
   {"value": {"type": "fixed", "unit_price": {"amount": "9388", "currency": "USD"}, "per_unit": "tonne"},
    "verified": true, "evidence": [{"locator": "Offers!D9"}, {"locator": "Offers!D8", "quote": "Price (USD/t)"}]},
   {"value": {"type": "fixed", "unit_price": {"amount": "9420.00", "currency": "USD"}, "per_unit": "tonne"},
    "verified": true, "evidence": [{"locator": "Terms!A5"}]}]}
```

Missing: not stated, so not filled in.

```json
{"name": "valid_until", "kind": "date", "status": "missing", "value": null, "critical": true,
 "reasons": ["not stated in the document"], "evidence": [], "candidates": []}
```

Unavailable: index-linked EUR premium, no reference rate offline. The original is kept and nothing is converted.

```json
{"type": "index_linked", "index": "LME Aluminium official cash settlement",
 "averaging_period": "average of the month of delivery", "premium_per_kg": {"amount": "0.34", "currency": "EUR"},
 "reporting": {"currency": "USD", "status": "unavailable", "fx": null, "premium_per_kg": null}}
```

Error: save blocked with reasons. Other codes: `not_found` 404, `invalid_request` 400, `unknown_field`,
`invalid_value`, `evidence_not_verified`, `document_unreadable`, `planning_unavailable` 422,
`invalid_transition`, `stale_version`, `idempotency_conflict` 409.

```json
{"error": {"code": "save_blocked", "message": "the offer cannot be saved until these are resolved",
  "blocking_reasons": [
    {"code": "field_missing", "field": "settlement_currency", "message": "settlement_currency is missing"},
    {"code": "field_missing", "field": "valid_until", "message": "valid_until is missing"},
    {"code": "field_unverified", "field": "items[0].quantity", "message": "items[0].quantity is unverified"}]}}
```

## 4. Routes

Implemented in Python (`procure/offers/http.py`, mounted on the existing app). Proposed gateway equivalents follow
the `/v2` convention and the existing workspace, principal and idempotency rules.

| Python (implemented) | Proposed gateway `/v2` | Notes |
|---|---|---|
| `POST /offers/extractions` | `POST /v2/offers/extractions` | JSON `{filename, content_base64, mode, as_of, reporting_currency}`. Same document returns the existing offer (`deduplicated: true`) |
| `GET /offers/extractions/{id}` | `GET /v2/offers/extractions/{id}` | Extraction plus every located segment, for side-by-side evidence |
| `GET /offers?status=&as_of=` | `GET /v2/offers` | Summaries; `as_of` previews expiry without changing stored status/version/events |
| `GET /offers/{id}?include_events=true&as_of=` | `GET /v2/offers/{id}` | Includes audit events; `as_of` is a read-only expiry preview |
| `POST /offers/{id}/corrections` | `POST /v2/offers/{id}/corrections` | `{field, value, evidence?, expected_version}`. Cited evidence is re-checked against the stored document |
| `POST /offers/{id}/save` | `POST /v2/offers/{id}/save` | Save gate |
| `POST /offers/{id}/reject` | `POST /v2/offers/{id}/reject` | Reason required |
| `POST /offers/planning-input` | `POST /v2/offers/planning-input` | Saved offers become a planning input and are marked `used_in_plan` |
| `POST /offers/{id}/rfq-draft` | via `/v2/actions` `prepare_rfq` | See section 7 |

Correction/save/reject take `expected_version`; planning input and RFQ draft take an idempotency key but no
`expected_version` in this local prototype. Every mutation takes an idempotency key (body field or
`Idempotency-Key` header). A replay returns the originally accepted response with `replayed: true`; it is
historical evidence, not a fresh expiry/eligibility check. A changed body or supplied actor with the same key
returns `idempotency_conflict`. The legacy mutation-body `as_of` field is ignored; mutations use the server
clock. In the gateway, `actor` must come from the authenticated principal, not the body.

## 5. Persistence

Local store: DuckDB tables in the Python service: `offer_documents` (located segments, not raw bytes),
`offer_extractions`, `offers` (versioned JSON payload), `offer_events` (audit) and `offer_requests`
(idempotency). A local operation commits state, audit, any recommendation/action, and receipt in one DuckDB
transaction; due expiry is a separate system transition and audit event under the server clock. Planning
entries carry `field_provenance` by field path (`status`, `source`, `evidence_status`, `corrected_by`,
`evidence_refs`), so a reviewer assertion without document evidence remains explicit. Planning rejects duplicate
offer IDs and invalid/nonfinite buyer numbers before use. For the shared PostgreSQL store, a migration owned
by you could mirror these five tables one to one. The process lock serialises local changes; cross-instance
concurrency and authenticated workspace scoping need the PostgreSQL/gateway implementation.

## 6. Decisions needed

1. **Settlement currency versus price currency.** Since #24, a price currency is not taken as the settlement
   currency. Most quotes do not state an invoice currency, so `settlement_currency` is usually `missing` and
   blocks save until a reviewer confirms it. Options: keep it critical (current), or make it optional and
   default the comparison to the price currency with a visible assumption. My preference is to keep it
   critical for now.
2. **Offer price into planning.** The legacy `/recommend` engine does not take offer prices, MOQ, capacity or
   payment terms as inputs. `planning-input` therefore returns full offer data plus a legacy request with
   stated assumptions (lead time in days rounded up to months). A multi-supplier `PlanningRequest` with
   `supplier_offer_ids` is needed for a real allocation.
3. **Restricted service.** `service_app.py` exposes an allowlist of routes; the `/offers` routes are not on it.

## 7. End-to-end gaps

What works locally: saved offer, then `planning-input`, then the existing `recommend()`, then the existing
`actions.create_rfq()`. This gives a local RFQ draft; nothing is sent. Missing pieces:

- Contract schemas and generated types for `SupplierOffer` and the routes above (`contracts/openapi.yaml`).
- Gateway proxy routes for `/v2/offers/*`, plus binding of principal and workspace to `actor`.
- Linking an offer to the gateway's `prepare_rfq` approval and receipt flow, which needs the plan action to reference
  `offer_id` and `version`.
- The RFQ text from `actions.build_rfq` is generic (fixed Pune wording, no supplier or offer terms).
- PostgreSQL migration for the five tables (section 5).

## 8. Dependency request

`procurement-engine/requirements-service.txt` (yours) lacks the PDF reader dependencies, so the deployed service
would fail on PDF uploads. Please add:

```text
pdfminer.six==20260107
pdfplumber==0.11.10
```

Both are MIT licensed (pdfplumber also pulls in `pypdfium2`, Apache-2.0/BSD-3-Clause). `fpdf2` is only used to
generate test fixtures and is not needed in the service.

## 9. Sourcing candidates (proposed, not wired)

`sourcing.py` turns accepted, unexpired offers into per-item sourcing candidates. `store.sourcing_candidates(today=)` builds them
from every stored offer. It is read-only: no status, version or audit change. Nothing in `decision.py`, the gateway or
`contracts/` reads it yet. This is the shape I propose you consume, for example to fill `PlanningRequest.supplier_offer_ids`
and the cash and validity constraints in the planning engine.

```text
SourcingCandidates {
  contract: "offers-sourcing-candidates/proposed-0.1", as_of: date, synthetic: bool
  candidates: [SourcingCandidate]                  # sorted by material, offer_id, item_index
  excluded: [{offer_id, offer_version, item_index: int | null, reason, detail}]
  notes: string[]
}
SourcingCandidate {
  candidate_id: "<offer_id>@v<version>#item<i>"    # changes when the offer version changes
  offer_id, offer_version, offer_status: saved | used_in_plan, item_index, synthetic, document_id
  supplier: {name, origin_country, origin_country_code}
  material: catalogue key, description, grade
  quantity: {amount: decimal, unit: tonne | kg | lb, kg: decimal}
  min_order_kg: decimal | null, capacity: {kg, period} | null, lead_time_days: int | null
  incoterm: {code, place}, settlement_currency: ISO 4217 | null
  price: {type: fixed, unit_price: Money, per_unit, per_kg: Money}
       | {type: index_linked, index, averaging_period, premium_per_kg: Money}
  landed_price: {
    status: incoterm_price_only | incoterm_unknown | index_linked_final_price_unknown
    basis: quoted_price_on_incoterm, incoterm, place
    includes: string[], not_included: string[]     # Incoterms 2020 cost split, for example CIF excludes import duty
    per_kg: Money | null                           # quote currency; null for index-linked prices
    reporting: {currency, status: ok | not_needed | unavailable, per_kg: Money | null,
                fx: {base, quote, rate, rate_date, source} | null}
  }
  validity: {state: valid, valid_until: date, days_left: int, evidence: [{locator, quote}]}
  payment_terms: normalized.payment_terms (section 2): instalments with share, instrument, event, days
  evidence: [{field, locator, text_sha256, document_id}]   # every verified field of the offer and this item
  field_sources: {<field>: {status: extracted | corrected, source: rules | gemini | user}}
}
```

Rules:
- **Accepted:** status `saved` or `used_in_plan`. Other statuses are excluded as `not_accepted`, with the status in
  `detail`.
- **Validity:** an offer past its valid-until date on `as_of` is excluded as `expired`. A missing date is excluded as
  `validity_not_stated`; an unresolved one as `validity_unavailable`. Save already blocks both, so they only appear
  for older stored offers.
- **Items:** each needs a verified material, a normalised quantity and a normalised price. Otherwise it is excluded
  as `item_incomplete`, with what is missing.
- **Landed price:** this is the quoted price on its Incoterm, not a full landed cost. Duty, port and inland costs
  are listed under `not_included` and never estimated here. The reporting-currency price keeps the FX rate, its
  date and its source. When no dated rate exists it is `unavailable`, not filled.

Example (real output for synthetic Q01 after a reviewer confirmed USD and saved it; trimmed):

```json
{"contract": "offers-sourcing-candidates/proposed-0.1", "as_of": "2026-10-07", "synthetic": true,
 "candidates": [{"candidate_id": "17ac3e84318e@v3#item0", "offer_id": "17ac3e84318e", "offer_version": 3,
   "offer_status": "saved", "item_index": 0, "synthetic": true,
   "supplier": {"name": "Atacama Red Metals SpA [SYNTHETIC]", "origin_country": "Chile", "origin_country_code": "CL"},
   "material": "copper", "quantity": {"amount": "250", "unit": "tonne", "kg": "250000"},
   "min_order_kg": "25000", "capacity": {"kg": "500000", "period": "month"}, "lead_time_days": 45,
   "incoterm": {"code": "CIF", "place": "Nhava Sheva, India"}, "settlement_currency": "USD",
   "price": {"type": "fixed", "unit_price": {"amount": "9412.50", "currency": "USD"}, "per_unit": "tonne",
             "per_kg": {"amount": "9.4125", "currency": "USD"}},
   "landed_price": {"status": "incoterm_price_only", "basis": "quoted_price_on_incoterm", "incoterm": "CIF",
     "includes": ["export_clearance", "main_freight", "insurance"],
     "not_included": ["import_duty_and_taxes", "destination_port_charges", "inland_freight_to_plant"],
     "per_kg": {"amount": "9.4125", "currency": "USD"},
     "reporting": {"currency": "USD", "status": "not_needed", "per_kg": {"amount": "9.4125", "currency": "USD"}, "fx": null}},
   "validity": {"state": "valid", "valid_until": "2026-10-31", "days_left": 24,
                "evidence": [{"locator": "p1:l16", "quote": "31 October 2026"}]},
   "payment_terms": {"status": "parsed", "instalments": [{"percent": "30", "instrument": "telegraphic_transfer",
                     "anchor": "order", "days": 0, "timing": "on"}, "..."]},
   "evidence": [{"field": "supplier_name", "locator": "p1:l2", "text_sha256": "a27d49df...",
                 "document_id": "sha256:5032b401..."}, "..."],
   "field_sources": {"settlement_currency": {"status": "corrected", "source": "user"}}}],
 "excluded": [{"offer_id": "276155a8572c", "offer_version": 1, "item_index": null, "reason": "not_accepted",
               "detail": "status needs_review"}]}
```

Questions for you:
1. **Accepted statuses.** Should `rfq_drafted` offers count as accepted? They are excluded for now.
2. **Grain.** Is one candidate per offer item the grain your `PlanningRequest` wants, or one per offer?
3. **Full landed cost.** The `not_included` costs need your inputs (duty by HS line, port charges, inland freight).
   Should the planner add them to `landed_price.per_kg`, or should this model take them as explicit assumptions?
4. **Route.** I can add `GET /offers/sourcing-candidates?as_of=` to `offers/http.py` if you want to proxy it.
