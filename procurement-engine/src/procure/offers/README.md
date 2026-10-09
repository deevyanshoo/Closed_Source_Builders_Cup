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

## Credentials and the live-call gate

- Vertex AI with Application Default Credentials when `GOOGLE_GENAI_USE_VERTEXAI=true` and `GOOGLE_CLOUD_PROJECT` and
  `GOOGLE_CLOUD_LOCATION` are set (`api_version=v1`, 15 s timeout). An explicit Vertex selection with either setting
  missing fails closed, even if `GEMINI_API_KEY` exists. API-key mode requires a separate non-Vertex selection.
  `OFFLINE=1` blocks both.
- Real calls also need `RUN_LIVE_GEMINI=1` and stay within `MAX_LIVE_CALLS` per process (default 0, so none).
  Otherwise extraction falls back to rules with `live_calls_disabled` or `live_call_budget_exhausted`.
- Missing or failed credentials fall back with `vertex_config_incomplete`, `no_api_key` or `credentials_unavailable`.
  Audit records show `auth` (`vertex_adc` or `api_key`), `call` (`real` or `mocked`) and per-process reserved-attempt
  counts. Credential/token values, keys, project IDs and credential paths are excluded; aggregate token counts may
  be recorded. The per-process gate is not a global pilot-budget ledger.

## Real versus mocked Gemini

- The default test suite never calls Google. `tests/conftest.py` removes any key loaded from `.env`; mocked
  responses are labelled `call: mocked`.
- Real-call diagnostics are recorded separately by `scripts/gemini_live_check.py` into unique create-only
  `evaluation/offers/gemini_live_<date>_<run id>.json` files (date, revision, model, latency, aggregate token counts).
  `completed` requires both checks to use real Gemini and the injection checks to pass; zero or partial outcomes are
  labelled separately. The key itself is never written.
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

## Run it locally

From `procurement-engine/` (Python 3.10 or later):

```bash
make setup                                    # creates .venv and installs requirements
.venv/bin/python -m pytest tests/test_documents.py tests/test_offer_*.py   # offline: no network, no Gemini
make api                                      # FastAPI on http://127.0.0.1:8000
.venv/bin/python scripts/offer_e2e_demo.py    # synthetic quote to saved offer, planning input and a local RFQ draft; offline, nothing sent
.venv/bin/python scripts/score_offer_extraction.py --mode rules --split train
```

The service routes are under `/offers`:
- **Extraction:** `POST /offers/extractions` and `GET /offers/extractions/{id}`.
- **Offers:** `GET /offers` and `GET /offers/{offer_id}`.
- **Review actions:** `POST /offers/{offer_id}/corrections`, `/save`, `/reject` and `/rfq-draft`. An RFQ draft is a draft only; nothing is sent.
- **Planning input:** `POST /offers/planning-input`.

Without a key, or with `OFFLINE=1`, extraction uses the rules extractor.

The web page is `/offers` in `apps/web`: `npm install`, then `npm run dev`. It runs on a labelled local
fixture adapter (`apps/web/src/offers.ts`). The adapter matches an uploaded file to a synthetic fixture by SHA-256
and never invents an offer for any other file. To regenerate its data after changing extraction:
`.venv/bin/python scripts/export_offer_fixtures_for_web.py`.

## What is synthetic

Everything:
- **Quotes:** the 18 quotes in `tests/fixtures/offers` (10 training, 8 held-out) and their manifest answer key are
  generated by `tests/fixtures/offers/generate_quotes.py`. Supplier names carry a `[SYNTHETIC]` tag.
- **FX:** outside tests, rates come live from Frankfurter or from its cached response. Tests run with `OFFLINE=1` and mocked HTTP (`tests/test_offer_normalize.py`).
- **Web page:** the page's offers are the rules pipeline's output for those quotes. Its corrections and saves stay in the browser tab.

## What is verified

- **Evidence checks.** Every value shown as `extracted` is bound to its locator, hash and visible text by
  `evidence.py`. The tests in `tests/test_offer_evidence*.py` and `tests/test_offer_extraction*.py` cover missing,
  conflicting, hidden and instruction-like content.
- **Lifecycle.** Idempotency, version checks, the audit log and save-blocking reasons are covered by
  `tests/test_offer_lifecycle.py` and `tests/test_offer_api.py`.
- **Accuracy figures.** The rules extractor's field accuracy on the synthetic sets is recorded in
  `evaluation/offers/README.md`, with the files and revisions behind each figure. Read the caveat there: the same
  author wrote fixtures and rules, so held-out scores are likely optimistic.

## The page

The final click path and expected screens are in `docs/Demo_Quotes_and_Dispatch.md`, and the release checks in
`docs/Walkthrough_Checklist_Taher.md`. In short:
1. Upload `SYN-Q01_atacama_copper_cif.pdf`.
2. Select a field name to highlight its source line.
3. Correct the settlement currency.
4. Try to save. The local adapter blocks it until the service revalidates.
5. Download the reviewed offer as synthetic JSON or CSV (`offerReport` in `apps/web/src/offers.ts`).

What the export contains:
- **Per field:** status, exact value, source and evidence (locator and quote). Conflicting candidates retain each exact value, verification flag and locator in both formats.
- **For the offer:** the blocking reasons and review flags.
- **Labels:** every JSON file and every CSV row is labelled synthetic.
- **Untrusted text:** CSV text beginning with a formula character, including after leading whitespace, is prefixed with an apostrophe. Execution behavior in spreadsheet applications has not been independently checked.

Accessibility:
- Each field name is a keyboard-reachable button with `aria-pressed`.
- Edit buttons are named after their field.
- Status chips carry screen-reader text.
- The current page is marked in the header navigation, and focus is visible.
- Tap targets are at least 24 by 24 px.
- It was checked in headless Chrome at 1440, 768 and 390 px.

## Known limits

- **Settlement currency.** No synthetic quote states one, so it is `missing` on all 18 and blocks save until a
  reviewer supplies it. The fixture set has no positive example.
- **Gateway.** The page is not connected yet. It needs the `/offers` routes exposed through the gateway under the workspace; see `CONTRACT_PROPOSAL.md`.
- **Saving in the browser.** The local web adapter cannot re-run normalisation or FX. A corrected price, quantity, currency,
  Incoterm, validity, origin, minimum or capacity therefore blocks save with "revalidation required", until the
  page is connected to the service through the gateway.
- **Gemini evidence.** One real run on synthetic documents (see above) says nothing about real supplier quotes.
- **Storage.** The store is local DuckDB. There is no multi-user locking beyond the version check.
