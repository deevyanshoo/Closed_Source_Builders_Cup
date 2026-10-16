"""Dispatch fixture export at an explicit cutoff: every synthetic time moves by whole days, results stay the same.

Runs the export script's build in memory over the labelled synthetic fixtures. Nothing is written to apps/web and
nothing calls a network service.
"""
from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("export_dispatch", ROOT / "scripts" / "export_dispatch_fixtures_for_web.py")
export = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(export)

COMMITTED = ROOT.parent / "apps" / "web" / "src" / "dispatch-fixtures.json"
LATER = "2026-10-12T12:00:00Z"  # four days after the default cutoff, a Monday rather than a Thursday


@pytest.fixture(scope="module")
def default():
    return export.build_export()


@pytest.fixture(scope="module")
def shifted():
    return export.build_export(as_of=LATER)


def outcomes(out: dict) -> dict:
    """What the page shows as results, without times or content hashes."""
    shipments = {row["shipment_id"]: (row["status"], tuple(row["signals"]), row["on_hold"], row["last_event_type"],
                                      (row["expected_arrival"] or {}).get("basis"))
                 for row in out["shipments"]["shipments"]}
    replay = {}
    for sample_id, result in out["replay"]["results"].items():
        text = json.dumps({k: v for k, v in result.items() if k not in ("as_of", "now", "sample")}, sort_keys=True)
        replay[sample_id] = (result.get("status"), tuple(result.get("reasons") or []),
                             # status, shortfall and wording; timestamps and content-hash ids removed
                             re.sub(r"[0-9a-f]{64}", "<hash>",
                                    re.sub(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?([+-]\d{2}:\d{2}|Z)?", "<t>", text)))
    hazards = {}
    for shipment_id, result in out["hazards"].items():
        hazards[shipment_id] = sorted(
            (a["provider_id"], a["location"]["code"], a["state"], a["reason"], a["freshness"], a["usable"],
             json.dumps([{k: v[k] for k in ("rule_id", "value", "unit", "threshold", "met", "distance_km")
                          if k in v} for v in a["values"]], sort_keys=True))
            for a in result["assessments"])
    return {"shipments": shipments, "replay": replay, "hazards": hazards}


def test_default_build_is_unchanged(default):
    committed = json.loads(COMMITTED.read_text(encoding="utf-8"))
    drop = ("generated_from_revision",)
    assert {k: v for k, v in default.items() if k not in drop} == {k: v for k, v in committed.items() if k not in drop}
    assert "cutoff_shift" not in default


def test_new_cutoff_keeps_statuses_replays_and_hazards(default, shifted):
    assert shifted["as_of"] == "2026-10-12T12:00:00+00:00"
    assert outcomes(shifted) == outcomes(default)
    statuses = {r["shipment_id"]: r["status"] for r in shifted["shipments"]["shipments"]}
    assert statuses == {"SHP-SYN-001": "unknown", "SHP-SYN-002": "on_hold", "SHP-SYN-003": "unknown",
                        "SHP-SYN-004": "delayed", "SHP-SYN-005": "on_time", "SHP-SYN-008": "unknown"}


def test_every_time_moves_by_the_same_whole_days(default, shifted):
    row = next(r for r in shifted["shipments"]["shipments"] if r["shipment_id"] == "SHP-SYN-002")
    assert row["expected_arrival"]["at"] == "2026-10-16T02:30:00+00:00"
    assert row["baseline_arrival_at"] == "2026-10-14T00:00:00+00:00"
    hold = shifted["replay"]["results"]["innsa-hold-new-eta"]
    assert "2026-10-20T02:30:00" in json.dumps(hold)
    assert shifted["cutoff_shift"]["from"] == "2026-10-08T12:00:00+00:00"
    assert shifted["cutoff_shift"]["days"] == 4


def test_lead_time_changes_are_reported_not_hidden(shifted):
    week = export.build_export(as_of="2026-10-15T12:00:00Z")
    assert week["cutoff_shift"]["lead_time_changes"] == []
    for change in shifted["cutoff_shift"]["lead_time_changes"]:
        assert set(change) == {"shipment_id", "before", "after"} and change["before"] != change["after"]


@pytest.mark.parametrize("as_of", ["2026-10-12T13:00:00Z", "2026-10-12", "yesterday", "2027-01-05T12:00:00Z"])
def test_cutoffs_that_cannot_shift_cleanly_are_refused(as_of):
    with pytest.raises(ValueError):
        export.build_export(as_of=as_of)


@pytest.mark.parametrize("before,after", [
    ("2026-10-08T00:00", "2026-10-12T00:00"),
    ("2026-10-06T00:00:00", "2026-10-10T00:00:00"),
    ("2026-10-05T10:00:00+05:30", "2026-10-09T10:00:00+05:30"),
    ("2026-09-20T14:00:00-03:00", "2026-09-24T14:00:00-03:00"),
    ("2026-10-08T12:00:00Z", "2026-10-12T12:00:00Z"),
    ("2026-10-05", "2026-10-09"),
    ("SHP-SYN-002", "SHP-SYN-002"),
    ("Synthetic closure day IN-B", "Synthetic closure day IN-B"),
])
def test_shift_keeps_each_time_format(before, after):
    assert export.shift_times({"x": [before]}, 4) == {"x": [after]}
