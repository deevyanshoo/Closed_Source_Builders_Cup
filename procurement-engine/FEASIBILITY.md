# Feasibility report

Every number here was measured by running the prototype on 2 October 2026 with live data, unless marked otherwise. Nothing is estimated or hand-entered.

## 1. Data reality

| Source | Worked live? | Depth | Notes |
|---|---|---|---|
| World Bank Pink Sheet (monthly) | Yes | Jan 2010 to Aug 2026, 200 months per material | Latest file "Updated on September 02, 2026". Link discovered from the landing page; the URL in the brief is an older edition. Header row found by searching for "Copper", units on the next row, missing values shown as "…". Values checked: copper USD 14,326/t, aluminium USD 3,251/t, iron ore USD 96.3/dmt (Aug 2026). |
| Frankfurter USD/INR | Yes, both endpoints | 4,290 daily rates, Jan 2010 to Oct 2026 | Full range was not thinned. USD/INR in Aug 2026: 95.45. |
| GDELT DOC 2.0 | Mostly no | 14 days | HTTP 429 on most calls from this network, even with 5 to 30 second gaps. The one successful copper call returned 23 articles, almost all off topic (GDELT matches article bodies, so titles like "FAA adds record number of controller trainees" appear). |
| Google News RSS (added) | Yes | 14 days, 48 to 50 headlines per material | Relevant, current headlines (Chile strike votes at Escondida and Centinela, Indonesian aluminium expansion, weak Chinese iron ore demand). |

**Gaps.** Prices lag by about one month: on 2 October the latest price is August. The engine treats the latest month as "now" and says so. Monthly averages hide intra-month moves, which matter to a buyer who orders on a specific day. There is no Indian duty or freight data, so landed-cost inputs are demo assumptions.

**Fallbacks proven.** With `OFFLINE=1` on the real data directory, every source used its cache. With `OFFLINE=1` on an empty directory, prices and FX were synthetic and news was the bundled sample. In both cases the smoke test and the UI check passed, and the UI showed the demo-data banner.

## 2. Forecast reality

Walk-forward over the last 36 monthly origins (Aug 2023 to Jul 2026). Each origin sees only past data. MAPE is for the INR per kg price.

**Average over horizons 1 to 6 months**

| Material | Naive MAPE | ETS MAPE | GBM MAPE | Best vs naive | Model used | Naive band coverage |
|---|---|---|---|---|---|---|
| Copper | 7.72% | 7.63% | 7.70% | ETS 1.1% better | naive | 81.8% |
| Aluminium | 7.93% | 7.85% | 7.98% | ETS 0.9% better | naive | 85.1% |
| Iron ore | 7.79% | 7.97% | 8.45% | ETS 2.4% worse | naive | 99.5% |

**By horizon (copper, as an example)**: naive MAPE 3.8% at 1 month, 6.7% at 3 months, 11.9% at 6 months. Coverage 92% at 1 month, 82% at 3 months, 71% at 6 months.

ETS directional accuracy was 61% for copper and 62% for aluminium on average, but 42% for copper at 1 month and 48% for iron ore. This does not translate into lower error. GBM bands were too narrow (75% to 79% coverage for copper and aluminium).

**Verdict.** At monthly granularity, with only price history as input, these prices are not predictable beyond a random walk. No model cleared the 2% bar, so the engine uses the naive model everywhere and marks confidence LOW. That is the expected result for liquid commodity markets, not a failure of implementation. What the models *can* do reliably is size the uncertainty: after calibration on an earlier window, P10 to P90 bands held 82% (copper) and 85% (aluminium) of outcomes, close to the 80% target. Iron ore's bands are too wide (99.5%) because its calibration window (2021 to 2023) was much more volatile than the test period.

## 3. Decision value (regret meter)

Standard request every month: 100,000 kg needed in 3 months, 1 month lead time, medium risk, buy months 0 to 2, no news overlay. 36 origins, Jul 2023 to Jun 2026. Costs are landed INR at realised prices.

**Default policy (regret risk measure)**

| Material | Saved vs just in time | Avg saving | Months beating JIT | vs buy all now | vs even split | Worst month | Best month |
|---|---|---|---|---|---|---|---|
| Copper | INR 2.54 cr | +0.55% | 58% | INR -4.66 cr | INR -1.17 cr | INR -22.6 lakh (2025-02) | INR +66.2 lakh (2025-11) |
| Aluminium | INR 37.7 lakh | +0.32% | 64% | INR -77.4 lakh | INR -20.8 lakh | INR -17.3 lakh (2026-05) | INR +15.9 lakh (2026-02) |
| Iron ore | INR -2.0 lakh | -0.62% | 44% | INR +4.6 lakh | INR +1.3 lakh | INR -0.74 lakh (2024-01) | INR +0.53 lakh (2023-10) |

**Literal spec formula (overshoot risk measure)** always chose "buy 100% now": copper +INR 7.21 cr vs JIT (+1.51%), aluminium +INR 1.15 cr (+0.90%), iron ore -INR 6.6 lakh (-1.94%).

**Extra cost versus perfect hindsight** (average %, worst month %; lower is better)

| Material | Engine (regret) | Just in time | Buy all now | Even split |
|---|---|---|---|---|
| Copper | 3.39, 14.3 | 4.05, 20.7 | 2.21, 9.7 | 3.09, 10.0 |
| Aluminium | 3.60, 13.4 | 4.02, 18.5 | 2.80, 17.5 | 3.39, 10.0 |
| Iron ore | 3.38, **7.2** | 2.81, 11.4 | 4.63, 22.6 | 3.71, 11.6 |

