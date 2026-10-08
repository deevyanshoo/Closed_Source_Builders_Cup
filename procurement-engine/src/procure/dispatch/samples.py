"""The labelled synthetic data the read-only /dispatch routes serve.

There is no shipment store or provider feed yet, so the routes read the synthetic milestone,
propagation-snapshot and route-evidence fixtures. Every file must be labelled ``synthetic: true``;
anything missing, unreadable or not labelled synthetic makes the data unavailable rather than
falling back to something else.
"""
from __future__ import annotations

import json
from pathlib import Path

from ..settings import PROJECT_ROOT

SAMPLE_FILES = {
    "milestones": PROJECT_ROOT / "tests" / "fixtures" / "shipments" / "milestones.json",
    "snapshot": PROJECT_ROOT / "tests" / "fixtures" / "shipments" / "snapshot.json",
    "route_evidence": PROJECT_ROOT / "tests" / "fixtures" / "route_evidence" / "hazards.json",
}
SOURCE = {"kind": "synthetic_fixture",
          "notice": "Synthetic shipment milestones, plan snapshot and route evidence. Not live tracking, port or "
                    "weather data."}


class SampleUnavailable(RuntimeError):
    pass


def load_samples() -> dict:
    data = {}
    for name, path in SAMPLE_FILES.items():
        try:
            content = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise SampleUnavailable(name) from exc
        if not isinstance(content, dict) or content.get("synthetic") is not True:
            raise SampleUnavailable(name)
        data[name] = content
    return data


REPLAY_FILE = PROJECT_ROOT / "tests" / "fixtures" / "shipments" / "replay_events.json"


def load_replay_samples() -> dict:
    """The labelled synthetic replay events, keyed by sample ID, with the file notice."""
    try:
        content = json.loads(Path(REPLAY_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SampleUnavailable("replay_events") from exc
    if not isinstance(content, dict) or content.get("synthetic") is not True or \
            not isinstance(content.get("samples"), list) or \
            not all(isinstance(s, dict) and isinstance(s.get("record"), dict) and s["record"].get("synthetic") is True
                    for s in content["samples"]):
        raise SampleUnavailable("replay_events")
    return content


def sample_view(sample: dict) -> dict:
    """What a client needs to offer a replay sample, without the full record."""
    record = sample["record"]
    return {"sample_id": sample.get("sample_id"), "title": sample.get("title"), "description": sample.get("description"),
            "event_id": record.get("event_id"), "shipment_id": record.get("shipment_id"),
            "event_type": record.get("type"), "synthetic": True}


def source_info() -> dict:
    return {**SOURCE, "files": [str(Path(p).relative_to(PROJECT_ROOT)) for p in SAMPLE_FILES.values()]}
