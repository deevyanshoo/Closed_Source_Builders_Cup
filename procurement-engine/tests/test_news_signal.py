from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from procure import news_signal as ns


@pytest.mark.parametrize("headline,material,direction", [
    ("Major copper mine strike halts 10% of global supply", "copper", "up"),
    ("Aluminium surplus grows as new smelters ramp up", "aluminium", "down"),
    ("Iron ore inventory build at Chinese ports", "iron_ore", "down"),
    ("Indonesia export restriction on copper concentrate extended", "copper", "up"),
    ("China: Iron ore spot prices hit over 1-month low on weak demand, ample supply", "iron_ore", "down"),
])
def test_rules_direction_and_material(headline, material, direction):
    s = ns.extract_rules(headline)
    assert s.material == material
    assert s.relevant
    assert s.direction == direction
    assert 0 < s.severity <= 1
    assert 1 <= s.horizon_months <= 6
    assert s.reason and s.extractor == "rules"


def test_rules_irrelevant_headline():
    s = ns.extract_rules("Gold prices rise on safe haven demand")
    assert not s.relevant and s.material is None


def test_strong_headline_scores_higher_than_weak():
    strong = ns.extract_rules("Major copper mine strike halts 10% of global supply")
    weak = ns.extract_rules("Copper tightens slightly")
    assert strong.severity > weak.severity


def _item(direction, severity, days, title="t"):
    return {"id": title, "title": title, "relevant": True, "direction": direction, "severity": severity,
            "seen_at": (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()}


def test_index_decays_with_age_and_is_bounded():
    fresh, _ = ns.news_index([_item("up", 1.0, 0, "copper mine strike chile")])
    old, _ = ns.news_index([_item("up", 1.0, 14, "copper mine strike chile")])
    assert fresh > old > 0
    many = [_item("up", 1.0, 0, f"story number {i} unique words {i}x{i}") for i in range(50)]
    idx, _ = ns.news_index(many)
    assert -1 <= idx <= 1
    down, _ = ns.news_index([_item("down", 0.8, 1, "surplus")])
    assert down < 0


def test_half_life_is_seven_days():
    c0 = ns.contribution("up", 1.0, 0, 7)
    c7 = ns.contribution("up", 1.0, 7, 7)
    assert c7 == pytest.approx(c0 / 2)


def test_syndicated_duplicates_count_once():
    a = _item("up", 0.5, 1, "Unions at Centinela copper mine ready for strike")
    b = _item("up", 0.5, 1, "Unions at Centinela copper mine ready for strike action")
    one, _ = ns.news_index([a])
    two, scored = ns.news_index([a, b])
    assert one == pytest.approx(two)
    assert sum(s["duplicate_of_story"] for s in scored) == 1


def test_llm_failure_falls_back_to_rules(monkeypatch):
    class S:
        gemini_api_key = "fake"

    monkeypatch.setattr(ns, "get_settings", lambda: S())

    def boom(*a, **k):
        raise RuntimeError("network down")

    monkeypatch.setattr(ns, "extract_llm", boom)
    out = ns.extract_many(["Copper smelter outage in Chile"])
    assert out[0].extractor == "rules" and out[0].direction == "up"


def test_no_key_uses_rules():
    assert ns.extract_one("Copper smelter outage").extractor == "rules"
