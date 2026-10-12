"""Export the dispatch read models for the web app's local adapter.

Runs the same read models the read-only /dispatch routes use, over the labelled synthetic fixtures,
at a fixed cutoff, and writes apps/web/src/dispatch-fixtures.json. The web page uses this file only
until the gateway serves dispatch routes, and labels it as a local synthetic adapter. The export
records ``now`` equal to the cutoff; the page judges freshness against the browser clock.

Run from procurement-engine/:  .venv/bin/python scripts/export_dispatch_fixtures_for_web.py

``--as-of 2026-10-14T12:00:00Z`` exports at another cutoff. Every synthetic time (milestones, plan snapshot, route
evidence, replay events, recorded hazard responses and the synthetic holiday table) moves by the same whole number of
days, so statuses, signals, replay outcomes, hazard states and distances stay the same. Working-day lead times can
change because weekends do not move; the export lists any change under ``cutoff_shift.lead_time_changes``.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import hashlib
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from procure.calendars import SyntheticHolidayProvider  # noqa: E402
from procure.calendars.synthetic import TABLE as HOLIDAY_TABLE  # noqa: E402
from procure.dispatch import affected_shipments, replay_event, samples, shipment_list, shipment_timeline  # noqa: E402
from procure.dispatch.lead_time import shipment_lead_time  # noqa: E402
from procure.events.ingest import normalize_milestones  # noqa: E402
from procure.route_evidence import evidence_for_locations, normalize_route_evidence  # noqa: E402
from procure.route_evidence.hazards import hazard_exposure, hazards_for_locations, recorded_assessments  # noqa: E402

OUT = ROOT.parent / "apps" / "web" / "src" / "dispatch-fixtures.json"
AS_OF = "2026-10-08T12:00:00Z"


_TIME = re.compile(r"^(\d{4}-\d{2}-\d{2})(T\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:\d{2})?)?$")


def shift_times(value, days: int):
    """Move the date part of every full date or timestamp string by whole days; keep its format and offset."""
    if isinstance(value, dict):
        return {key: shift_times(item, days) for key, item in value.items()}
    if isinstance(value, list):
        return [shift_times(item, days) for item in value]
    if isinstance(value, str) and days and (match := _TIME.match(value)):
        moved = dt.date.fromisoformat(match.group(1)) + dt.timedelta(days=days)
        return moved.isoformat() + (match.group(2) or "")
    return value


def _shift_days(as_of: str) -> int:
    """Whole days from the default cutoff to ``as_of``; anything else is refused."""
    if not isinstance(as_of, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(Z|[+-]\d{2}:\d{2})", as_of):
        raise ValueError("as_of must be a full UTC instant such as 2026-10-14T12:00:00Z")
    delta = dt.datetime.fromisoformat(as_of.replace("Z", "+00:00")) - dt.datetime.fromisoformat(AS_OF.replace("Z", "+00:00"))
    if delta.seconds or delta.microseconds:
        raise ValueError("as_of must be a whole number of days from 2026-10-08T12:00:00Z (keep 12:00:00Z)")
    return delta.days


def _shifted_file(source: Path, target: Path, days: int) -> None:
    """Copy one synthetic file, moving its times; files without times are copied byte for byte."""
    body = source.read_bytes()
    try:
        data = json.loads(body)
    except ValueError:
        target.write_bytes(body)
        return
    moved = shift_times(data, days)
    if moved == data:
        target.write_bytes(body)
    else:
        target.write_text(json.dumps(moved, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build_export(as_of: str | None = None) -> dict:
    """The fixture the web adapter reads, at the default cutoff or shifted to ``as_of``. Writes nothing to apps/web."""
    days = 0 if as_of is None else _shift_days(as_of)
    cutoff = shift_times(AS_OF, days) if days else AS_OF
    with tempfile.TemporaryDirectory(prefix="dispatch-cutoff-") as directory:
        work = Path(directory)
        holiday_table = HOLIDAY_TABLE
        recordings = samples.HAZARD_RECORDINGS
        if days:
            holiday_table = work / HOLIDAY_TABLE.name
            _shifted_file(HOLIDAY_TABLE, holiday_table, days)
            table = json.loads(holiday_table.read_text(encoding="utf-8"))
            years = set(table["years"])
            dates = [row["date"] for entry in table["countries"].values() for row in entry["holidays"]]
            if any(int(d[:4]) not in years for d in dates) or int(cutoff[:4]) not in years:
                raise ValueError("as_of moves the synthetic holiday table outside its years")
            hazard_dir = work / "hazards"
            hazard_dir.mkdir()
            for path in samples.HAZARD_RECORDINGS.parent.glob("*.json"):
                _shifted_file(path, hazard_dir / path.name, days)
            recordings = hazard_dir / samples.HAZARD_RECORDINGS.name
        out = _build(cutoff, days, SyntheticHolidayProvider(holiday_table), recordings)
    if days:
        before = {sid: _lead_view(t["lead_time"]) for sid, t in build_export()["timelines"].items()}
        changes = [{"shipment_id": sid, "before": before[sid], "after": _lead_view(t["lead_time"])}
                   for sid, t in sorted(out["timelines"].items()) if _lead_view(t["lead_time"]) != before[sid]]
        out["cutoff_shift"] = {"from": "2026-10-08T12:00:00+00:00", "to": out["as_of"], "days": days,
                               "lead_time_changes": changes,
                               "notice": "Synthetic times moved by whole days to this cutoff; results are unchanged."}
    return out


def _lead_view(lead: dict) -> dict:
    return {key: lead.get(key) for key in ("status", "reason", "working_days", "calendar_days", "weekend_days")} | \
        {"holidays": len(lead.get("holidays") or [])}


def _build(cutoff: str, days: int, calendar, recordings: Path) -> dict:
    data = shift_times(samples.load_samples(), days)
    normalized = normalize_milestones(data["milestones"]["records"], as_of=cutoff)
    evidence = normalize_route_evidence(data["route_evidence"]["records"], as_of=cutoff)
    listing = shipment_list(normalized, now=cutoff, snapshot=data["snapshot"], route_evidence=evidence)
    _require_readable(listing)
    timelines = {row["shipment_id"]: shipment_timeline(normalized, row["shipment_id"], now=cutoff,
                                                       records=data["milestones"]["records"])
                 for row in listing["shipments"]}
    for timeline in timelines.values():
        timeline["lead_time"] = shipment_lead_time(timeline, calendar)
    unavailable = sorted(e["event_id"] for e in normalized["events"] if not e["operating_evidence"])
    affected = {e["event_id"]: affected_shipments(normalized, e["event_id"], now=cutoff)
                for e in normalized["events"] if e["operating_evidence"]}
    route = {row["shipment_id"]: evidence_for_locations(evidence, row["location_keys"])
             for row in listing["shipments"] if row["location_keys"]}
    keys = sorted({k for row in listing["shipments"] for k in row["location_keys"]})
    exposure = hazard_exposure(recorded_assessments(recordings, as_of=cutoff, location_codes=keys), as_of=cutoff)
    hazards = {row["shipment_id"]: hazards_for_locations(exposure, row["location_keys"])
               for row in listing["shipments"] if row["location_keys"]}
    for result in (*timelines.values(), *affected.values(), *route.values()):
        _require_readable(result)
    replay_content = shift_times(samples.load_replay_samples(), days)
    replay_results = {}
    for sample in replay_content["samples"]:
        result = replay_event(data["milestones"]["records"], sample["record"], as_of=cutoff, now=cutoff,
                              snapshot=data["snapshot"], route_evidence_records=data["route_evidence"]["records"])
        _require_replay_result(result, sample)
        replay_results[sample["sample_id"]] = {**result, "sample": samples.sample_view(sample)}
    replay = {"notice": replay_content["notice"],
              "samples": [samples.sample_view(sample) for sample in replay_content["samples"]],
              "results": replay_results}
    revision_result = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                                     capture_output=True, text=True, check=True)
    revision = revision_result.stdout.strip()
    if not revision:
        raise ValueError("missing_revision")
    source_hashes = {name: hashlib.sha256(Path(path).read_bytes()).hexdigest()
                     for name, path in samples.SAMPLE_FILES.items()}
    return {"synthetic": True, "source": "local_fixture_adapter", "generated_from_revision": revision,
            "as_of": listing["as_of"], "notice": samples.SOURCE["notice"], "files": samples.source_info()["files"],
            "source_sha256": source_hashes, "unavailable_affected_event_ids": unavailable,
            "shipments": listing, "timelines": timelines, "affected": affected, "route_evidence": route,
            "replay": replay,
            "hazards": hazards, "hazard_notice": json.loads(samples.HAZARD_RECORDINGS.read_text())["notice"],
            "hazard_source_sha256": hashlib.sha256(samples.HAZARD_RECORDINGS.read_bytes()).hexdigest()}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export the synthetic dispatch fixtures for the web adapter.")
    parser.add_argument("--as-of", help="cutoff such as 2026-10-14T12:00:00Z; whole days from 2026-10-08T12:00:00Z")
    args = parser.parse_args([] if argv is None else argv)
    temporary: Path | None = None
    try:
        out = build_export(args.as_of)
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
            subprocess.CalledProcessError) as exc:
        reason = f" ({exc})" if isinstance(exc, ValueError) and str(exc).startswith("as_of") else ""
        print(f"Dispatch export failed{reason}; prior fixture retained.", file=sys.stderr)
        return 1
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    print(f"Wrote {len(out['timelines'])} shipments, {len(out['affected'])} events, {len(out['replay']['results'])} "
          f"replays at cutoff {out['as_of']} to {OUT} ({len(payload) // 1024} KiB)")
    for change in out.get("cutoff_shift", {}).get("lead_time_changes", []):
        print(f"Lead time changed for {change['shipment_id']}: {change['before']} -> {change['after']}")
    return 0


def _require_readable(result: object) -> None:
    if (not isinstance(result, dict) or result.get("status") not in {"ok", "empty", "stale"}
            or not isinstance(result.get("reasons"), list)
            or not all(isinstance(reason, str) for reason in result["reasons"])):
        raise ValueError("unreadable_dispatch_result")


def _require_replay_result(result: object, sample: dict) -> None:
    if isinstance(result, dict) and result.get("status") == "error":
        if result.get("reasons") != ["event_after_as_of"]:
            raise ValueError("unreadable_replay_result")
        later = normalize_milestones([sample["record"]], as_of="9999-12-31T23:59:59Z")
        if later["rejected"] or len(later["events"]) != 1:
            raise ValueError("invalid_cutoff_replay_sample")
        return
    _require_readable(result)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
