"""Fixed local shipment child protocol and unchanged public-math checks."""
from __future__ import annotations

from copy import deepcopy
import builtins
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "contracts" / "examples" / "recovery_request.json"
FORBIDDEN_IMPORTS = ("google", "dotenv", "procure.settings", "procure.service_app")


@pytest.fixture(autouse=True)
def offline_guard(monkeypatch):
    imported = builtins.__import__
    opened = builtins.open

    def guarded_import(name, *args, **kwargs):
        if any(name == prefix or name.startswith(prefix + ".") for prefix in FORBIDDEN_IMPORTS):
            raise AssertionError("prohibited import: " + name)
        return imported(name, *args, **kwargs)

    def guarded_open(file, *args, **kwargs):
        if isinstance(file, (str, bytes, os.PathLike)):
            path = Path(os.fsdecode(file))
            low = path.name.lower()
            if (any(part == ".env" or part.startswith(".env.") for part in path.parts)
                    or "credential" in low or low in ("adc.json", "key.json", "keys.json")):
                raise AssertionError("prohibited sensitive read")
        return opened(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    monkeypatch.setattr(builtins, "open", guarded_open)
    monkeypatch.setattr(socket, "socket", lambda *_a, **_kw: (_ for _ in ()).throw(
        AssertionError("network prohibited")))


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def make_request():
    production = json.loads(FIXTURE.read_text())["snapshot"]
    graph = {
        "workspace_id": "W", "plan_version": "v7", "snapshot_id": "graph-1",
        "as_of": "2026-10-09T00:00:00Z", "synthetic": True,
        "nodes": [{"id": "SH", "kind": "shipment"}, {"id": "PO", "kind": "purchase_order"},
                  {"id": "C", "kind": "component"}, {"id": "O-A", "kind": "production_order"},
                  {"id": "K", "kind": "commitment"}],
        "edges": [{"from": "SH", "to": "PO"}, {"from": "PO", "to": "C"},
                  {"from": "C", "to": "O-A"}, {"from": "O-A", "to": "K"}],
        "inventory": [{"component_id": "C", "unit": "kg", "starting_stock": "2"}],
        "arrival_lines": [{"line_id": "L1", "shipment_id": "SH", "purchase_order_id": "PO",
                           "component_id": "C", "unit": "kg", "quantity": "4",
                           "arrival_at": "2026-10-10T09:00:00Z"}],
        "commitments": [{"commitment_id": "K", "production_order_id": "O-A",
                         "required_at": "2026-10-10T09:00:00Z", "due_at": "2026-10-10T10:00:00Z",
                         "demands": [{"component_id": "C", "unit": "kg", "quantity": "6"}]}],
    }
    raw = [{"event_id": "event-1", "shipment_id": "SH", "type": "eta_changed",
            "occurred_at": "2026-10-08T01:00:00Z", "observed_at": "2026-10-08T01:01:00Z",
            "eta": "2026-10-10T10:00:00Z", "synthetic": True,
            "lane": {"origin": "AEJEA", "destination": "INMAA", "mode": "ocean"},
            "source": {"provider_id": "synthetic-carrier", "kind": "carrier_feed"},
            "evidence": [{"evidence_id": "e1", "kind": "carrier_status",
                          "locator": "synthetic://status/1", "content_sha256": "a" * 64,
                          "retrieved_at": "2026-10-08T01:01:00Z", "valid_until": None}]}]
    return {"adapter_version": "1", "raw_records": raw, "propagation_snapshot": graph,
            "production_snapshot": production, "joins": [{"line_id": "L1", "receipt_id": "r-C"}],
            "recovery_assignments": [{"order_id": "O-A", "period_id": "P2"},
                                     {"order_id": "O-B", "period_id": "P2"},
                                     {"order_id": "O-U", "period_id": "P1"}]}


def envelope(request=None):
    request = make_request() if request is None else request
    request_json = canonical(request).decode("utf-8")
    graph, production = request["propagation_snapshot"], request["production_snapshot"]
    host = {"principal_id": "principal-1", "workspace_id": "W",
            "request_id": "a" * 32, "context_id": "b" * 32, "context_revision": "1",
            "plan_version": "v7", "propagation_snapshot_id": graph["snapshot_id"],
            "production_snapshot_id": production["snapshot_id"], "as_of": "2026-10-09T00:00:00Z",
            "request_sha256": hashlib.sha256(request_json.encode()).hexdigest()}
    return {"bridge_version": "1", "host_context_json": canonical(host).decode(),
            "request_json": request_json}


def child(payload):
    env = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(ROOT / "procurement-engine/src"),
           "PYTHONDONTWRITEBYTECODE": "1"}
    return subprocess.run([sys.executable, "-m", "procure.production.shipment_local_bridge"],
                          input=canonical(payload), stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, env=env, cwd=ROOT, timeout=10, check=False)