**Where the value comes from.** Not from prediction: the model is a random walk. Copper and aluminium rose over the period and iron ore fell, so "buy all now" won on the first two and lost on the third. That is the trend, not skill. The engine's plans sit between the two extremes. It beat just in time on two of three materials, beat "buy all now" on one, and never had the worst average. Its value is hedging: it avoids being badly wrong in either direction, and on iron ore it had the smallest worst month of any plan (7.2% above hindsight against 11% to 23%). Even so, on copper and aluminium a plain even split had a smaller worst case. This is a modest, honest finding: the engine is a disciplined hedger, not a market-timer, and the rupee savings in this backtest are mostly luck of trend direction.

**A design finding.** With the literal score `E + lambda * (P90 - E)`, the risk term is linear in the share bought later, because today's price is known. The optimiser therefore always picks a corner, and with real data it said BUY NOW for every material and risk level. The regret measure (default) produces real splits that move with news and risk appetite. Both are kept and selectable.

## 4. News signal

Rules extractor on real headlines (no Gemini key was available, so the LLM path was tested only for its fallback):

| Material | Headlines | Marked relevant | News index |
|---|---|---|---|
| Copper | 48 | 21 (17 distinct stories) | +0.94 |
| Aluminium | 48 | 7 | +0.05 |
| Iron ore | 50 | 6 | -0.30 |

**Five real examples**

| Headline | Rules output | Verdict |
|---|---|---|
| Unions at Escondida, Centinela, Philex's Padcal copper mines vote for strike action | copper, up, 0.5 | Correct |
| China: Iron ore spot prices hit over 1-month low on weak demand, ample supply | iron ore, down, 0.8 | Correct |
| Indonesia's aluminium expansion set to add millions of tonnes to global supply | aluminium, down, 0.35 | Correct |
| KGHM Completes Maintenance And Modernization Shutdown Of Glogow Copper Smelter Ahead Of Schedule | copper, up, 0.5 | Wrong: the smelter is coming back, which is mildly bearish |
| Japanese smelter Furukawa plans 3% cut in second-half copper output | not relevant | Missed: an output cut is a supply signal |

Other failures seen: "Assaying Underway at Cowtrail and Lightning Strike" (an exploration project name) read as a strike. "Copper prices may surge another 50%, predicts Deutsche Bank" was missed (an opinion, arguably right to skip). Keyword rules are transparent and work for blunt headlines, but they miss negation, completion and phrasing. An LLM extractor is the obvious upgrade.

**Limits of the overlay.** The effect of news is a configured heuristic: drift shifts by index x 2% per month for the news horizon, and bands widen by 1 + 0.5 x |index|. It is not trained, because there is no labelled history of news and price moves. Two consequences showed up:

- **Saturation.** Copper coverage already holds the index at +0.94, so pasting "Major copper mine strike halts 10% of global supply" moves it only to +0.97 and the plan stays the same. The diff explains that the risk is already in the forecast. On aluminium, where coverage is calm (+0.05), the same kind of shock moved the index to +0.36, raised the expected price about 1.4%, and lifted the buy-now share from 20% to 40%.
- **Granularity.** A shift of 1% to 1.5% in expected price often does not cross the next 20% step in the schedule (iron ore export ban: +1.5%, plan unchanged).

## 5. Performance

Apple Silicon Mac, scikit-learn GBM backend.

| Step | Time |
|---|---|
| Prices and FX ingest (live) | 2.2 s |
| News ingest, 3 materials (live, GDELT rate-limited) | 45 s, nearly all spent waiting on GDELT retries |
| Full refresh with fresh news cache, including model evaluation | 12 to 14 s |
| Walk-forward evaluation, 3 models x 3 materials x 36 origins | 12 s (23 s on first run) |
| One recommendation (2,000 paths, all candidate schedules) | about 11 ms |
| Headline impact (two recommendations) | about 20 ms |
| Backtest, 36 months, naive model | 0.08 s per material (ETS: 0.5 to 2 s) |
| Test suite (62 tests, offline) | about 30 s |

## 6. Verdict

**Yes, the procurement engine is implementable for a hackathon, and it runs end to end on free, keyless data.**

- **Real:** the data pipeline (World Bank, ECB rates, live headlines, with provenance and fallbacks), INR landed-cost maths, calibrated uncertainty bands, the Monte Carlo decision engine, the leakage-free backtest, and the RFQ and audit workflow.
- **Heuristic:** the news effect (a configured drift shift, not learned), the rules extractor, the landed-cost rates, and the risk weights.
- **Not supported by the data:** any claim that the engine predicts price direction. With monthly prices alone it does not, and the product says so. The honest pitch is "a disciplined, explainable hedging assistant that turns news and uncertainty into a buying schedule", not "AI that beats the market".

## 7. Next steps

1. **Daily prices** (LME or MCX settlement) to cut the one-month lag and allow intra-month timing.
2. **Real Indian landed costs:** CBIC duty rates by HS code, freight quotes and the company's own cost of capital.
3. **A trained news effect:** archive headlines with an LLM extractor (Gemini with structured output), then estimate the price response from that history instead of a fixed 2% per month.
4. **A learned or tuned policy:** calibrate lambda and the risk measure to the company's real tolerance using the regret meter, then try reinforcement learning or stochastic programming over rolling multi-period needs.
5. **More materials and contracts:** steel HRC, nickel, zinc and plastics through `config/materials.yaml`, plus supplier price terms (fixed versus index-linked) as decision options.
