# Issue 1 numerical correctness evidence

Scope: [issue 1](https://github.com/deevyanshoo/Closed_Source_Builders_Cup/issues/1), based on approved planning commit `7ff75b8caf5a97558afcccf6c96cd30506c5b7bc`. Work branch: `feature/core-correctness`. Baseline history, endpoint shapes, reference UI, schedule enumeration and cost/risk comparisons are retained. Only decision/replay/selection code and directly related tests are changed.

## Inventory convention

A period is one month of consumption. The horizon is periods `0` through `need_by_month - 1`. An order bought in month `m` arrives at the start of month `m + lead_time`, before that period's consumption. Zero-lead month-0 orders are usable immediately. An arrival exactly at `need_by_month` is on hand at the deadline but cannot supply consumption in an earlier period.

Every period adds arriving lots, serves demand from available stock, records unmet kilograms and carries nonnegative remaining stock forward. Unmet demand is lost demand, not a backlog; a later shipment does not retroactively satisfy it. The shortage penalty remains unmet kilograms multiplied by current landed price and the configured multiplier. It is a soft penalty, not a feasibility constraint. If the selected schedule still misses demand, the existing warnings and explanation identify that shortage; no quantity or arrival is moved to force feasibility.

Hand cases with consumption of 1,000 kg/month over three periods:

- Zero stock, 1,000 kg order, 20% now/80% month 2, zero lead: 800 + 1,000 + 200 = 2,000 kg unmet.
- The same order with 2,000 kg starting stock: zero unmet.
- Zero stock, 3,000 kg order, 20% now/80% month 2: 1,400 kg unmet despite sufficient total supply. Buying all now or thirds at months 0/1/2 covers demand.
- A delivery arriving after the horizon does not cover any earlier consumption; its shortage remains visible.

## Historical replay

Every replay origin selects models and calibrates residual scale from its history prefix. A non-persisting selection helper retains the existing model comparison and calibration method; ordinary latest evaluation still persists its results. Historical replay does not overwrite current selection tables.

The replay result cache uses the full frame content and configuration, and uncached replay clears existing forecast fit caches whose keys otherwise omit interior observations. The regression poisons an interior future month while preserving history endpoints; it proves two actual decisions run, earlier model/scale/decision agree, and a realised even-split comparison changes. This prevents a cache hit from masquerading as future-data isolation.

## Verification and review ledger

- Inventory red: three parameter cases failed because first-arrival scoring missed subsequent demand.
- Replay red: actual `run_backtest` regression failed on present-day persisted selection access.
- Inventory focused green initially: 15 passed. Additional sufficient-total, timely split, late-arrival and recommendation explanation cases were then added.
- Full preserved offline Python suite after production repairs: 70 passed in about 37 seconds; tightened regression passed separately in 17.3 seconds; final full suite after the tightened regression: 70 passed in about 36.5 seconds. Only the existing Starlette/httpx TestClient deprecation warning remains. `git diff --check` passed.
- Bounded task/whole-branch review of numerical commit `e13804a`: spec compliance and code quality approved; no Critical or Important findings. A synthetic serial changed-prefix probe confirmed two replay misses cleared production fit caches and cached residuals matched a fresh fit, without database or provider use.
- Minor (deferred): the suffix-poisoning regression manually clears fit caches, so it does not directly protect the production cache-clear safeguard. Current behavior passed the independent probe; add a warm-cache changed-prefix regression when next editing these tests. Concurrent cache invalidation was not proved by the serial probe.

Tests use the existing virtualenv read-only with `PYTHONPATH` pointing at this worktree's `procurement-engine/src`, `PYTHONDONTWRITEBYTECODE=1`, and the existing isolated temporary fixture database. Reference servers and provider/network smoke were not exercised.

## Limitations and next dependency

Historical observation dates are not publication/first-available timestamps. Revised values may differ from the original release; this is an observation-date replay, not a publication-aware backtest. Historical news is excluded. Realised comparison totals remain purchase/landed costs, not measured stockout losses or customer savings. Selection/calibration work grows per origin; no latency claim is made here.

The compatibility planner still uses monthly periods, soft penalties, existing fixed quantity and 20% schedule increments. Global offer, currency, capacity and cash contracts remain separate follow-up work. Gemini/Google provider behavior is unverified in this package. Merge is not authorized by this kickoff.