def child_bytes(raw):
    env = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(ROOT / "procurement-engine/src"),
           "PYTHONDONTWRITEBYTECODE": "1"}
    return subprocess.run([sys.executable, "-m", "procure.production.shipment_local_bridge"],
                          input=raw, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          env=env, cwd=ROOT, timeout=10, check=False)


def success(payload):
    result = child(payload)
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    wire = json.loads(result.stdout)
    assert set(wire) == {"bridge_version", "binding", "outcome_json"}
    assert wire["bridge_version"] == "1"
    return wire, json.loads(wire["outcome_json"])


def test_fixed_child_returns_complete_unchanged_public_result():
    from procure.production.recovery import compare_receipt_recovery

    payload = envelope()
    wire, outcome = success(payload)
    row = outcome["comparisons"][0]
    expected = compare_receipt_recovery(make_request()["production_snapshot"],
                                        row["receipt_scenario"], row["recovery"])
    assert row["result"] == expected
    assert row["result"]["disrupted_baseline"]["orders"][0]["demands"][0]["shortfall"] == "4"
    assert row["result"]["recovery"]["orders"][0]["late_by_seconds"] == "3600"
    host = json.loads(payload["host_context_json"])
    assert wire["binding"] == {
        "principal_id": host["principal_id"], "workspace_id": host["workspace_id"],
        "request_id": host["request_id"], "context_id": host["context_id"],
        "context_revision": host["context_revision"],
        "host_sha256": hashlib.sha256(payload["host_context_json"].encode()).hexdigest(),
        "request_sha256": host["request_sha256"],
    }


@pytest.mark.parametrize("kind", ["noop", "precision", "unsupported"])
def test_fixed_child_preserves_full_public_result_for_other_cases(kind):
    from decimal import localcontext
    from procure.production.recovery import compare_receipt_recovery

    request = make_request()
    if kind == "noop":
        request["raw_records"][0]["eta"] = request["propagation_snapshot"]["arrival_lines"][0]["arrival_at"]
        request["recovery_assignments"] = deepcopy(request["production_snapshot"]["baseline_assignments"])
    elif kind == "precision":
        quantity, stock = "4.123456789123456789", "2.123456789123456789"
        request["propagation_snapshot"]["arrival_lines"][0]["quantity"] = quantity
        request["production_snapshot"]["receipts"][0]["quantity"] = quantity
        request["propagation_snapshot"]["inventory"][0]["starting_stock"] = stock
        request["production_snapshot"]["inventory"][0]["starting_stock"] = stock
    else:
        huge = "9" * 1024
        request["production_snapshot"]["orders"][0]["output_quantity"] = huge
        request["production_snapshot"]["orders"][1]["output_quantity"] = huge
        request["production_snapshot"]["routings"][0]["run_seconds_per_output_unit"] = huge
    before = deepcopy(request)
    with localcontext() as context:
        context.prec = 3
        _, outcome = success(envelope(request))
    assert request == before
    assert outcome["status"] == "evaluated"
    row = outcome["comparisons"][0]
    expected = compare_receipt_recovery(request["production_snapshot"],
                                        row["receipt_scenario"], row["recovery"])
    assert row["result"] == expected
    assert hashlib.sha256(canonical(row["result"])).hexdigest() == hashlib.sha256(canonical(expected)).hexdigest()
    if kind == "noop":
        assert row["result"]["receipt_changes"] == []
        assert row["result"]["original_baseline"] == row["result"]["disrupted_baseline"] == row["result"]["recovery"]
    elif kind == "precision":
        assert row["result"]["disrupted_baseline"]["orders"][0]["demands"][0]["shortfall"] == "3.876543210876543211"
    else:
        assert row["result"]["status"] == "unsupported"
        assert row["result"]["reason"] == "invalid_quantity"


