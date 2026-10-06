"""Synthetic point-in-time assertions; no provider, article text or numeric effect."""
from __future__ import annotations

from copy import deepcopy

import pytest

from procure.event_features import project_events


DEC = "2025-12-31T23:00:00Z"
JAN = "2026-01-01T01:00:00+00:00"
FEB = "2026-02-01T00:00:00Z"


def revision(**changes):
    row = {
        "source_id": "source-a", "source_url": "https://example.test/item/a",
        "story_id": "story-1", "revision_id": "r1", "content_hash": "a" * 64,
        "source_kind": "news", "event_type": "supply_warning", "effect_class": "price",
        "event_at": DEC, "effective_at": None, "published_at": DEC, "edited_at": None,
        "first_seen_at": DEC,
        "first_seen_witness": {"source_id": "source-a", "revision_id": "r1", "content_hash": "a" * 64},
        "archive_witnesses": [],
        "permissions": {"known_at": DEC, "uses": ["training", "planning"]},
        "corroboration": {"known_at": DEC, "status": "corroborated", "evidence_refs": ["evidence-a"]},
        "scope": {"known_at": DEC, "materials": ["copper"], "countries": [], "hs": [],
                  "routes": ["IN-DE"], "ports": [], "global": []},
        "confidence": 0.7, "synthetic": False, "sample": False,
    }
    row.update(changes)
    return row


def gate(rows, *, as_of=JAN, purpose="training", scope=None, allow_demo=False):
    if scope is None:
        scope = {"material": "copper", "route": "IN-DE"}
    return project_events(rows, as_of=as_of, scope=scope, purpose=purpose, allow_demo=allow_demo)


def reasons(result):
    return {item["reason"] for item in result.exclusions}


def test_known_revision_survives_future_edit_and_post_origin_append():
    original = revision()
    edited = revision(revision_id="r2", content_hash="b" * 64, first_seen_at=FEB,
                      first_seen_witness={"source_id": "source-a", "revision_id": "r2", "content_hash": "b" * 64},
                      edited_at=FEB, permissions={"known_at": FEB, "uses": ["training", "planning"]},
                      corroboration={"known_at": FEB, "status": "corroborated", "evidence_refs": ["later"]},
                      scope={**original["scope"], "known_at": FEB})
    before = gate([original], as_of=JAN)
    after_append = gate([original, edited], as_of=JAN)
    assert len(before.features) == 1
    assert before.features == after_append.features
    assert before.features[0]["provenance"][0]["revision_id"] == "r1"
    assert len(gate([original, edited], as_of="2026-02-02T00:00:00Z").features) == 1
    assert gate([original, edited], as_of="2026-02-02T00:00:00Z").features[0]["provenance"][0]["revision_id"] == "r2"


def test_unknown_publication_firstseen_witness_and_earlier_archive():
    unknown = revision(published_at=None)
    assert len(gate([unknown]).features) == 1
    mismatched = revision(published_at=None, first_seen_witness={**unknown["first_seen_witness"],
                                                              "content_hash": "f" * 64})
    assert not gate([mismatched]).features
    assert "witness_mismatch" in reasons(gate([mismatched]))
    archive = revision(published_at=None, first_seen_at=FEB,
                       archive_witnesses=[{"at": DEC, "source_id": "source-a", "revision_id": "r1",
                                           "content_hash": "a" * 64, "evidence_ref": "archive://a"}])
    assert len(gate([archive], as_of=JAN).features) == 1
    provenance = gate([archive], as_of=JAN).features[0]["provenance"][0]
    assert provenance["content_available_at"] == DEC.replace("Z", "+00:00")
    assert provenance["archive_witnesses"] == ({"at": DEC.replace("Z", "+00:00"),
                                                "evidence_ref": "archive://a",
                                                "source_id": "source-a", "revision_id": "r1",
                                                "content_hash": "a" * 64},)
    assert provenance["permissions"] == {"known_at": DEC.replace("Z", "+00:00"),
                                          "uses": ("planning", "training")}
    assert provenance["corroboration_snapshot"]["known_at"] == DEC.replace("Z", "+00:00")
    assert provenance["scope_known_at"] == DEC.replace("Z", "+00:00")
    archive["archive_witnesses"][0]["evidence_ref"] = ""
    assert not gate([archive], as_of=JAN).features


