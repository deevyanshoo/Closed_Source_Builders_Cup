# Quote intelligence (issue #3)

Supplier quote documents (PDF, XLSX, CSV) become evidence-checked, reviewable offers.

| Module | Role |
|---|---|
| `../documents/` | Read files into located, hashed segments: `p1:l10`, `Offers!D9`, `r3:c6` |
| `extract_gemini.py` | Gemini structured extraction from located segments (never raw files) |
| `extract_rules.py` | Offline rule-based extractor, used without a key, with `OFFLINE=1` or after a failed call |
| `evidence.py` | Every value must be quoted at its locator, hash-matched, visible and readable from that text |
| `normalize.py`, `fx_rates.py` | Decimal numbers, exact unit conversion, dated Frankfurter rates (live or cache, else unavailable) |
| `extraction.py` | Runs the above and adds review flags and normalised values |
| `scoring.py` | Compares an extraction with the synthetic manifest answer key |

Field statuses: `extracted`, `missing`, `conflict`, `unverified` (and `corrected` after review). Only `extracted` and
`corrected` carry a value; nothing is filled in by guesswork.

## Untrusted document text

Document text is data. Hidden or visibility-unverified text is excluded as evidence. Instruction-like text is
flagged for review; visible text can still support a field only if it passes the normal source-binding checks.
Flagging alone does not categorically exclude visible instruction-like text from evidence. Gemini's response schema has no action fields,
unknown keys are rejected, and extraction has no side effects. Tests: `tests/test_offer_injection.py`.

## Real versus mocked Gemini

- The default test suite never calls Google. `tests/conftest.py` removes any key loaded from `.env`; mocked
  responses are labelled `call: mocked`.
- Real calls are recorded separately by `scripts/gemini_live_check.py` into `evaluation/offers/gemini_live_<date>.json`
  (date, revision, model, latency, tokens). The key itself is never written.
- Opt-in real test: `RUN_LIVE_GEMINI=1 .venv/bin/python -m pytest -m live`.

Recorded on 2026-10-07 (revision 6ff9c3e plus uncommitted Step 4 work), `gemini-2.5-flash`, synthetic inputs only:

| Check | Result |
|---|---|
| Q01 (training) real extraction | 14 of 15 fields correct; about 7.4 s. The one difference: Gemini returned the supplier name without the fixture's `[SYNTHETIC]` tag, which the scorer now ignores |
| Q07 (held-out) real injection check | All five checks passed: instruction flagged as plain text, absent from every value, no hidden evidence accepted, no action rows created |

One run on synthetic documents is not a measure of accuracy on real supplier quotes.

The scorer's field `accuracy` measures expected fields with positional item alignment. `quote_correct` also
requires matching item counts; `quotes_correct` and `quote_accuracy` summarize whole-quote acceptance.
An invented extra item cannot pass a quote even when all expected fields are correct. These synthetic metrics
do not establish a general item-identity policy or supplier-document accuracy.
