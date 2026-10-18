# Price outlook backtest, version 1

Built 2026-10-16 by `procurement-engine/scripts/build_price_predictions.py` from `config/prediction_history_v1.json` (World Bank Pink Sheet and BIS monthly averages, 2016-09 to 2026-08, month m usable from the end of m+1). Test split: the final 24 target months (2024-09 to 2026-08), never used for model choice or interval calibration. Skill, bootstrap interval and coverage come from `scripts/evaluate_forecasts.py` (forecast-scorecard.v1) on the test split; MAPE and directional accuracy from `select.metrics_from`.

Pre-registered statuses: **PREDICTION** needs skill vs naive of at least 0.02, a bootstrap interval lower bound above 0, P10 to P90 coverage 70 to 90% and at least 18 test origins; **RANGE_ONLY** needs the calibrated naive band to cover 70 to 90%; otherwise **NO_RELIABLE_FORECAST**.

| Series | Horizon | Selected model | MAPE | Naive MAPE | MAPE ratio | Skill | Bootstrap CI (abs. error reduction) | Coverage | Naive coverage | Directional | Origins | Status |
|---|---:|---|---:|---:|---:|---:|---|---:|---:|---:|---:|---|
| commodity:aluminium | 1 | naive |  | 6.1% |  |  |  |  | 79.2% |  | 24 | RANGE_ONLY |
| commodity:aluminium | 3 | naive |  | 8.0% |  |  |  |  | 83.3% |  | 24 | RANGE_ONLY |
| commodity:aluminium | 6 | naive |  | 9.9% |  |  |  |  | 66.7% |  | 24 | NO_RELIABLE_FORECAST |
| commodity:copper | 1 | naive |  | 5.0% |  |  |  |  | 79.2% |  | 24 | RANGE_ONLY |
| commodity:copper | 3 | naive |  | 7.3% |  |  |  |  | 83.3% |  | 24 | RANGE_ONLY |
| commodity:copper | 6 | naive |  | 11.6% |  |  |  |  | 62.5% |  | 24 | NO_RELIABLE_FORECAST |
| commodity:cotton | 1 | naive |  | 3.4% |  |  |  |  | 91.7% |  | 24 | NO_RELIABLE_FORECAST |
| commodity:cotton | 3 | naive |  | 5.4% |  |  |  |  | 83.3% |  | 24 | RANGE_ONLY |
| commodity:cotton | 6 | naive |  | 8.6% |  |  |  |  | 95.8% |  | 24 | NO_RELIABLE_FORECAST |
| commodity:nickel | 1 | naive |  | 4.3% |  |  |  |  | 91.7% |  | 24 | NO_RELIABLE_FORECAST |
| commodity:nickel | 3 | naive |  | 5.9% |  |  |  |  | 100.0% |  | 24 | NO_RELIABLE_FORECAST |
| commodity:nickel | 6 | naive |  | 8.7% |  |  |  |  | 100.0% |  | 24 | NO_RELIABLE_FORECAST |
| commodity:sugar | 1 | naive |  | 5.3% |  |  |  |  | 95.8% |  | 24 | NO_RELIABLE_FORECAST |
| commodity:sugar | 3 | naive |  | 8.4% |  |  |  |  | 100.0% |  | 24 | NO_RELIABLE_FORECAST |
| commodity:sugar | 6 | naive |  | 11.7% |  |  |  |  | 100.0% |  | 24 | NO_RELIABLE_FORECAST |
| commodity:sunflower_oil | 1 | naive |  | 4.0% |  |  |  |  | 87.5% |  | 24 | RANGE_ONLY |
| commodity:sunflower_oil | 3 | naive |  | 7.1% |  |  |  |  | 91.7% |  | 24 | NO_RELIABLE_FORECAST |
| commodity:sunflower_oil | 6 | naive |  | 12.7% |  |  |  |  | 87.5% |  | 24 | RANGE_ONLY |
| commodity:wheat | 1 | naive |  | 5.0% |  |  |  |  | 79.2% |  | 24 | RANGE_ONLY |
| commodity:wheat | 3 | naive |  | 7.0% |  |  |  |  | 70.8% |  | 24 | RANGE_ONLY |
| commodity:wheat | 6 | naive |  | 10.0% |  |  |  |  | 75.0% |  | 24 | RANGE_ONLY |
| fx:AED | 1 | naive |  | 0.0% |  |  |  |  | 100.0% |  | 24 | NO_RELIABLE_FORECAST |
| fx:AED | 3 | naive |  | 0.0% |  |  |  |  | 100.0% |  | 24 | NO_RELIABLE_FORECAST |
| fx:AED | 6 | naive |  | 0.0% |  |  |  |  | 100.0% |  | 24 | NO_RELIABLE_FORECAST |
| fx:BDT | 1 | ridge | 1.2% | 0.7% | 1.675 | -0.678 | -1.219 to -0.004737 | 0.0% | 100.0% | 79.2% | 24 | NO_RELIABLE_FORECAST |
| fx:BDT | 3 | ridge | 2.2% | 1.3% | 1.733 | -0.743 | -2.802 to 0.403 | 0.0% | 100.0% | 83.3% | 24 | NO_RELIABLE_FORECAST |
| fx:BDT | 6 | ridge | 4.1% | 2.6% | 1.560 | -0.573 | -5.158 to 1.015 | 0.0% | 87.5% | 87.5% | 24 | RANGE_ONLY |
| fx:BWP | 1 | naive |  | 1.5% |  |  |  |  | 79.2% |  | 24 | RANGE_ONLY |
| fx:BWP | 3 | naive |  | 1.9% |  |  |  |  | 95.8% |  | 24 | NO_RELIABLE_FORECAST |
| fx:BWP | 6 | naive |  | 2.2% |  |  |  |  | 100.0% |  | 24 | NO_RELIABLE_FORECAST |
| fx:CHF | 1 | naive |  | 2.4% |  |  |  |  | 83.3% |  | 24 | RANGE_ONLY |
| fx:CHF | 3 | naive |  | 3.9% |  |  |  |  | 79.2% |  | 24 | RANGE_ONLY |
| fx:CHF | 6 | naive |  | 4.4% |  |  |  |  | 83.3% |  | 24 | RANGE_ONLY |
| fx:CNY | 1 | gbm | 1.2% | 1.0% | 1.207 | -0.211 | -0.02702 to -0.0009152 | 83.3% | 100.0% | 54.2% | 24 | NO_RELIABLE_FORECAST |
| fx:CNY | 3 | gbm | 2.8% | 1.7% | 1.610 | -0.608 | -0.107 to -0.03789 | 58.3% | 100.0% | 25.0% | 24 | NO_RELIABLE_FORECAST |
| fx:CNY | 6 | gbm | 4.6% | 2.3% | 2.017 | -1.014 | -0.228 to -0.07303 | 50.0% | 100.0% | 33.3% | 24 | NO_RELIABLE_FORECAST |
| fx:EUR | 1 | naive |  | 2.2% |  |  |  |  | 79.2% |  | 24 | RANGE_ONLY |
| fx:EUR | 3 | naive |  | 3.2% |  |  |  |  | 75.0% |  | 24 | RANGE_ONLY |
| fx:EUR | 6 | naive |  | 3.7% |  |  |  |  | 83.3% |  | 24 | RANGE_ONLY |
| fx:INR | 1 | ets | 1.3% | 1.4% | 0.929 | 0.073 | -0.1738 to 0.352 | 50.0% | 45.8% | 79.2% | 24 | NO_RELIABLE_FORECAST |
| fx:INR | 3 | ets | 2.0% | 2.5% | 0.823 | 0.183 | -0.3327 to 0.8304 | 45.8% | 33.3% | 75.0% | 24 | NO_RELIABLE_FORECAST |
| fx:INR | 6 | ets | 2.5% | 3.5% | 0.700 | 0.295 | 0.503 to 1.231 | 58.3% | 41.7% | 87.5% | 24 | NO_RELIABLE_FORECAST |
| fx:JPY | 1 | ridge | 2.4% | 2.5% | 0.978 | 0.022 | -0.2372 to 0.4724 | 0.0% | 91.7% | 66.7% | 24 | NO_RELIABLE_FORECAST |
| fx:JPY | 3 | ridge | 4.1% | 3.8% | 1.093 | -0.089 | -1.093 to 1.095 | 0.0% | 100.0% | 50.0% | 24 | NO_RELIABLE_FORECAST |
| fx:JPY | 6 | ridge | 5.8% | 3.7% | 1.577 | -0.556 | -7.019 to 1.608 | 0.0% | 100.0% | 54.2% | 24 | NO_RELIABLE_FORECAST |
| fx:KES | 1 | ets | 0.3% | 0.4% | 0.709 | 0.291 | 0.06755 to 0.2745 | 100.0% | 100.0% | 45.8% | 24 | NO_RELIABLE_FORECAST |
| fx:KES | 3 | ets | 0.7% | 0.8% | 0.887 | 0.113 | -0.1034 to 0.4153 | 100.0% | 100.0% | 33.3% | 24 | NO_RELIABLE_FORECAST |
| fx:KES | 6 | ets | 3.1% | 2.1% | 1.445 | -0.445 | -3.074 to 0.4678 | 87.5% | 95.8% | 37.5% | 24 | NO_RELIABLE_FORECAST |
| fx:KRW | 1 | gbm | 3.0% | 2.5% | 1.180 | -0.175 | -15.56 to 1.554 | 79.2% | 79.2% | 54.2% | 24 | RANGE_ONLY |
| fx:KRW | 3 | gbm | 3.9% | 3.5% | 1.109 | -0.106 | -13.27 to 7.26 | 87.5% | 91.7% | 54.2% | 24 | NO_RELIABLE_FORECAST |
| fx:KRW | 6 | gbm | 3.3% | 4.0% | 0.831 | 0.176 | 1.758 to 33.51 | 100.0% | 100.0% | 75.0% | 24 | NO_RELIABLE_FORECAST |
| fx:MXN | 1 | naive |  | 2.3% |  |  |  |  | 87.5% |  | 24 | RANGE_ONLY |
| fx:MXN | 3 | naive |  | 4.5% |  |  |  |  | 87.5% |  | 24 | RANGE_ONLY |
| fx:MXN | 6 | naive |  | 8.3% |  |  |  |  | 75.0% |  | 24 | RANGE_ONLY |
| fx:MYR | 1 | naive |  | 2.3% |  |  |  |  | 95.8% |  | 24 | NO_RELIABLE_FORECAST |
| fx:MYR | 3 | naive |  | 4.0% |  |  |  |  | 83.3% |  | 24 | RANGE_ONLY |
| fx:MYR | 6 | naive |  | 5.3% |  |  |  |  | 91.7% |  | 24 | NO_RELIABLE_FORECAST |
| fx:NAD | 1 | naive |  | 2.5% |  |  |  |  | 87.5% |  | 24 | RANGE_ONLY |
| fx:NAD | 3 | naive |  | 3.6% |  |  |  |  | 87.5% |  | 24 | RANGE_ONLY |
| fx:NAD | 6 | naive |  | 4.9% |  |  |  |  | 87.5% |  | 24 | RANGE_ONLY |
| fx:OMR | 1 | naive |  | 0.0% |  |  |  |  | 100.0% |  | 24 | NO_RELIABLE_FORECAST |
| fx:OMR | 3 | naive |  | 0.0% |  |  |  |  | 100.0% |  | 24 | NO_RELIABLE_FORECAST |
| fx:OMR | 6 | naive |  | 0.0% |  |  |  |  | 100.0% |  | 24 | NO_RELIABLE_FORECAST |
| fx:SGD | 1 | ridge | 1.3% | 1.4% | 0.897 | 0.103 | 0.000228 to 0.005504 | 0.0% | 79.2% | 75.0% | 24 | RANGE_ONLY |
| fx:SGD | 3 | ridge | 1.8% | 2.1% | 0.867 | 0.130 | 0.001011 to 0.008937 | 0.0% | 79.2% | 70.8% | 24 | RANGE_ONLY |
| fx:SGD | 6 | ridge | 1.6% | 1.9% | 0.821 | 0.175 | -0.0008774 to 0.01141 | 0.0% | 91.7% | 70.8% | 24 | NO_RELIABLE_FORECAST |
| fx:ZAR | 1 | ridge | 2.9% | 2.5% | 1.188 | -0.182 | -0.1731 to 0.03133 | 0.0% | 87.5% | 29.2% | 24 | RANGE_ONLY |
| fx:ZAR | 3 | ridge | 4.6% | 3.5% | 1.308 | -0.298 | -0.3495 to 0.02174 | 0.0% | 87.5% | 29.2% | 24 | RANGE_ONLY |
| fx:ZAR | 6 | ridge | 7.2% | 4.8% | 1.497 | -0.488 | -0.6172 to -0.05783 | 0.0% | 87.5% | 20.8% | 24 | RANGE_ONLY |

**Status counts over 69 series-horizon rows:** PREDICTION 0, RANGE_ONLY 31, NO_RELIABLE_FORECAST 38.

**Model selected on the validation split, per series:** ets 2, gbm 2, naive 15, ridge 4.

**No history available:** fx:TZS.

These are backtest results on latest-vintage public data, not point-in-time publication vintages. The ridge candidate is a point forecast (P10 = P50 = P90), so it cannot meet the coverage rule. Nothing here is a purchasing recommendation.