def test_no_eligible_and_valid_null_integer_source_metadata():
    payload = envelope()
    _, outcome = success(payload)
    event = outcome["evidence"]["scenarios"][0]["source_receipt"]["event"]
    assert type(event["record_index"]) is int and event["record_index"] == 0
    assert payload["request_json"].find('"valid_until":null') >= 0
    request = make_request()
    request["raw_records"] = []
    _, outcome = success(envelope(request))
    assert outcome["status"] == "no_eligible_scenario"
    assert outcome["comparisons"] == []
    assert outcome["evidence"]["scenarios"] == []


@pytest.mark.parametrize("change,section,reason", [
    (lambda raw: raw.update(observed_at="2026-10-09T01:00:00Z"),
     "normalization_rejected", "observed_after_as_of"),
    (lambda raw: raw["evidence"][0].update(valid_until="2026-10-08T02:00:00Z"),
     "excluded", "evidence_expired"),
    (lambda raw: (raw.update(type="port_hold"), raw.pop("eta")),
     "excluded", "hold_without_new_eta"),
    (lambda raw: (raw.update(type="partially_delivered"),
                  raw.update(quantity={"amount": "2", "unit": "kg"})),
     "excluded", "partial_quantity_not_supported"),
])
def test_noneligible_factory_evidence_survives_bridge(change, section, reason):
    request = make_request()
    change(request["raw_records"][0])
    _, outcome = success(envelope(request))
    assert outcome["status"] == "no_eligible_scenario"
    assert outcome["comparisons"] == []
    assert [row["reason"] for row in outcome["evidence"][section]] == [reason]


@pytest.mark.parametrize("record", [None, {"event_id": "", "shipment_id": "SH",
                                          "type": "eta_changed", "synthetic": True}])
def test_known_malformed_or_nondict_milestones_keep_factory_rejection(record):
    request = make_request()
    request["raw_records"] = [record]
    _, outcome = success(envelope(request))
    assert outcome["status"] == "no_eligible_scenario"
    assert outcome["comparisons"] == []
    assert len(outcome["evidence"]["normalization_rejected"]) == 1


def test_adapter_rejection_is_complete_successful_protocol_output():
    request = make_request()
    request["joins"] = []
    wire, outcome = success(envelope(request))
    assert wire["binding"]["request_sha256"] == hashlib.sha256(envelope(request)["request_json"].encode()).hexdigest()
    assert (outcome["status"], outcome["stage"], outcome["reason"]) == (
        "rejected", "join", "invalid_join")
    assert outcome["comparisons"] == []


@pytest.mark.parametrize("mutation", [
    lambda p: p.update(extra="x"),
    lambda p: p.update(bridge_version="2"),
    lambda p: p.update(request_json=p["request_json"] + " true"),
    lambda p: p.update(request_json=p["request_json"].replace('"adapter_version":"1"', '"adapter_version":"1","adapter_version":"1"')),
    lambda p: p.update(request_json=p["request_json"].replace('"adapter_version":"1"', '"adapter_version":1')),
    lambda p: p.update(request_json=p["request_json"].replace('"valid_until":null', '"valid_until":NaN')),
    lambda p: p.update(request_json=p["request_json"].replace('"event_id":"event-1"', '"event_id":"\\ud800"')),
    lambda p: p.update(host_context_json=p["host_context_json"].replace('"context_revision":"1"', '"context_revision":1')),
    lambda p: p.update(host_context_json=p["host_context_json"].replace('"context_revision":"1"', '"context_revision":"01"')),
    lambda p: p.update(host_context_json=p["host_context_json"].replace('"request_sha256":"', '"request_sha256":"0')),
])
def test_protocol_rejects_poisoned_inputs_with_no_stdout(mutation):
    payload = envelope()
    mutation(payload)
    result = child(payload)
    assert result.returncode != 0 and result.stdout == b""


@pytest.mark.parametrize("raw", [
    b'{"bridge_version":"1","bridge_version":"1"}',
    b'{"bridge_version":"1"} trailing',
    b'\xff',
    b'{"bridge_version":"\\ud800"}',
])
def test_outer_malformed_bytes_have_no_partial_stdout(raw):
    result = child_bytes(raw)
    assert result.returncode != 0 and result.stdout == b""


def test_host_byte_hash_binds_exact_original_text_and_scope():
    payload = envelope()
    host = json.loads(payload["host_context_json"])
    payload["host_context_json"] = json.dumps(host, indent=2)
    wire, _ = success(payload)
    assert wire["binding"]["host_sha256"] == hashlib.sha256(payload["host_context_json"].encode()).hexdigest()
    assert wire["binding"]["host_sha256"] != hashlib.sha256(canonical(host)).hexdigest()
    host["workspace_id"] = "foreign"
    payload["host_context_json"] = canonical(host).decode()
    result = child(payload)
    assert result.returncode != 0 and result.stdout == b""


