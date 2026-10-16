# Forecast scorecard

Stage 1: pure offline metric infrastructure over caller-supplied frozen predictions. No observed-data eligibility, leakage-free protocol, model training, forecast accuracy, or deployment is established by this run.

Metadata is caller-supplied and unverified. No source publication vintages are verified; overlapping targets and small samples limit uncertainty interpretation.

Input SHA-256: `42c582be9ffc762b15182e320d23a9eebf5520bfdd41c76a09b526d50b199745`. Rows: 13248. Groups: 552.

The CSV reports MAE/RMSE in original units, training-scale MASE when defined, pinball loss, inclusive interval coverage/width, strict-direction hit rate, and naive skill on matched test cells only. Missing matches and undefined denominators remain explicit. Bootstrap intervals are paired absolute-error reduction over chronological origin blocks of six months.

| Kind | Series | Split | Model / backend | Horizon | Rows / origins | MAE | MASE | 80% coverage | Naive matched / missing | Skill |
|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| commodity | commodity:aluminium | test | ets / statsmodels-holt-damped-w60 | 1 | 24 / 24 | 202.9 | 2.466 | 0.75 | 24 / 0 | -0.1278 |
| commodity | commodity:aluminium | test | ets / statsmodels-holt-damped-w60 | 3 | 24 / 24 | 293.2 | 3.592 | 0.8333 | 24 / 0 | -0.2122 |
| commodity | commodity:aluminium | test | ets / statsmodels-holt-damped-w60 | 6 | 24 / 24 | 396.2 | 4.883 | 0.7083 | 24 / 0 | -0.3008 |
| commodity | commodity:aluminium | test | gbm / sklearn-quantile-gbm-w60 | 1 | 24 / 24 | 193.8 | 2.357 | 0.7083 | 24 / 0 | -0.07695 |
| commodity | commodity:aluminium | test | gbm / sklearn-quantile-gbm-w60 | 3 | 24 / 24 | 256.6 | 3.156 | 0.6667 | 24 / 0 | -0.06108 |
| commodity | commodity:aluminium | test | gbm / sklearn-quantile-gbm-w60 | 6 | 24 / 24 | 351.7 | 4.342 | 0.625 | 24 / 0 | -0.1548 |
| commodity | commodity:aluminium | test | naive / persistence-bootstrap-w60 | 1 | 24 / 24 | 179.9 | 2.189 | 0.7917 | 0 / 0 | undefined (not_applicable) |
| commodity | commodity:aluminium | test | naive / persistence-bootstrap-w60 | 3 | 24 / 24 | 241.9 | 2.966 | 0.8333 | 0 / 0 | undefined (not_applicable) |
| commodity | commodity:aluminium | test | naive / persistence-bootstrap-w60 | 6 | 24 / 24 | 304.6 | 3.759 | 0.6667 | 0 / 0 | undefined (not_applicable) |
| commodity | commodity:aluminium | test | ridge / monthly-projection-ridge-alpha10-w60 | 1 | 24 / 24 | 184.4 | 2.246 | 0 | 24 / 0 | -0.02469 |
| commodity | commodity:aluminium | test | ridge / monthly-projection-ridge-alpha10-w60 | 3 | 24 / 24 | 243.8 | 2.995 | 0 | 24 / 0 | -0.008087 |
| commodity | commodity:aluminium | test | ridge / monthly-projection-ridge-alpha10-w60 | 6 | 24 / 24 | 312.4 | 3.853 | 0 | 24 / 0 | -0.02559 |
| commodity | commodity:aluminium | validation | ets / statsmodels-holt-damped-w60 | 1 | 24 / 24 | 131.7 | 1.583 | 0.7083 | 0 / 0 | undefined (not_applicable) |
| commodity | commodity:aluminium | validation | ets / statsmodels-holt-damped-w60 | 3 | 24 / 24 | 224.7 | 2.687 | 0.625 | 0 / 0 | undefined (not_applicable) |
| commodity | commodity:aluminium | validation | ets / statsmodels-holt-damped-w60 | 6 | 24 / 24 | 410.9 | 5.2 | 0.7083 | 0 / 0 | undefined (not_applicable) |
| commodity | commodity:aluminium | validation | gbm / sklearn-quantile-gbm-w60 | 1 | 24 / 24 | 131.9 | 1.58 | 0.6667 | 0 / 0 | undefined (not_applicable) |
| commodity | commodity:aluminium | validation | gbm / sklearn-quantile-gbm-w60 | 3 | 24 / 24 | 230.9 | 2.767 | 0.5417 | 0 / 0 | undefined (not_applicable) |
| commodity | commodity:aluminium | validation | gbm / sklearn-quantile-gbm-w60 | 6 | 24 / 24 | 471.6 | 5.955 | 0.5833 | 0 / 0 | undefined (not_applicable) |
| commodity | commodity:aluminium | validation | naive / persistence-bootstrap-w60 | 1 | 24 / 24 | 111.3 | 1.335 | 0.9167 | 0 / 0 | undefined (not_applicable) |
| commodity | commodity:aluminium | validation | naive / persistence-bootstrap-w60 | 3 | 24 / 24 | 160.7 | 1.925 | 0.8333 | 0 / 0 | undefined (not_applicable) |
| commodity | commodity:aluminium | validation | naive / persistence-bootstrap-w60 | 6 | 24 / 24 | 282.4 | 3.563 | 0.875 | 0 / 0 | undefined (not_applicable) |
| commodity | commodity:aluminium | validation | ridge / monthly-projection-ridge-alpha10-w60 | 1 | 24 / 24 | 113 | 1.355 | 0 | 0 / 0 | undefined (not_applicable) |
| commodity | commodity:aluminium | validation | ridge / monthly-projection-ridge-alpha10-w60 | 3 | 24 / 24 | 169.3 | 2.024 | 0 | 0 / 0 | undefined (not_applicable) |
| commodity | commodity:aluminium | validation | ridge / monthly-projection-ridge-alpha10-w60 | 6 | 24 / 24 | 319.8 | 4.047 | 0 | 0 / 0 | undefined (not_applicable) |
| commodity | commodity:copper | test | ets / statsmodels-holt-damped-w60 | 1 | 24 / 24 | 579.6 | 2.14 | 0.9167 | 24 / 0 | -0.0253 |
| commodity | commodity:copper | test | ets / statsmodels-holt-damped-w60 | 3 | 24 / 24 | 865.9 | 3.229 | 0.7917 | 24 / 0 | -0.01376 |
| commodity | commodity:copper | test | ets / statsmodels-holt-damped-w60 | 6 | 24 / 24 | 1473 | 5.54 | 0.6667 | 24 / 0 | -0.0744 |
| commodity | commodity:copper | test | gbm / sklearn-quantile-gbm-w60 | 1 | 24 / 24 | 672.3 | 2.487 | 0.7083 | 24 / 0 | -0.1894 |
| commodity | commodity:copper | test | gbm / sklearn-quantile-gbm-w60 | 3 | 24 / 24 | 1234 | 4.57 | 0.7917 | 24 / 0 | -0.4445 |
| commodity | commodity:copper | test | gbm / sklearn-quantile-gbm-w60 | 6 | 24 / 24 | 1767 | 6.635 | 0.5417 | 24 / 0 | -0.289 |
| commodity | commodity:copper | test | naive / persistence-bootstrap-w36 | 1 | 24 / 24 | 565.3 | 2.087 | 0.7917 | 0 / 0 | undefined (not_applicable) |
| commodity | commodity:copper | test | naive / persistence-bootstrap-w36 | 3 | 24 / 24 | 854.1 | 3.167 | 0.8333 | 0 / 0 | undefined (not_applicable) |
| commodity | commodity:copper | test | naive / persistence-bootstrap-w36 | 6 | 24 / 24 | 1371 | 5.139 | 0.625 | 0 / 0 | undefined (not_applicable) |
| commodity | commodity:copper | test | ridge / monthly-projection-ridge-alpha10-w60 | 1 | 24 / 24 | 593.9 | 2.195 | 0 | 24 / 0 | -0.05067 |
| commodity | commodity:copper | test | ridge / monthly-projection-ridge-alpha10-w60 | 3 | 24 / 24 | 927.6 | 3.444 | 0 | 24 / 0 | -0.08602 |
| commodity | commodity:copper | test | ridge / monthly-projection-ridge-alpha10-w60 | 6 | 24 / 24 | 1451 | 5.436 | 0 | 24 / 0 | -0.05786 |
| commodity | commodity:copper | validation | ets / statsmodels-holt-damped-w60 | 1 | 24 / 24 | 598.1 | 2.349 | 0.75 | 0 / 0 | undefined (not_applicable) |
| commodity | commodity:copper | validation | ets / statsmodels-holt-damped-w60 | 3 | 24 / 24 | 828.2 | 3.288 | 0.6667 | 0 / 0 | undefined (not_applicable) |
| commodity | commodity:copper | validation | ets / statsmodels-holt-damped-w60 | 6 | 24 / 24 | 1187 | 4.835 | 0.7083 | 0 / 0 | undefined (not_applicable) |
| commodity | commodity:copper | validation | gbm / sklearn-quantile-gbm-w60 | 1 | 24 / 24 | 513.5 | 2.019 | 0.5417 | 0 / 0 | undefined (not_applicable) |
| commodity | commodity:copper | validation | gbm / sklearn-quantile-gbm-w60 | 3 | 24 / 24 | 876.2 | 3.5 | 0.5417 | 0 / 0 | undefined (not_applicable) |
| commodity | commodity:copper | validation | gbm / sklearn-quantile-gbm-w60 | 6 | 24 / 24 | 1240 | 4.974 | 0.5 | 0 / 0 | undefined (not_applicable) |
| commodity | commodity:copper | validation | naive / persistence-bootstrap-w36 | 1 | 24 / 24 | 454.7 | 1.789 | 0.875 | 0 / 0 | undefined (not_applicable) |
| commodity | commodity:copper | validation | naive / persistence-bootstrap-w36 | 3 | 24 / 24 | 697.1 | 2.79 | 0.7083 | 0 / 0 | undefined (not_applicable) |
| commodity | commodity:copper | validation | naive / persistence-bootstrap-w36 | 6 | 24 / 24 | 905.8 | 3.715 | 0.8333 | 0 / 0 | undefined (not_applicable) |
| commodity | commodity:copper | validation | ridge / monthly-projection-ridge-alpha10-w60 | 1 | 24 / 24 | 446.2 | 1.754 | 0 | 0 / 0 | undefined (not_applicable) |
| commodity | commodity:copper | validation | ridge / monthly-projection-ridge-alpha10-w60 | 3 | 24 / 24 | 677.7 | 2.716 | 0 | 0 / 0 | undefined (not_applicable) |
| commodity | commodity:copper | validation | ridge / monthly-projection-ridge-alpha10-w60 | 6 | 24 / 24 | 978.9 | 4.034 | 0 | 0 / 0 | undefined (not_applicable) |
| commodity | commodity:cotton | test | ets / statsmodels-holt-damped-w60 | 1 | 24 / 24 | 0.08169 | 1.112 | 0.9167 | 24 / 0 | -0.2709 |
| commodity | commodity:cotton | test | ets / statsmodels-holt-damped-w60 | 3 | 24 / 24 | 0.1068 | 1.44 | 0.875 | 24 / 0 | -0.06495 |
| commodity | commodity:cotton | test | ets / statsmodels-holt-damped-w60 | 6 | 24 / 24 | 0.1686 | 2.234 | 0.8333 | 24 / 0 | -0.06622 |
| commodity | commodity:cotton | test | gbm / sklearn-quantile-gbm-w60 | 1 | 24 / 24 | 0.0804 | 1.086 | 0.9167 | 24 / 0 | -0.2508 |
| commodity | commodity:cotton | test | gbm / sklearn-quantile-gbm-w60 | 3 | 24 / 24 | 0.1373 | 1.848 | 0.8333 | 24 / 0 | -0.3701 |
| commodity | commodity:cotton | test | gbm / sklearn-quantile-gbm-w60 | 6 | 24 / 24 | 0.1897 | 2.497 | 0.75 | 24 / 0 | -0.1997 |
| commodity | commodity:cotton | test | naive / persistence-bootstrap-w60 | 1 | 24 / 24 | 0.06428 | 0.8784 | 0.9167 | 0 / 0 | undefined (not_applicable) |
| commodity | commodity:cotton | test | naive / persistence-bootstrap-w60 | 3 | 24 / 24 | 0.1002 | 1.368 | 0.8333 | 0 / 0 | undefined (not_applicable) |
| commodity | commodity:cotton | test | naive / persistence-bootstrap-w60 | 6 | 24 / 24 | 0.1582 | 2.087 | 0.9583 | 0 / 0 | undefined (not_applicable) |
| commodity | commodity:cotton | test | ridge / monthly-projection-ridge-alpha10-w60 | 1 | 24 / 24 | 0.06941 | 0.9485 | 0 | 24 / 0 | -0.07974 |
| commodity | commodity:cotton | test | ridge / monthly-projection-ridge-alpha10-w60 | 3 | 24 / 24 | 0.1141 | 1.557 | 0 | 24 / 0 | -0.1385 |
| commodity | commodity:cotton | test | ridge / monthly-projection-ridge-alpha10-w60 | 6 | 24 / 24 | 0.1878 | 2.473 | 0 | 24 / 0 | -0.1874 |
| commodity | commodity:cotton | validation | ets / statsmodels-holt-damped-w60 | 1 | 24 / 24 | 0.1303 | 1.591 | 0.7083 | 0 / 0 | undefined (not_applicable) |
| commodity | commodity:cotton | validation | ets / statsmodels-holt-damped-w60 | 3 | 24 / 24 | 0.2819 | 3.592 | 0.75 | 0 / 0 | undefined (not_applicable) |
| commodity | commodity:cotton | validation | ets / statsmodels-holt-damped-w60 | 6 | 24 / 24 | 0.4193 | 5.628 | 0.75 | 0 / 0 | undefined (not_applicable) |
| commodity | commodity:cotton | validation | gbm / sklearn-quantile-gbm-w60 | 1 | 24 / 24 | 0.1679 | 2.049 | 0.5833 | 0 / 0 | undefined (not_applicable) |
| commodity | commodity:cotton | validation | gbm / sklearn-quantile-gbm-w60 | 3 | 24 / 24 | 0.3943 | 4.946 | 0.4583 | 0 / 0 | undefined (not_applicable) |
| commodity | commodity:cotton | validation | gbm / sklearn-quantile-gbm-w60 | 6 | 24 / 24 | 0.6722 | 8.684 | 0.4167 | 0 / 0 | undefined (not_applicable) |
| commodity | commodity:cotton | validation | naive / persistence-bootstrap-w60 | 1 | 24 / 24 | 0.1461 | 1.787 | 0.75 | 0 / 0 | undefined (not_applicable) |
| commodity | commodity:cotton | validation | naive / persistence-bootstrap-w60 | 3 | 24 / 24 | 0.2706 | 3.41 | 0.7083 | 0 / 0 | undefined (not_applicable) |
| commodity | commodity:cotton | validation | naive / persistence-bootstrap-w60 | 6 | 24 / 24 | 0.3872 | 5.069 | 0.6667 | 0 / 0 | undefined (not_applicable) |
| commodity | commodity:cotton | validation | ridge / monthly-projection-ridge-alpha10-w60 | 1 | 24 / 24 | 0.1459 | 1.786 | 0 | 0 / 0 | undefined (not_applicable) |
| commodity | commodity:cotton | validation | ridge / monthly-projection-ridge-alpha10-w60 | 3 | 24 / 24 | 0.3006 | 3.79 | 0 | 0 / 0 | undefined (not_applicable) |
| commodity | commodity:cotton | validation | ridge / monthly-projection-ridge-alpha10-w60 | 6 | 24 / 24 | 0.4831 | 6.335 | 0 | 0 / 0 | undefined (not_applicable) |
| commodity | commodity:nickel | test | ets / statsmodels-holt-damped-w60 | 1 | 24 / 24 | 731 | 0.7487 | 0.9583 | 24 / 0 | -0.007884 |
| commodity | commodity:nickel | test | ets / statsmodels-holt-damped-w60 | 3 | 24 / 24 | 1053 | 1.058 | 0.9583 | 24 / 0 | -0.07554 |
| commodity | commodity:nickel | test | ets / statsmodels-holt-damped-w60 | 6 | 24 / 24 | 1522 | 1.519 | 0.9583 | 24 / 0 | -0.04915 |
| commodity | commodity:nickel | test | gbm / sklearn-quantile-gbm-w60 | 1 | 24 / 24 | 1161 | 1.182 | 0.8333 | 24 / 0 | -0.6008 |
| commodity | commodity:nickel | test | gbm / sklearn-quantile-gbm-w60 | 3 | 24 / 24 | 1404 | 1.4 | 0.9167 | 24 / 0 | -0.435 |
| commodity | commodity:nickel | test | gbm / sklearn-quantile-gbm-w60 | 6 | 24 / 24 | 2429 | 2.379 | 0.875 | 24 / 0 | -0.6746 |
| commodity | commodity:nickel | test | naive / persistence-bootstrap-w60 | 1 | 24 / 24 | 725.2 | 0.7428 | 0.9167 | 0 / 0 | undefined (not_applicable) |
| commodity | commodity:nickel | test | naive / persistence-bootstrap-w60 | 3 | 24 / 24 | 978.6 | 0.9864 | 1 | 0 / 0 | undefined (not_applicable) |
| commodity | commodity:nickel | test | naive / persistence-bootstrap-w60 | 6 | 24 / 24 | 1451 | 1.45 | 1 | 0 / 0 | undefined (not_applicable) |
| commodity | commodity:nickel | test | ridge / monthly-projection-ridge-alpha10-w60 | 1 | 24 / 24 | 763.3 | 0.7792 | 0 | 24 / 0 | -0.05253 |
| commodity | commodity:nickel | test | ridge / monthly-projection-ridge-alpha10-w60 | 3 | 24 / 24 | 1201 | 1.203 | 0 | 24 / 0 | -0.2269 |
| commodity | commodity:nickel | test | ridge / monthly-projection-ridge-alpha10-w60 | 6 | 24 / 24 | 1872 | 1.855 | 0 | 24 / 0 | -0.2904 |
| commodity | commodity:nickel | validation | ets / statsmodels-holt-damped-w60 | 1 | 24 / 24 | 2120 | 2.031 | 0.8333 | 0 / 0 | undefined (not_applicable) |
| commodity | commodity:nickel | validation | ets / statsmodels-holt-damped-w60 | 3 | 24 / 24 | 3699 | 3.593 | 0.625 | 0 / 0 | undefined (not_applicable) |
| commodity | commodity:nickel | validation | ets / statsmodels-holt-damped-w60 | 6 | 24 / 24 | 6636 | 6.977 | 0.5833 | 0 / 0 | undefined (not_applicable) |
| commodity | commodity:nickel | validation | gbm / sklearn-quantile-gbm-w60 | 1 | 24 / 24 | 2305 | 2.225 | 0.5417 | 0 / 0 | undefined (not_applicable) |
| commodity | commodity:nickel | validation | gbm / sklearn-quantile-gbm-w60 | 3 | 24 / 24 | 4231 | 4.063 | 0.4583 | 0 / 0 | undefined (not_applicable) |
| commodity | commodity:nickel | validation | gbm / sklearn-quantile-gbm-w60 | 6 | 24 / 24 | 5334 | 5.204 | 0.4167 | 0 / 0 | undefined (not_applicable) |
| commodity | commodity:nickel | validation | naive / persistence-bootstrap-w60 | 1 | 24 / 24 | 1968 | 1.889 | 0.8333 | 0 / 0 | undefined (not_applicable) |
| commodity | commodity:nickel | validation | naive / persistence-bootstrap-w60 | 3 | 24 / 24 | 3413 | 3.316 | 0.7083 | 0 / 0 | undefined (not_applicable) |
| commodity | commodity:nickel | validation | naive / persistence-bootstrap-w60 | 6 | 24 / 24 | 3801 | 3.788 | 0.75 | 0 / 0 | undefined (not_applicable) |
| commodity | commodity:nickel | validation | ridge / monthly-projection-ridge-alpha10-w60 | 1 | 24 / 24 | 2327 | 2.22 | 0 | 0 / 0 | undefined (not_applicable) |
| commodity | commodity:nickel | validation | ridge / monthly-projection-ridge-alpha10-w60 | 3 | 24 / 24 | 4023 | 3.871 | 0 | 0 / 0 | undefined (not_applicable) |
| commodity | commodity:nickel | validation | ridge / monthly-projection-ridge-alpha10-w60 | 6 | 24 / 24 | 5178 | 5.115 | 0 | 0 / 0 | undefined (not_applicable) |
| commodity | commodity:sugar | test | ets / statsmodels-holt-damped-w36 | 1 | 24 / 24 | 0.0219 | 1.31 | 0.9583 | 24 / 0 | -0.0903 |
| commodity | commodity:sugar | test | ets / statsmodels-holt-damped-w36 | 3 | 24 / 24 | 0.03481 | 2.073 | 0.9583 | 24 / 0 | -0.1193 |
| commodity | commodity:sugar | test | ets / statsmodels-holt-damped-w36 | 6 | 24 / 24 | 0.04331 | 2.565 | 1 | 24 / 0 | -0.04116 |
| commodity | commodity:sugar | test | gbm / sklearn-quantile-gbm-w36 | 1 | 24 / 24 | 0.02434 | 1.459 | 0.875 | 24 / 0 | -0.2118 |
| commodity | commodity:sugar | test | gbm / sklearn-quantile-gbm-w36 | 3 | 24 / 24 | 0.03844 | 2.298 | 0.875 | 24 / 0 | -0.236 |
| commodity | commodity:sugar | test | gbm / sklearn-quantile-gbm-w36 | 6 | 24 / 24 | 0.03514 | 2.094 | 0.9583 | 24 / 0 | 0.1553 |
| commodity | commodity:sugar | test | naive / persistence-bootstrap-w36 | 1 | 24 / 24 | 0.02009 | 1.199 | 0.9583 | 0 / 0 | undefined (not_applicable) |
| commodity | commodity:sugar | test | naive / persistence-bootstrap-w36 | 3 | 24 / 24 | 0.0311 | 1.85 | 1 | 0 / 0 | undefined (not_applicable) |
| commodity | commodity:sugar | test | naive / persistence-bootstrap-w36 | 6 | 24 / 24 | 0.0416 | 2.464 | 1 | 0 / 0 | undefined (not_applicable) |
| commodity | commodity:sugar | test | ridge / monthly-projection-ridge-alpha10-w36 | 1 | 24 / 24 | 0.02265 | 1.356 | 0 | 24 / 0 | -0.1278 |
| commodity | commodity:sugar | test | ridge / monthly-projection-ridge-alpha10-w36 | 3 | 24 / 24 | 0.03384 | 2.02 | 0 | 24 / 0 | -0.08806 |
| commodity | commodity:sugar | test | ridge / monthly-projection-ridge-alpha10-w36 | 6 | 24 / 24 | 0.05026 | 2.984 | 0 | 24 / 0 | -0.2082 |
| commodity | commodity:sugar | validation | ets / statsmodels-holt-damped-w36 | 1 | 24 / 24 | 0.03695 | 2.332 | 0.7083 | 0 / 0 | undefined (not_applicable) |
| commodity | commodity:sugar | validation | ets / statsmodels-holt-damped-w36 | 3 | 24 / 24 | 0.05571 | 3.529 | 0.625 | 0 / 0 | undefined (not_applicable) |
| commodity | commodity:sugar | validation | ets / statsmodels-holt-damped-w36 | 6 | 24 / 24 | 0.08953 | 5.704 | 0.5 | 0 / 0 | undefined (not_applicable) |
| commodity | commodity:sugar | validation | gbm / sklearn-quantile-gbm-w36 | 1 | 24 / 24 | 0.03398 | 2.144 | 0.6667 | 0 / 0 | undefined (not_applicable) |
| commodity | commodity:sugar | validation | gbm / sklearn-quantile-gbm-w36 | 3 | 24 / 24 | 0.06623 | 4.221 | 0.5 | 0 / 0 | undefined (not_applicable) |
| commodity | commodity:sugar | validation | gbm / sklearn-quantile-gbm-w36 | 6 | 24 / 24 | 0.08316 | 5.341 | 0.5 | 0 / 0 | undefined (not_applicable) |
| commodity | commodity:sugar | validation | naive / persistence-bootstrap-w36 | 1 | 24 / 24 | 0.03399 | 2.153 | 0.7083 | 0 / 0 | undefined (not_applicable) |
| commodity | commodity:sugar | validation | naive / persistence-bootstrap-w36 | 3 | 24 / 24 | 0.05372 | 3.419 | 0.6667 | 0 / 0 | undefined (not_applicable) |
| commodity | commodity:sugar | validation | naive / persistence-bootstrap-w36 | 6 | 24 / 24 | 0.07653 | 4.913 | 0.5833 | 0 / 0 | undefined (not_applicable) |
| commodity | commodity:sugar | validation | ridge / monthly-projection-ridge-alpha10-w36 | 1 | 24 / 24 | 0.03627 | 2.279 | 0 | 0 / 0 | undefined (not_applicable) |
| commodity | commodity:sugar | validation | ridge / monthly-projection-ridge-alpha10-w36 | 3 | 24 / 24 | 0.05654 | 3.563 | 0 | 0 / 0 | undefined (not_applicable) |
| commodity | commodity:sugar | validation | ridge / monthly-projection-ridge-alpha10-w36 | 6 | 24 / 24 | 0.08569 | 5.442 | 0 | 0 / 0 | undefined (not_applicable) |
| commodity | commodity:sunflower_oil | test | ets / statsmodels-holt-damped-w60 | 1 | 24 / 24 | 51.97 | 0.9983 | 0.9583 | 24 / 0 | 0.01688 |
| commodity | commodity:sunflower_oil | test | ets / statsmodels-holt-damped-w60 | 3 | 24 / 24 | 93.37 | 1.785 | 1 | 24 / 0 | -0.009209 |
| commodity | commodity:sunflower_oil | test | ets / statsmodels-holt-damped-w60 | 6 | 24 / 24 | 162.3 | 3.064 | 1 | 24 / 0 | 0.02761 |
| commodity | commodity:sunflower_oil | test | gbm / sklearn-quantile-gbm-w60 | 1 | 24 / 24 | 57.93 | 1.101 | 0.875 | 24 / 0 | -0.09593 |
| commodity | commodity:sunflower_oil | test | gbm / sklearn-quantile-gbm-w60 | 3 | 24 / 24 | 111 | 2.082 | 0.875 | 24 / 0 | -0.1996 |
| commodity | commodity:sunflower_oil | test | gbm / sklearn-quantile-gbm-w60 | 6 | 24 / 24 | 130.4 | 2.423 | 0.875 | 24 / 0 | 0.2189 |
| commodity | commodity:sunflower_oil | test | naive / persistence-bootstrap-w36 | 1 | 24 / 24 | 52.86 | 1.012 | 0.875 | 0 / 0 | undefined (not_applicable) |
| commodity | commodity:sunflower_oil | test | naive / persistence-bootstrap-w36 | 3 | 24 / 24 | 92.51 | 1.759 | 0.9167 | 0 / 0 | undefined (not_applicable) |
| commodity | commodity:sunflower_oil | test | naive / persistence-bootstrap-w36 | 6 | 24 / 24 | 166.9 | 3.14 | 0.875 | 0 / 0 | undefined (not_applicable) |
| commodity | commodity:sunflower_oil | test | ridge / monthly-projection-ridge-alpha10-w60 | 1 | 24 / 24 | 49.38 | 0.9463 | 0 | 24 / 0 | 0.06579 |
| commodity | commodity:sunflower_oil | test | ridge / monthly-projection-ridge-alpha10-w60 | 3 | 24 / 24 | 80.2 | 1.531 | 0 | 24 / 0 | 0.1331 |
| commodity | commodity:sunflower_oil | test | ridge / monthly-projection-ridge-alpha10-w60 | 6 | 24 / 24 | 138.9 | 2.625 | 0 | 24 / 0 | 0.1676 |
| commodity | commodity:sunflower_oil | validation | ets / statsmodels-holt-damped-w60 | 1 | 24 / 24 | 85.02 | 1.512 | 0.8333 | 0 / 0 | undefined (not_applicable) |
| commodity | commodity:sunflower_oil | validation | ets / statsmodels-holt-damped-w60 | 3 | 24 / 24 | 167.3 | 3.114 | 0.7083 | 0 / 0 | undefined (not_applicable) |
| commodity | commodity:sunflower_oil | validation | ets / statsmodels-holt-damped-w60 | 6 | 24 / 24 | 388.1 | 7.775 | 0.5833 | 0 / 0 | undefined (not_applicable) |
| commodity | commodity:sunflower_oil | validation | gbm / sklearn-quantile-gbm-w60 | 1 | 24 / 24 | 100.1 | 1.774 | 0.75 | 0 / 0 | undefined (not_applicable) |
| commodity | commodity:sunflower_oil | validation | gbm / sklearn-quantile-gbm-w60 | 3 | 24 / 24 | 210.4 | 3.855 | 0.7083 | 0 / 0 | undefined (not_applicable) |
| commodity | commodity:sunflower_oil | validation | gbm / sklearn-quantile-gbm-w60 | 6 | 24 / 24 | 425.5 | 8.138 | 0.5 | 0 / 0 | undefined (not_applicable) |
| commodity | commodity:sunflower_oil | validation | naive / persistence-bootstrap-w36 | 1 | 24 / 24 | 86.27 | 1.535 | 1 | 0 / 0 | undefined (not_applicable) |
| commodity | commodity:sunflower_oil | validation | naive / persistence-bootstrap-w36 | 3 | 24 / 24 | 150.2 | 2.794 | 0.9167 | 0 / 0 | undefined (not_applicable) |
| commodity | commodity:sunflower_oil | validation | naive / persistence-bootstrap-w36 | 6 | 24 / 24 | 276.3 | 5.428 | 0.7917 | 0 / 0 | undefined (not_applicable) |
| commodity | commodity:sunflower_oil | validation | ridge / monthly-projection-ridge-alpha10-w60 | 1 | 24 / 24 | 95.64 | 1.699 | 0 | 0 / 0 | undefined (not_applicable) |
| commodity | commodity:sunflower_oil | validation | ridge / monthly-projection-ridge-alpha10-w60 | 3 | 24 / 24 | 218.9 | 4.046 | 0 | 0 / 0 | undefined (not_applicable) |
| commodity | commodity:sunflower_oil | validation | ridge / monthly-projection-ridge-alpha10-w60 | 6 | 24 / 24 | 454.5 | 8.885 | 0 | 0 / 0 | undefined (not_applicable) |
| commodity | commodity:wheat | test | ets / statsmodels-holt-damped-w60 | 1 | 24 / 24 | 13 | 1.03 | 0.9167 | 24 / 0 | 0.03889 |
| commodity | commodity:wheat | test | ets / statsmodels-holt-damped-w60 | 3 | 24 / 24 | 19.13 | 1.525 | 0.9167 | 24 / 0 | -0.005732 |
| commodity | commodity:wheat | test | ets / statsmodels-holt-damped-w60 | 6 | 24 / 24 | 27.5 | 2.173 | 0.875 | 24 / 0 | -0.01448 |
| commodity | commodity:wheat | test | gbm / sklearn-quantile-gbm-w60 | 1 | 24 / 24 | 16.79 | 1.329 | 0.9167 | 24 / 0 | -0.241 |
| commodity | commodity:wheat | test | gbm / sklearn-quantile-gbm-w60 | 3 | 24 / 24 | 30.14 | 2.38 | 0.8333 | 24 / 0 | -0.5841 |
| commodity | commodity:wheat | test | gbm / sklearn-quantile-gbm-w60 | 6 | 24 / 24 | 45.14 | 3.528 | 0.9167 | 24 / 0 | -0.6649 |
| commodity | commodity:wheat | test | naive / persistence-bootstrap-w36 | 1 | 24 / 24 | 13.53 | 1.072 | 0.7917 | 0 / 0 | undefined (not_applicable) |
| commodity | commodity:wheat | test | naive / persistence-bootstrap-w36 | 3 | 24 / 24 | 19.03 | 1.516 | 0.7083 | 0 / 0 | undefined (not_applicable) |
| commodity | commodity:wheat | test | naive / persistence-bootstrap-w36 | 6 | 24 / 24 | 27.11 | 2.142 | 0.75 | 0 / 0 | undefined (not_applicable) |
| commodity | commodity:wheat | test | ridge / monthly-projection-ridge-alpha10-w60 | 1 | 24 / 24 | 14.66 | 1.166 | 0 | 24 / 0 | -0.08389 |
| commodity | commodity:wheat | test | ridge / monthly-projection-ridge-alpha10-w60 | 3 | 24 / 24 | 22.2 | 1.77 | 0 | 24 / 0 | -0.1666 |
| commodity | commodity:wheat | test | ridge / monthly-projection-ridge-alpha10-w60 | 6 | 24 / 24 | 32.91 | 2.594 | 0 | 24 / 0 | -0.214 |
| commodity | commodity:wheat | validation | ets / statsmodels-holt-damped-w60 | 1 | 24 / 24 | 21.71 | 1.629 | 0.8333 | 0 / 0 | undefined (not_applicable) |
| commodity | commodity:wheat | validation | ets / statsmodels-holt-damped-w60 | 3 | 24 / 24 | 41.32 | 3.205 | 0.7917 | 0 / 0 | undefined (not_applicable) |
| commodity | commodity:wheat | validation | ets / statsmodels-holt-damped-w60 | 6 | 24 / 24 | 81.29 | 6.503 | 0.5417 | 0 / 0 | undefined (not_applicable) |
| commodity | commodity:wheat | validation | gbm / sklearn-quantile-gbm-w60 | 1 | 24 / 24 | 23.05 | 1.724 | 0.75 | 0 / 0 | undefined (not_applicable) |
| commodity | commodity:wheat | validation | gbm / sklearn-quantile-gbm-w60 | 3 | 24 / 24 | 40.9 | 3.128 | 0.7083 | 0 / 0 | undefined (not_applicable) |
| commodity | commodity:wheat | validation | gbm / sklearn-quantile-gbm-w60 | 6 | 24 / 24 | 86.69 | 6.793 | 0.4167 | 0 / 0 | undefined (not_applicable) |
| commodity | commodity:wheat | validation | naive / persistence-bootstrap-w36 | 1 | 24 / 24 | 20.42 | 1.533 | 0.9583 | 0 / 0 | undefined (not_applicable) |
| commodity | commodity:wheat | validation | naive / persistence-bootstrap-w36 | 3 | 24 / 24 | 32.43 | 2.482 | 0.9167 | 0 / 0 | undefined (not_applicable) |
| commodity | commodity:wheat | validation | naive / persistence-bootstrap-w36 | 6 | 24 / 24 | 53.31 | 4.167 | 0.9167 | 0 / 0 | undefined (not_applicable) |
| commodity | commodity:wheat | validation | ridge / monthly-projection-ridge-alpha10-w60 | 1 | 24 / 24 | 26.94 | 2.015 | 0 | 0 / 0 | undefined (not_applicable) |
| commodity | commodity:wheat | validation | ridge / monthly-projection-ridge-alpha10-w60 | 3 | 24 / 24 | 50.54 | 3.854 | 0 | 0 / 0 | undefined (not_applicable) |
| commodity | commodity:wheat | validation | ridge / monthly-projection-ridge-alpha10-w60 | 6 | 24 / 24 | 89.36 | 6.962 | 0 | 0 / 0 | undefined (not_applicable) |
| fx | fx:AED | test | ets / statsmodels-holt-damped-w36 | 1 | 24 / 24 | 0 | undefined (no_positive_scale) | 1 | 24 / 0 | undefined (zero_naive_error) |
| fx | fx:AED | test | ets / statsmodels-holt-damped-w36 | 3 | 24 / 24 | 0 | undefined (no_positive_scale) | 1 | 24 / 0 | undefined (zero_naive_error) |
| fx | fx:AED | test | ets / statsmodels-holt-damped-w36 | 6 | 24 / 24 | 0 | undefined (no_positive_scale) | 1 | 24 / 0 | undefined (zero_naive_error) |
| fx | fx:AED | test | gbm / sklearn-quantile-gbm-w36 | 1 | 24 / 24 | 0 | undefined (no_positive_scale) | 1 | 24 / 0 | undefined (zero_naive_error) |
| fx | fx:AED | test | gbm / sklearn-quantile-gbm-w36 | 3 | 24 / 24 | 0 | undefined (no_positive_scale) | 1 | 24 / 0 | undefined (zero_naive_error) |
| fx | fx:AED | test | gbm / sklearn-quantile-gbm-w36 | 6 | 24 / 24 | 0 | undefined (no_positive_scale) | 1 | 24 / 0 | undefined (zero_naive_error) |
| fx | fx:AED | test | naive / persistence-bootstrap-w36 | 1 | 24 / 24 | 0 | undefined (no_positive_scale) | 1 | 0 / 0 | undefined (not_applicable) |
| fx | fx:AED | test | naive / persistence-bootstrap-w36 | 3 | 24 / 24 | 0 | undefined (no_positive_scale) | 1 | 0 / 0 | undefined (not_applicable) |
| fx | fx:AED | test | naive / persistence-bootstrap-w36 | 6 | 24 / 24 | 0 | undefined (no_positive_scale) | 1 | 0 / 0 | undefined (not_applicable) |
| fx | fx:AED | test | ridge / monthly-projection-ridge-alpha10-w36 | 1 | 24 / 24 | 0 | undefined (no_positive_scale) | 1 | 24 / 0 | undefined (zero_naive_error) |
| fx | fx:AED | test | ridge / monthly-projection-ridge-alpha10-w36 | 3 | 24 / 24 | 0 | undefined (no_positive_scale) | 1 | 24 / 0 | undefined (zero_naive_error) |
| fx | fx:AED | test | ridge / monthly-projection-ridge-alpha10-w36 | 6 | 24 / 24 | 0 | undefined (no_positive_scale) | 1 | 24 / 0 | undefined (zero_naive_error) |
| fx | fx:AED | validation | ets / statsmodels-holt-damped-w36 | 1 | 24 / 24 | 0 | undefined (no_positive_scale) | 1 | 0 / 0 | undefined (not_applicable) |
| fx | fx:AED | validation | ets / statsmodels-holt-damped-w36 | 3 | 24 / 24 | 0 | undefined (no_positive_scale) | 1 | 0 / 0 | undefined (not_applicable) |
| fx | fx:AED | validation | ets / statsmodels-holt-damped-w36 | 6 | 24 / 24 | 0 | undefined (no_positive_scale) | 1 | 0 / 0 | undefined (not_applicable) |
| fx | fx:AED | validation | gbm / sklearn-quantile-gbm-w36 | 1 | 24 / 24 | 0 | undefined (no_positive_scale) | 1 | 0 / 0 | undefined (not_applicable) |
| fx | fx:AED | validation | gbm / sklearn-quantile-gbm-w36 | 3 | 24 / 24 | 0 | undefined (no_positive_scale) | 1 | 0 / 0 | undefined (not_applicable) |
| fx | fx:AED | validation | gbm / sklearn-quantile-gbm-w36 | 6 | 24 / 24 | 0 | undefined (no_positive_scale) | 1 | 0 / 0 | undefined (not_applicable) |
| fx | fx:AED | validation | naive / persistence-bootstrap-w36 | 1 | 24 / 24 | 0 | undefined (no_positive_scale) | 1 | 0 / 0 | undefined (not_applicable) |
| fx | fx:AED | validation | naive / persistence-bootstrap-w36 | 3 | 24 / 24 | 0 | undefined (no_positive_scale) | 1 | 0 / 0 | undefined (not_applicable) |
| fx | fx:AED | validation | naive / persistence-bootstrap-w36 | 6 | 24 / 24 | 0 | undefined (no_positive_scale) | 1 | 0 / 0 | undefined (not_applicable) |
| fx | fx:AED | validation | ridge / monthly-projection-ridge-alpha10-w36 | 1 | 24 / 24 | 0 | undefined (no_positive_scale) | 1 | 0 / 0 | undefined (not_applicable) |
| fx | fx:AED | validation | ridge / monthly-projection-ridge-alpha10-w36 | 3 | 24 / 24 | 0 | undefined (no_positive_scale) | 1 | 0 / 0 | undefined (not_applicable) |
| fx | fx:AED | validation | ridge / monthly-projection-ridge-alpha10-w36 | 6 | 24 / 24 | 0 | undefined (no_positive_scale) | 1 | 0 / 0 | undefined (not_applicable) |
| fx | fx:BDT | test | ets / statsmodels-holt-damped-w60 | 1 | 24 / 24 | 0.738 | 1.631 | 0.9167 | 24 / 0 | 0.1631 |
| fx | fx:BDT | test | ets / statsmodels-holt-damped-w60 | 3 | 24 / 24 | 1.391 | 3.104 | 0.9167 | 24 / 0 | 0.109 |
| fx | fx:BDT | test | ets / statsmodels-holt-damped-w60 | 6 | 24 / 24 | 3.206 | 7.696 | 0.75 | 24 / 0 | -0.01022 |
| fx | fx:BDT | test | gbm / sklearn-quantile-gbm-w36 | 1 | 24 / 24 | 1.007 | 2.24 | 0.9583 | 24 / 0 | -0.1418 |
| fx | fx:BDT | test | gbm / sklearn-quantile-gbm-w36 | 3 | 24 / 24 | 2.076 | 4.593 | 1 | 24 / 0 | -0.33 |
| fx | fx:BDT | test | gbm / sklearn-quantile-gbm-w36 | 6 | 24 / 24 | 5.023 | 11.17 | 0.6667 | 24 / 0 | -0.5829 |
| fx | fx:BDT | test | naive / persistence-bootstrap-w60 | 1 | 24 / 24 | 0.8819 | 1.966 | 1 | 0 / 0 | undefined (not_applicable) |
| fx | fx:BDT | test | naive / persistence-bootstrap-w60 | 3 | 24 / 24 | 1.561 | 3.496 | 1 | 0 / 0 | undefined (not_applicable) |
| fx | fx:BDT | test | naive / persistence-bootstrap-w60 | 6 | 24 / 24 | 3.173 | 7.633 | 0.875 | 0 / 0 | undefined (not_applicable) |
| fx | fx:BDT | test | ridge / monthly-projection-ridge-alpha10-w60 | 1 | 24 / 24 | 1.48 | 3.286 | 0 | 24 / 0 | -0.6781 |
| fx | fx:BDT | test | ridge / monthly-projection-ridge-alpha10-w60 | 3 | 24 / 24 | 2.721 | 6.026 | 0 | 24 / 0 | -0.7427 |
| fx | fx:BDT | test | ridge / monthly-projection-ridge-alpha10-w60 | 6 | 24 / 24 | 4.992 | 11.32 | 0 | 24 / 0 | -0.573 |
| fx | fx:BDT | validation | ets / statsmodels-holt-damped-w60 | 1 | 24 / 24 | 1.675 | 4.85 | 0.2083 | 0 / 0 | undefined (not_applicable) |
| fx | fx:BDT | validation | ets / statsmodels-holt-damped-w60 | 3 | 24 / 24 | 4.224 | 15.43 | 0.08333 | 0 / 0 | undefined (not_applicable) |
| fx | fx:BDT | validation | ets / statsmodels-holt-damped-w60 | 6 | 24 / 24 | 7.357 | 34.02 | 0.1667 | 0 / 0 | undefined (not_applicable) |
| fx | fx:BDT | validation | gbm / sklearn-quantile-gbm-w36 | 1 | 24 / 24 | 1.615 | 5.082 | 0.625 | 0 / 0 | undefined (not_applicable) |
| fx | fx:BDT | validation | gbm / sklearn-quantile-gbm-w36 | 3 | 24 / 24 | 3.613 | 13.33 | 0.625 | 0 / 0 | undefined (not_applicable) |
| fx | fx:BDT | validation | gbm / sklearn-quantile-gbm-w36 | 6 | 24 / 24 | 5.541 | 27.63 | 0.5 | 0 / 0 | undefined (not_applicable) |
| fx | fx:BDT | validation | naive / persistence-bootstrap-w60 | 1 | 24 / 24 | 2.476 | 7.728 | 0.3333 | 0 / 0 | undefined (not_applicable) |
| fx | fx:BDT | validation | naive / persistence-bootstrap-w60 | 3 | 24 / 24 | 4.733 | 17.1 | 0.2083 | 0 / 0 | undefined (not_applicable) |
| fx | fx:BDT | validation | naive / persistence-bootstrap-w60 | 6 | 24 / 24 | 7.771 | 35.07 | 0.125 | 0 / 0 | undefined (not_applicable) |
| fx | fx:BDT | validation | ridge / monthly-projection-ridge-alpha10-w60 | 1 | 24 / 24 | 1.651 | 5.019 | 0 | 0 / 0 | undefined (not_applicable) |
| fx | fx:BDT | validation | ridge / monthly-projection-ridge-alpha10-w60 | 3 | 24 / 24 | 3.392 | 12.26 | 0 | 0 / 0 | undefined (not_applicable) |
| fx | fx:BDT | validation | ridge / monthly-projection-ridge-alpha10-w60 | 6 | 24 / 24 | 4.25 | 22.01 | 0 | 0 / 0 | undefined (not_applicable) |
| fx | fx:BWP | test | ets / statsmodels-holt-damped-w60 | 1 | 24 / 24 | 0.2062 | 1.008 | 0.9167 | 24 / 0 | -0.04593 |
| fx | fx:BWP | test | ets / statsmodels-holt-damped-w60 | 3 | 24 / 24 | 0.2684 | 1.299 | 1 | 24 / 0 | -0.06029 |
| fx | fx:BWP | test | ets / statsmodels-holt-damped-w60 | 6 | 24 / 24 | 0.2963 | 1.42 | 1 | 24 / 0 | 0.006985 |
| fx | fx:BWP | test | gbm / sklearn-quantile-gbm-w60 | 1 | 24 / 24 | 0.2563 | 1.254 | 0.8333 | 24 / 0 | -0.3 |
| fx | fx:BWP | test | gbm / sklearn-quantile-gbm-w60 | 3 | 24 / 24 | 0.444 | 2.152 | 0.7917 | 24 / 0 | -0.754 |
| fx | fx:BWP | test | gbm / sklearn-quantile-gbm-w60 | 6 | 24 / 24 | 0.6646 | 3.173 | 0.8333 | 24 / 0 | -1.227 |
| fx | fx:BWP | test | naive / persistence-bootstrap-w36 | 1 | 24 / 24 | 0.1972 | 0.9663 | 0.7917 | 0 / 0 | undefined (not_applicable) |
| fx | fx:BWP | test | naive / persistence-bootstrap-w36 | 3 | 24 / 24 | 0.2531 | 1.229 | 0.9583 | 0 / 0 | undefined (not_applicable) |
| fx | fx:BWP | test | naive / persistence-bootstrap-w36 | 6 | 24 / 24 | 0.2984 | 1.434 | 1 | 0 / 0 | undefined (not_applicable) |
| fx | fx:BWP | test | ridge / monthly-projection-ridge-alpha10-w60 | 1 | 24 / 24 | 0.2439 | 1.194 | 0 | 24 / 0 | -0.237 |
| fx | fx:BWP | test | ridge / monthly-projection-ridge-alpha10-w60 | 3 | 24 / 24 | 0.3755 | 1.825 | 0 | 24 / 0 | -0.4835 |
| fx | fx:BWP | test | ridge / monthly-projection-ridge-alpha10-w60 | 6 | 24 / 24 | 0.5651 | 2.725 | 0 | 24 / 0 | -0.8936 |
| fx | fx:BWP | validation | ets / statsmodels-holt-damped-w60 | 1 | 24 / 24 | 0.4056 | 2.156 | 0.7917 | 0 / 0 | undefined (not_applicable) |
| fx | fx:BWP | validation | ets / statsmodels-holt-damped-w60 | 3 | 24 / 24 | 0.4554 | 2.505 | 0.8333 | 0 / 0 | undefined (not_applicable) |
| fx | fx:BWP | validation | ets / statsmodels-holt-damped-w60 | 6 | 24 / 24 | 0.6382 | 3.724 | 0.7917 | 0 / 0 | undefined (not_applicable) |
| fx | fx:BWP | validation | gbm / sklearn-quantile-gbm-w60 | 1 | 24 / 24 | 0.4459 | 2.377 | 0.8333 | 0 / 0 | undefined (not_applicable) |
| fx | fx:BWP | validation | gbm / sklearn-quantile-gbm-w60 | 3 | 24 / 24 | 0.6562 | 3.457 | 0.6667 | 0 / 0 | undefined (not_applicable) |
| fx | fx:BWP | validation | gbm / sklearn-quantile-gbm-w60 | 6 | 24 / 24 | 0.8827 | 4.841 | 0.7083 | 0 / 0 | undefined (not_applicable) |
| fx | fx:BWP | validation | naive / persistence-bootstrap-w36 | 1 | 24 / 24 | 0.3964 | 2.142 | 0.875 | 0 / 0 | undefined (not_applicable) |
| fx | fx:BWP | validation | naive / persistence-bootstrap-w36 | 3 | 24 / 24 | 0.4452 | 2.505 | 0.8333 | 0 / 0 | undefined (not_applicable) |
| fx | fx:BWP | validation | naive / persistence-bootstrap-w36 | 6 | 24 / 24 | 0.6598 | 3.889 | 0.7917 | 0 / 0 | undefined (not_applicable) |
| fx | fx:BWP | validation | ridge / monthly-projection-ridge-alpha10-w60 | 1 | 24 / 24 | 0.4219 | 2.245 | 0 | 0 / 0 | undefined (not_applicable) |
| fx | fx:BWP | validation | ridge / monthly-projection-ridge-alpha10-w60 | 3 | 24 / 24 | 0.4865 | 2.616 | 0 | 0 / 0 | undefined (not_applicable) |
| fx | fx:BWP | validation | ridge / monthly-projection-ridge-alpha10-w60 | 6 | 24 / 24 | 0.6744 | 3.774 | 0 | 0 / 0 | undefined (not_applicable) |
| fx | fx:CHF | test | ets / statsmodels-holt-damped-w36 | 1 | 24 / 24 | 0.01933 | 1.651 | 0.875 | 24 / 0 | 0.04633 |
| fx | fx:CHF | test | ets / statsmodels-holt-damped-w36 | 3 | 24 / 24 | 0.0313 | 2.676 | 0.7917 | 24 / 0 | 0.03301 |
| fx | fx:CHF | test | ets / statsmodels-holt-damped-w36 | 6 | 24 / 24 | 0.03163 | 2.706 | 0.875 | 24 / 0 | 0.1126 |
| fx | fx:CHF | test | gbm / sklearn-quantile-gbm-w60 | 1 | 24 / 24 | 0.02661 | 2.266 | 0.5833 | 24 / 0 | -0.3127 |
| fx | fx:CHF | test | gbm / sklearn-quantile-gbm-w60 | 3 | 24 / 24 | 0.03462 | 2.964 | 0.6667 | 24 / 0 | -0.06975 |
| fx | fx:CHF | test | gbm / sklearn-quantile-gbm-w60 | 6 | 24 / 24 | 0.02873 | 2.452 | 0.875 | 24 / 0 | 0.194 |
| fx | fx:CHF | test | naive / persistence-bootstrap-w36 | 1 | 24 / 24 | 0.02027 | 1.727 | 0.8333 | 0 / 0 | undefined (not_applicable) |
| fx | fx:CHF | test | naive / persistence-bootstrap-w36 | 3 | 24 / 24 | 0.03237 | 2.763 | 0.7917 | 0 / 0 | undefined (not_applicable) |
| fx | fx:CHF | test | naive / persistence-bootstrap-w36 | 6 | 24 / 24 | 0.03565 | 3.04 | 0.8333 | 0 / 0 | undefined (not_applicable) |
| fx | fx:CHF | test | ridge / monthly-projection-ridge-alpha10-w60 | 1 | 24 / 24 | 0.0207 | 1.764 | 0 | 24 / 0 | -0.02103 |
| fx | fx:CHF | test | ridge / monthly-projection-ridge-alpha10-w60 | 3 | 24 / 24 | 0.03168 | 2.703 | 0 | 24 / 0 | 0.0212 |
| fx | fx:CHF | test | ridge / monthly-projection-ridge-alpha10-w60 | 6 | 24 / 24 | 0.03117 | 2.66 | 0 | 24 / 0 | 0.1255 |
| fx | fx:CHF | validation | ets / statsmodels-holt-damped-w36 | 1 | 24 / 24 | 0.02332 | 2.09 | 0.5417 | 0 / 0 | undefined (not_applicable) |
| fx | fx:CHF | validation | ets / statsmodels-holt-damped-w36 | 3 | 24 / 24 | 0.02805 | 2.516 | 0.6667 | 0 / 0 | undefined (not_applicable) |
| fx | fx:CHF | validation | ets / statsmodels-holt-damped-w36 | 6 | 24 / 24 | 0.03483 | 3.209 | 0.6667 | 0 / 0 | undefined (not_applicable) |
| fx | fx:CHF | validation | gbm / sklearn-quantile-gbm-w60 | 1 | 24 / 24 | 0.02261 | 2.026 | 0.4583 | 0 / 0 | undefined (not_applicable) |
| fx | fx:CHF | validation | gbm / sklearn-quantile-gbm-w60 | 3 | 24 / 24 | 0.02906 | 2.611 | 0.625 | 0 / 0 | undefined (not_applicable) |
| fx | fx:CHF | validation | gbm / sklearn-quantile-gbm-w60 | 6 | 24 / 24 | 0.04465 | 4.102 | 0.5417 | 0 / 0 | undefined (not_applicable) |
| fx | fx:CHF | validation | naive / persistence-bootstrap-w36 | 1 | 24 / 24 | 0.02332 | 2.089 | 0.625 | 0 / 0 | undefined (not_applicable) |
| fx | fx:CHF | validation | naive / persistence-bootstrap-w36 | 3 | 24 / 24 | 0.02696 | 2.419 | 0.6667 | 0 / 0 | undefined (not_applicable) |
| fx | fx:CHF | validation | naive / persistence-bootstrap-w36 | 6 | 24 / 24 | 0.03458 | 3.183 | 0.7083 | 0 / 0 | undefined (not_applicable) |
| fx | fx:CHF | validation | ridge / monthly-projection-ridge-alpha10-w60 | 1 | 24 / 24 | 0.02255 | 2.021 | 0 | 0 / 0 | undefined (not_applicable) |
| fx | fx:CHF | validation | ridge / monthly-projection-ridge-alpha10-w60 | 3 | 24 / 24 | 0.02683 | 2.407 | 0 | 0 / 0 | undefined (not_applicable) |
| fx | fx:CHF | validation | ridge / monthly-projection-ridge-alpha10-w60 | 6 | 24 / 24 | 0.03727 | 3.427 | 0 | 0 / 0 | undefined (not_applicable) |
| fx | fx:CNY | test | ets / statsmodels-holt-damped-w36 | 1 | 24 / 24 | 0.07389 | 1.218 | 0.875 | 24 / 0 | -0.07555 |
| fx | fx:CNY | test | ets / statsmodels-holt-damped-w36 | 3 | 24 / 24 | 0.1269 | 2.091 | 0.9583 | 24 / 0 | -0.05474 |
| fx | fx:CNY | test | ets / statsmodels-holt-damped-w36 | 6 | 24 / 24 | 0.1895 | 3.11 | 0.9167 | 24 / 0 | -0.1975 |
| fx | fx:CNY | test | gbm / sklearn-quantile-gbm-w60 | 1 | 24 / 24 | 0.0832 | 1.371 | 0.8333 | 24 / 0 | -0.2111 |
| fx | fx:CNY | test | gbm / sklearn-quantile-gbm-w60 | 3 | 24 / 24 | 0.1935 | 3.2 | 0.5833 | 24 / 0 | -0.6083 |
| fx | fx:CNY | test | gbm / sklearn-quantile-gbm-w60 | 6 | 24 / 24 | 0.3188 | 5.266 | 0.5 | 24 / 0 | -1.014 |
| fx | fx:CNY | test | naive / persistence-bootstrap-w60 | 1 | 24 / 24 | 0.0687 | 1.135 | 1 | 0 / 0 | undefined (not_applicable) |
| fx | fx:CNY | test | naive / persistence-bootstrap-w60 | 3 | 24 / 24 | 0.1203 | 1.987 | 1 | 0 / 0 | undefined (not_applicable) |
| fx | fx:CNY | test | naive / persistence-bootstrap-w60 | 6 | 24 / 24 | 0.1583 | 2.609 | 1 | 0 / 0 | undefined (not_applicable) |
| fx | fx:CNY | test | ridge / monthly-projection-ridge-alpha10-w60 | 1 | 24 / 24 | 0.09629 | 1.598 | 0 | 24 / 0 | -0.4017 |
| fx | fx:CNY | test | ridge / monthly-projection-ridge-alpha10-w60 | 3 | 24 / 24 | 0.1685 | 2.791 | 0 | 24 / 0 | -0.4 |
| fx | fx:CNY | test | ridge / monthly-projection-ridge-alpha10-w60 | 6 | 24 / 24 | 0.2323 | 3.833 | 0 | 24 / 0 | -0.4675 |
| fx | fx:CNY | validation | ets / statsmodels-holt-damped-w36 | 1 | 24 / 24 | 0.1324 | 2.106 | 0.7083 | 0 / 0 | undefined (not_applicable) |
| fx | fx:CNY | validation | ets / statsmodels-holt-damped-w36 | 3 | 24 / 24 | 0.237 | 3.766 | 0.4583 | 0 / 0 | undefined (not_applicable) |
| fx | fx:CNY | validation | ets / statsmodels-holt-damped-w36 | 6 | 24 / 24 | 0.3574 | 5.787 | 0.5417 | 0 / 0 | undefined (not_applicable) |
| fx | fx:CNY | validation | gbm / sklearn-quantile-gbm-w60 | 1 | 24 / 24 | 0.1183 | 1.899 | 0.625 | 0 / 0 | undefined (not_applicable) |
| fx | fx:CNY | validation | gbm / sklearn-quantile-gbm-w60 | 3 | 24 / 24 | 0.1505 | 2.429 | 0.6667 | 0 / 0 | undefined (not_applicable) |
| fx | fx:CNY | validation | gbm / sklearn-quantile-gbm-w60 | 6 | 24 / 24 | 0.263 | 4.318 | 0.5833 | 0 / 0 | undefined (not_applicable) |
| fx | fx:CNY | validation | naive / persistence-bootstrap-w60 | 1 | 24 / 24 | 0.1377 | 2.199 | 0.7083 | 0 / 0 | undefined (not_applicable) |
| fx | fx:CNY | validation | naive / persistence-bootstrap-w60 | 3 | 24 / 24 | 0.2016 | 3.229 | 0.625 | 0 / 0 | undefined (not_applicable) |
| fx | fx:CNY | validation | naive / persistence-bootstrap-w60 | 6 | 24 / 24 | 0.2385 | 3.934 | 0.75 | 0 / 0 | undefined (not_applicable) |
| fx | fx:CNY | validation | ridge / monthly-projection-ridge-alpha10-w60 | 1 | 24 / 24 | 0.1283 | 2.05 | 0 | 0 / 0 | undefined (not_applicable) |
| fx | fx:CNY | validation | ridge / monthly-projection-ridge-alpha10-w60 | 3 | 24 / 24 | 0.1956 | 3.133 | 0 | 0 / 0 | undefined (not_applicable) |
| fx | fx:CNY | validation | ridge / monthly-projection-ridge-alpha10-w60 | 6 | 24 / 24 | 0.2392 | 3.951 | 0 | 0 / 0 | undefined (not_applicable) |
| fx | fx:EUR | test | ets / statsmodels-holt-damped-w60 | 1 | 24 / 24 | 0.01893 | 1.697 | 0.875 | 24 / 0 | 0.02662 |
| fx | fx:EUR | test | ets / statsmodels-holt-damped-w60 | 3 | 24 / 24 | 0.03098 | 2.782 | 0.7917 | 24 / 0 | -0.0858 |
| fx | fx:EUR | test | ets / statsmodels-holt-damped-w60 | 6 | 24 / 24 | 0.03593 | 3.223 | 0.8333 | 24 / 0 | -0.09689 |
| fx | fx:EUR | test | gbm / sklearn-quantile-gbm-w60 | 1 | 24 / 24 | 0.02275 | 2.031 | 0.7917 | 24 / 0 | -0.1697 |
| fx | fx:EUR | test | gbm / sklearn-quantile-gbm-w60 | 3 | 24 / 24 | 0.03014 | 2.693 | 0.7917 | 24 / 0 | -0.0564 |
| fx | fx:EUR | test | gbm / sklearn-quantile-gbm-w60 | 6 | 24 / 24 | 0.04033 | 3.612 | 0.7917 | 24 / 0 | -0.2313 |
| fx | fx:EUR | test | naive / persistence-bootstrap-w60 | 1 | 24 / 24 | 0.01945 | 1.739 | 0.7917 | 0 / 0 | undefined (not_applicable) |
| fx | fx:EUR | test | naive / persistence-bootstrap-w60 | 3 | 24 / 24 | 0.02853 | 2.559 | 0.75 | 0 / 0 | undefined (not_applicable) |
| fx | fx:EUR | test | naive / persistence-bootstrap-w60 | 6 | 24 / 24 | 0.03275 | 2.937 | 0.8333 | 0 / 0 | undefined (not_applicable) |
| fx | fx:EUR | test | ridge / monthly-projection-ridge-alpha10-w60 | 1 | 24 / 24 | 0.02019 | 1.804 | 0 | 24 / 0 | -0.0381 |
| fx | fx:EUR | test | ridge / monthly-projection-ridge-alpha10-w60 | 3 | 24 / 24 | 0.03105 | 2.782 | 0 | 24 / 0 | -0.08833 |
| fx | fx:EUR | test | ridge / monthly-projection-ridge-alpha10-w60 | 6 | 24 / 24 | 0.03749 | 3.356 | 0 | 24 / 0 | -0.1447 |
| fx | fx:EUR | validation | ets / statsmodels-holt-damped-w60 | 1 | 24 / 24 | 0.02041 | 1.885 | 0.75 | 0 / 0 | undefined (not_applicable) |
| fx | fx:EUR | validation | ets / statsmodels-holt-damped-w60 | 3 | 24 / 24 | 0.0304 | 2.866 | 0.7083 | 0 / 0 | undefined (not_applicable) |
| fx | fx:EUR | validation | ets / statsmodels-holt-damped-w60 | 6 | 24 / 24 | 0.04416 | 4.265 | 0.7083 | 0 / 0 | undefined (not_applicable) |
| fx | fx:EUR | validation | gbm / sklearn-quantile-gbm-w60 | 1 | 24 / 24 | 0.02185 | 2.021 | 0.6667 | 0 / 0 | undefined (not_applicable) |
| fx | fx:EUR | validation | gbm / sklearn-quantile-gbm-w60 | 3 | 24 / 24 | 0.03351 | 3.151 | 0.5417 | 0 / 0 | undefined (not_applicable) |
| fx | fx:EUR | validation | gbm / sklearn-quantile-gbm-w60 | 6 | 24 / 24 | 0.05478 | 5.233 | 0.5 | 0 / 0 | undefined (not_applicable) |
| fx | fx:EUR | validation | naive / persistence-bootstrap-w60 | 1 | 24 / 24 | 0.01922 | 1.78 | 0.6667 | 0 / 0 | undefined (not_applicable) |
| fx | fx:EUR | validation | naive / persistence-bootstrap-w60 | 3 | 24 / 24 | 0.02713 | 2.558 | 0.75 | 0 / 0 | undefined (not_applicable) |
| fx | fx:EUR | validation | naive / persistence-bootstrap-w60 | 6 | 24 / 24 | 0.03449 | 3.349 | 0.6667 | 0 / 0 | undefined (not_applicable) |
| fx | fx:EUR | validation | ridge / monthly-projection-ridge-alpha10-w60 | 1 | 24 / 24 | 0.01975 | 1.828 | 0 | 0 / 0 | undefined (not_applicable) |
| fx | fx:EUR | validation | ridge / monthly-projection-ridge-alpha10-w60 | 3 | 24 / 24 | 0.02823 | 2.662 | 0 | 0 / 0 | undefined (not_applicable) |
| fx | fx:EUR | validation | ridge / monthly-projection-ridge-alpha10-w60 | 6 | 24 / 24 | 0.03953 | 3.827 | 0 | 0 / 0 | undefined (not_applicable) |
| fx | fx:INR | test | ets / statsmodels-holt-damped-w60 | 1 | 24 / 24 | 1.202 | 1.861 | 0.5 | 24 / 0 | 0.07339 |
| fx | fx:INR | test | ets / statsmodels-holt-damped-w60 | 3 | 24 / 24 | 1.831 | 2.842 | 0.4583 | 24 / 0 | 0.1826 |
| fx | fx:INR | test | ets / statsmodels-holt-damped-w60 | 6 | 24 / 24 | 2.257 | 3.494 | 0.5833 | 24 / 0 | 0.2951 |
| fx | fx:INR | test | gbm / sklearn-quantile-gbm-w60 | 1 | 24 / 24 | 1.077 | 1.672 | 0.5417 | 24 / 0 | 0.1694 |
| fx | fx:INR | test | gbm / sklearn-quantile-gbm-w60 | 3 | 24 / 24 | 1.83 | 2.841 | 0.5417 | 24 / 0 | 0.1829 |
| fx | fx:INR | test | gbm / sklearn-quantile-gbm-w60 | 6 | 24 / 24 | 2.079 | 3.218 | 0.4583 | 24 / 0 | 0.3506 |
| fx | fx:INR | test | naive / persistence-bootstrap-w36 | 1 | 24 / 24 | 1.297 | 2.011 | 0.4583 | 0 / 0 | undefined (not_applicable) |
| fx | fx:INR | test | naive / persistence-bootstrap-w36 | 3 | 24 / 24 | 2.24 | 3.471 | 0.3333 | 0 / 0 | undefined (not_applicable) |
| fx | fx:INR | test | naive / persistence-bootstrap-w36 | 6 | 24 / 24 | 3.202 | 4.95 | 0.4167 | 0 / 0 | undefined (not_applicable) |
| fx | fx:INR | test | ridge / monthly-projection-ridge-alpha10-w60 | 1 | 24 / 24 | 0.9013 | 1.397 | 0 | 24 / 0 | 0.3052 |
| fx | fx:INR | test | ridge / monthly-projection-ridge-alpha10-w60 | 3 | 24 / 24 | 1.5 | 2.325 | 0 | 24 / 0 | 0.3304 |
| fx | fx:INR | test | ridge / monthly-projection-ridge-alpha10-w60 | 6 | 24 / 24 | 1.695 | 2.618 | 0 | 24 / 0 | 0.4705 |
| fx | fx:INR | validation | ets / statsmodels-holt-damped-w60 | 1 | 24 / 24 | 0.45 | 0.6164 | 0.9583 | 0 / 0 | undefined (not_applicable) |
| fx | fx:INR | validation | ets / statsmodels-holt-damped-w60 | 3 | 24 / 24 | 0.7321 | 0.9925 | 0.9583 | 0 / 0 | undefined (not_applicable) |
| fx | fx:INR | validation | ets / statsmodels-holt-damped-w60 | 6 | 24 / 24 | 1.391 | 1.882 | 0.8333 | 0 / 0 | undefined (not_applicable) |
| fx | fx:INR | validation | gbm / sklearn-quantile-gbm-w60 | 1 | 24 / 24 | 0.5299 | 0.7221 | 0.9583 | 0 / 0 | undefined (not_applicable) |
| fx | fx:INR | validation | gbm / sklearn-quantile-gbm-w60 | 3 | 24 / 24 | 1.114 | 1.521 | 0.9167 | 0 / 0 | undefined (not_applicable) |
| fx | fx:INR | validation | gbm / sklearn-quantile-gbm-w60 | 6 | 24 / 24 | 1.963 | 2.678 | 0.8333 | 0 / 0 | undefined (not_applicable) |
| fx | fx:INR | validation | naive / persistence-bootstrap-w36 | 1 | 24 / 24 | 0.492 | 0.6751 | 0.9583 | 0 / 0 | undefined (not_applicable) |
| fx | fx:INR | validation | naive / persistence-bootstrap-w36 | 3 | 24 / 24 | 1.01 | 1.373 | 0.9167 | 0 / 0 | undefined (not_applicable) |
| fx | fx:INR | validation | naive / persistence-bootstrap-w36 | 6 | 24 / 24 | 1.826 | 2.469 | 0.7917 | 0 / 0 | undefined (not_applicable) |
| fx | fx:INR | validation | ridge / monthly-projection-ridge-alpha10-w60 | 1 | 24 / 24 | 0.5285 | 0.7324 | 0 | 0 / 0 | undefined (not_applicable) |
| fx | fx:INR | validation | ridge / monthly-projection-ridge-alpha10-w60 | 3 | 24 / 24 | 1.004 | 1.38 | 0 | 0 / 0 | undefined (not_applicable) |
| fx | fx:INR | validation | ridge / monthly-projection-ridge-alpha10-w60 | 6 | 24 / 24 | 1.759 | 2.397 | 0 | 0 / 0 | undefined (not_applicable) |
| fx | fx:JPY | test | ets / statsmodels-holt-damped-w60 | 1 | 24 / 24 | 3.865 | 1.957 | 0.9167 | 24 / 0 | -0.03523 |
| fx | fx:JPY | test | ets / statsmodels-holt-damped-w60 | 3 | 24 / 24 | 6.23 | 3.162 | 0.9167 | 24 / 0 | -0.09066 |
| fx | fx:JPY | test | ets / statsmodels-holt-damped-w60 | 6 | 24 / 24 | 7.399 | 3.756 | 1 | 24 / 0 | -0.3097 |
| fx | fx:JPY | test | gbm / sklearn-quantile-gbm-w36 | 1 | 24 / 24 | 4.856 | 2.452 | 0.8333 | 24 / 0 | -0.3005 |
| fx | fx:JPY | test | gbm / sklearn-quantile-gbm-w36 | 3 | 24 / 24 | 7.694 | 3.925 | 0.8333 | 24 / 0 | -0.3468 |
| fx | fx:JPY | test | gbm / sklearn-quantile-gbm-w36 | 6 | 24 / 24 | 10.27 | 5.276 | 0.75 | 24 / 0 | -0.8176 |
| fx | fx:JPY | test | naive / persistence-bootstrap-w36 | 1 | 24 / 24 | 3.734 | 1.886 | 0.9167 | 0 / 0 | undefined (not_applicable) |
| fx | fx:JPY | test | naive / persistence-bootstrap-w36 | 3 | 24 / 24 | 5.712 | 2.884 | 1 | 0 / 0 | undefined (not_applicable) |
| fx | fx:JPY | test | naive / persistence-bootstrap-w36 | 6 | 24 / 24 | 5.649 | 2.819 | 1 | 0 / 0 | undefined (not_applicable) |
| fx | fx:JPY | test | ridge / monthly-projection-ridge-alpha10-w36 | 1 | 24 / 24 | 3.652 | 1.85 | 0 | 24 / 0 | 0.02188 |
| fx | fx:JPY | test | ridge / monthly-projection-ridge-alpha10-w36 | 3 | 24 / 24 | 6.221 | 3.165 | 0 | 24 / 0 | -0.08901 |
| fx | fx:JPY | test | ridge / monthly-projection-ridge-alpha10-w36 | 6 | 24 / 24 | 8.791 | 4.507 | 0 | 24 / 0 | -0.5561 |
| fx | fx:JPY | validation | ets / statsmodels-holt-damped-w60 | 1 | 24 / 24 | 5.488 | 3.203 | 0.5417 | 0 / 0 | undefined (not_applicable) |
| fx | fx:JPY | validation | ets / statsmodels-holt-damped-w60 | 3 | 24 / 24 | 8.65 | 5.074 | 0.5 | 0 / 0 | undefined (not_applicable) |
| fx | fx:JPY | validation | ets / statsmodels-holt-damped-w60 | 6 | 24 / 24 | 13.06 | 8.4 | 0.5 | 0 / 0 | undefined (not_applicable) |
| fx | fx:JPY | validation | gbm / sklearn-quantile-gbm-w36 | 1 | 24 / 24 | 4.581 | 2.675 | 0.7917 | 0 / 0 | undefined (not_applicable) |
| fx | fx:JPY | validation | gbm / sklearn-quantile-gbm-w36 | 3 | 24 / 24 | 8.012 | 4.757 | 0.7083 | 0 / 0 | undefined (not_applicable) |
| fx | fx:JPY | validation | gbm / sklearn-quantile-gbm-w36 | 6 | 24 / 24 | 8.933 | 5.754 | 0.5833 | 0 / 0 | undefined (not_applicable) |
| fx | fx:JPY | validation | naive / persistence-bootstrap-w36 | 1 | 24 / 24 | 5.254 | 3.065 | 0.7083 | 0 / 0 | undefined (not_applicable) |
| fx | fx:JPY | validation | naive / persistence-bootstrap-w36 | 3 | 24 / 24 | 7.419 | 4.421 | 0.5 | 0 / 0 | undefined (not_applicable) |
| fx | fx:JPY | validation | naive / persistence-bootstrap-w36 | 6 | 24 / 24 | 9.677 | 6.188 | 0.5833 | 0 / 0 | undefined (not_applicable) |
| fx | fx:JPY | validation | ridge / monthly-projection-ridge-alpha10-w36 | 1 | 24 / 24 | 4.458 | 2.633 | 0 | 0 / 0 | undefined (not_applicable) |
| fx | fx:JPY | validation | ridge / monthly-projection-ridge-alpha10-w36 | 3 | 24 / 24 | 6.73 | 4.066 | 0 | 0 / 0 | undefined (not_applicable) |
| fx | fx:JPY | validation | ridge / monthly-projection-ridge-alpha10-w36 | 6 | 24 / 24 | 9.551 | 6.266 | 0 | 0 / 0 | undefined (not_applicable) |
| fx | fx:KES | test | ets / statsmodels-holt-damped-w60 | 1 | 24 / 24 | 0.3703 | 0.3261 | 1 | 24 / 0 | 0.2906 |
| fx | fx:KES | test | ets / statsmodels-holt-damped-w60 | 3 | 24 / 24 | 0.9462 | 0.8037 | 1 | 24 / 0 | 0.1128 |
| fx | fx:KES | test | ets / statsmodels-holt-damped-w60 | 6 | 24 / 24 | 3.948 | 3.379 | 0.875 | 24 / 0 | -0.4448 |
| fx | fx:KES | test | gbm / sklearn-quantile-gbm-w60 | 1 | 24 / 24 | 0.6086 | 0.5307 | 1 | 24 / 0 | -0.1658 |
| fx | fx:KES | test | gbm / sklearn-quantile-gbm-w60 | 3 | 24 / 24 | 1.545 | 1.31 | 1 | 24 / 0 | -0.4491 |
| fx | fx:KES | test | gbm / sklearn-quantile-gbm-w60 | 6 | 24 / 24 | 7.664 | 6.612 | 0.875 | 24 / 0 | -1.805 |
| fx | fx:KES | test | naive / persistence-bootstrap-w60 | 1 | 24 / 24 | 0.5221 | 0.4599 | 1 | 0 / 0 | undefined (not_applicable) |
| fx | fx:KES | test | naive / persistence-bootstrap-w60 | 3 | 24 / 24 | 1.067 | 0.9242 | 1 | 0 / 0 | undefined (not_applicable) |
| fx | fx:KES | test | naive / persistence-bootstrap-w60 | 6 | 24 / 24 | 2.732 | 2.419 | 0.9583 | 0 / 0 | undefined (not_applicable) |
| fx | fx:KES | test | ridge / monthly-projection-ridge-alpha10-w60 | 1 | 24 / 24 | 0.991 | 0.8737 | 0 | 24 / 0 | -0.8981 |
| fx | fx:KES | test | ridge / monthly-projection-ridge-alpha10-w60 | 3 | 24 / 24 | 2.217 | 1.913 | 0 | 24 / 0 | -1.079 |
| fx | fx:KES | test | ridge / monthly-projection-ridge-alpha10-w60 | 6 | 24 / 24 | 5.466 | 4.769 | 0 | 24 / 0 | -1.001 |
| fx | fx:KES | validation | ets / statsmodels-holt-damped-w60 | 1 | 24 / 24 | 4.53 | 4.812 | 0.5417 | 0 / 0 | undefined (not_applicable) |
| fx | fx:KES | validation | ets / statsmodels-holt-damped-w60 | 3 | 24 / 24 | 9.292 | 10.58 | 0.2917 | 0 / 0 | undefined (not_applicable) |
| fx | fx:KES | validation | ets / statsmodels-holt-damped-w60 | 6 | 24 / 24 | 13.72 | 17.53 | 0.04167 | 0 / 0 | undefined (not_applicable) |
| fx | fx:KES | validation | gbm / sklearn-quantile-gbm-w60 | 1 | 24 / 24 | 4.687 | 5.201 | 0.4583 | 0 / 0 | undefined (not_applicable) |
| fx | fx:KES | validation | gbm / sklearn-quantile-gbm-w60 | 3 | 24 / 24 | 10.07 | 11.68 | 0.2083 | 0 / 0 | undefined (not_applicable) |
| fx | fx:KES | validation | gbm / sklearn-quantile-gbm-w60 | 6 | 24 / 24 | 14.81 | 19.02 | 0.04167 | 0 / 0 | undefined (not_applicable) |
| fx | fx:KES | validation | naive / persistence-bootstrap-w60 | 1 | 24 / 24 | 5.547 | 6.485 | 0.2083 | 0 / 0 | undefined (not_applicable) |
| fx | fx:KES | validation | naive / persistence-bootstrap-w60 | 3 | 24 / 24 | 10.3 | 12.69 | 0.08333 | 0 / 0 | undefined (not_applicable) |
| fx | fx:KES | validation | naive / persistence-bootstrap-w60 | 6 | 24 / 24 | 15 | 20.13 | 0 | 0 / 0 | undefined (not_applicable) |
| fx | fx:KES | validation | ridge / monthly-projection-ridge-alpha10-w60 | 1 | 24 / 24 | 5.04 | 5.751 | 0 | 0 / 0 | undefined (not_applicable) |
| fx | fx:KES | validation | ridge / monthly-projection-ridge-alpha10-w60 | 3 | 24 / 24 | 9.815 | 11.78 | 0 | 0 / 0 | undefined (not_applicable) |
| fx | fx:KES | validation | ridge / monthly-projection-ridge-alpha10-w60 | 6 | 24 / 24 | 14.97 | 19.35 | 0 | 0 / 0 | undefined (not_applicable) |
| fx | fx:KRW | test | ets / statsmodels-holt-damped-w60 | 1 | 24 / 24 | 36.36 | 2.121 | 0.875 | 24 / 0 | -0.02168 |
| fx | fx:KRW | test | ets / statsmodels-holt-damped-w60 | 3 | 24 / 24 | 51.04 | 2.988 | 0.9167 | 24 / 0 | -0.01465 |
| fx | fx:KRW | test | ets / statsmodels-holt-damped-w60 | 6 | 24 / 24 | 54.88 | 3.21 | 1 | 24 / 0 | 0.04244 |
| fx | fx:KRW | test | gbm / sklearn-quantile-gbm-w60 | 1 | 24 / 24 | 41.82 | 2.439 | 0.7917 | 24 / 0 | -0.175 |
| fx | fx:KRW | test | gbm / sklearn-quantile-gbm-w60 | 3 | 24 / 24 | 55.62 | 3.264 | 0.875 | 24 / 0 | -0.1058 |
| fx | fx:KRW | test | gbm / sklearn-quantile-gbm-w60 | 6 | 24 / 24 | 47.22 | 2.777 | 1 | 24 / 0 | 0.1761 |
| fx | fx:KRW | test | naive / persistence-bootstrap-w36 | 1 | 24 / 24 | 35.59 | 2.077 | 0.7917 | 0 / 0 | undefined (not_applicable) |
| fx | fx:KRW | test | naive / persistence-bootstrap-w36 | 3 | 24 / 24 | 50.3 | 2.945 | 0.9167 | 0 / 0 | undefined (not_applicable) |
| fx | fx:KRW | test | naive / persistence-bootstrap-w36 | 6 | 24 / 24 | 57.31 | 3.36 | 1 | 0 / 0 | undefined (not_applicable) |
| fx | fx:KRW | test | ridge / monthly-projection-ridge-alpha10-w60 | 1 | 24 / 24 | 34.89 | 2.036 | 0 | 24 / 0 | 0.01972 |
| fx | fx:KRW | test | ridge / monthly-projection-ridge-alpha10-w60 | 3 | 24 / 24 | 46.98 | 2.754 | 0 | 24 / 0 | 0.06592 |
| fx | fx:KRW | test | ridge / monthly-projection-ridge-alpha10-w60 | 6 | 24 / 24 | 48.34 | 2.839 | 0 | 24 / 0 | 0.1564 |
| fx | fx:KRW | validation | ets / statsmodels-holt-damped-w60 | 1 | 24 / 24 | 47.71 | 3.153 | 0.5833 | 0 / 0 | undefined (not_applicable) |
| fx | fx:KRW | validation | ets / statsmodels-holt-damped-w60 | 3 | 24 / 24 | 59.63 | 4.043 | 0.7083 | 0 / 0 | undefined (not_applicable) |
| fx | fx:KRW | validation | ets / statsmodels-holt-damped-w60 | 6 | 24 / 24 | 77.09 | 5.44 | 0.625 | 0 / 0 | undefined (not_applicable) |
| fx | fx:KRW | validation | gbm / sklearn-quantile-gbm-w60 | 1 | 24 / 24 | 39.17 | 2.585 | 0.625 | 0 / 0 | undefined (not_applicable) |
| fx | fx:KRW | validation | gbm / sklearn-quantile-gbm-w60 | 3 | 24 / 24 | 42.49 | 2.932 | 0.7917 | 0 / 0 | undefined (not_applicable) |
| fx | fx:KRW | validation | gbm / sklearn-quantile-gbm-w60 | 6 | 24 / 24 | 61.83 | 4.408 | 0.5833 | 0 / 0 | undefined (not_applicable) |
| fx | fx:KRW | validation | naive / persistence-bootstrap-w36 | 1 | 24 / 24 | 41.73 | 2.764 | 0.7083 | 0 / 0 | undefined (not_applicable) |
| fx | fx:KRW | validation | naive / persistence-bootstrap-w36 | 3 | 24 / 24 | 50.94 | 3.481 | 0.7083 | 0 / 0 | undefined (not_applicable) |
| fx | fx:KRW | validation | naive / persistence-bootstrap-w36 | 6 | 24 / 24 | 57.51 | 4.074 | 0.8333 | 0 / 0 | undefined (not_applicable) |
| fx | fx:KRW | validation | ridge / monthly-projection-ridge-alpha10-w60 | 1 | 24 / 24 | 42.89 | 2.832 | 0 | 0 / 0 | undefined (not_applicable) |
| fx | fx:KRW | validation | ridge / monthly-projection-ridge-alpha10-w60 | 3 | 24 / 24 | 51.52 | 3.517 | 0 | 0 / 0 | undefined (not_applicable) |
| fx | fx:KRW | validation | ridge / monthly-projection-ridge-alpha10-w60 | 6 | 24 / 24 | 56.62 | 4.053 | 0 | 0 / 0 | undefined (not_applicable) |
| fx | fx:MXN | test | ets / statsmodels-holt-damped-w36 | 1 | 24 / 24 | 0.4035 | 0.9911 | 0.875 | 24 / 0 | 0.07819 |
| fx | fx:MXN | test | ets / statsmodels-holt-damped-w36 | 3 | 24 / 24 | 0.8462 | 2.062 | 0.75 | 24 / 0 | 0.02529 |
| fx | fx:MXN | test | ets / statsmodels-holt-damped-w36 | 6 | 24 / 24 | 1.589 | 3.846 | 0.5833 | 24 / 0 | -0.00881 |
| fx | fx:MXN | test | gbm / sklearn-quantile-gbm-w36 | 1 | 24 / 24 | 0.5786 | 1.413 | 0.9167 | 24 / 0 | -0.3218 |
| fx | fx:MXN | test | gbm / sklearn-quantile-gbm-w36 | 3 | 24 / 24 | 1.044 | 2.543 | 0.7917 | 24 / 0 | -0.2024 |
| fx | fx:MXN | test | gbm / sklearn-quantile-gbm-w36 | 6 | 24 / 24 | 2.044 | 4.953 | 0.4583 | 24 / 0 | -0.2976 |
| fx | fx:MXN | test | naive / persistence-bootstrap-w36 | 1 | 24 / 24 | 0.4377 | 1.069 | 0.875 | 0 / 0 | undefined (not_applicable) |
| fx | fx:MXN | test | naive / persistence-bootstrap-w36 | 3 | 24 / 24 | 0.8681 | 2.115 | 0.875 | 0 / 0 | undefined (not_applicable) |
| fx | fx:MXN | test | naive / persistence-bootstrap-w36 | 6 | 24 / 24 | 1.575 | 3.827 | 0.75 | 0 / 0 | undefined (not_applicable) |
| fx | fx:MXN | test | ridge / monthly-projection-ridge-alpha10-w36 | 1 | 24 / 24 | 0.5975 | 1.459 | 0 | 24 / 0 | -0.365 |
| fx | fx:MXN | test | ridge / monthly-projection-ridge-alpha10-w36 | 3 | 24 / 24 | 1.217 | 2.966 | 0 | 24 / 0 | -0.4016 |
| fx | fx:MXN | test | ridge / monthly-projection-ridge-alpha10-w36 | 6 | 24 / 24 | 2.15 | 5.23 | 0 | 24 / 0 | -0.3649 |
| fx | fx:MXN | validation | ets / statsmodels-holt-damped-w36 | 1 | 24 / 24 | 0.5357 | 1.27 | 0.5833 | 0 / 0 | undefined (not_applicable) |
| fx | fx:MXN | validation | ets / statsmodels-holt-damped-w36 | 3 | 24 / 24 | 0.8176 | 1.923 | 0.625 | 0 / 0 | undefined (not_applicable) |
| fx | fx:MXN | validation | ets / statsmodels-holt-damped-w36 | 6 | 24 / 24 | 0.9294 | 2.156 | 0.75 | 0 / 0 | undefined (not_applicable) |
| fx | fx:MXN | validation | gbm / sklearn-quantile-gbm-w36 | 1 | 24 / 24 | 0.6045 | 1.431 | 0.5417 | 0 / 0 | undefined (not_applicable) |
| fx | fx:MXN | validation | gbm / sklearn-quantile-gbm-w36 | 3 | 24 / 24 | 0.9924 | 2.33 | 0.4167 | 0 / 0 | undefined (not_applicable) |
| fx | fx:MXN | validation | gbm / sklearn-quantile-gbm-w36 | 6 | 24 / 24 | 1.179 | 2.722 | 0.5417 | 0 / 0 | undefined (not_applicable) |
| fx | fx:MXN | validation | naive / persistence-bootstrap-w36 | 1 | 24 / 24 | 0.5481 | 1.297 | 0.625 | 0 / 0 | undefined (not_applicable) |
| fx | fx:MXN | validation | naive / persistence-bootstrap-w36 | 3 | 24 / 24 | 0.82 | 1.924 | 0.625 | 0 / 0 | undefined (not_applicable) |
| fx | fx:MXN | validation | naive / persistence-bootstrap-w36 | 6 | 24 / 24 | 0.9044 | 2.096 | 0.7083 | 0 / 0 | undefined (not_applicable) |
| fx | fx:MXN | validation | ridge / monthly-projection-ridge-alpha10-w36 | 1 | 24 / 24 | 0.5634 | 1.335 | 0 | 0 / 0 | undefined (not_applicable) |
| fx | fx:MXN | validation | ridge / monthly-projection-ridge-alpha10-w36 | 3 | 24 / 24 | 0.8481 | 1.996 | 0 | 0 / 0 | undefined (not_applicable) |
| fx | fx:MXN | validation | ridge / monthly-projection-ridge-alpha10-w36 | 6 | 24 / 24 | 1.076 | 2.487 | 0 | 0 / 0 | undefined (not_applicable) |
| fx | fx:MYR | test | ets / statsmodels-holt-damped-w60 | 1 | 24 / 24 | 0.09909 | 2.015 | 0.9583 | 24 / 0 | -0.0153 |
| fx | fx:MYR | test | ets / statsmodels-holt-damped-w60 | 3 | 24 / 24 | 0.1749 | 3.564 | 0.8333 | 24 / 0 | -0.04068 |
| fx | fx:MYR | test | ets / statsmodels-holt-damped-w60 | 6 | 24 / 24 | 0.2404 | 4.904 | 0.875 | 24 / 0 | -0.08348 |
| fx | fx:MYR | test | gbm / sklearn-quantile-gbm-w36 | 1 | 24 / 24 | 0.1109 | 2.25 | 0.9167 | 24 / 0 | -0.1362 |
| fx | fx:MYR | test | gbm / sklearn-quantile-gbm-w36 | 3 | 24 / 24 | 0.2034 | 4.154 | 0.75 | 24 / 0 | -0.2103 |
| fx | fx:MYR | test | gbm / sklearn-quantile-gbm-w36 | 6 | 24 / 24 | 0.2718 | 5.563 | 0.625 | 24 / 0 | -0.2245 |
| fx | fx:MYR | test | naive / persistence-bootstrap-w60 | 1 | 24 / 24 | 0.0976 | 1.984 | 0.9583 | 0 / 0 | undefined (not_applicable) |
| fx | fx:MYR | test | naive / persistence-bootstrap-w60 | 3 | 24 / 24 | 0.1681 | 3.424 | 0.8333 | 0 / 0 | undefined (not_applicable) |
| fx | fx:MYR | test | naive / persistence-bootstrap-w60 | 6 | 24 / 24 | 0.2219 | 4.52 | 0.9167 | 0 / 0 | undefined (not_applicable) |
| fx | fx:MYR | test | ridge / monthly-projection-ridge-alpha10-w60 | 1 | 24 / 24 | 0.1005 | 2.044 | 0 | 24 / 0 | -0.02967 |
| fx | fx:MYR | test | ridge / monthly-projection-ridge-alpha10-w60 | 3 | 24 / 24 | 0.1783 | 3.633 | 0 | 24 / 0 | -0.06095 |
| fx | fx:MYR | test | ridge / monthly-projection-ridge-alpha10-w60 | 6 | 24 / 24 | 0.2628 | 5.354 | 0 | 24 / 0 | -0.184 |
| fx | fx:MYR | validation | ets / statsmodels-holt-damped-w60 | 1 | 24 / 24 | 0.1056 | 2.348 | 0.7083 | 0 / 0 | undefined (not_applicable) |
| fx | fx:MYR | validation | ets / statsmodels-holt-damped-w60 | 3 | 24 / 24 | 0.1389 | 3.132 | 0.6667 | 0 / 0 | undefined (not_applicable) |
| fx | fx:MYR | validation | ets / statsmodels-holt-damped-w60 | 6 | 24 / 24 | 0.1959 | 4.539 | 0.4583 | 0 / 0 | undefined (not_applicable) |
| fx | fx:MYR | validation | gbm / sklearn-quantile-gbm-w36 | 1 | 24 / 24 | 0.1176 | 2.619 | 0.5 | 0 / 0 | undefined (not_applicable) |
| fx | fx:MYR | validation | gbm / sklearn-quantile-gbm-w36 | 3 | 24 / 24 | 0.154 | 3.458 | 0.5417 | 0 / 0 | undefined (not_applicable) |
| fx | fx:MYR | validation | gbm / sklearn-quantile-gbm-w36 | 6 | 24 / 24 | 0.1602 | 3.644 | 0.7083 | 0 / 0 | undefined (not_applicable) |
| fx | fx:MYR | validation | naive / persistence-bootstrap-w60 | 1 | 24 / 24 | 0.09943 | 2.219 | 0.6667 | 0 / 0 | undefined (not_applicable) |
| fx | fx:MYR | validation | naive / persistence-bootstrap-w60 | 3 | 24 / 24 | 0.1326 | 2.986 | 0.5833 | 0 / 0 | undefined (not_applicable) |
| fx | fx:MYR | validation | naive / persistence-bootstrap-w60 | 6 | 24 / 24 | 0.1601 | 3.668 | 0.6667 | 0 / 0 | undefined (not_applicable) |
| fx | fx:MYR | validation | ridge / monthly-projection-ridge-alpha10-w60 | 1 | 24 / 24 | 0.1014 | 2.257 | 0 | 0 / 0 | undefined (not_applicable) |
| fx | fx:MYR | validation | ridge / monthly-projection-ridge-alpha10-w60 | 3 | 24 / 24 | 0.1324 | 2.982 | 0 | 0 / 0 | undefined (not_applicable) |
| fx | fx:MYR | validation | ridge / monthly-projection-ridge-alpha10-w60 | 6 | 24 / 24 | 0.1602 | 3.672 | 0 | 0 / 0 | undefined (not_applicable) |
| fx | fx:NAD | test | ets / statsmodels-holt-damped-w60 | 1 | 24 / 24 | 0.4112 | 1.04 | 0.875 | 24 / 0 | 0.06484 |
| fx | fx:NAD | test | ets / statsmodels-holt-damped-w60 | 3 | 24 / 24 | 0.6319 | 1.587 | 0.875 | 24 / 0 | 0.002696 |
| fx | fx:NAD | test | ets / statsmodels-holt-damped-w60 | 6 | 24 / 24 | 0.9022 | 2.244 | 0.75 | 24 / 0 | -0.07504 |
| fx | fx:NAD | test | gbm / sklearn-quantile-gbm-w60 | 1 | 24 / 24 | 0.6194 | 1.562 | 0.4583 | 24 / 0 | -0.4085 |
| fx | fx:NAD | test | gbm / sklearn-quantile-gbm-w60 | 3 | 24 / 24 | 1.04 | 2.612 | 0.3333 | 24 / 0 | -0.6411 |
| fx | fx:NAD | test | gbm / sklearn-quantile-gbm-w60 | 6 | 24 / 24 | 1.5 | 3.719 | 0.4167 | 24 / 0 | -0.7879 |
| fx | fx:NAD | test | naive / persistence-bootstrap-w60 | 1 | 24 / 24 | 0.4397 | 1.112 | 0.875 | 0 / 0 | undefined (not_applicable) |
| fx | fx:NAD | test | naive / persistence-bootstrap-w60 | 3 | 24 / 24 | 0.6336 | 1.593 | 0.875 | 0 / 0 | undefined (not_applicable) |
| fx | fx:NAD | test | naive / persistence-bootstrap-w60 | 6 | 24 / 24 | 0.8392 | 2.093 | 0.875 | 0 / 0 | undefined (not_applicable) |
| fx | fx:NAD | test | ridge / monthly-projection-ridge-alpha10-w60 | 1 | 24 / 24 | 0.5104 | 1.291 | 0 | 24 / 0 | -0.1606 |
| fx | fx:NAD | test | ridge / monthly-projection-ridge-alpha10-w60 | 3 | 24 / 24 | 0.8066 | 2.027 | 0 | 24 / 0 | -0.2731 |
| fx | fx:NAD | test | ridge / monthly-projection-ridge-alpha10-w60 | 6 | 24 / 24 | 1.233 | 3.073 | 0 | 24 / 0 | -0.4689 |
| fx | fx:NAD | validation | ets / statsmodels-holt-damped-w60 | 1 | 24 / 24 | 0.5249 | 1.23 | 0.9583 | 0 / 0 | undefined (not_applicable) |
| fx | fx:NAD | validation | ets / statsmodels-holt-damped-w60 | 3 | 24 / 24 | 0.6542 | 1.543 | 0.9167 | 0 / 0 | undefined (not_applicable) |
| fx | fx:NAD | validation | ets / statsmodels-holt-damped-w60 | 6 | 24 / 24 | 1.062 | 2.502 | 0.875 | 0 / 0 | undefined (not_applicable) |
| fx | fx:NAD | validation | gbm / sklearn-quantile-gbm-w60 | 1 | 24 / 24 | 0.5178 | 1.218 | 0.9167 | 0 / 0 | undefined (not_applicable) |
| fx | fx:NAD | validation | gbm / sklearn-quantile-gbm-w60 | 3 | 24 / 24 | 0.7429 | 1.752 | 0.8333 | 0 / 0 | undefined (not_applicable) |
| fx | fx:NAD | validation | gbm / sklearn-quantile-gbm-w60 | 6 | 24 / 24 | 1.259 | 2.954 | 0.7917 | 0 / 0 | undefined (not_applicable) |
| fx | fx:NAD | validation | naive / persistence-bootstrap-w60 | 1 | 24 / 24 | 0.5063 | 1.185 | 0.9583 | 0 / 0 | undefined (not_applicable) |
| fx | fx:NAD | validation | naive / persistence-bootstrap-w60 | 3 | 24 / 24 | 0.6304 | 1.485 | 0.9167 | 0 / 0 | undefined (not_applicable) |
| fx | fx:NAD | validation | naive / persistence-bootstrap-w60 | 6 | 24 / 24 | 1.034 | 2.438 | 0.875 | 0 / 0 | undefined (not_applicable) |
| fx | fx:NAD | validation | ridge / monthly-projection-ridge-alpha10-w60 | 1 | 24 / 24 | 0.5127 | 1.205 | 0 | 0 / 0 | undefined (not_applicable) |
| fx | fx:NAD | validation | ridge / monthly-projection-ridge-alpha10-w60 | 3 | 24 / 24 | 0.6711 | 1.583 | 0 | 0 / 0 | undefined (not_applicable) |
| fx | fx:NAD | validation | ridge / monthly-projection-ridge-alpha10-w60 | 6 | 24 / 24 | 0.9465 | 2.228 | 0 | 0 / 0 | undefined (not_applicable) |
| fx | fx:OMR | test | ets / statsmodels-holt-damped-w36 | 1 | 24 / 24 | 0 | undefined (no_positive_scale) | 1 | 24 / 0 | undefined (zero_naive_error) |
| fx | fx:OMR | test | ets / statsmodels-holt-damped-w36 | 3 | 24 / 24 | 0 | undefined (no_positive_scale) | 1 | 24 / 0 | undefined (zero_naive_error) |
| fx | fx:OMR | test | ets / statsmodels-holt-damped-w36 | 6 | 24 / 24 | 0 | undefined (no_positive_scale) | 1 | 24 / 0 | undefined (zero_naive_error) |
| fx | fx:OMR | test | gbm / sklearn-quantile-gbm-w36 | 1 | 24 / 24 | 0 | undefined (no_positive_scale) | 1 | 24 / 0 | undefined (zero_naive_error) |
| fx | fx:OMR | test | gbm / sklearn-quantile-gbm-w36 | 3 | 24 / 24 | 0 | undefined (no_positive_scale) | 1 | 24 / 0 | undefined (zero_naive_error) |
| fx | fx:OMR | test | gbm / sklearn-quantile-gbm-w36 | 6 | 24 / 24 | 0 | undefined (no_positive_scale) | 1 | 24 / 0 | undefined (zero_naive_error) |
| fx | fx:OMR | test | naive / persistence-bootstrap-w36 | 1 | 24 / 24 | 0 | undefined (no_positive_scale) | 1 | 0 / 0 | undefined (not_applicable) |
| fx | fx:OMR | test | naive / persistence-bootstrap-w36 | 3 | 24 / 24 | 0 | undefined (no_positive_scale) | 1 | 0 / 0 | undefined (not_applicable) |
| fx | fx:OMR | test | naive / persistence-bootstrap-w36 | 6 | 24 / 24 | 0 | undefined (no_positive_scale) | 1 | 0 / 0 | undefined (not_applicable) |
| fx | fx:OMR | test | ridge / monthly-projection-ridge-alpha10-w36 | 1 | 24 / 24 | 0 | undefined (no_positive_scale) | 1 | 24 / 0 | undefined (zero_naive_error) |
| fx | fx:OMR | test | ridge / monthly-projection-ridge-alpha10-w36 | 3 | 24 / 24 | 0 | undefined (no_positive_scale) | 1 | 24 / 0 | undefined (zero_naive_error) |
| fx | fx:OMR | test | ridge / monthly-projection-ridge-alpha10-w36 | 6 | 24 / 24 | 0 | undefined (no_positive_scale) | 1 | 24 / 0 | undefined (zero_naive_error) |
| fx | fx:OMR | validation | ets / statsmodels-holt-damped-w36 | 1 | 24 / 24 | 0 | undefined (no_positive_scale) | 1 | 0 / 0 | undefined (not_applicable) |
| fx | fx:OMR | validation | ets / statsmodels-holt-damped-w36 | 3 | 24 / 24 | 0 | undefined (no_positive_scale) | 1 | 0 / 0 | undefined (not_applicable) |
| fx | fx:OMR | validation | ets / statsmodels-holt-damped-w36 | 6 | 24 / 24 | 0 | undefined (no_positive_scale) | 1 | 0 / 0 | undefined (not_applicable) |
| fx | fx:OMR | validation | gbm / sklearn-quantile-gbm-w36 | 1 | 24 / 24 | 0 | undefined (no_positive_scale) | 1 | 0 / 0 | undefined (not_applicable) |
| fx | fx:OMR | validation | gbm / sklearn-quantile-gbm-w36 | 3 | 24 / 24 | 0 | undefined (no_positive_scale) | 1 | 0 / 0 | undefined (not_applicable) |
| fx | fx:OMR | validation | gbm / sklearn-quantile-gbm-w36 | 6 | 24 / 24 | 0 | undefined (no_positive_scale) | 1 | 0 / 0 | undefined (not_applicable) |
| fx | fx:OMR | validation | naive / persistence-bootstrap-w36 | 1 | 24 / 24 | 0 | undefined (no_positive_scale) | 1 | 0 / 0 | undefined (not_applicable) |
| fx | fx:OMR | validation | naive / persistence-bootstrap-w36 | 3 | 24 / 24 | 0 | undefined (no_positive_scale) | 1 | 0 / 0 | undefined (not_applicable) |
| fx | fx:OMR | validation | naive / persistence-bootstrap-w36 | 6 | 24 / 24 | 0 | undefined (no_positive_scale) | 1 | 0 / 0 | undefined (not_applicable) |
| fx | fx:OMR | validation | ridge / monthly-projection-ridge-alpha10-w36 | 1 | 24 / 24 | 0 | undefined (no_positive_scale) | 1 | 0 / 0 | undefined (not_applicable) |
| fx | fx:OMR | validation | ridge / monthly-projection-ridge-alpha10-w36 | 3 | 24 / 24 | 0 | undefined (no_positive_scale) | 1 | 0 / 0 | undefined (not_applicable) |
| fx | fx:OMR | validation | ridge / monthly-projection-ridge-alpha10-w36 | 6 | 24 / 24 | 0 | undefined (no_positive_scale) | 1 | 0 / 0 | undefined (not_applicable) |
| fx | fx:SGD | test | ets / statsmodels-holt-damped-w60 | 1 | 24 / 24 | 0.01899 | 1.681 | 0.7917 | 24 / 0 | -0.008264 |
| fx | fx:SGD | test | ets / statsmodels-holt-damped-w60 | 3 | 24 / 24 | 0.02786 | 2.465 | 0.7917 | 24 / 0 | -0.02256 |
| fx | fx:SGD | test | ets / statsmodels-holt-damped-w60 | 6 | 24 / 24 | 0.0236 | 2.084 | 0.9167 | 24 / 0 | 0.05626 |
| fx | fx:SGD | test | gbm / sklearn-quantile-gbm-w60 | 1 | 24 / 24 | 0.02264 | 2.006 | 0.7083 | 24 / 0 | -0.2021 |
| fx | fx:SGD | test | gbm / sklearn-quantile-gbm-w60 | 3 | 24 / 24 | 0.03475 | 3.078 | 0.5417 | 24 / 0 | -0.2754 |
| fx | fx:SGD | test | gbm / sklearn-quantile-gbm-w60 | 6 | 24 / 24 | 0.0319 | 2.817 | 0.9583 | 24 / 0 | -0.2754 |
| fx | fx:SGD | test | naive / persistence-bootstrap-w36 | 1 | 24 / 24 | 0.01883 | 1.668 | 0.7917 | 0 / 0 | undefined (not_applicable) |
| fx | fx:SGD | test | naive / persistence-bootstrap-w36 | 3 | 24 / 24 | 0.02725 | 2.412 | 0.7917 | 0 / 0 | undefined (not_applicable) |
| fx | fx:SGD | test | naive / persistence-bootstrap-w36 | 6 | 24 / 24 | 0.02501 | 2.207 | 0.9167 | 0 / 0 | undefined (not_applicable) |
| fx | fx:SGD | test | ridge / monthly-projection-ridge-alpha10-w36 | 1 | 24 / 24 | 0.0169 | 1.498 | 0 | 24 / 0 | 0.1026 |
| fx | fx:SGD | test | ridge / monthly-projection-ridge-alpha10-w36 | 3 | 24 / 24 | 0.02369 | 2.099 | 0 | 24 / 0 | 0.1305 |
| fx | fx:SGD | test | ridge / monthly-projection-ridge-alpha10-w36 | 6 | 24 / 24 | 0.02062 | 1.825 | 0 | 24 / 0 | 0.1755 |
| fx | fx:SGD | validation | ets / statsmodels-holt-damped-w60 | 1 | 24 / 24 | 0.02006 | 1.857 | 0.7083 | 0 / 0 | undefined (not_applicable) |
| fx | fx:SGD | validation | ets / statsmodels-holt-damped-w60 | 3 | 24 / 24 | 0.0272 | 2.53 | 0.75 | 0 / 0 | undefined (not_applicable) |
| fx | fx:SGD | validation | ets / statsmodels-holt-damped-w60 | 6 | 24 / 24 | 0.03411 | 3.251 | 0.6667 | 0 / 0 | undefined (not_applicable) |
| fx | fx:SGD | validation | gbm / sklearn-quantile-gbm-w60 | 1 | 24 / 24 | 0.02064 | 1.914 | 0.7083 | 0 / 0 | undefined (not_applicable) |
| fx | fx:SGD | validation | gbm / sklearn-quantile-gbm-w60 | 3 | 24 / 24 | 0.0251 | 2.327 | 0.75 | 0 / 0 | undefined (not_applicable) |
| fx | fx:SGD | validation | gbm / sklearn-quantile-gbm-w60 | 6 | 24 / 24 | 0.03199 | 3.017 | 0.7083 | 0 / 0 | undefined (not_applicable) |
| fx | fx:SGD | validation | naive / persistence-bootstrap-w36 | 1 | 24 / 24 | 0.01969 | 1.824 | 0.7083 | 0 / 0 | undefined (not_applicable) |
| fx | fx:SGD | validation | naive / persistence-bootstrap-w36 | 3 | 24 / 24 | 0.02655 | 2.475 | 0.75 | 0 / 0 | undefined (not_applicable) |
| fx | fx:SGD | validation | naive / persistence-bootstrap-w36 | 6 | 24 / 24 | 0.0326 | 3.103 | 0.7083 | 0 / 0 | undefined (not_applicable) |
| fx | fx:SGD | validation | ridge / monthly-projection-ridge-alpha10-w36 | 1 | 24 / 24 | 0.01966 | 1.821 | 0 | 0 / 0 | undefined (not_applicable) |
| fx | fx:SGD | validation | ridge / monthly-projection-ridge-alpha10-w36 | 3 | 24 / 24 | 0.02616 | 2.436 | 0 | 0 / 0 | undefined (not_applicable) |
| fx | fx:SGD | validation | ridge / monthly-projection-ridge-alpha10-w36 | 6 | 24 / 24 | 0.03054 | 2.911 | 0 | 0 / 0 | undefined (not_applicable) |
| fx | fx:ZAR | test | ets / statsmodels-holt-damped-w60 | 1 | 24 / 24 | 0.4429 | 1.122 | 0.875 | 24 / 0 | -0.02855 |
| fx | fx:ZAR | test | ets / statsmodels-holt-damped-w60 | 3 | 24 / 24 | 0.6479 | 1.631 | 0.8333 | 24 / 0 | -0.05066 |
| fx | fx:ZAR | test | ets / statsmodels-holt-damped-w60 | 6 | 24 / 24 | 0.9156 | 2.284 | 0.7917 | 24 / 0 | -0.1068 |
| fx | fx:ZAR | test | gbm / sklearn-quantile-gbm-w60 | 1 | 24 / 24 | 0.6809 | 1.722 | 0.4583 | 24 / 0 | -0.5814 |
| fx | fx:ZAR | test | gbm / sklearn-quantile-gbm-w60 | 3 | 24 / 24 | 1.032 | 2.596 | 0.375 | 24 / 0 | -0.6739 |
| fx | fx:ZAR | test | gbm / sklearn-quantile-gbm-w60 | 6 | 24 / 24 | 1.627 | 4.042 | 0.2917 | 24 / 0 | -0.9663 |
| fx | fx:ZAR | test | naive / persistence-bootstrap-w60 | 1 | 24 / 24 | 0.4306 | 1.091 | 0.875 | 0 / 0 | undefined (not_applicable) |
| fx | fx:ZAR | test | naive / persistence-bootstrap-w60 | 3 | 24 / 24 | 0.6167 | 1.554 | 0.875 | 0 / 0 | undefined (not_applicable) |
| fx | fx:ZAR | test | naive / persistence-bootstrap-w60 | 6 | 24 / 24 | 0.8273 | 2.068 | 0.875 | 0 / 0 | undefined (not_applicable) |
| fx | fx:ZAR | test | ridge / monthly-projection-ridge-alpha10-w60 | 1 | 24 / 24 | 0.509 | 1.29 | 0 | 24 / 0 | -0.1821 |
| fx | fx:ZAR | test | ridge / monthly-projection-ridge-alpha10-w60 | 3 | 24 / 24 | 0.8007 | 2.017 | 0 | 24 / 0 | -0.2984 |
| fx | fx:ZAR | test | ridge / monthly-projection-ridge-alpha10-w60 | 6 | 24 / 24 | 1.231 | 3.076 | 0 | 24 / 0 | -0.488 |
| fx | fx:ZAR | validation | ets / statsmodels-holt-damped-w60 | 1 | 24 / 24 | 0.5206 | 1.223 | 0.9583 | 0 / 0 | undefined (not_applicable) |
| fx | fx:ZAR | validation | ets / statsmodels-holt-damped-w60 | 3 | 24 / 24 | 0.6756 | 1.596 | 0.9167 | 0 / 0 | undefined (not_applicable) |
| fx | fx:ZAR | validation | ets / statsmodels-holt-damped-w60 | 6 | 24 / 24 | 1.063 | 2.508 | 0.9167 | 0 / 0 | undefined (not_applicable) |
| fx | fx:ZAR | validation | gbm / sklearn-quantile-gbm-w60 | 1 | 24 / 24 | 0.5559 | 1.313 | 0.875 | 0 / 0 | undefined (not_applicable) |
| fx | fx:ZAR | validation | gbm / sklearn-quantile-gbm-w60 | 3 | 24 / 24 | 0.7874 | 1.866 | 0.8333 | 0 / 0 | undefined (not_applicable) |
| fx | fx:ZAR | validation | gbm / sklearn-quantile-gbm-w60 | 6 | 24 / 24 | 1.144 | 2.692 | 0.9167 | 0 / 0 | undefined (not_applicable) |
| fx | fx:ZAR | validation | naive / persistence-bootstrap-w60 | 1 | 24 / 24 | 0.4966 | 1.165 | 0.9583 | 0 / 0 | undefined (not_applicable) |
| fx | fx:ZAR | validation | naive / persistence-bootstrap-w60 | 3 | 24 / 24 | 0.6336 | 1.497 | 0.9167 | 0 / 0 | undefined (not_applicable) |
| fx | fx:ZAR | validation | naive / persistence-bootstrap-w60 | 6 | 24 / 24 | 1.042 | 2.459 | 0.875 | 0 / 0 | undefined (not_applicable) |
| fx | fx:ZAR | validation | ridge / monthly-projection-ridge-alpha10-w60 | 1 | 24 / 24 | 0.5142 | 1.213 | 0 | 0 / 0 | undefined (not_applicable) |
| fx | fx:ZAR | validation | ridge / monthly-projection-ridge-alpha10-w60 | 3 | 24 / 24 | 0.6668 | 1.578 | 0 | 0 / 0 | undefined (not_applicable) |
| fx | fx:ZAR | validation | ridge / monthly-projection-ridge-alpha10-w60 | 6 | 24 / 24 | 0.937 | 2.21 | 0 | 0 / 0 | undefined (not_applicable) |
