# Pitch notes: quotes and dispatch (Taher)

Notes for presenting quote intelligence (#3) and the dispatch workspace (#5, #60). Every number below has a source
in this repository. Checked on main at 497b53e on 2026-10-12. The demo click path is in
[Demo_Quotes_and_Dispatch.md](Demo_Quotes_and_Dispatch.md); how to start it is in [Run_The_Demo.md](Run_The_Demo.md).

## a. Features, one line each

| Feature | One line |
|---|---|
| Quote extraction with evidence (#3) | Reads PDF, XLSX and CSV quotes into fields, and each value is bound to the exact page line or cell it came from, checked by hash |
| Never guess | A value that is missing, conflicting or unverifiable stays empty with its status, and blocks save when it is critical |
| Untrusted documents | Hidden text is never used as evidence. Instruction-like text is flagged and shown as text only, and never followed |
| Review and save | A reviewer can correct a field; the correction is recorded as a reviewer entry, and economic changes must be re-checked before save |
| Synthetic exports | Offers and shipments download as JSON and CSV, labelled synthetic on every row, with spreadsheet formulas neutralised |
| Expiry and payment timing (#73) | Shows whether a quote is still open on a decision date, and places each payment instalment on its date, in Decimal, with any rate entered by the user |
| Sourcing candidates (#74) | A read-only model that turns accepted, unexpired offers into sourcing inputs with landed-price basis, FX date and evidence links |
| Status from evidence (#5) | A shipment is on time, delayed, at risk or unknown only from milestones, a plan baseline or route evidence, never from a guess |
| Working-day lead time | Counts weekends and a holiday table (synthetic for India) and says "not available" when it cannot |
| Delay replay | Injects one synthetic event and shows what changes, including a new material shortage, and rejects events after the data cutoff |
| Hazard and weather exposure (#60) | Turns Open-Meteo and GDACS answers into exposure only: never "safe", never a confirmed delay, never a changed arrival date |
| News and policy notices (#49, #12) | Offline snapshot and notice parsers for the event gate; they have no page yet |

## b. Measured numbers

| Number | What it measures | Source |
|---|---|---|
| **88.5%** (115 of 130 fields) | Rules extraction on 8 held-out synthetic quotes; rerun on main 3f9ee96 identical to the 21d891b record | `docs/Extraction_Eval_Refresh_2026-10-10.md` |
| 1 of 8 | Held-out quotes with every field correct | same |
| 0 | Invented values in the held-out rules run | `procurement-engine/evaluation/offers/README.md` |
| 15 of 16, 1 of 1, 1 of 1, 1 of 1 | Held-out flags: missing fields, conflict, expired (0 false positives), embedded instruction | `docs/Extraction_Eval_Refresh_2026-10-10.md` |
| 89.3% (134 of 150 fields) | Rules extraction on the 10 training quotes, revision 21d891b | `procurement-engine/evaluation/offers/README.md` |
| 14 of 15 fields, 5 of 5 checks | One real Gemini run on synthetic Q01, and the Q07 injection checks | `procurement-engine/evaluation/offers/README.md`, `procurement-engine/src/procure/offers/README.md` |
| 8 of 8 | Held-out payment terms that parse (recorded as-is; treat as optimistic) | `procurement-engine/src/procure/offers/README.md` |
| 1,075 passed, 2 skipped | 36 Python test files for quotes, documents, dispatch, route evidence, hazards, news, policy, calendars and Nager, on main 497b53e. The 2 skips are opt-in live Gemini tests | Command below |
| 92 passed | 7 web test files for `/offers`, `/dispatch` and the shared report export, on main 497b53e | Command below |
| 1440, 768 and 390 px | Widths at which `/offers` and `/dispatch` were checked in headless Chrome, with keyboard access, visible focus, screen-reader labels and tap targets of at least 24 px | `procurement-engine/src/procure/offers/README.md`, `procurement-engine/src/procure/dispatch/README.md`, `docs/Walkthrough_Checklist_Taher.md` |

Test commands, from `procurement-engine` and `apps/web`:

```sh
.venv/bin/python -m pytest -p no:warnings $(ls tests/test_*.py | grep -E "test_offer_|test_documents|dispatch|route_evidence|hazard|news|policy|event_features|shipment_fixtures|nager|milestone_normalizer|calendars" | grep -v -E "test_offer_local_app|test_offer_tenancy")
npx vitest run src/offers.test.ts src/offers-page.test.tsx src/offers-scenario.test.ts src/dispatch.test.ts src/dispatch-page.test.tsx src/dispatch-navigation.test.tsx src/report.test.tsx
```

The Python list leaves out `test_offer_local_app.py` and `test_offer_tenancy.py`. Those are Divyanshu's files for
the offers-only service. When they run in the same process, two tests in `test_offer_local_app.py` fail and leave
state that makes later offer files fail. Each of those tests passes on its own.

## c. What we must not claim

- **Real data:** every quote, shipment, port, hazard, holiday and replay event in the demo is synthetic.
- **Real-world accuracy:** 88.5% is on synthetic quotes written by the same author as the rules. It says nothing about real supplier documents.
- **Live providers:** Open-Meteo, GDACS, Nager.Date, GDELT and RSS adapters have only run on hand-written payloads. No live call is made in the demo.
- **Gemini quality:** one real run on synthetic documents is not an accuracy figure.
- **Safety:** "no exposure found" is not a statement that a route is safe, and exposure never confirms a delay.
- **Verified coordinates:** on main the port coordinates are hand-entered; #72 (open) cites sources for six ports, and Mundra and Jebel Ali stay unverified.
- **Real holidays:** the India holiday table is invented.
- **Connected saves:** in the demo, quote corrections and saves stay in the browser tab.
- **Landed cost:** the sourcing model gives the quoted price on its Incoterm; duty, port and inland costs are not estimated.
- **Not built:** speech, hosting, carbon and compliance, Comtrade export discovery, and a text proposal source.

## d. Likely judge questions

| Question | Short honest answer |
|---|---|
| How accurate is the extraction? | 88.5% of fields on 8 held-out synthetic quotes, rules only, with no invented values. We have not measured it on real supplier quotes. |
| What happens when a quote is ambiguous? | The field stays missing, conflicting or unverified, with the reason, and save is blocked until a reviewer resolves it. |
| Can a malicious quote make the system act? | Document text is data. Instruction-like text is flagged and shown as text; extraction has no actions, and the Gemini schema has no action fields. The injection check passed 5 of 5 on synthetic Q07. |
| Is Gemini used? | The extractor can use Gemini behind a gate that is off by default. The demo runs the offline rules extractor. One real run on synthetic Q01 matched 14 of 15 fields. |
| Is this real shipment data? | No. All shipments, ports and events are synthetic and labelled. The adapters for live sources exist but are not switched on. |
| How do you know a delay is real? | We separate exposure from confirmation. Only a dated operating notice or milestone confirms a status; weather and hazards only raise exposure. |
| What does the replay prove? | That one synthetic event flows through to status and material need. For example, a port hold with a new ETA creates a 3000 kg copper shortfall. It is not a forecast. |
| How do you handle money? | Decimal text with explicit currency and unit everywhere; FX has a dated rate and source, or it is marked unavailable. |
| Why are payment terms useful? | They turn "30% advance, 70% against B/L" into dated cash. In the demo, Q03's terms move payment 98.1 days later than Q01's. The value of that depends on a rate the user enters. |
| What is left before production? | Real document evaluation, live provider approval and budgets, gateway connection for saves, hosting, and the not-built modules (speech, carbon, Comtrade). |
