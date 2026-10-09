# Dispatch workspace (issue #5)

Read-only views over shipment milestones: what each shipment's status is and why, its timeline,
which shipments share a location with an event, route evidence, working-day lead time, and a
before-and-after replay of one synthetic event. Nothing here writes, sends or computes recovery.

| Module | Role |
|---|---|
| `../events/ingest/milestones.py` | Normalises raw milestone records at a cutoff (`as_of`): validates, de-duplicates, rejects with reasons, flags |
| `read_models.py` | `shipment_timeline` (events plus current ETA, hold and delivered quantity) and `affected_shipments` (exposure only) |
| `summary.py` | `shipment_list`: one evidence-based status per shipment with the signals behind it |
| `../route_evidence/evidence.py` | Route and hazard evidence, split into confirmed operating status and exposure, with expiry |
| `lead_time.py` | Working-day lead time for a timeline over a holiday provider (`../calendars/`) |
| `replay.py` | `replay_event`: inject one labelled synthetic milestone and compare before and after |
| `../route_evidence/hazards.py` | Open-Meteo and GDACS exposure from recorded synthetic responses (see `../route_evidence/HAZARDS.md`) |
| `samples.py`, `http.py` | The synthetic fixtures the routes serve, and the read-only `/dispatch` routes |

## Status rules

Precedence, from `summary.py`:

1. `delivered`: there is an accepted delivered milestone.
2. `on_hold`: there is an open port hold.
3. `delayed`: the expected arrival (actual arrival, else the open ETA) is after the plan baseline.
4. `at_risk`: the shipment has not arrived, and current route evidence touches its facilities or lane ends.
5. `on_time`: one plan baseline exists, and the expected arrival is not after it.
6. `unknown`: none of the above can be shown, for example when there is no baseline.

Sharing a port or lane with an event is **exposure**, never a confirmed delay. Only an operating
source (a port authority, carrier or facility operator) can confirm an operating status.
Weather and hazard sources are always exposure.

## Replay

`GET /dispatch/replay?sample=<id>&as_of=...` runs one bundled sample from
`tests/fixtures/shipments/replay_events.json` through the same normaliser and read models. The
result has:
- **Before and after:** shipment rows, and `changes` with the fields that changed and the signals added and removed.
- **Timeline:** before and after, with the injected entry marked `replay_injected`.
- **Exposure:** the shipments exposed to the injected event.
- **Route evidence:** the evidence at its location, which the replay never changes.
- **Material screen:** Divyanshu's unmodified `project_arrival_change`, run only when the event states an arrival time. Otherwise the result gives the reason it was not run (for example `hold_without_new_eta`).

The replay rejects:
- an event that occurred, was observed or had evidence retrieved after `as_of` (`event_after_as_of`)
- an event not labelled synthetic
- a reused event ID
- anything the normaliser rejects

The result links to `/shipment-recovery` and never computes recovery options.

## Run it locally

From `procurement-engine/`:

```bash
.venv/bin/python -m pytest tests/test_milestone_*.py tests/test_dispatch_*.py tests/test_route_evidence.py \
    tests/test_calendars.py tests/test_nager_adapter.py
make api
curl "http://127.0.0.1:8000/dispatch/shipments?as_of=2026-10-08T12:00:00Z"
curl "http://127.0.0.1:8000/dispatch/shipments/SHP-SYN-004?as_of=2026-10-08T12:00:00Z"
curl "http://127.0.0.1:8000/dispatch/events/evt-002-hold/affected?as_of=2026-10-08T12:00:00Z"
curl "http://127.0.0.1:8000/dispatch/route-evidence?location=INNSA&as_of=2026-10-08T12:00:00Z"
curl "http://127.0.0.1:8000/dispatch/replay/samples"
curl "http://127.0.0.1:8000/dispatch/replay?sample=innsa-release&as_of=2026-10-08T12:00:00Z"
curl "http://127.0.0.1:8000/dispatch/hazards?location=INNSA&location=INPRT&as_of=2026-10-08T12:00:00Z"
```

`make api` fits the forecast models at startup, so wait for "Application startup complete" (about 20 seconds
on a laptop) before calling. Every route needs `as_of`, except `/replay/samples`. `now` is optional: it defaults to the server clock, and the
response reports which one was used.