def test_duplicate_or_extra_host_field_rejects_before_dispatch():
    payload = envelope()
    payload["host_context_json"] = payload["host_context_json"].replace(
        '"workspace_id":"W"', '"workspace_id":"W","workspace_id":"W"')
    result = child(payload)
    assert result.returncode != 0 and result.stdout == b""
    payload = envelope()
    host = json.loads(payload["host_context_json"])
    host["untrusted"] = True
    payload["host_context_json"] = canonical(host).decode()
    result = child(payload)
    assert result.returncode != 0 and result.stdout == b""


def test_exact_request_byte_hash_requires_fresh_host_assertion():
    payload = envelope()
    payload["request_json"] = json.dumps(json.loads(payload["request_json"]), indent=2)
    result = child(payload)
    assert result.returncode != 0 and result.stdout == b""
    host = json.loads(payload["host_context_json"])
    host["request_sha256"] = hashlib.sha256(payload["request_json"].encode()).hexdigest()
    payload["host_context_json"] = canonical(host).decode()
    _, outcome = success(payload)
    assert outcome["status"] == "evaluated"


@pytest.mark.parametrize("part", ["stdin", "host", "request", "raw", "propagation", "depth", "array", "entries", "string"])
def test_byte_and_tree_bounds_reject_whole_protocol(part):
    payload = envelope()
    request = json.loads(payload["request_json"])
    host = json.loads(payload["host_context_json"])
    if part == "stdin":
        raw = b" " * ((4 << 20) + (32 << 10) + 1)
        result = child_bytes(raw)
        assert result.returncode != 0 and result.stdout == b""
        return
    if part == "host":
        host["principal_id"] = "x" * 8192
    elif part == "request":
        request["production_snapshot"]["padding"] = "x" * ((2 << 20) + 1)
    elif part == "raw":
        request["raw_records"][0]["evidence"][0]["locator"] = "x" * ((1 << 20) + 1)
    elif part == "propagation":
        request["propagation_snapshot"]["notice"] = "x" * ((1 << 20) + 1)
    elif part == "depth":
        value = "x"
        for _ in range(18):
            value = [value]
        request["propagation_snapshot"]["notice"] = value
    elif part == "array":
        request["raw_records"] = [None] * 257
    elif part == "entries":
        request["production_snapshot"]["padding"] = [["x"] * 256 for _ in range(65)]
    else:
        request["propagation_snapshot"]["notice"] = "x" * ((128 << 10) + 1)
    payload["request_json"] = canonical(request).decode()
    host["request_sha256"] = hashlib.sha256(payload["request_json"].encode()).hexdigest()
    payload["host_context_json"] = canonical(host).decode()
    result = child(payload)
    assert result.returncode != 0 and result.stdout == b""


def test_second_real_comparison_failure_returns_no_partial_rows(monkeypatch):
    import procure.production.shipment_local_bridge as bridge
    import procure.production.shipment_recovery as adapter

    request = make_request()
    second = deepcopy(request["raw_records"][0])
    second["event_id"], second["shipment_id"] = "event-2", "SH-OTHER"
    request["raw_records"].append(second)
    graph = request["propagation_snapshot"]
    graph["nodes"].append({"id": "SH-OTHER", "kind": "shipment"})
    graph["edges"].append({"from": "SH-OTHER", "to": "PO"})
    line = deepcopy(graph["arrival_lines"][0])
    line["line_id"], line["shipment_id"] = "L2", "SH-OTHER"
    graph["arrival_lines"].append(line)
    receipt = deepcopy(request["production_snapshot"]["receipts"][0])
    receipt["receipt_id"] = "r-C2"
    request["production_snapshot"]["receipts"].append(receipt)
    request["joins"].append({"line_id": "L2", "receipt_id": "r-C2"})
    real = adapter.compare_receipt_recovery
    calls = []

    def fail_second(snapshot, scenario, recovery):
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError("hidden")
        return real(snapshot, scenario, recovery)

    monkeypatch.setattr(adapter, "compare_receipt_recovery", fail_second)
    wire = json.loads(bridge.process(canonical(envelope(request))))
    outcome = json.loads(wire["outcome_json"])
    assert calls == [1, 1]
    assert (outcome["status"], outcome["stage"], outcome["reason"]) == (
        "rejected", "runtime", "comparison_unavailable")
    assert len(outcome["evidence"]["scenarios"]) == 2
    assert outcome["comparisons"] == []