def test_old_publication_cannot_backdate_freshly_retrieved_edit():
    fresh = revision(published_at=DEC, edited_at=FEB, first_seen_at=FEB,
                     permissions={"known_at": FEB, "uses": ["training"]},
                     corroboration={"known_at": FEB, "status": "corroborated", "evidence_refs": ["evidence-feb"]},
                     scope={**revision()["scope"], "known_at": FEB})
    assert not gate([fresh], as_of=JAN).features
    assert "after_origin" in reasons(gate([fresh], as_of=JAN))


def test_future_effective_policy_is_known_current_information():
    policy = revision(event_type="tariff_notice", effect_class="policy", effective_at=FEB)
    result = gate([policy], as_of=JAN)
    assert len(result.features) == 1
    assert result.features[0]["effect_class"] == "policy"
    assert result.features[0]["provenance"][0]["effective_at"] == FEB.replace("Z", "+00:00")


@pytest.mark.parametrize("change,reason", [
    ({"permissions": {"known_at": JAN, "uses": []}}, "permission_denied"),
    ({"corroboration": {"known_at": JAN, "status": "retracted", "evidence_refs": []}}, "retracted"),
    ({"scope": {"known_at": JAN, "materials": ["iron_ore"], "countries": [], "hs": [],
                "routes": ["IN-DE"], "ports": [], "global": []}}, "scope_mismatch"),
])
def test_latest_known_denial_retraction_or_scope_change_blocks_fallback(change, reason):
    older = revision()
    newer = revision(revision_id="r2", first_seen_witness={"source_id": "source-a", "revision_id": "r2", "content_hash": "a" * 64}, **change)
    result = gate([older, newer], as_of="2026-01-02T00:00:00Z")
    assert not result.features
    assert reason in reasons(result)


def test_tied_conflicting_snapshots_are_ambiguous():
    older = revision()
    conflict = revision(revision_id="r2", first_seen_witness={"source_id": "source-a", "revision_id": "r2", "content_hash": "a" * 64}, permissions={"known_at": DEC, "uses": []})
    result = gate([older, conflict])
    assert not result.features
    assert "ambiguous_revision" in reasons(result)


def test_same_story_reposts_deduplicate_without_aggregate_confidence():
    first = revision()
    repost = revision(source_id="source-b", source_url="https://example.test/item/b", revision_id="b1",
                      content_hash="b" * 64, confidence=0.2,
                      first_seen_witness={"source_id": "source-b", "revision_id": "b1", "content_hash": "b" * 64})
    one = gate([first])
    two = gate([repost, first])
    assert len(one.features) == len(two.features) == 1
    feature = two.features[0]
    assert feature["story_id"] == "story-1"
    assert {(p["source_id"], p["content_hash"], p["confidence"]) for p in feature["provenance"]} == {
        ("source-a", "a" * 64, 0.7), ("source-b", "b" * 64, 0.2)}
    assert "confidence" not in feature and "corroboration_count" not in feature
    assert two.features == gate([first, repost]).features


def test_effect_scope_hs_revision_and_explicit_global_are_separate():
    first = revision()
    delay = revision(source_id="source-b", revision_id="b1", content_hash="b" * 64,
                     first_seen_witness={"source_id": "source-b", "revision_id": "b1", "content_hash": "b" * 64},
                     effect_class="delay")
    assert {f["effect_class"] for f in gate([first, delay]).features} == {"price", "delay"}
    wrong_route = revision(scope={**first["scope"], "routes": []})
    assert not gate([wrong_route]).features
    global_route = revision(scope={**first["scope"], "routes": [], "global": ["route"]})
    assert len(gate([global_route]).features) == 1
    restricted_material = revision(scope={**first["scope"], "global": ["material", "route"]})
    assert not gate([restricted_material], scope={"material": "aluminium", "route": "IN-DE"}).features
    hs_scope = {**first["scope"], "hs": [{"code": "7403", "revision": "HS2022"}]}
    hs_row = revision(scope=hs_scope)
    assert len(gate([hs_row], scope={"material": "copper", "hs": {"code": "7403", "revision": "HS2022"}}).features) == 1
    assert not gate([hs_row], scope={"material": "copper", "hs": {"code": "7403", "revision": "HS2017"}}).features


