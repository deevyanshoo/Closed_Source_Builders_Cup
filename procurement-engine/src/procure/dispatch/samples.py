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


def source_info() -> dict:
    return {**SOURCE, "files": [str(Path(p).relative_to(PROJECT_ROOT)) for p in SAMPLE_FILES.values()]}
