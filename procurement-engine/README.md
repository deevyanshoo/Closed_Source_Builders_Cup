# AI Procurement Engine (prototype)

A working prototype that tells an Indian manufacturer **when to buy** imported copper, aluminium and iron ore, **how much**, and **why**. It pays in USD and budgets in INR, so every number is shown in INR per kg.

It climbs the full value ladder:

| Level | What the prototype produces |
|---|---|
| Data | Monthly World Bank prices, ECB USD/INR, live news headlines, with provenance |
| Insight | Forecast bands (P10, P50, P90) in INR per kg, a "price weather" line, a news risk index |
| Recommendation | Buy now, wait, or a split schedule chosen on 2,000 simulated price paths, with a why-panel |
| Action | RFQ email drafts that a person approves or rejects, all stored in an audit log |

The goal is to test **feasibility**: can open data plus simple, honest models give useful buying advice? The honest answer is in [FEASIBILITY.md](FEASIBILITY.md). Design choices and fallbacks are in [DECISIONS.md](DECISIONS.md).

## Quick start

Needs Python 3.10 or newer (tested on 3.13). No API keys are required.

**macOS / Linux**

```bash
cd procurement-engine
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pip install -e .
.venv/bin/python scripts/run_all.py
```

**Windows (PowerShell)**

```powershell
cd procurement-engine
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m pip install -e .
.venv\Scripts\python scripts\run_all.py
```

Then open **http://localhost:8501** (UI). The API and its interactive docs are at **http://localhost:8000/docs**.

The first start downloads data and tests the forecast models. That takes about 30 to 60 seconds (most of it is waiting politely on the GDELT news API). Later starts are instant.

`make setup`, `make run`, `make test` and `make smoke` do the same on macOS and Linux.

### Add a Gemini key (optional)

Copy `.env.example` to `.env` and set `GEMINI_API_KEY=...`. Headlines are then read by Gemini with a strict JSON schema. If the key is missing or any call fails, the keyword rules take over automatically. Click **Refresh data** in the Data status tab to re-read headlines with Gemini.

### Run offline

```bash
OFFLINE=1 .venv/bin/python scripts/run_all.py          # macOS / Linux
$env:OFFLINE=1; .venv\Scripts\python scripts\run_all.py  # Windows PowerShell
```

With `OFFLINE=1` no network call is made. Each source falls back to the latest cached file in `data/raw/`, and if there is none, to calibrated synthetic prices and bundled sample headlines. The UI shows a banner whenever synthetic or sample data is in use.

### Tests and checks

```bash
.venv/bin/python -m pytest                 # 62 unit and API tests, run offline on an isolated database
.venv/bin/python scripts/smoke_test.py     # end to end against the running API
.venv/bin/python scripts/ui_check.py       # headless click-through of every UI tab
```

Note: DuckDB allows one process to hold the database file. Stop the API before running scripts that open `data/app.duckdb` directly (`python -m procure.ingest.refresh`). Tests use their own temporary database, so they can run while the app is up.

## Project structure

```
procurement-engine/
  config/materials.yaml      materials, source series, news keywords (add a material here)
  config/assumptions.yaml    duty, freight, holding cost, risk weights, news heuristic (demo values)
  data/raw/                  cached downloads and the bundled sample_news.json
  data/app.duckdb            database (created on first run)
  src/procure/
    ingest/                  prices.py (World Bank), fx.py (Frankfurter), news.py (GDELT, Google News RSS),
                             synthetic.py, refresh.py (orchestration and provenance)
    features.py              INR per kg series, returns, volatility, momentum
    forecast/                models.py (naive, ETS, quantile GBM), select.py (walk-forward), simulate.py (Monte Carlo)
    news_signal.py           headline extraction (Gemini or rules) and the news risk index
    market.py                gathers data, model choice, news and simulation for one material
    landed_cost.py           price, duty, freight, holding cost
    decision.py              schedules, scoring, recommendation, why-panel, headline impact
    backtest.py              regret meter
    actions.py               RFQ drafts, approve or reject, audit log
    api.py                   FastAPI app
  ui/app.py                  Streamlit UI
  scripts/                   run_all.py, smoke_test.py, ui_check.py
  tests/                     pytest suite
```

## Walkthrough of the UI

1. **Overview.** One card per material: today's INR per kg, 1 and 3 month change, the price weather line ("49% chance of a price rise above 5% in 3 months"), a news risk gauge from -1 (prices may ease) to +1 (supply shock), and badges showing whether prices and news are Live, Cached, Synthetic or Sample.
2. **Plan a purchase.** Enter quantity, when you need it, supplier lead time, stock, monthly use and risk appetite. You get a big action label (BUY NOW, SPLIT BUY or WAIT), the buying schedule, a comparison with three simple plans (buy all now, just in time, even split), a fan chart with the planned buys marked, a why-panel listing every signal and number behind the advice, a confidence flag, and a **Draft RFQ** button.
3. **Headline to impact.** Paste a headline or click an example. The engine shows what it read (material, direction, severity, horizon), then the plan before and after, side by side, with a plain-language explanation of what changed.
4. **Regret meter.** Replays the engine over the last 36 months using only the data available each month, and prices its plans at the prices that really happened. Shows rupees saved or lost against the three simple plans, the share of months it won, the best and worst month, a cumulative savings chart, and the extra cost versus perfect hindsight.
5. **Actions and audit.** RFQ drafts with Approve and Reject (with a reason). No email is ever sent. The audit log lists every recommendation and action with time stamps and input snapshots.
6. **Data status.** Which source each input came from, when, and how many rows, plus the forecast accuracy table (each model against the naive baseline for horizons 1 to 6) and a **Refresh data** button.

## Data sources

| Input | Source | Fallbacks |
|---|---|---|
| Commodity prices (monthly) | World Bank Pink Sheet, `CMO-Historical-Data-Monthly.xlsx` | latest cached xlsx, then synthetic |
| USD/INR (daily, averaged monthly) | Frankfurter (ECB reference rates) | latest cached JSON, then synthetic |
| News (last 14 days) | GDELT DOC 2.0 plus Google News RSS search | cache under 6 hours, older cache, then bundled samples |

All are keyless. Raw responses are cached in `data/raw/` with a timestamp, and every ingest run is logged with the source used.
