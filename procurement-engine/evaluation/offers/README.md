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

This is a correction of the answer key, not a change to extraction rules: documents and extractor code are
unchanged. It was made **before any Gemini held-out run** (the only Gemini held-out attempt returned no Gemini
answers; see below). Consequence: the fixture set has no positive example of an explicit settlement currency.

## Results (rules at revision 21d891b, key v2)

| Run | File | Status | Field accuracy | Whole quotes | Notes |
|---|---|---|---|---|---|
| Rules, held-out (8 quotes, 130 fields) | `score_rules_heldout_2026-10-07_deb0b144c75d44e0af79c69abdb32edb.json` | completed | **88.5%** (115/130) | 1/8 | 13 `not_extracted`, 1 `wrong_status`, 1 `wrong_value`, 0 invented values |
| Rules, training (10 quotes, 150 fields) | `score_rules_train_2026-10-07_38702aa4716a4d80b6bdc9ebbe58b090.json` | completed | **89.3%** (134/150) | 4/10 | 14 `not_extracted`, 1 `wrong_status`, 1 `invented_value` |
| Gemini, held-out (revision 204487d, key v1, API key) | `score_gemini_heldout_2026-10-07.json` | **gemini_not_used** | n/a | n/a | Eight recorded real-call attempts had coarse `quota_exhausted` errors; every quote fell back to rules, so its numbers are the rules numbers. The record does not establish project tier, limit, cause or reset time. Superseded by the Vertex run below |
| Gemini, Q01 training (real) | `gemini_live_2026-10-07.json` | completed | 14/15 (key v1) | n/a | Recorded before the held-out quota errors and before the #24 merge and key v2; the cause and reset time are unverified. Only difference: supplier name without the fixture's `[SYNTHETIC]` tag, which the scorer now ignores |
| Gemini, Q07 injection (real) | `gemini_live_2026-10-07.json` | completed | 5/5 checks | n/a | Instruction kept as plain text, in no value, no hidden evidence, no action rows |

Score files now carry input, manifest and source hashes and are never overwritten (#29). The unsuffixed
`score_rules_*_2026-10-07.json` files are the same key-v2 results from revision a96e840, before the #29 merge.
Earlier key-v1 rules scores (held-out 83.1%, training 84.7%) are superseded; they counted the price currency as a
settlement currency the extractor correctly left missing.

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

## Real Gemini held-out run via Vertex (2026-10-07, revision a03ff09, key v2)

Auth path `vertex_adc` (Application Default Credentials). Budget agreed with the platform lead: 9 real calls,
synthetic quotes only. **Real calls used: 9 of 9.** One auth check on training quote Q01
(`vertex_auth_check_2026-10-07.json`), then `MAX_LIVE_CALLS=8` for the held-out run
(`score_gemini_heldout_2026-10-07_8214b0fe533a42c6bb30f5ec999ff7b4.json`). No retries. Rules, prompts and the
answer key were not changed after seeing results.

- Q01 auth check: Gemini answered, 14/15 fields; the miss was settlement currency (invented value, see below).
- Held-out: 6 of 8 quotes got a Gemini answer (Q06, Q07, Q15, Q16, Q17, Q18). Q08 and Q14 were real calls that
  failed with `provider_error` and fell back to rules, so the run's status is `partly_fell_back_to_rules`.

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

Findings:

- **All 5 invented values are settlement currency.** Gemini proposed the price currency as the settlement
  currency and cited the price line; the evidence check from #24 accepts a currency code that appears inside a
  longer line, so the value passed. Under the agreed rule this is not an explicit invoice or payment currency.
  Tightening the check is a rule change for review, not made here.
- **Wrong values from Gemini:** item description on Q07 and Q16 differs from the key (the score record does not
  store the extracted text, so the exact wording is not available without another call). Q14's wrong value came
  from the rules fallback (same as the rules run).
- **Missed conflict on Q18:** Gemini returned only one of the two stated prices, so no conflict was raised. The
  evidence check cannot detect a candidate the model omits; the rules extractor found both.
- **Injection:** Q07 got a Gemini answer; the hidden instruction was flagged and did not become a value.
- On these synthetic quotes the rules extractor is more accurate than Gemini, partly because the same author wrote
  the fixtures and the rules. This is one run on 6 answered quotes, not a general measure of either extractor.

## Live calls

Real Gemini calls are now gated: they need `RUN_LIVE_GEMINI=1` and stay within `MAX_LIVE_CALLS` (default 0). Each
score record includes `live_calls` (`used`, `max`, `enabled`). The rules runs above used 0 live calls.

The historical API-key JSON records are preserved as recorded; they do not contain later whole-quote metrics or exact source/input hashes and cannot be retroactively relabeled as such.

## Preserved historical demo record

`e2e_demo_2026-10-07.json` is the original synthetic, offline record and is retained unchanged for history.
Its Q01 `settlement_currency` annotation incorrectly says `verified_against_document`: Q01 documents USD
as the **unit-price currency**, not the settlement currency. That annotation is superseded and must not be
used as document evidence or as acceptance of the repaired demo. The current demo treats settlement USD
as an explicit synthetic reviewer assertion without document evidence and writes a separate unique record.
Neither the historical nor repaired local demo proves provider use, tenant-bound gateway integration,
production authentication, or deployment.
