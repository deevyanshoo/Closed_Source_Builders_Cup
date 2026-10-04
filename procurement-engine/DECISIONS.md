# Decisions and fallbacks

Every non-obvious choice made while building the prototype, and why.

## Environment

| Decision | Why |
|---|---|
| Python 3.13 venv, not the system default 3.14 | 3.14 was released very recently and some scientific wheels may lag. 3.13 has wheels for every dependency. |
| **LightGBM fallback taken: scikit-learn `GradientBoostingRegressor(loss="quantile")`** | `lightgbm` installs but fails to load on this Mac: `libomp.dylib` is missing. I did not install system packages (Homebrew). The code tries LightGBM first and falls back automatically; `/health` reports `gbm_backend`. Installing `libomp` (`brew install libomp`) switches it back with no code change. |
| DuckDB (SQLite fallback coded but not needed) | DuckDB installed fine. The DB layer uses only portable SQL and ISO text dates so `sqlite3` works if DuckDB is unavailable. |
| Package installed in editable mode (`pip install -e .`) | Lets the API, UI, scripts and tests import `procure` without path hacks on any OS. |
| Versions pinned in `requirements.txt` after a successful install | As requested. |

## Data

| Decision | Why |
|---|---|
| Pink Sheet link discovered from the landing page, known URL as second try | The live page linked a newer file (`...-0050012026/...`) than the URL in the brief, which shows why discovery comes first. |
| Header row found by searching for `Copper`; the units row is the next row; `…` is treated as missing | Matches the real sheet layout (title rows, then names, then units). Values were checked: copper USD 14,326/t, aluminium USD 3,251/t, iron ore USD 96.3/dmt for Aug 2026. A sanity check warns if a median falls outside a plausible range. |
| Frankfurter: full range first, year by year if the response is thinned | The full range came back with 4,290 daily rates, so year by year was not needed, but the detection is in place and tested. |
| Only months present in both price and FX tables are used | FX runs to the current month (Oct 2026); Pink Sheet prices run to Aug 2026. |
| **The latest month of price data is treated as "now"** | The Pink Sheet is published with a lag of about one month. Month 0 is priced at the latest monthly average (Aug 2026); the UI says so. |
| **News: GDELT plus Google News RSS, merged** | GDELT returned HTTP 429 for most calls from this network even with 5+ second gaps, and when it did answer for copper, most titles were off topic (GDELT matches article bodies). Google News RSS search is also keyless, matches titles, and returned 48 to 50 current headlines per material. Both are queried and merged; either counts as "live". After a GDELT rate limit it is skipped for 10 minutes so a refresh does not stall. |
| Sample headlines avoid naming real companies | They are fictional and labelled `is_sample: true`. Inventing events about real firms would be misleading. |
| `smelter` removed from the copper detection keywords (kept in the search query) | It made an aluminium smelter headline count as copper. |
| Synthetic series: mean-reverting GBM with fixed seed, soft-clipped to the brief's ranges | Only used when live and cache both fail. Every row is flagged `is_synthetic`. |

## Forecasting

| Decision | Why |
|---|---|
| One model type per material, applied to both the commodity and USD/INR series | Keeps selection simple and the INR price coherent. |
| All models share one interface: a median drift path plus residuals for bootstrapping | The Monte Carlo engine and the evaluator treat every model the same way, so the comparison is fair. |
| Uncertainty bands for every model come from bootstrapped same-month (commodity, FX) residual pairs | Preserves the historical correlation between metal prices and the rupee, as required. |
| Quantile GBM: direct P50 model per horizon; P10 and P90 at 1 month scale the noise (clipped 0.5 to 2) | Uses the quantile heads for conditional volatility without a separate simulator. GBM residuals for bootstrapping are the demeaned historical returns, because in-sample GBM residuals are too small. |
| GBM refits every 6 months during walk-forward, using only data up to that point | Refitting at every origin took 60 s per material on scikit-learn; this took the whole evaluation down to about 12 to 23 s with no leakage. |
| Residual window of 120 months | Long enough for stable bands, short enough to reflect recent behaviour. |
| **Band calibration chosen on the 24 months before the 36-month test window** | The brief asks for about 80% coverage and recalibration if far off. Calibrating on the test window would leak. The chosen scale (copper 1.35, aluminium 1.5, iron ore 1.1) is then tested out of sample. Iron ore ends up too wide (99.5% coverage) because its calibration window was more volatile than 2023 to 2026. Reported, not tuned away. |
| Directional accuracy is not reported for naive | A zero-drift forecast makes no directional call. |
| Selection: lowest average MAPE over horizons 1 to 6, and it must beat naive by 2% (relative) | As specified. Naive won for all three materials. |

## Decision engine

| Decision | Why |
|---|---|
| **Default risk term is P90 regret, not P90 minus mean (configurable: `risk.measure`)** | With the literal formula `E + lambda * (P90 - E)`, month 0 has no uncertainty, so the risk term is exactly linear in the share bought later. The optimiser then always picks a corner: BUY NOW for every material and every risk level with real data (WAIT once, with no news and high appetite). That makes the engine uninformative. The regret measure is the P90 of (plan cost minus the cheapest single-month plan on the same path), which is what a procurement manager is judged against in hindsight and what the regret meter measures. It penalises both "bought, then the price fell" and "waited, then it rose", so it hedges with genuine splits that respond to news and risk appetite. The literal formula is kept as `overshoot`, selectable per request and in the regret meter, and both are backtested in FEASIBILITY.md. |
| Holding cost charged on the INR price for each month a lot is bought before the just-in-time month (`need_by - lead_time`) | "Bought before it is needed" given the lead time. Just-in-time lots carry no holding cost. |
| Stockout penalty = shortfall kg x current landed price x 10 | Large but finite, so if every plan runs short the engine still prefers the earliest delivery. |
| Optimiser chooses among the 20% grid; the even split is reported as a baseline only | The even split is often not on the 20% grid (33.3%). |
| Price weather = probability that the INR price at the need-by month is more than 5% above today | "Within the window" read as "by the end of it", like a forecast. |
| Confidence is LOW whenever no model beats naive | As specified. With real data this is always the case today, and the reason is shown. |

## News overlay

| Decision | Why |
|---|---|
| News index = tanh(sum of direction x severity x 0.5^(age/7 days) / 3) | A squashed sum stays within -1 to +1 without hard clipping. |
| **Syndicated duplicates count once** (token Jaccard 0.3 or more) | Many outlets carry the same story. Without this, copper sat at +0.98. With it, +0.94: the coverage really is strongly bullish (several Chile strike stories). |
| A pasted headline is added at full freshness, exactly like a fetched one | As specified. Consequence: when real coverage has already pushed the index near its cap (copper), one more headline adds little, and the diff text says so. |
| Overlay horizon = contribution-weighted average horizon of the stories | The brief says "for the headline's horizon"; with many headlines this is their weighted average. |
| Rules lexicon extended after reading real headlines | Added phrases such as "supply crunch", "production cuts", "weak demand", "ample supply". |

## Product and safety

| Decision | Why |
|---|---|
| RFQ addressed to "Supplier team", signed "Procurement team" plus `COMPANY_NAME` if set | No placeholders. Nothing is ever emailed. |
| Approve or reject only from `draft`; a second decision returns 409 | Keeps the audit trail unambiguous. |
| UI shows the synthetic or sample banner above the tabs | Visible whichever tab is open. |
| Large rupee amounts shown in lakh and crore | Matches how the persona reads money. |