def test_social_training_rights_dated_metadata_and_demo_separation():
    social = revision(source_kind="social", corroboration={"known_at": DEC, "status": "uncorroborated", "evidence_refs": []})
    assert not gate([social], purpose="training").features
    assert "social_uncorroborated" in reasons(gate([social], purpose="training"))
    assert len(gate([social], purpose="planning").features) == 1
    future_rights = revision(permissions={"known_at": FEB, "uses": ["training"]})
    assert not gate([future_rights], as_of=JAN).features
    sample = revision(source_id="sample", story_id="sample-story", revision_id="s1", content_hash="c" * 64,
                      first_seen_witness={"source_id": "sample", "revision_id": "s1", "content_hash": "c" * 64},
                      sample=True)
    assert not gate([sample]).features
    assert len(gate([sample], allow_demo=True).features) == 1
    assert gate([sample], allow_demo=True).features[0]["sample"] is True
    synthetic = revision(source_id="synthetic", story_id="synthetic-story", revision_id="s2", content_hash="d" * 64,
                         first_seen_witness={"source_id": "synthetic", "revision_id": "s2", "content_hash": "d" * 64},
                         synthetic=True)
    assert len(gate([sample, synthetic], allow_demo=True).features) == 2


@pytest.mark.parametrize("field,value", [
    ("first_seen_at", "2025-12-31"),
    ("published_at", "2025-12-31T23:00:00"),
    ("content_hash", "bad-hash"),
    ("story_id", ""),
])
def test_malformed_record_quarantined_with_reason(field, value):
    row = revision(**{field: value})
    result = gate([row])
    assert not result.features
    assert result.exclusions and result.exclusions[0]["reason"] in {"invalid_timestamp", "invalid_record"}


def test_unknown_availability_empty_and_invalid_query():
    row = revision(first_seen_at=None, first_seen_witness=None, archive_witnesses=[])
    result = gate([row])
    assert not result.features and "unknown_availability" in reasons(result)
    assert gate([]).features == ()
    with pytest.raises(ValueError):
        gate([revision()], as_of="2026-01-01")
    with pytest.raises(ValueError):
        gate([revision()], purpose="other")


def test_post_origin_metadata_append_is_feature_invariant():
    older = revision()
    later = deepcopy(older)
    later["revision_id"] = "r2"
    later["first_seen_witness"] = {"source_id": "source-a", "revision_id": "r2", "content_hash": "a" * 64}
    later["confidence"] = 0.1
    later["permissions"] = {"known_at": FEB, "uses": []}
    later["scope"] = {**later["scope"], "known_at": FEB}
    later["corroboration"] = {**later["corroboration"], "known_at": FEB}
    assert gate([older], as_of=JAN).features == gate([older, later], as_of=JAN).features


def test_stable_identity_can_be_id_only_or_url_only_with_exact_witness():
    id_only = revision(source_url=None)
    url_only = revision(source_id=None, source_url="https://example.test/url-only",
                        first_seen_witness={"source_url": "https://example.test/url-only",
                                            "revision_id": "r1", "content_hash": "a" * 64})
    assert len(gate([id_only]).features) == 1
    assert len(gate([url_only]).features) == 1
    wrong_url_witness = revision(source_id=None, source_url="https://example.test/url-only",
                                 first_seen_witness={"source_url": "https://example.test/other",
                                                     "revision_id": "r1", "content_hash": "a" * 64})
    assert "witness_mismatch" in reasons(gate([wrong_url_witness]))


def test_known_unknown_event_time_is_allowed_but_missing_timestamp_field_is_invalid():
    unknown = revision(event_at=None, effective_at=None, published_at=None, edited_at=None)
    assert len(gate([unknown]).features) == 1
    missing = revision()
    del missing["event_at"]
    assert "invalid_record" in reasons(gate([missing]))


def test_exact_content_witness_before_known_edit_or_publication_is_quarantined():
    edited = revision(edited_at=JAN)
    published = revision(published_at=JAN)
    for row in (edited, published):
        result = gate([row], as_of="2026-01-02T00:00:00Z")
        assert not result.features
        assert "contradictory_chronology" in reasons(result)


def test_social_corroboration_needs_evidence_and_source_kind_cannot_bypass_gate():
    unevidenced = revision(source_kind="social",
                           corroboration={"known_at": DEC, "status": "corroborated", "evidence_refs": []})
    assert not gate([unevidenced], purpose="training").features
    assert "corroboration_unavailable" in reasons(gate([unevidenced], purpose="training"))
    invalid_kind = revision(source_kind="Social",
                            corroboration={"known_at": DEC, "status": "uncorroborated", "evidence_refs": []})
    assert not gate([invalid_kind], purpose="training").features
    assert "invalid_record" in reasons(gate([invalid_kind], purpose="training"))


