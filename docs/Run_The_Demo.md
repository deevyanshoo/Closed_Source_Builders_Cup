# Run the ZOORQ demo

ZOORQ is the hosted Closed Source demo: <https://closed-source-quote-demo-ryoeyuqvta-el.a.run.app/demo>. It runs in a normal browser; nothing needs installing.

Everything below marked **seen** was checked on that URL on 16 October 2026 (desktop, rules extraction, no voice). Features marked **waiting** are built and tested on open pull requests but are not deployed yet; Divyanshu integrates and deploys them.

## What is live and what is waiting

| Feature | State on 16 Oct | Where |
|---|---|---|
| Company hub, ten fictional companies, light and dark appearance | Live (seen) | `/demo` |
| Overview: stock cover against supplier lead for the selected company | Live (seen) | Overview |
| News inbox: observed public headlines matched to company materials and routes, plus Global Watch | Live (seen) | News |
| Quote engine: read a sample quote, review each field with its evidence line, accept it | Live (seen, rules extraction) | Procurement, steps 01 and 02 |
| Comparison: single-supplier options, arrival, unit price, total, decision | Live (seen) | Procurement, step 03 |
| Supplier delay what-if and unsent RFQ draft | Live (visible; not exercised in this check) | Procurement, steps 03 and 04 |
| Drafts and activity: unsent drafts, simulated sending only | Live (seen) | Drafts & activity |
| Talk to ZOORQ / Type to ZOORQ (Gemini voice and text operator) | Live (not exercised in this check) | Header |
| Carbon components and catalogue coverage | Waiting: PR #114 | Procurement Compare, Emissions |
| Carbon programmes and green finance discovery | Waiting: PR #115 (after #114) | Emissions |
| Market reference: global benchmarks and synthetic origin quotes | Waiting: PR #116 | Compare, Market reference |
| Price and FX outlook with backtest | Waiting: PR #125 | Compare, Market reference |
| Synthetic dispatch for the selected company | Waiting: PR #117 (`/dispatch` returns 404 on the live site today) | Supply |
| Carbon, programmes and market answers in the operator chat and voice | Divyanshu's task (integration plan, Task 7) | Operator |

## Walkthrough (about 3 minutes, live features only)

| Step | Click | You should see | Say |
|---|---|---|---|
| 1 | Open `/demo`, choose **Solara Cables**, **Open workspace** | Overview: copper cathode needs review, 6 days of stock cover against a 10-day supplier lead | "ZOORQ starts from one company and shows which material needs attention." |
| 2 | **News** | Observed headlines matched to copper and the company's routes, with source and age | "These are real public headlines, ranked by how they touch this company's materials and routes." |
| 3 | **Procurement**, step 01: choose **Swift copper**, extraction **Rules**, **Read quote** | Field table with each value and the document line it came from | "Every value points to the line in the quote it came from; nothing is guessed." |
| 4 | **Accept reviewed quote**; repeat with **Europa copper** | Two accepted quotes | "Only reviewed quotes can be compared." |
| 5 | Step 03: tick both quotes, **Use company buyer inputs**, **Run comparison** | Europa Copper SA is feasible: 8,000 kg to buy, 82,000 USD selected total, Swift 86,000 USD | "A deterministic comparison: arrival, budget and currency are checked for each supplier." |
| 6 | Read the line under the table | "Europa Copper SA is selected in the current feasible plan. Review its unsent RFQ draft next." with a **Review RFQ draft** button | "The next step is an unsent draft for a person to review." |
| 7 | **Drafts & activity** | "No external delivery": prepared drafts are unsent and simulated records are kept separate | "Simulated sending is labelled and never reaches a supplier." |

## Limits to state

| Limit | What to say |
|---|---|
| Synthetic data | Companies, stock, orders, quotes, shipments and buyer inputs are synthetic. News headlines are observed public data. |
| Sessions | Work is kept in a browser session for a limited time; it is not a company system. |
| No real sending | RFQs and emails are drafts or simulations; nothing reaches a supplier and no order is placed. |
| Carbon | When deployed: estimates from published average factors, material and freight shown as separate components; not a footprint claim. |
| Programmes | When deployed: possible matches to check, never an eligibility decision or a guaranteed amount. |
| Price outlook | When deployed: projections with uncertainty, not a recommendation. Most series show a range or "No reliable forecast". |

## Also available locally

The engine and the older local pages (offers, dispatch, recovery, scenarios) still run on a laptop. From `procurement-engine`:

```bash
PYTHONPATH=src PROCURE_CONFIG_DIR=$PWD/config PROCURE_DATA_DIR=/tmp/cs-demo \
  DEMO_WEB_DIR=../apps/web/dist OFFLINE=1 .venv/bin/python -m uvicorn procure.demo.app:app --port 8090
```

Build the web app first (`cd apps/web && npm ci && npm run build`), then open <http://127.0.0.1:8090/demo>. `OFFLINE=1` turns off news refresh, Gemini and research, so use **Rules** extraction.
