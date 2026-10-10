# Quote extraction: held-out rerun on main (2026-10-10)

Part of #3. A rerun of the rules extractor on the 8 held-out synthetic quotes, against the current main. Nothing
was tuned: no rule, fixture or answer key changed before or after this run.

## Run

| | |
|---|---|
| Revision | `3f9ee96ab7ffd61e1267e24182923853aa2e348a` (main, clean source tree) |
| Run at | 2026-10-10 10:21:37 UTC |
| Mode | `rules` only, `OFFLINE=1`, no key in the environment (`key_configured: false`) |
| Calls | none: `extractors_used: {rules: 8}`, `calls: {none: 8}`, live calls used 0 of 0 |
| Inputs | 8 held-out quotes (Q06, Q07, Q08, Q14, Q15, Q16, Q17, Q18), answer key `manifest_version` 2 |
| Record | [`extraction_eval/score_rules_heldout_2026-10-10.json`](extraction_eval/score_rules_heldout_2026-10-10.json), with manifest, input and source hashes |

How it was run: by calling `run("rules", "heldout")` from `procurement-engine/scripts/score_offer_extraction.py`
and saving the report here. The script's command line writes into `procurement-engine/evaluation/`, which this
change does not touch.

## Result

Field accuracy **88.5%** (115 of 130 fields). Whole quotes correct: 1 of 8 (Q07). No invented values and no extra
items. Errors: 13 `not_extracted`, 1 `wrong_status`, 1 `wrong_value`.

| Field | Correct | Accuracy |
|---|---|---|
| supplier_name | 8/8 | 100% |
| origin_country | 8/8 | 100% |
| incoterm | 8/8 | 100% |
| settlement_currency | 7/8 | 87.5% |
| payment_terms | 8/8 | 100% |
| valid_until | 8/8 | 100% |
| lead_time_days | 8/8 | 100% |
| min_order | 4/8 | 50% |
| capacity | 5/8 | 62.5% |
| shipments | 5/8 | 62.5% |
| items: material | 10/10 | 100% |
| items: description | 10/10 | 100% |
| items: grade | 10/10 | 100% |
| items: quantity | 9/10 | 90% |
| items: pricing | 7/10 | 70% |

Flags: missing fields 15 of 16, conflict 1 of 1, expired 1 of 1 with no false positives, embedded instruction 1 of 1.

## Misses

| Quote | Field | Expected | Got | Error |
|---|---|---|---|---|
| Q06 | items[0].pricing | present | unverified | not_extracted |
| Q08 | settlement_currency | missing | unverified | wrong_status |
| Q08 | min_order | present | unverified | not_extracted |
| Q14 | shipments | present | unverified | not_extracted |
| Q14 | items[0].pricing | present | extracted, different value | wrong_value |
| Q15 | min_order | present | unverified | not_extracted |
| Q15 | capacity | present | unverified | not_extracted |
| Q15 | shipments | present | unverified | not_extracted |
| Q16 | items[0].pricing | present | unverified | not_extracted |
| Q17 | min_order | present | unverified | not_extracted |
| Q17 | capacity | present | unverified | not_extracted |
| Q17 | shipments | present | unverified | not_extracted |
| Q17 | items[0].quantity | present | unverified | not_extracted |
| Q18 | min_order | present | unverified | not_extracted |
| Q18 | capacity | present | unverified | not_extracted |

Every miss but one leaves the field unverified rather than filling in a wrong value. The exception is Q14's
pricing, the known case where the FX-conversion clause lands in the averaging period. Q08's settlement currency is
held back as unverified where the answer key says missing; both block save.

## Compared with the last record

The previous held-out rules record is `score_rules_heldout_2026-10-07_deb0b144...json`, at revision 21d891b. This
run is identical to it:
- the same 115 of 130 fields
- the same per-field results, flags and misses

Since then:
- **Inputs:** the input file hashes are unchanged.
- **Answer key:** the manifest hash changed only because one note in `answer_key_changes` was reworded.
- **Extraction code:** `evidence.py` and `extract_gemini.py` changed, with the settlement-currency evidence
  hardening in commits 119fa01, 8f54920, 37c8d1e, ef445b1 and cc436b0. None of those changes moved a held-out
  result.

## Caveats

- **Synthetic and optimistic.** The same author wrote the fixtures and the rules, so these scores are likely
  optimistic for real supplier documents.
- **Held-out not used for tuning.** These results were not used to change any rule. The misses above stay as they
  are until a fix can be justified from training quotes alone.
- **Official table not updated.** The summary table in `procurement-engine/evaluation/offers/README.md` is not
  updated here. That directory is outside this change.