def test_missing_metadata_known_at_is_explicitly_unavailable():
    row = revision(permissions={"known_at": None, "uses": ["planning", "training"]})
    assert not gate([row]).features
    assert "metadata_unavailable" in reasons(gate([row]))


@pytest.mark.parametrize("changes", [
    {"corroboration": {"known_at": DEC, "status": [], "evidence_refs": []}},
    {"confidence": 10**1000},
])
def test_unhashable_corroboration_status_and_huge_confidence_are_quarantined(changes):
    result = gate([revision(**changes)])
    assert not result.features
    assert len(result.exclusions) == 1
    assert reasons(result) == {"invalid_record"}


def test_id_and_url_identity_namespaces_do_not_suppress_each_other():
    shared = "https://example.test/shared"
    allowed_id = revision(
        source_id=shared, source_url=None,
        first_seen_witness={"source_id": shared, "revision_id": "r1",
                            "content_hash": "a" * 64},
    )
    denied_url = revision(
        source_id=None, source_url=shared, revision_id="r2", content_hash="b" * 64,
        first_seen_witness={"source_url": shared, "revision_id": "r2",
                            "content_hash": "b" * 64},
        permissions={"known_at": JAN, "uses": []},
    )
    result = gate([allowed_id, denied_url], as_of="2026-01-02T00:00:00Z")
    assert len(result.features) == 1
    assert len(result.features[0]["provenance"]) == 1
    assert result.features[0]["provenance"][0]["content_hash"] == "a" * 64
    assert "permission_denied" in reasons(result)


def test_same_value_id_and_url_provenance_order_is_input_invariant():
    shared = "https://example.test/shared"
    id_only = revision(
        source_id=shared, source_url=None,
        first_seen_witness={"source_id": shared, "revision_id": "r1",
                            "content_hash": "a" * 64},
    )
    url_only = revision(
        source_id=None, source_url=shared,
        first_seen_witness={"source_url": shared, "revision_id": "r1",
                            "content_hash": "a" * 64},
    )
    forward = gate([id_only, url_only]).features
    backward = gate([url_only, id_only]).features
    assert forward == backward
    assert len(forward) == 1
    assert len(forward[0]["provenance"]) == 2


@pytest.mark.parametrize("changes", [
    {"content_hash": int("1" * 64),
     "first_seen_witness": {"source_id": "source-a", "revision_id": "r1",
                            "content_hash": int("1" * 64)}},
    {"source_url": b"https://example.test/item"},
    {"source_url": {"bad": "url"}},
    {"source_id": b"source-a"},
    {"source_id": ""},
])
def test_supplied_hash_and_source_identifiers_require_actual_strings(changes):
    result = gate([revision(**changes)])
    assert not result.features
    assert reasons(result) == {"invalid_record"}


def test_url_only_invalid_record_keeps_safe_typed_identity_in_diagnostic():
    url = "https://example.test/url-only"
    row = revision(source_id=None, source_url=url, content_hash="bad")
    result = gate([row])
    assert not result.features
    assert result.exclusions == ({
        "source_id": None, "source_url": url, "source_identity_kind": "source_url",
        "story_id": "story-1", "reason": "invalid_record",
    },)
    denied = revision(source_id=None, source_url=url,
                      first_seen_witness={"source_url": url, "revision_id": "r1",
                                          "content_hash": "a" * 64},
                      permissions={"known_at": DEC, "uses": []})
    assert gate([denied]).exclusions[0]["source_identity_kind"] == "source_url"


def test_conflicting_tie_and_bad_url_exclusions_are_order_invariant():
    first = revision(source_url="https://example.test/a")
    second = revision(source_url="https://example.test/b")
    forward_tie = gate([first, second]).exclusions
    backward_tie = gate([second, first]).exclusions
    assert forward_tie == backward_tie
    assert forward_tie == ({
        "source_id": "source-a", "source_url": None,
        "source_identity_kind": "source_id", "story_id": "story-1",
        "reason": "ambiguous_revision",
    },)


def test_bad_url_only_exclusions_are_order_invariant():
    bad_a = revision(source_id=None, source_url="https://example.test/a",
                     content_hash="bad")
    bad_b = revision(source_id=None, source_url="https://example.test/b",
                     content_hash="bad")
    assert gate([bad_a, bad_b]).exclusions == gate([bad_b, bad_a]).exclusions
