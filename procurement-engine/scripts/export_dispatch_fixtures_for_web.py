"""Export the dispatch read models for the web app's local adapter.

Runs the same read models the read-only /dispatch routes use, over the labelled synthetic fixtures,
at a fixed cutoff, and writes apps/web/src/dispatch-fixtures.json. The web page uses this file only
until the gateway serves dispatch routes, and labels it as a local synthetic adapter. The export
records ``now`` equal to the cutoff; the page judges freshness against the browser clock.

Run from procurement-engine/:  .venv/bin/python scripts/export_dispatch_fixtures_for_web.py
"""
from __future__ import annotations

import json
import hashlib
import os
import subprocess
import sys
import tempfile
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
    temporary: Path | None = None
    try:
        data = samples.load_samples()
        normalized = normalize_milestones(data["milestones"]["records"], as_of=AS_OF)
        evidence = normalize_route_evidence(data["route_evidence"]["records"], as_of=AS_OF)
        listing = shipment_list(normalized, now=AS_OF, snapshot=data["snapshot"], route_evidence=evidence)
        _require_readable(listing)
        timelines = {row["shipment_id"]: shipment_timeline(normalized, row["shipment_id"], now=AS_OF,
                                                           records=data["milestones"]["records"])
                     for row in listing["shipments"]}
        calendar = SyntheticHolidayProvider()
        for timeline in timelines.values():
            timeline["lead_time"] = shipment_lead_time(timeline, calendar)
        unavailable = sorted(e["event_id"] for e in normalized["events"] if not e["operating_evidence"])
        affected = {e["event_id"]: affected_shipments(normalized, e["event_id"], now=AS_OF)
                    for e in normalized["events"] if e["operating_evidence"]}
        route = {row["shipment_id"]: evidence_for_locations(evidence, row["location_keys"])
                 for row in listing["shipments"] if row["location_keys"]}
        for result in (*timelines.values(), *affected.values(), *route.values()):
            _require_readable(result)
        replay_content = samples.load_replay_samples()
        replay = {"notice": replay_content["notice"],
                  "samples": [samples.sample_view(sample) for sample in replay_content["samples"]],
                  "results": {sample["sample_id"]: {
                      **replay_event(data["milestones"]["records"], sample["record"], as_of=AS_OF, now=AS_OF,
                                     snapshot=data["snapshot"],
                                     route_evidence_records=data["route_evidence"]["records"]),
                      "sample": samples.sample_view(sample)} for sample in replay_content["samples"]}}
        revision_result = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                                         capture_output=True, text=True, check=True)
        revision = revision_result.stdout.strip()
        if not revision:
            raise ValueError("missing_revision")
        source_hashes = {name: hashlib.sha256(Path(path).read_bytes()).hexdigest()
                         for name, path in samples.SAMPLE_FILES.items()}
        out = {"synthetic": True, "source": "local_fixture_adapter", "generated_from_revision": revision,
               "as_of": listing["as_of"], "notice": samples.SOURCE["notice"], "files": samples.source_info()["files"],
               "source_sha256": source_hashes, "unavailable_affected_event_ids": unavailable,
               "shipments": listing, "timelines": timelines, "affected": affected, "route_evidence": route,
               "replay": replay}
        payload = (json.dumps(out, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        fd, name = tempfile.mkstemp(prefix=f".{OUT.name}.", suffix=".tmp", dir=OUT.parent)
        temporary = Path(name)
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, OUT)
        temporary = None
    except (samples.SampleUnavailable, ValueError, OSError, KeyError, TypeError,
            subprocess.CalledProcessError):
        print("Dispatch export failed; prior fixture retained.", file=sys.stderr)
        return 1
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    print(f"Wrote {len(timelines)} shipments, {len(affected)} events, {len(replay['results'])} replays to {OUT} ({len(payload) // 1024} KiB)")
    return 0


def _require_readable(result: object) -> None:
    if (not isinstance(result, dict) or result.get("status") not in {"ok", "empty", "stale"}
            or not isinstance(result.get("reasons"), list)
            or not all(isinstance(reason, str) for reason in result["reasons"])):
        raise ValueError("unreadable_dispatch_result")


if __name__ == "__main__":
    raise SystemExit(main())