def test_output_cap_and_complete_input_copy(monkeypatch):
    import procure.production.shipment_local_bridge as bridge

    payload = envelope()
    before = deepcopy(payload)
    wire = json.loads(bridge.process(canonical(payload)))
    assert payload == before and json.loads(wire["outcome_json"])["status"] == "evaluated"
    monkeypatch.setattr(bridge, "_OUTCOME_LIMIT", 128)
    with pytest.raises(bridge.ProtocolError):
        bridge.process(canonical(payload))
    monkeypatch.setattr(bridge, "_OUTCOME_LIMIT", 4 << 20)
    monkeypatch.setattr(bridge, "_STDOUT_LIMIT", 128)
    with pytest.raises(bridge.ProtocolError):
        bridge.process(canonical(payload))


def test_invalid_request_never_dispatches_to_adapter(monkeypatch):
    import procure.production.shipment_local_bridge as bridge

    payload = envelope()
    request = json.loads(payload["request_json"])
    request["raw_records"][0]["event_id"] = 1
    payload["request_json"] = canonical(request).decode()
    host = json.loads(payload["host_context_json"])
    host["request_sha256"] = hashlib.sha256(payload["request_json"].encode()).hexdigest()
    payload["host_context_json"] = canonical(host).decode()
    calls = []
    monkeypatch.setattr(bridge, "compare_shipment_production_recovery",
                        lambda *_a, **_kw: calls.append(1))
    with pytest.raises(bridge.ProtocolError):
        bridge.process(canonical(payload))
    assert calls == []
    result = child(payload)
    assert result.returncode != 0 and result.stdout == b""


@pytest.mark.parametrize("poison", [
    '"principal_id":"\\ud800"',
    '"\\ud800":"value"',
    '"plan_version":"v7\\ud800"',
])
def test_decoded_host_surrogate_rejects_before_adapter_dispatch(monkeypatch, poison):
    import procure.production.shipment_local_bridge as bridge

    payload = envelope()
    host = payload["host_context_json"]
    if poison.startswith('"principal_id"'):
        host = host.replace('"principal_id":"principal-1"', poison)
    elif poison.startswith('"plan_version"'):
        host = host.replace('"plan_version":"v7"', poison)
    else:
        host = host[:-1] + "," + poison + "}"
    # The outer transport is valid UTF-8 JSON; the inner escape decodes to a lone surrogate.
    payload["host_context_json"] = host
    calls = []
    monkeypatch.setattr(bridge, "compare_shipment_production_recovery",
                        lambda *_a, **_kw: calls.append(1))
    with pytest.raises(bridge.ProtocolError):
        bridge.process(canonical(payload))
    assert calls == []
    result = child(payload)
    assert result.returncode != 0 and result.stdout == b""


@pytest.mark.parametrize("where", [
    "raw", "source", "evidence", "graph", "node", "edge", "inventory", "line",
    "commitment", "demand",
])
def test_unknown_input_fields_reject_before_copy_or_dispatch(monkeypatch, where):
    import procure.production.shipment_local_bridge as bridge

    payload = envelope()
    request = json.loads(payload["request_json"])
    graph = request["propagation_snapshot"]
    targets = {
        "raw": request["raw_records"][0],
        "source": request["raw_records"][0]["source"],
        "evidence": request["raw_records"][0]["evidence"][0],
        "graph": graph,
        "node": graph["nodes"][0],
        "edge": graph["edges"][0],
        "inventory": graph["inventory"][0],
        "line": graph["arrival_lines"][0],
        "commitment": graph["commitments"][0],
        "demand": graph["commitments"][0]["demands"][0],
    }
    targets[where]["unexpected_authority"] = "x"
    payload["request_json"] = canonical(request).decode()
    host = json.loads(payload["host_context_json"])
    host["request_sha256"] = hashlib.sha256(payload["request_json"].encode()).hexdigest()
    payload["host_context_json"] = canonical(host).decode()
    calls = []
    monkeypatch.setattr(bridge, "_bytes", lambda *_a, **_kw: calls.append("copy"))
    monkeypatch.setattr(bridge, "compare_shipment_production_recovery",
                        lambda *_a, **_kw: calls.append("adapter"))
    with pytest.raises(bridge.ProtocolError):
        bridge.process(canonical(payload))
    assert calls == []
