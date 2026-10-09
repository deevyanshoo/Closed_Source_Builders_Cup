"""Failure publication and status gates for the local synthetic dispatch export."""
from __future__ import annotations

import copy
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "export_dispatch_fixtures_for_web.py"
SPEC = importlib.util.spec_from_file_location("dispatch_export", SCRIPT)
assert SPEC and SPEC.loader
export = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(export)


@pytest.fixture
def destination(tmp_path, monkeypatch):
    target = tmp_path / "dispatch-fixtures.json"
    target.write_bytes(b"existing verified export\n")
    monkeypatch.setattr(export, "OUT", target)
    return target


def test_malformed_snapshot_does_not_replace_existing_export(destination, monkeypatch, capsys):
    data = copy.deepcopy(export.samples.load_samples())
    data["snapshot"]["arrival_lines"] = "malformed"
    monkeypatch.setattr(export.samples, "load_samples", lambda: data)

    assert export.main() != 0
    assert destination.read_bytes() == b"existing verified export\n"
    assert list(destination.parent.iterdir()) == [destination]
    assert "Wrote" not in capsys.readouterr().out


def test_route_error_does_not_replace_existing_export(destination, monkeypatch, capsys):
    monkeypatch.setattr(export, "normalize_route_evidence",
                        lambda *_args, **_kwargs: {"status": "error", "reasons": ["invalid_route_evidence"]})

    assert export.main() != 0
    assert destination.read_bytes() == b"existing verified export\n"
    assert list(destination.parent.iterdir()) == [destination]
    assert "Wrote" not in capsys.readouterr().out


def test_unknown_status_does_not_replace_existing_export(destination, monkeypatch):
    monkeypatch.setattr(export, "shipment_list", lambda *_args, **_kwargs: {"status": "unknown_state", "reasons": []})
    assert export.main() != 0
    assert destination.read_bytes() == b"existing verified export\n"
    assert list(destination.parent.iterdir()) == [destination]


def test_malformed_replay_does_not_replace_existing_export(destination, monkeypatch):
    content = copy.deepcopy(export.samples.load_replay_samples())
    content["samples"][0]["record"].pop("evidence")
    monkeypatch.setattr(export.samples, "load_replay_samples", lambda: content)
    monkeypatch.setattr(export.subprocess, "run", lambda *_args, **_kwargs: SimpleNamespace(stdout="testrev\n"))

    assert export.main() != 0
    assert destination.read_bytes() == b"existing verified export\n"
    assert list(destination.parent.iterdir()) == [destination]


def test_intended_cutoff_rejection_remains_exported(destination, monkeypatch):
    import json

    monkeypatch.setattr(export.subprocess, "run", lambda *_args, **_kwargs: SimpleNamespace(stdout="testrev\n"))
    assert export.main() == 0
    result = json.loads(destination.read_text())
    assert (result["replay"]["results"]["eta-after-cutoff"]["status"],
            result["replay"]["results"]["eta-after-cutoff"]["reasons"]) == (
        "error", ["event_after_as_of"])


def test_replace_failure_keeps_prior_bytes_and_removes_temporary_file(destination, monkeypatch, capsys):
    def failed_replace(*_args):
        raise OSError("synthetic publication failure")
    monkeypatch.setattr(export.os, "replace", failed_replace)
    assert export.main() != 0
    assert destination.read_bytes() == b"existing verified export\n"
    assert list(destination.parent.iterdir()) == [destination]
    assert "Wrote" not in capsys.readouterr().out


def test_export_omits_expired_action_trigger_and_has_no_error_entries(destination):
    import json

    assert export.main() == 0
    result = json.loads(destination.read_text())
    assert "evt-008-eta" not in result["affected"]
    assert "evt-008-eta" in result["unavailable_affected_event_ids"]
    responses = [result["shipments"], *result["timelines"].values(),
                 *result["affected"].values(), *result["route_evidence"].values()]
    assert all(entry["status"] in {"ok", "empty", "stale"} for entry in responses)
    assert result["synthetic"] is True and result["source"] == "local_fixture_adapter"
    assert set(result["source_sha256"]) == set(export.samples.SAMPLE_FILES)
