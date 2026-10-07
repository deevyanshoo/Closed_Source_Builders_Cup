"""Gemini authentication paths and the live-call gate. Mocks only: no test makes a real call.

`google.genai.Client` is replaced by a fake that records how it was constructed, so these tests
check which credentials path would be used without contacting Google.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest
from google import genai

from procure.documents import read_document
from procure.offers import extract_gemini
from procure.offers.extraction import extract_offer
from procure.settings import reset_caches

FIXTURES = Path(__file__).parent / "fixtures" / "offers"
Q01 = next(q for q in json.loads((FIXTURES / "manifest.json").read_text())["quotes"] if q["id"] == "Q01")
AS_OF = dt.date(2026, 10, 7)
FIELDS = ("supplier_name", "origin_country", "incoterm", "settlement_currency", "payment_terms", "valid_until",
          "lead_time_days", "min_order", "capacity", "shipments", "items")


class FakeGenaiClient:
    instances: list["FakeGenaiClient"] = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.calls = 0
        self.models = self
        FakeGenaiClient.instances.append(self)

    def generate_content(self, *, model, contents, config):
        self.calls += 1
        return type("Resp", (), {"text": json.dumps({k: [] for k in FIELDS}), "usage_metadata": None})()


@pytest.fixture
def env(monkeypatch):
    """Clean credentials environment with a fake Google client; returns a setter."""
    for name in ("GEMINI_API_KEY", "GOOGLE_GENAI_USE_VERTEXAI", "GOOGLE_CLOUD_PROJECT", "GOOGLE_CLOUD_LOCATION",
                 "RUN_LIVE_GEMINI", "MAX_LIVE_CALLS", "GOOGLE_APPLICATION_CREDENTIALS"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OFFLINE", "0")
    monkeypatch.setattr(genai, "Client", FakeGenaiClient)
    FakeGenaiClient.instances = []
    extract_gemini.reset_live_calls()

    def set_env(**values):
        for key, value in values.items():
            monkeypatch.setenv(key, str(value))
        reset_caches()

    reset_caches()
    yield set_env
    monkeypatch.setenv("OFFLINE", "1")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    reset_caches()
    extract_gemini.reset_live_calls()


VERTEX = {"GOOGLE_GENAI_USE_VERTEXAI": "true", "GOOGLE_CLOUD_PROJECT": "demo-project-not-real",
          "GOOGLE_CLOUD_LOCATION": "us-central1"}
LIVE = {"RUN_LIVE_GEMINI": "1", "MAX_LIVE_CALLS": "1"}


def run():
    return extract_offer(read_document(FIXTURES / Q01["file"]), mode="gemini", as_of=AS_OF)


def test_vertex_adc_client_is_built_with_v1_and_a_timeout(env):
    env(**VERTEX, **LIVE)
    ex = run()
    client = FakeGenaiClient.instances[0]
    assert client.kwargs["vertexai"] is True
    assert (client.kwargs["project"], client.kwargs["location"]) == ("demo-project-not-real", "us-central1")
    assert "api_key" not in client.kwargs
    options = client.kwargs["http_options"]
    assert (options.api_version, options.timeout) == ("v1", 15000)
    assert ex.extractor == "gemini"
    assert (ex.extractor_detail["auth"], ex.extractor_detail["call"]) == ("vertex_adc", "real")


def test_api_key_path_is_kept_when_vertex_is_not_configured(env):
    env(GEMINI_API_KEY="test-key-not-real", **LIVE)
    ex = run()
    assert FakeGenaiClient.instances[0].kwargs.get("api_key") == "test-key-not-real"
    assert "vertexai" not in FakeGenaiClient.instances[0].kwargs
    assert ex.extractor_detail["auth"] == "api_key"


def test_vertex_takes_precedence_over_an_api_key(env):
    env(GEMINI_API_KEY="test-key-not-real", **VERTEX, **LIVE)
    assert run().extractor_detail["auth"] == "vertex_adc"


@pytest.mark.parametrize("missing", ["GOOGLE_CLOUD_PROJECT", "GOOGLE_CLOUD_LOCATION"])
def test_incomplete_vertex_config_falls_back_without_building_a_client(env, monkeypatch, missing):
    env(**VERTEX, **LIVE)
    monkeypatch.delenv(missing)
    ex = run()
    assert (ex.extractor, ex.extractor_detail["fallback_reason"]) == ("rules", "vertex_config_incomplete")
    assert FakeGenaiClient.instances == []


@pytest.mark.parametrize("config", [VERTEX, {"GEMINI_API_KEY": "test-key-not-real"}])
def test_offline_blocks_both_paths(env, config):
    env(**config, **LIVE, OFFLINE="1")
    ex = run()
    assert ex.extractor_detail["fallback_reason"] == "offline"
    assert FakeGenaiClient.instances == []


def test_failed_credentials_fall_back_with_a_text_free_reason(env, monkeypatch):
    from google.auth.exceptions import DefaultCredentialsError

    def broken(**_kwargs):
        raise DefaultCredentialsError("File /secret/path/adc.json was not found; token abc123")

    env(**VERTEX, **LIVE)
    monkeypatch.setattr(genai, "Client", broken)
    ex = run()
    assert (ex.extractor, ex.extractor_detail["fallback_reason"]) == ("rules", "credentials_unavailable")
    assert ex.extractor_detail["auth"] == "vertex_adc"
    text = json.dumps(ex.to_dict())
    assert "/secret/path" not in text and "abc123" not in text and "demo-project-not-real" not in text


def test_real_calls_need_run_live_gemini(env):
    env(**VERTEX, MAX_LIVE_CALLS="5")
    ex = run()
    assert ex.extractor_detail["fallback_reason"] == "live_calls_disabled"
    assert FakeGenaiClient.instances == []


def test_live_call_budget_defaults_to_zero(env):
    env(**VERTEX, RUN_LIVE_GEMINI="1")
    ex = run()
    assert ex.extractor_detail["fallback_reason"] == "live_call_budget_exhausted"
    assert FakeGenaiClient.instances == []


def test_live_call_budget_is_enforced_and_counted(env):
    env(**VERTEX, RUN_LIVE_GEMINI="1", MAX_LIVE_CALLS="1")
    first, second = run(), run()
    assert first.extractor == "gemini"
    assert second.extractor_detail["fallback_reason"] == "live_call_budget_exhausted"
    assert extract_gemini.live_call_stats() == {"used": 1, "max": 1, "enabled": True}
    assert first.extractor_detail["live_calls"] == {"used": 1, "max": 1}


def test_mocked_client_records_the_configured_auth_path_and_spends_no_budget(env):
    env(**VERTEX)
    client = FakeGenaiClient()
    FakeGenaiClient.instances = []
    ex = extract_offer(read_document(FIXTURES / Q01["file"]), mode="gemini", as_of=AS_OF, client=client)
    assert (ex.extractor_detail["call"], ex.extractor_detail["auth"]) == ("mocked", "vertex_adc")
    assert client.calls == 1
    assert extract_gemini.live_call_stats()["used"] == 0


def test_audit_never_contains_the_key(env):
    env(GEMINI_API_KEY="test-key-not-real", **LIVE)
    assert "test-key-not-real" not in json.dumps(run().to_dict())


def test_scoring_report_counts_live_calls(env):
    import importlib.util

    path = Path(__file__).parents[1] / "scripts" / "score_offer_extraction.py"
    spec = importlib.util.spec_from_file_location("score_offer_extraction", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    env(OFFLINE="1")
    report = module.run(mode="rules", split="heldout")
    assert report["live_calls"] == {"used": 0, "max": 0, "enabled": False}
