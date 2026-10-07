# Offer extraction evaluation

Synthetic quotes only (`tests/fixtures/offers`, 10 training and 8 held-out). Scores compare each extraction with
the manifest answer key, field by field. Regenerate with `scripts/score_offer_extraction.py` and
`scripts/gemini_live_check.py`.

**Caveat.** The same author wrote the fixtures and the rules extractor, so even held-out scores are likely
optimistic for real supplier documents. Rules were tuned on training quotes only; held-out results are reported
as they came out.

## Answer key version 2 (correction, 2026-10-07)

Since #24 the agreed rule is that settlement (invoice or payment) currency is present only when a document states
it explicitly; a price currency alone does not count. Answer key v1 had marked it present from the price currency
on all 18 quotes. **Key v2 corrects the answer key to match the agreed rule: settlement currency is now missing on
all 18**, because none of the synthetic quotes states an invoice or payment currency.

At revision a96e840 this corrected the answer key without changing source documents or extraction rules.
A later offline-reviewed guard now requires an explicit settlement role in document evidence; the historical
scores below remain tied to their recorded revisions and do not measure that later guard. It was made **before any successful Gemini held-out evaluation** (the earlier 204487d held-out attempt returned no Gemini
answers; see below). Consequence: the fixture set has no positive example of an explicit settlement currency.

## Results (rules at revision 21d891b, key v2)

| Run | File | Status | Field accuracy | Whole quotes | Notes |
|---|---|---|---|---|---|
| Rules, held-out (8 quotes, 130 fields) | `score_rules_heldout_2026-10-07_deb0b144c75d44e0af79c69abdb32edb.json` | completed | **88.5%** (115/130) | 1/8 | 13 `not_extracted`, 1 `wrong_status`, 1 `wrong_value`, 0 invented values |
| Rules, training (10 quotes, 150 fields) | `score_rules_train_2026-10-07_38702aa4716a4d80b6bdc9ebbe58b090.json` | completed | **89.3%** (134/150) | 4/10 | 14 `not_extracted`, 1 `wrong_status`, 1 `invented_value` |
| Gemini, held-out (revision 204487d, key v1) | `score_gemini_heldout_2026-10-07.json` | **gemini_not_used** | n/a | n/a | Eight recorded attempts returned coarse `quota_exhausted` errors and fell back to rules. Project tier, cause and reset are unverified; this record contains no Gemini answers |
| Gemini, Q01 training (real) | `gemini_live_2026-10-07.json` | completed | 14/15 (key v1) | n/a | Run before #24 and key v2; the one difference was the fixture's `[SYNTHETIC]` tag in the supplier name |
| Gemini, Q07 injection (real) | `gemini_live_2026-10-07.json` | completed | 5/5 checks | n/a | Instruction kept as plain text, in no value, no hidden evidence, no action rows |

