# Offer extraction evaluation

Synthetic quotes only (`tests/fixtures/offers`, 10 training and 8 held-out). Scores compare each extraction with
the manifest answer key, field by field. Regenerate with `scripts/score_offer_extraction.py` and
`scripts/gemini_live_check.py`.

**Caveat.** The same author wrote the fixtures and the rules extractor, so even held-out scores are likely
optimistic for real supplier documents. Rules were tuned on training quotes only; held-out results are reported
as they came out.

## Results, 2026-10-07 (rules runs at revision 30e8cd4, after merging #26's stricter scorer)

| Run | File | Status | Field accuracy | Notes |
|---|---|---|---|---|
| Rules, held-out (8 quotes, 130 fields) | `score_rules_heldout_2026-10-07.json` | completed | **83.1%** (108/130); whole quotes 0/8 | 21 `not_extracted`, 1 `wrong_value` (Q14 index price: the FX-conversion clause landed in the averaging period), 0 invented values. No quote is fully correct because settlement currency is missing on all 8 |
| Rules, training (10 quotes) | `score_rules_train_2026-10-07.json` | completed | 84.7% | Down from 100% before #24's stricter settlement-currency and binding rules |
| Gemini, held-out (revision 204487d) | `score_gemini_heldout_2026-10-07.json` | **gemini_not_used** | n/a | All 8 real calls rejected: free-tier quota exhausted (20 requests/day for gemini-2.5-flash). Every quote fell back to rules, so its numbers are the rules numbers |
| Gemini, Q01 training (real) | `gemini_live_2026-10-07.json` | completed | 14/15 | Run before the quota ran out and before the #24 merge. Only difference: supplier name without the fixture's `[SYNTHETIC]` tag, which the scorer now ignores |
| Gemini, Q07 injection (real) | `gemini_live_2026-10-07.json` | completed | 5/5 checks | Instruction kept as plain text, in no value, no hidden evidence, no action rows |

Held-out flag detection (rules): missing fields 8/8, conflict 1/1, expired 1/1 (0 false positives), embedded
instruction 1/1.

Held-out per field (rules): supplier, origin, Incoterm, payment terms, validity, lead time, material, description
and grade are all 100%. Settlement currency 0/8: since #24, a price currency is not taken as the invoice
currency, while the answer key assumes it is (decision 1 in `src/procure/offers/CONTRACT_PROPOSAL.md`).
Excluding that field: 108/122 (88.5%). The rest are fail-closed `unverified` results (MOQ 4/8, capacity 5/8,
shipments 5/8, quantity 9/10), where values without an unambiguous unit binding are rejected, and pricing 7/10
(two unverified, one wrong: Q14). Q14 is held-out, so the rule was not changed in response.

**To do:** rerun `--mode gemini --split heldout` after the free-tier quota resets (or on a billed project, which
needs owner approval) to get a real Gemini held-out score.
