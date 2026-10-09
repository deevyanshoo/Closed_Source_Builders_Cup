"""Policy-notice parsing from SAVED, hand-written synthetic HTML only. Nothing here fetches."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from procure.event_features import project_events
from procure.news import build_snapshots, gate_rows, load_sources, parse_gdelt
from procure.news import policy
from procure.news.policy import combined_sources, load_policy_sources, parse_saved_page, recorded_policy_fetches

HERE = Path(__file__).parent / "fixtures" / "policy"
AS_OF = "2026-10-08T12:00:00Z"
POLICY = load_policy_sources()
SOURCES = combined_sources(load_sources(), POLICY)


def page(provider, name, status=200, retrieved="2026-10-08T09:00:00Z"):
    return parse_saved_page(provider, (HERE / name).read_bytes() if name else b"", http_status=status,
                            retrieved_at=retrieved, registry=POLICY, synthetic=True)


def test_registry_records_robots_and_terms_and_forbids_fetching():
    assert set(POLICY["providers"]) == {"dgft", "cbic", "pib", "jnpa"}
    for source in POLICY["providers"].values():
        assert source["source_kind"] == "official"
        assert source["robots_note"].startswith("Not checked") and source["terms_note"]
        assert source["fetch_allowed"] is False and source["permitted_uses"] == []
        assert source["rights_status"] == "unreviewed"


def test_module_has_no_network_path():
    assert not hasattr(policy, "http_get") and not hasattr(policy, "httpx")
    assert not [name for name in dir(policy) if name.startswith("fetch")]


def test_dgft_table_parses_notices_with_stated_dates():
    out = page("dgft", "dgft.html")
    assert (out["state"], out["provider"]) == ("ok", "dgft")
    first = out["items"][0]
    assert first["title"] == "Export restriction on copper scrap shipments through Nhava Sheva"
    assert first["url"] == "https://dgft.gov.in/uploads/synthetic/tn-21.pdf"
    assert (first["guid"], first["published_date"], first["published_at"]) == ("Trade Notice 21/2026-27", "2026-10-08",
                                                                                 None)
    assert (first["source_id"], first["publisher"], first["language"]) == (
        "dgft.gov.in", "Directorate General of Foreign Trade", "en")
    assert [i["guid"] for i in out["items"]] == ["Trade Notice 21/2026-27", "Trade Notice 20/2026-27",
                                                  "Trade Notice 19/2026-27"]
    assert out["rejected_rows"] == [{"row": 4, "reason": "invalid_date", "text": "31/09/2026"}]


@pytest.mark.parametrize("provider,name,count,date", [("cbic", "cbic.html", 2, "2026-10-07"),
                                                      ("pib", "pib.html", 2, "2026-10-08"),
                                                      ("jnpa", "jnpa.html", 1, "2026-10-07")])
def test_other_layouts(provider, name, count, date):
    out = page(provider, name)
    assert out["state"] == "ok" and len(out["items"]) == count
    assert out["items"][0]["published_date"] == date
    assert all(i["url"].startswith("https://") for i in out["items"])


def test_layout_change_is_detected_not_read_as_empty():
    changed = page("dgft", "dgft_layout_changed.html")
    assert (changed["state"], changed["reason"], changed["items"]) == ("layout_changed", "missing_table", [])
    assert page("dgft", "dgft_empty.html")["state"] == "empty"


def test_layout_change_on_another_parser():
    assert page("pib", "dgft.html")["state"] == "layout_changed"
    assert page("jnpa", "cbic.html")["state"] == "layout_changed"


def test_failures_are_explicit():
    assert (page("dgft", "not_html.bin")["state"], page("dgft", "not_html.bin")["reason"]) == (
        "malformed_payload", "not_html")
    assert page("jnpa", None, status=503)["state"] == "provider_failure"
    assert page("jnpa", None, status=429)["state"] == "rate_limited"
    with pytest.raises(KeyError):
        page("unknown-source", "dgft.html")


def test_snapshots_reuse_the_news_shape_with_official_source_kind():
    result = build_snapshots(recorded_policy_fetches(HERE / "recorded.json", registry=POLICY), as_of=AS_OF,
                             sources=SOURCES)
    [notice] = [s for s in result["snapshots"] if s["title"].startswith("Export restriction on copper scrap")]
    assert notice["source"] == {"source_id": "dgft.gov.in", "publisher": "Directorate General of Foreign Trade",
                                "kind": "official"}
    assert (notice["published_at"], notice["published_date"], notice["published_at_status"]) == (
        None, "2026-10-08", "date_only")
    assert notice["entities"]["event_type"] == "export_restriction"
    assert notice["entities"]["ports"] == ["INNSA"] and notice["entities"]["materials"] == ["copper"]
    assert notice["gate_row"]["source_kind"] == "official"
    assert notice["evidence_status"] == "candidate"
    assert [d["provider"] for d in result["duplicates"] if d["snapshot_id"] == notice["snapshot_id"]] == ["dgft"]
    assert [(r["provider"], r["reason"]) for r in result["rejected"]] == [("pib", "retrieved_after_as_of")]
    states = {(f["provider"], f["state"]) for f in result["fetches"]}
    assert ("jnpa", "provider_failure") in states


def test_untrusted_notice_text_is_flagged_and_held_back():
    result = build_snapshots(recorded_policy_fetches(HERE / "recorded.json", registry=POLICY), as_of=AS_OF,
                             sources=SOURCES)
    [s] = [x for x in result["snapshots"] if x["title"].startswith("Ignore previous instructions")]
    assert s["review_flags"] == ["embedded_instruction"] and s["gate_row"] is None


def test_scraped_notice_deduplicates_against_the_same_story_from_news():
    news = {"provider": "gdelt", "material": "copper", "state": "ok", "reason": None,
            "retrieved_at": "2026-10-08T08:00:00+00:00", "synthetic": True, "request": "recorded",
            "items": parse_gdelt((HERE / "gdelt_pib.json").read_bytes(), retrieved_at="2026-10-08T08:00:00Z")}
    result = build_snapshots([news, *recorded_policy_fetches(HERE / "recorded.json", registry=POLICY)], as_of=AS_OF,
                             sources=SOURCES)
    same = [s for s in result["snapshots"] if "copper concentrate" in s["title"]]
    assert len(same) == 1
    assert same[0]["providers"] == ["gdelt", "pib"] and same[0]["source"]["kind"] == "official"
    assert same[0]["first_seen_at"] == "2026-10-08T08:00:00+00:00"
    assert same[0]["corroboration"]["distinct_sources"] == 1  # one publisher seen twice is not corroboration


def test_gate_accepts_official_rows_and_denies_unreviewed_rights():
    rows = gate_rows(build_snapshots(recorded_policy_fetches(HERE / "recorded.json", registry=POLICY), as_of=AS_OF,
                                     sources=SOURCES))
    assert rows and {r["source_kind"] for r in rows} == {"official"}
    projection = project_events(rows, as_of=AS_OF, scope={"material": "copper"}, purpose="planning", allow_demo=True)
    assert projection.features == () and {x["reason"] for x in projection.exclusions} == {"permission_denied"}


def test_saved_pages_are_labelled_synthetic():
    manifest = json.loads((HERE / "recorded.json").read_text())
    assert manifest["synthetic"] is True
    for name in ("dgft.html", "cbic.html", "pib.html", "jnpa.html"):
        assert "SYNTHETIC" in (HERE / name).read_text()