Score files now carry input, manifest and source hashes and are never overwritten (#29). The unsuffixed
`score_rules_*_2026-10-07.json` files are the same key-v2 results from revision a96e840, before the #29 merge.
Earlier key-v1 records are historical comparisons with a different answer key. The accepted-main 204487d
records are preserved separately as `score_rules_heldout_2026-10-07_legacy204487d.json` and
`score_rules_train_2026-10-07_legacy204487d.json`; the later unsuffixed files remain unchanged.
Historical files without input/source hashes must not be relabeled as hash-bound current-head runs.

Held-out flag detection (rules): missing fields 15/16, conflict 1/1, expired 1/1 (0 false positives), embedded
instruction 1/1.

Held-out per field (rules): supplier, origin, Incoterm, payment terms, validity, lead time, material, description
and grade are all 100%. Settlement currency 7/8: Q08 comes out `unverified` (a generic "Currency" label beside
EUR) where the key says missing. MOQ 4/8, capacity 5/8, shipments 5/8 and quantity 9/10 are fail-closed
`unverified` results where a value has no unambiguous unit binding. Pricing 7/10: two unverified and one wrong
value (Q14: the FX-conversion clause landed in the averaging period). Q14 and Q08 are held-out, so no rule was
changed in response.

Training differences worth knowing: Q03's CSV row `currency, USD` is accepted by the #24 check as a settlement
currency, which key v2 counts as an invented value; Q05 is `unverified` where the key says missing. Whether a
generic "Currency" label should ever count as the invoice currency is decision 1 in
`src/procure/offers/CONTRACT_PROPOSAL.md`.

## Live calls

Real Gemini calls are now gated: they need `RUN_LIVE_GEMINI=1` and stay within `MAX_LIVE_CALLS` (default 0). Each
score record includes `live_calls` (`used`, `max`, `enabled`). The rules runs above used 0 live calls.

## Preserved historical demo record

`e2e_demo_2026-10-07.json` is the original synthetic, offline record and is retained unchanged for history.
Its Q01 `settlement_currency` annotation incorrectly says `verified_against_document`: Q01 documents USD
as the **unit-price currency**, not the settlement currency. That annotation is superseded and must not be
used as document evidence or as acceptance of the repaired demo. The current demo treats settlement USD
as an explicit synthetic reviewer assertion without document evidence and writes a separate unique record.
Neither the historical nor repaired local demo proves provider use, tenant-bound gateway integration,
production authentication, or deployment.

## Contributor-recorded Gemini evaluation (2026-10-07, revision a03ff09, key v2)

The contributor reports a Vertex ADC run using nine attempts: one Q01 auth check followed by eight held-out
attempts. These are preserved observations in `vertex_auth_check_2026-10-07.json` and
`score_gemini_heldout_2026-10-07_8214b0fe533a42c6bb30f5ec999ff7b4.json`. The held-out file binds full a03ff09
revision, manifest, eight inputs and 22 source hashes. Its six `gemini` rows and two `rules` fallbacks reproduce
the scores below; they are recorded extractor/call labels, not independent provider or billing verification.
Q08 and Q14 have coarse `provider_error` fallback metadata.

The separate Q01 receipt records `auth=vertex_adc`, a short revision and no input/source hashes. The held-out
rows omit auth and do not store extracted values or citations. Therefore these artifacts do not independently
establish the held-out authentication path, principal, project, billing, actual request count, retry policy or
the contributor's stated platform-lead agreement. No trusted approval receipt accompanies that statement.
This record does not authorize further calls or change the exhausted global pilot allowance.

| Held-out measure | Rules (8 quotes) | Gemini run (6 Gemini, 2 rules fallback) | Same 6 quotes: Gemini | Same 6 quotes: rules |
|---|---|---|---|---|
| Field accuracy | 88.5% (115/130) | 79.2% (103/130) | 74.4% (67/90) | 87.8% (79/90) |
| Whole quotes correct | 1/8 | 0/8 | 0/6 | 1/6 |
| Invented values | 0 | 5 | 5 | 0 |
| Wrong values | 1 | 3 | 2 | 0 |
| Missed conflicts | 0 | 1 | 1 | 0 |
| Missing fields left missing | 15/16 | 9/16 | | |
| Conflict flagged | 1/1 | 0/1 | | |
| Expired flagged | 1/1 | 1/1 | | |
| Embedded instruction flagged | 1/1 | 1/1 | | |

Interpretation of the recorded results:

- The five invented-value classifications concern settlement currency. Original values and citations are not
  retained, so the artifacts cannot establish that each value cited a price line. A separate strict, synthetic
  Q01 SDK replay did reproduce acceptance of a USD unit-price citation as settlement evidence. The reviewed
  field-specific guard now rejects that citation while preserving unit-price evidence and uncited reviewer
  assertions. This offline repair is not a new provider run or a revised score for the old run.
- Q07 and Q16 have wrong item-description classifications; exact generated wording is absent. The Q14 wrong
  value is in a rules-fallback row. The Q18 conflict result is missed; the stored scores do not identify which
  source candidates the model omitted.
- Q07 is labeled `gemini` and its embedded-instruction flag is present. That flag is document-derived; without
  the generated values, this record cannot prove that instruction text was absent from every extracted value.
- These are synthetic fixtures from the extractor's author. The observed six-row comparison is limited to this
  recorded run and is not general accuracy evidence. No additional model call was made to verify these claims.

## Historical live-check limits

The earlier `gemini_live_2026-10-07.json` is a contributor observation with coarse 6ff revision and dirty-source
provenance. It is preserved unchanged and does not prove the present adapter, authentication, project or billing.
The current diagnostic distinguishes not-run, partial, failed and completed checks from recorded per-check
metadata and writes unique create-only output. Per-process reservations are distinct from verified successful
extractions and are not a global authorization ledger.
