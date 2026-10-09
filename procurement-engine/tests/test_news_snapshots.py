"""News snapshots in the shape #14's point-in-time event gate accepts. Offline: recorded,
hand-written synthetic responses only; live fetch entrypoints cannot reach transport."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from procure.event_features import project_events
from procure.news import (
    CANDIDATE_NOTE, build_snapshots, fetch_gdelt_items, fetch_rss_items, gate_rows, load_sources, parse_gdelt,
    parse_rss, recorded_fetches,
)
from procure.news import fetch as fetch_module

HERE = Path(__file__).parent / "fixtures" / "news"
AS_OF = "2026-10-08T12:00:00Z"
SOURCES = load_sources()


@pytest.fixture(scope="module")
def built():
    return build_snapshots(recorded_fetches(HERE / "recorded.json"), as_of=AS_OF, sources=SOURCES)


def by_title(result, text):
    return [s for s in result["snapshots"] if text in s["title"]]


# Parsing and fetch states


def test_gdelt_parse_keeps_provider_fields_and_untrusted_title():
    items = parse_gdelt((HERE / "gdelt_copper.json").read_bytes(), retrieved_at="2026-10-08T07:00:00Z")
    first = items[0]
    assert first["provider"] == "gdelt"
    assert first["source_id"] == "example-wire.test"
    assert first["url"] == "https://example-wire.test/markets/copper-port-strike-chile"
    assert (first["language"], first["published_at"]) == ("English", None)
    assert first["provider_first_seen_at"] == "2026-10-08T06:15:00+00:00"


def test_rss_parse_strips_publisher_and_keeps_missing_dates_explicit():
    items = parse_rss((HERE / "rss_copper.xml").read_bytes(), retrieved_at="2026-10-08T08:00:00Z")
    assert items[0]["title"] == "Workers strike at Chile copper port of Valparaiso"
    assert (items[0]["source_id"], items[0]["publisher"]) == ("example-wire.test", "Example Wire")
    assert items[0]["published_at"] == "2026-10-08T05:30:00+00:00"
    assert items[3]["published_at"] is None
    assert items[0]["language"] == "en-IN" and items[0]["url"] is None


def test_recorded_fetch_states_are_explicit():
    states = [(f["provider"], f["material"], f["state"], f["reason"]) for f in recorded_fetches(HERE / "recorded.json")]
    assert ("gdelt", "iron_ore", "rate_limited", "http_429") in states
    assert ("google_news_rss", "aluminium", "malformed_payload", "invalid_xml") in states
    assert ("gdelt", "copper", "ok", None) in states


def test_offline_mode_never_fetches():
    # tests/conftest.py sets OFFLINE=1; the reused ingest http_get refuses before any network access
    assert fetch_gdelt_items("copper", retrieved_at=AS_OF)["state"] == "offline"
    assert fetch_rss_items("copper", retrieved_at=AS_OF)["state"] == "offline"


def test_recorded_http_states_are_explicit():
    response = fetch_module._from_body("gdelt", "copper", 429, b"", AS_OF, True)
    assert (response["state"], response["reason"]) == ("rate_limited", "http_429")
    response = fetch_module._from_body("google_news_rss", "copper", 503, b"", AS_OF, True)
    assert (response["state"], response["reason"]) == ("provider_failure", "http_503")
    response = fetch_module._from_body("gdelt", "copper", 200, b'{"articles": []}', AS_OF, True)
    assert (response["state"], response["reason"]) == ("empty", "no_items")


def test_recorded_response_reuses_existing_item_parser():
    raw = (HERE / "gdelt_copper.json").read_bytes()
    out = fetch_module._from_body("gdelt", "copper", 200, raw, "2026-10-08T07:00:00Z", True)
    assert out["state"] == "ok" and len(out["items"]) == 4
    assert out["items"] == parse_gdelt(raw, retrieved_at="2026-10-08T07:00:00Z")


# Snapshots


def test_snapshot_fields(built):
    [s] = [x for x in by_title(built, "India weighs") if x]
    for key in ("snapshot_id", "providers", "source", "story_id", "revision_id", "url", "title", "first_seen_at",
                "retrieved_at", "provider_first_seen_at", "published_at", "published_at_status", "content_sha256",
                "language", "rights", "synthetic", "sample", "entities", "corroboration", "evidence_status"):
        assert key in s
    assert (s["published_at"], s["published_at_status"]) == (None, "not_stated")
    assert s["synthetic"] is True and s["sample"] is False
    assert s["evidence_status"] == "candidate" and s["note"] == CANDIDATE_NOTE
    assert s["rights"] == [{"provider": "gdelt", "rights_status": "unreviewed", "permitted_uses": [],
                            "attribution": "Source: The GDELT Project (gdeltproject.org)",
                            "terms_note": SOURCES["providers"]["gdelt"]["terms_note"]}]
    assert "full_text" not in json.dumps(s)


def test_conservative_entity_tagging(built):
    [strike] = [s for s in by_title(built, "Workers strike at Chile") if s["source"]["source_id"] == "example-wire.test"]
    e = strike["entities"]
    assert (e["event_type"], e["event_type_status"]) == ("strike", "resolved")
    assert (e["materials"], e["countries"], e["ports"], e["routes"]) == (["copper"], ["CL"], ["CLVAP"], [])
    [cyclone] = by_title(built, "Cyclone warning")
    assert cyclone["entities"]["event_type"] == "weather"
    assert cyclone["entities"]["materials"] == [] and "material" in cyclone["entities"]["unresolved"]
    assert "country" in cyclone["entities"]["unresolved"]
    [both] = by_title(built, "Port workers strike as cyclone")
    assert (both["entities"]["event_type"], both["entities"]["event_type_status"]) == (None, "ambiguous")
    assert both["entities"]["event_type_candidates"] == ["strike", "weather"]
    [prices] = by_title(built, "Copper prices rise")
    assert (prices["entities"]["event_type"], prices["entities"]["event_type_status"]) == (None, "unresolved")


def test_instruction_like_headline_is_data_and_held_back(built):
    [s] = by_title(built, "Ignore previous instructions")
    assert s["review_flags"] == ["embedded_instruction"]
    assert s["entities"]["event_type"] == "closure"  # tagged by the same rules, nothing followed
    assert s["gate_row"] is None and s["gate_row_reason"] == "needs_review_instruction_like_text"


def test_cross_provider_duplicate_is_one_snapshot(built):
    same = [s for s in by_title(built, "Workers strike at Chile") if s["source"]["source_id"] == "example-wire.test"]
    assert len(same) == 1
    assert same[0]["providers"] == ["gdelt", "google_news_rss"]
    assert same[0]["first_seen_at"] == "2026-10-08T07:00:00+00:00"
    assert same[0]["published_at"] == "2026-10-08T05:30:00+00:00"


def test_corroboration_counts_distinct_publishers_only(built):
    pair = by_title(built, "Workers strike at Chile")
    assert sorted(s["source"]["source_id"] for s in pair) == ["andes-times.test", "example-wire.test"]
    for s in pair:
        assert s["corroboration"]["status"] == "corroborated"
        assert s["corroboration"]["distinct_sources"] == 2
        assert len(s["corroboration"]["evidence_refs"]) == 1
    [alone] = by_title(built, "India weighs")
    assert alone["corroboration"] == {"status": "uncorroborated", "distinct_sources": 1, "evidence_refs": [],
                                      "known_at": alone["first_seen_at"], "rule": alone["corroboration"]["rule"]}


def test_later_revision_is_a_new_revision_of_the_same_story(built):
    [old] = by_title(built, "India weighs")
    [new] = by_title(built, "India imposes")
    assert old["story_id"] == new["story_id"] and old["revision_id"] != new["revision_id"]
    assert new["first_seen_at"] == "2026-10-08T10:00:00+00:00"


def test_repeated_fetch_and_after_cutoff(built):
    assert any(d["snapshot_id"] == by_title(built, "Copper prices rise")[0]["snapshot_id"] for d in built["duplicates"])
    assert not by_title(built, "Jebel Ali")
    assert [(r["provider"], r["reason"]) for r in built["rejected"]] == [("gdelt", "retrieved_after_as_of")]


def test_deterministic_and_inputs_untouched():
    fetches = recorded_fetches(HERE / "recorded.json")
    before = copy.deepcopy(fetches)
    a = build_snapshots(fetches, as_of=AS_OF, sources=SOURCES)
    b = build_snapshots(list(reversed(fetches)), as_of=AS_OF, sources=SOURCES)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    assert fetches == before


def test_output_never_carries_price_effects_or_confirmation(built):
    rows = gate_rows(built)
    assert rows and all(r["effect_class"] in {"delay", "export_restriction", "policy"} for r in rows)
    text = json.dumps(built)
    assert '"confirmed"' not in text and '"price_effect"' not in text


# The real #14 gate accepts the rows


def test_gate_accepts_rows_and_denies_unreviewed_rights(built):
    projection = project_events(gate_rows(built), as_of=AS_OF, scope={"material": "copper"}, purpose="planning",
                                allow_demo=True)
    assert projection.features == ()
    reasons = {x["reason"] for x in projection.exclusions}
    assert reasons == {"permission_denied"}


def test_gate_selects_corroborated_strike_once_rights_allow_planning():
    sources = copy.deepcopy(SOURCES)
    for provider in sources["providers"].values():
        provider.update(rights_status="reviewed", permitted_uses=["planning"])
    result = build_snapshots(recorded_fetches(HERE / "recorded.json"), as_of=AS_OF, sources=sources)
    projection = project_events(gate_rows(result), as_of=AS_OF, scope={"material": "copper"}, purpose="planning",
                                allow_demo=True)
    selected = sorted(p["source_id"] for f in projection.features for p in f["provenance"])
    assert selected == ["andes-times.test", "example-wire.test"]
    assert all(f["effect_class"] == "delay" for f in projection.features)
    assert all(p["corroboration"] == "corroborated" for f in projection.features for p in f["provenance"])
    without_demo = project_events(gate_rows(result), as_of=AS_OF, scope={"material": "copper"}, purpose="planning")
    assert without_demo.features == () and {x["reason"] for x in without_demo.exclusions} == {"demo_excluded"}


def test_gate_point_in_time_picks_the_revision_known_then():
    sources = copy.deepcopy(SOURCES)
    for provider in sources["providers"].values():
        provider.update(rights_status="reviewed", permitted_uses=["planning"])
    result = build_snapshots(recorded_fetches(HERE / "recorded.json"), as_of=AS_OF, sources=sources)
    rows, titles = gate_rows(result), {s["revision_id"]: s["title"] for s in result["snapshots"]}
    early = project_events(rows, as_of="2026-10-08T09:00:00Z", scope={"material": "aluminium"}, purpose="planning",
                           allow_demo=True)
    late = project_events(rows, as_of=AS_OF, scope={"material": "aluminium"}, purpose="planning", allow_demo=True)
    assert [titles[p["revision_id"]] for f in early.features for p in f["provenance"]] == ["India weighs aluminium export curb"]
    assert [titles[p["revision_id"]] for f in late.features for p in f["provenance"]] == ["India imposes aluminium export curb"]


def test_corroboration_at_early_origin_excludes_later_publisher():
    sources = copy.deepcopy(SOURCES)
    sources["providers"]["gdelt"].update(rights_status="reviewed", permitted_uses=["planning"])
    fetches = []
    for hour, domain in (("07", "a.test"), ("08", "b.test"), ("10", "c.test")):
        retrieved = f"2026-10-08T{hour}:00:00Z"
        body = json.dumps({"articles": [{"url": f"https://{domain}/strike",
                                          "title": "Copper strike at Chennai port",
                                          "seendate": "20261008T060000Z", "domain": domain,
                                          "language": "English"}]}).encode()
        fetches.append({"provider": "gdelt", "material": "copper", "state": "ok", "reason": None,
                        "retrieved_at": f"2026-10-08T{hour}:00:00+00:00", "request": "synthetic",
                        "synthetic": True, "items": parse_gdelt(body, retrieved_at=retrieved)})
    rows = gate_rows(build_snapshots(fetches, as_of="2026-10-08T12:00:00Z", sources=sources))
    early = project_events(rows, as_of="2026-10-08T08:30:00Z", scope={"material": "copper"},
                           purpose="planning", allow_demo=True)
    provenance = [p for f in early.features for p in f["provenance"]]
    assert {p["source_id"] for p in provenance} == {"a.test", "b.test"}
    assert all("c.test" not in " ".join(p["evidence_refs"]) for p in provenance)
    assert all(p["corroboration_snapshot"]["known_at"] <= "2026-10-08T08:30:00+00:00"
               for p in provenance)


def test_unreviewed_rights_cannot_grant_planning_use():
    sources = copy.deepcopy(SOURCES)
    sources["providers"]["gdelt"]["permitted_uses"] = ["planning"]
    body = json.dumps({"articles": [{"url": "https://wire.test/strike",
                                  "title": "Copper strike at Chennai port",
                                  "seendate": "20261008T060000Z", "domain": "wire.test",
                                  "language": "English"}]}).encode()
    fetches = [{"provider": "gdelt", "material": "copper", "state": "ok", "reason": None,
                "retrieved_at": "2026-10-08T09:00:00+00:00", "request": "synthetic", "synthetic": True,
                "items": parse_gdelt(body, retrieved_at="2026-10-08T09:00:00Z")}]
    projection = project_events(gate_rows(build_snapshots(fetches, as_of=AS_OF, sources=sources)),
                                as_of=AS_OF, scope={"material": "copper"}, purpose="planning", allow_demo=True)
    assert projection.features == ()
    assert {e["reason"] for e in projection.exclusions} == {"permission_denied"}


def test_unreviewed_rights_registry_inconsistency_is_rejected(tmp_path):
    sources = copy.deepcopy(SOURCES)
    sources["providers"]["gdelt"]["permitted_uses"] = ["planning"]
    path = tmp_path / "synthetic-news-sources.json"
    path.write_text(json.dumps(sources))
    with pytest.raises(ValueError, match="unreviewed news rights"):
        load_sources(path)


@pytest.mark.parametrize("article,reason", [
    ({"url": "https://wire.test/story", "title": "Copper strike", "seendate": "20261340T250000Z"},
     "invalid_article"),
    ({"url": "https://[invalid", "title": "Copper strike", "seendate": "20261008T060000Z"},
     "invalid_article"),
])
def test_malformed_gdelt_article_has_explicit_state(article, reason):
    response = fetch_module._from_body("gdelt", "copper", 200,
                                       json.dumps({"articles": [article]}).encode(), AS_OF, True)
    assert (response["state"], response["reason"], response["items"]) == (
        "malformed_payload", reason, [])


def test_malformed_rss_source_url_has_explicit_state():
    raw = (b'<rss><channel><item><title>Copper strike</title>'
           b'<source url="https://[invalid">Wire</source></item></channel></rss>')
    response = fetch_module._from_body("google_news_rss", "copper", 200, raw, AS_OF, True)
    assert (response["state"], response["reason"], response["items"]) == (
        "malformed_payload", "invalid_item", [])


def test_live_fetch_entrypoints_refuse_without_approved_bounded_transport(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("network transport must not be reached")
    monkeypatch.setattr(fetch_module, "http_get", forbidden, raising=False)
    monkeypatch.setenv("OFFLINE", "0")
    for call in (fetch_gdelt_items, fetch_rss_items):
        result = call("copper", retrieved_at=AS_OF)
        assert result["state"] == "offline" and result["reason"] == "fetch_not_approved"


def test_reviewed_fetch_control_alone_cannot_enable_unreviewed_transport(monkeypatch, tmp_path):
    sources = copy.deepcopy(SOURCES)
    sources["providers"]["gdelt"].update(rights_status="reviewed", fetch_allowed=True)
    path = tmp_path / "synthetic-news-sources.json"
    path.write_text(json.dumps(sources))
    monkeypatch.setattr(fetch_module, "NEWS_SOURCES_FILE", path)
    monkeypatch.setenv("OFFLINE", "0")
    result = fetch_gdelt_items("copper", retrieved_at=AS_OF)
    assert (result["state"], result["reason"], result["items"]) == (
        "offline", "bounded_transport_unavailable", [])


def _dated_synthetic_fetch(provider, source_id, hour, published=None):
    retrieved = f"2026-10-08T{hour:02d}:00:00Z"
    title = "Copper strike at Chennai port"
    if provider == "gdelt":
        body = json.dumps({"articles": [{"url": f"https://{source_id}/strike", "title": title,
                                          "seendate": "20261008T060000Z", "domain": source_id,
                                          "language": "English"}]}).encode()
        items = parse_gdelt(body, retrieved_at=retrieved)
    else:
        date = f"<pubDate>{published}</pubDate>" if published else ""
        body = (f'<rss><channel><item><title>{title} - Wire</title>'
                f'<source url="https://{source_id}">Wire</source><guid>{source_id}-strike</guid>'
                f'{date}</item></channel></rss>').encode()
        items = parse_rss(body, retrieved_at=retrieved)
    return {"provider": provider, "material": "copper", "state": "ok", "reason": None,
            "retrieved_at": items[0]["retrieved_at"], "request": "generated-synthetic",
            "synthetic": True, "items": items}


def test_future_published_rss_witness_cannot_corroborate_prior_publisher():
    sources = copy.deepcopy(SOURCES)
    for provider in sources["providers"].values():
        provider.update(rights_status="reviewed", permitted_uses=["planning"])
    first = _dated_synthetic_fetch("gdelt", "a.test", 7)
    contradictory = _dated_synthetic_fetch("google_news_rss", "b.test", 8,
                                            "Thu, 08 Oct 2026 10:00:00 GMT")
    result = build_snapshots([first, contradictory], as_of=AS_OF, sources=sources)
    assert any(row["reason"] == "publication_after_retrieval" and row["provider"] == "google_news_rss"
               for row in result["rejected"])
    assert {s["source"]["source_id"] for s in result["snapshots"]} == {"a.test"}
    projection = project_events(gate_rows(result), as_of="2026-10-08T08:30:00Z",
                                scope={"material": "copper"}, purpose="planning", allow_demo=True)
    assert len(projection.features) == 1
    [provenance] = projection.features[0]["provenance"]
    assert provenance["source_id"] == "a.test"
    assert provenance["corroboration"] == "uncorroborated"
    assert provenance["evidence_refs"] == ()


def test_late_unreviewed_feed_copy_preserves_early_full_projection_and_later_denial():
    sources = copy.deepcopy(SOURCES)
    sources["providers"]["gdelt"].update(rights_status="reviewed", permitted_uses=["planning"])
    first = _dated_synthetic_fetch("gdelt", "a.test", 7)
    later = _dated_synthetic_fetch("google_news_rss", "a.test", 10,
                                   "Thu, 08 Oct 2026 06:00:00 GMT")
    early = "2026-10-08T08:30:00Z"
    project = lambda result, at: project_events(gate_rows(result), as_of=at, scope={"material": "copper"},
                                                 purpose="planning", allow_demo=True)
    first_early = project(build_snapshots([first], as_of=early, sources=sources), early)
    both_early = project(build_snapshots([first, later], as_of=early, sources=sources), early)
    assert first_early == both_early and len(both_early.features) == 1
    full_archive = build_snapshots([first, later], as_of=AS_OF, sources=sources)
    historical = project(full_archive, early)
    assert historical.features == first_early.features
    assert not any(x["reason"] == "permission_denied" for x in historical.exclusions)
    late = project(full_archive, AS_OF)
    assert late.features == ()
    assert any(x["reason"] == "permission_denied" for x in late.exclusions)


def test_later_url_cannot_rewrite_earlier_rss_story_identity():
    sources = copy.deepcopy(SOURCES)
    sources["providers"]["google_news_rss"].update(rights_status="reviewed", permitted_uses=["planning"])
    first = _dated_synthetic_fetch("google_news_rss", "a.test", 7)
    later = _dated_synthetic_fetch("gdelt", "a.test", 10)
    early = "2026-10-08T08:30:00Z"
    project = lambda result: project_events(gate_rows(result), as_of=early, scope={"material": "copper"},
                                             purpose="planning", allow_demo=True)
    early_only = project(build_snapshots([first], as_of=early, sources=sources))
    full_archive = project(build_snapshots([first, later], as_of=AS_OF, sources=sources))
    assert full_archive.features == early_only.features and len(full_archive.features) == 1
