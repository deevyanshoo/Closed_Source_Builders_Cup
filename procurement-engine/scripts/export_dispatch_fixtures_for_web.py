"""Export the dispatch read models for the web app's local adapter.

Runs the same read models the read-only /dispatch routes use, over the labelled synthetic fixtures,
at a fixed cutoff, and writes apps/web/src/dispatch-fixtures.json. The web page uses this file only
until the gateway serves dispatch routes, and labels it as a local synthetic adapter. The export
records ``now`` equal to the cutoff; the page judges freshness against the browser clock.

Run from procurement-engine/:  .venv/bin/python scripts/export_dispatch_fixtures_for_web.py
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from procure.calendars import SyntheticHolidayProvider  # noqa: E402
from procure.dispatch import affected_shipments, replay_event, samples, shipment_list, shipment_timeline  # noqa: E402
from procure.dispatch.lead_time import shipment_lead_time  # noqa: E402
from procure.events.ingest import normalize_milestones  # noqa: E402
from procure.route_evidence import evidence_for_locations, normalize_route_evidence  # noqa: E402

OUT = ROOT.parent / "apps" / "web" / "src" / "dispatch-fixtures.json"
AS_OF = "2026-10-08T12:00:00Z"


def main() -> int:
    data = samples.load_samples()
    normalized = normalize_milestones(data["milestones"]["records"], as_of=AS_OF)
    evidence = normalize_route_evidence(data["route_evidence"]["records"], as_of=AS_OF)
    listing = shipment_list(normalized, now=AS_OF, snapshot=data["snapshot"], route_evidence=evidence)
    timelines = {row["shipment_id"]: shipment_timeline(normalized, row["shipment_id"], now=AS_OF,
                                                       records=data["milestones"]["records"])
                 for row in listing["shipments"]}
    calendar = SyntheticHolidayProvider()
    for timeline in timelines.values():
        timeline["lead_time"] = shipment_lead_time(timeline, calendar)
    affected = {e["event_id"]: affected_shipments(normalized, e["event_id"], now=AS_OF) for e in normalized["events"]}
    route = {row["shipment_id"]: evidence_for_locations(evidence, row["location_keys"])
             for row in listing["shipments"] if row["location_keys"]}
    replay_content = samples.load_replay_samples()
    replay = {"notice": replay_content["notice"],
              "samples": [samples.sample_view(sample) for sample in replay_content["samples"]],
              "results": {sample["sample_id"]: {
                  **replay_event(data["milestones"]["records"], sample["record"], as_of=AS_OF, now=AS_OF,
                                 snapshot=data["snapshot"], route_evidence_records=data["route_evidence"]["records"]),
                  "sample": samples.sample_view(sample)} for sample in replay_content["samples"]}}
    revision = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True,
                              text=True).stdout.strip()
    out = {"synthetic": True, "source": "local_fixture_adapter", "generated_from_revision": revision,
           "as_of": listing["as_of"], "notice": samples.SOURCE["notice"], "files": samples.source_info()["files"],
           "shipments": listing, "timelines": timelines, "affected": affected, "route_evidence": route,
           "replay": replay}
    OUT.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"Wrote {len(timelines)} shipments, {len(affected)} events, {len(replay['results'])} replays to {OUT} ({OUT.stat().st_size // 1024} KiB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