Response states:
- **Every route:** `ok`, `empty`, `stale` (cutoff older than a day) or `error`.
- **HTTP codes:** 400 for bad parameters, 404 for an unknown event or sample, and 503 when the synthetic data is missing or unlabelled.

The web page is `/dispatch` in `apps/web` (`npm run dev`). It uses a labelled local adapter, the
Python output exported by `scripts/export_dispatch_fixtures_for_web.py` at the fixed cutoff
2026-10-08T12:00:00Z. Re-run that script after changing any read model.

## The page

The final click path and expected screens are in `docs/Demo_Quotes_and_Dispatch.md`, and the release checks in
`docs/Walkthrough_Checklist_Taher.md`. The page:
- **Shipments:** lists them by status, and shows the selected shipment's timeline, working-day lead time and route evidence.
- **Hazard and weather exposure:** a panel labelled "Exposure, not confirmed".
- **Replay:** a sample delay, before and after.
- **Export:** "Download synthetic JSON" and "Download synthetic CSV" (`dispatchReport` in `apps/web/src/dispatch.ts`) save the selected shipment's status, timeline, lead time, route evidence and hazards. Every JSON file and every CSV row is labelled synthetic, and cells are neutralised against spreadsheet formulas.
- **Accessibility:** header navigation marks the current page. Status, signal, evidence and assessment chips carry screen-reader text. All controls are keyboard reachable with a visible focus outline, and tap targets are at least 24 by 24 px. It was checked with a contrast and labelling audit in headless Chrome at 1440, 768 and 390 px.

## What is synthetic

Everything:
- the milestones, plan snapshot, route evidence and replay events in `tests/fixtures/shipments` and `tests/fixtures/route_evidence`
- the holiday table in `tests/fixtures/calendars`, whose dates are invented
- the Open-Meteo and GDACS payloads in `tests/fixtures/hazards`, which are hand-written

Every file is labelled `synthetic: true`, and the routes refuse to serve a file that is not.

## What is verified

- **Normaliser and read models:** covered by `tests/test_milestone_normalizer.py`, `tests/test_dispatch_read_models.py`, `tests/test_dispatch_shipment_list.py` and `tests/test_route_evidence.py`. This includes duplicates, out-of-order records, expired evidence, the cutoff and exact decimal quantities.
- **Material screen:** `tests/test_milestone_propagation.py` checks that milestones convert to the scenarios the propagation screen accepts.
- **Replay:** `tests/test_dispatch_replay.py` and `tests/test_dispatch_replay_api.py` check that it is deterministic, does not mutate its inputs, labels the injected event, rejects late events and keeps exposure separate from confirmed status.
- **Calendars:** `tests/test_calendars.py`, `tests/test_nager_adapter.py` and `tests/test_dispatch_lead_time.py` check lead times and the explicit unsupported states. The Nager tests use mocked responses only.
- **Web page:** `apps/web/src/dispatch.test.ts` and `dispatch-page.test.tsx` cover it. The page was also checked in headless Chrome at desktop and 390 px widths.

## Known limits

- **No live data.** There is no shipment store, carrier feed, port feed or weather feed.
- **Gateway.** The page is not connected to the gateway yet. It needs these routes under `GET /v2/workspaces/{workspace_id}/dispatch/`:
  - `shipments` and `shipments/{id}`
  - `events/{id}/affected`
  - `route-evidence`
  - `replay/samples` and `replay`
  - `hazards`
- **Hazards.** No provider has been called live. The location coordinates are unverified, and shipment statuses do not yet use hazard exposure.
- **Recovery link.** `/shipment-recovery` is linked but cannot preselect the shipment yet.
- **Sample coverage.** At the sample cutoff, no shipment is `delivered` or `at_risk`. Unit tests cover both statuses.
- **Lead time.**
  - It uses UTC calendar dates and the synthetic holiday table.
  - Every fixture destination is in India, which has no approved real holiday source (see `../calendars/README.md`).
  - Plant shifts and port cutoffs are not modelled.
- **Replay inputs.** The replay accepts only the bundled samples over HTTP. The Python function accepts any labelled synthetic record.
