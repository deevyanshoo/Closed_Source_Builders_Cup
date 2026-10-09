# Hazard and weather exposure (issue #60, offline)

`hazards.py` turns Open-Meteo forecasts and GDACS event alerts into **exposure** route evidence. `locations.py`
resolves facility and port codes to coordinates. Nothing here makes a network request:

- **No default transport.** The fetch functions need a `fetch` callable passed in.
- **Offline mode.** With `OFFLINE=1` they return `offline` without calling it.
- **Unknown locations.** They never call out for a location the registry does not know.
- **Test data.** The payloads in `tests/fixtures/hazards` are hand-written in the public response shapes and labelled synthetic.

## Inputs

| File | What it holds |
|---|---|
| `config/locations_v1.json` | Registry `locations-v1`: code, kind, name, lat, lon, source, `verified`. The coordinates are hand-entered approximations, **not verified** against UN/LOCODE. A code that is not listed, including synthetic plant codes, is `unsupported_location`; coordinates are never guessed |
| `config/hazard_sources_v1.json` | Thresholds `hazard-thresholds-v1`, as exact decimal strings. Open-Meteo: hourly wind of 62 km/h or more (Beaufort 8), gusts of 89 km/h or more (Beaufort 10), or 64.5 mm or more of rain in a rolling 24 hours (the IMD "heavy rainfall" lower bound), all within 72 hours after the cutoff. GDACS: an Orange or Red alert within 300 km |

## States

The provider answer for each location is one assessment, in one of these states:

| State | Meaning |
|---|---|
| `exposure_found` | A threshold is met. This produces a route-evidence record |
| `no_exposure_found` | Every rule was decided and none was met. It is **not** a statement of safety |
| `insufficient_data` | A rule could not be decided, because of gaps in the data or a forecast that does not cover the window |
| `unsupported_location` | The registry does not know the code |
| `offline` | `OFFLINE=1` was set, so nothing was fetched |
| `provider_failure` | An HTTP error, a timeout or a transport error |
| `rate_limited` | HTTP 429 |
| `malformed_payload` | Invalid JSON, a missing hourly block, an unexpected unit, irregular times, or an answer for a different grid point |
| `not_fetched` | No recorded response exists for the location |
| `retrieved_after_as_of` | Rejected, because the answer was not available at the cutoff |

How `hazard_exposure`:
- **Stale answers:** an answer whose `valid_until` is before the cutoff stays visible, marked stale and unusable. Its route evidence lands in the `expired` bucket.
- **Repeated fetches:** they are deduplicated by provider, location, period and payload SHA-256, keeping the earliest retrieval.
- **Record shape:** records follow exactly the shape `normalize_route_evidence` accepts:
  - status `exposure`, never `confirmed`, and no operating status
  - source kind `weather_service` (Open-Meteo) or `hazard_feed` (GDACS)
  - `content_sha256` is the hash of the payload
- **Values and thresholds:** the summary text states the values and thresholds, and `details[evidence_id]` holds the structured values with their units.
- **Arrival dates:** nothing changes them. Shipment statuses do not yet read hazard evidence; adding it to `shipment_list` would make shipments `at_risk`, and should be a reviewed follow-up.

`GET /dispatch/hazards?location=...&as_of=...` serves these over the recorded synthetic responses. `/dispatch` shows them
in the route-evidence panel with an "Exposure, not confirmed" label.

## What a live run needs (not done; needs approval)

The terms and attribution notes below were written without visiting the providers' pages in this offline phase. Confirm each one during the access and rights review.

| | Open-Meteo | GDACS |
|---|---|---|
| Endpoint | `GET https://api.open-meteo.com/v1/forecast` with the query in `config/hazard_sources_v1.json` | `GET https://www.gdacs.org/gdacsapi/api/events/geteventlist/SEARCH` |
| Key or account | None | None |
| Terms | The free hosted API is for **non-commercial use only**. Commercial use needs a paid plan, which we must not take without approval | European Commission JRC and UN OCHA service. Check the terms of use and disclaimer before relying on it |
| Attribution | "Weather data by Open-Meteo.com" (CC BY 4.0) | Credit GDACS as the source |
| Proposed budget | One request per supported location per refresh. 8 registry locations, 4 refreshes a day, so at most 32 a day, kept to a single bounded check first | One request per refresh, covering all locations. At most 4 a day |

Before a live run:
1. **Verify the registry.** Check its coordinates against UN/LOCODE.
2. **Get approval.** Confirm the terms and the request budget.
3. **Run a bounded check.** Record it with the date, revision and request count, in a separate file from the tests.
