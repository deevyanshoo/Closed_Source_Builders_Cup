# Planning calendar and observation dates

This issue7 increment corrects date-to-value mapping in the existing monthly Python model. It does not establish global coverage or implement expanded news ingestion.

## Semantics

A price observation date is a monthly period marker, not publication or first availability. Planning offset0 is the explicitly captured planning calendar month; offsetm is that month plus m. Historical simulate calls that omit a planning month retain the last observation as origin. Backtest origin truncation and past-only selection/calibration remain unchanged.

Live simulation fits the existing selected model from available joined observations through H+L monthly steps, where L is the calendar lag between last observation and planning month. It retains the forecast path values from columns L..L+H and their accumulated uncertainty. Intermediate and planning-month values are forecasts when L>0; the actual history and last observed price remain unchanged. This does not create a current observation or price publication date.

The existing current_inr_per_kg compatibility field remains the last observed joined monthly price. Additive observation_end/planning_month/bridge_months/basis/publication_at metadata explains the distinction. Unknown publication_at is null. Retrieved source timestamps remain provenance, never publication substitutes. Explicit warnings are propagated by the existing Go adapter into React.

Current heuristic news effects begin no earlier than the transition into the planning month. This is an assumed scenario drift/spread effect, not a trained causal estimate; ingestion adapters and Taher ownership remain unchanged.

Supplied dated contexts are sliced by their real date axes and values; they must cover the requested decision horizon. Invalid/gapped/future axes or insufficient coverage produce a narrow calendar-unavailable state. The request is never silently shortened, and missing paths are never fabricated.

## Compatibility and evidence

Python ready endpoint shapes retain their existing fields with additive date metadata. Calendar unavailability has a semantic Python503 code; the existing gateway conservatively surfaces generic recommendation-service unavailability. The gateway does not expose the Python forecast chart. No Go/React/chart expansion is planned.

Regression coverage: controlled2026-10-15/August synthetic input with hand-checked120 planning value versus100 observed value; genuine simulation retains bridged uncertainty; zero-lag/year rollover/short path/future/gap boundaries; actual build_context propagation; API serialization and calendar semantic errors; preserved full Python suite including future-price poisoning through actual historical selection. The full Python suite also preserves the existing inventory and historical replay checks.

Existing evaluation horizons1..6 are relative to observation origins. Bridging extends total observation-to-target horizons and does not establish newly calibrated coverage. Monthly history may be revised and lacks publication-vintage availability, so calendar alignment does not prove historical point-in-time ingestion. Synthetic checks do not prove live market or Gemini/news provider behavior. Prior services remain untouched.

## Additive metadata example

Synthetic August observations with an October planning month produce the following fields in the recommendation forecast and forecast endpoint. These dates are illustrative monthly markers, not observed provider evidence.

```json
{
  "observation_end": "2026-08-01",
  "planning_month": "2026-10-01",
  "bridge_months": 2,
  "planning_offset_zero_basis": "forecast",
  "publication_at": null
}
```

On a hand-checkable supplied path with August100, September110 and October120, planning offset0 uses October120. The compatibility current-price field remains August100. A supplied path need only cover the requested decision horizon after the bridge; the normal live context produces the complete six-month planning window.

Calendar errors return Python HTTP503 with `detail.code=planning_calendar_unavailable` and an explanatory message. Other errors keep their existing handling. Valid observation history for live bridging must contain ordered, unique, consecutive month-start periods. The selected GBM cannot form a window without observable training targets; the engine rejects that window rather than silently choosing another model.

Existing reference UI labels such as “Price today” refer to the compatibility last-observed field. Its dated help text and stale-input warning must be read together; this increment preserves that interface. React retains source observation/retrieval dates and forwards the stale warning, while Go currently maps calendar503 to generic `provider_unavailable`. No consumer or running reference-server change was made here.

Current-news drift/widening remains an assumed scenario applied from the transition into the planning month (or the first future transition at zero lag). Stale plans receive an explicit confidence downgrade because existing observation-origin calibration is not validation of their bridged horizons. Unknown publication/vintage availability remains a separate limitation.
