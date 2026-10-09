"""Build point-in-time news snapshots and the rows #14's event gate (``event_features.project_events``) accepts.

``build_snapshots(fetches, as_of=..., sources=...)`` is pure and deterministic:
- Fetches retrieved after ``as_of`` are rejected whole.
- Story identity: the canonical article URL when a provider states one (GDELT). An RSS item with
  the same publisher and the same normalised headline as a URL-identified item is the same story
  (cross-provider duplicate); otherwise its feed GUID identifies it.
- A revision is one exact headline of one story from one publisher (content hash). A later fetch
  with a changed headline is a new revision of the same story; an identical repeat is deduplicated,
  keeping the earliest retrieval.
- Entities are tagged only by the stated rules in config/news_sources_v1.json (word-boundary
  phrase matches on the headline). No match leaves a dimension unresolved; two event types make
  the event ambiguous. Headline text is untrusted: instruction-like text is flagged and held back.
- Corroboration counts distinct publishers whose headlines are identical after normalisation
  (wire copies). The same publisher via two providers counts once. Nothing else is assumed.
- Output is candidate evidence only. Effect classes map from the event type (delay, export
  restriction, policy), never price, and nothing is a confirmed operating status.
- ``confidence`` in gate rows is 0: no source states a confidence, and none is inferred.
- Permitted uses come from the provider registry; unreviewed rights grant none, so the gate
  excludes those rows with ``permission_denied``.
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
from functools import lru_cache
from pathlib import Path

from ..settings import PROJECT_ROOT
from .fetch import utc

SOURCES_FILE = PROJECT_ROOT / "config" / "news_sources_v1.json"
COUNTRIES_FILE = PROJECT_ROOT / "config" / "iso3166_alpha2.csv"
CANDIDATE_NOTE = ("Candidate evidence only: a headline can raise exposure for review. It never confirms an operating "
                  "status and never sets a price effect.")
CORROBORATION_RULE = "Distinct publishers with the same normalised headline; the same publisher counts once."


def load_sources(path: Path | None = None) -> dict:
    content = json.loads(Path(path or SOURCES_FILE).read_text(encoding="utf-8"))
    if not isinstance(content, dict) or not isinstance(content.get("providers"), dict) or \
            not isinstance(content.get("entities"), dict):
        raise ValueError("invalid news source registry")
    for provider in content["providers"].values():
        if not isinstance(provider.get("permitted_uses"), list):
            raise ValueError("permitted_uses must be a list")
    return content


@lru_cache(maxsize=1)
def _countries() -> tuple[tuple[str, str], ...]:
    rows = []
    with Path(COUNTRIES_FILE).open(encoding="utf-8") as stream:
        for row in csv.DictReader(stream):
            names = [row["name"], *[a for a in (row.get("aliases") or "").split("|") if a]]
            rows += [(name.strip(), row["code"]) for name in names if name.strip()]
    return tuple(rows)


def _matches(text: str, phrase: str, case_sensitive: bool) -> bool:
    pattern = r"(?<!\w)" + re.escape(phrase) + r"(?!\w)"
    return re.search(pattern, text, 0 if case_sensitive else re.IGNORECASE) is not None


def normalised_title(title: str) -> str:
    return re.sub(r"\s+", " ", title).strip().casefold()


def tag_entities(title: str, rules: dict) -> dict:
    materials = sorted(m for m, words in rules["materials"].items() if any(_matches(title, w, False) for w in words))
    ports = sorted(code for code, words in rules["ports"].items() if any(_matches(title, w, True) for w in words))
    routes = sorted(rid for rid, words in rules["routes"].items() if any(_matches(title, w, True) for w in words))
    countries = sorted({code for name, code in _countries() if _matches(title, name, True)})
    events = sorted(t for t, words in rules["event_types"].items() if any(_matches(title, w, False) for w in words))
    status = "resolved" if len(events) == 1 else ("ambiguous" if events else "unresolved")
    unresolved = [name for name, found in (("material", materials), ("country", countries),
                                           ("port_or_route", ports or routes), ("event_type", status == "resolved"))
                  if not found]
    return {"materials": materials, "countries": countries, "ports": ports, "routes": routes,
            "event_type": events[0] if status == "resolved" else None, "event_type_status": status,
            "event_type_candidates": events, "unresolved": unresolved, "rules_version": None}


def _sha(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def build_snapshots(fetches: list[dict], *, as_of: str, sources: dict | None = None) -> dict:
    sources = sources or load_sources()
    cutoff = utc(as_of)
    rules = sources["entities"]
    ordered = sorted(fetches, key=lambda f: (f["retrieved_at"], f["provider"], f["material"],
                                             json.dumps(f["items"], sort_keys=True)))
    rejected, states, items = [], [], []
    for f in ordered:
        states.append({k: f[k] for k in ("provider", "material", "state", "reason", "retrieved_at", "request")})
        if f["retrieved_at"] > cutoff:
            rejected.append({"provider": f["provider"], "material": f["material"], "retrieved_at": f["retrieved_at"],
                             "reason": "retrieved_after_as_of", "items": len(f["items"])})
            continue
        items += [{**item, "synthetic": f["synthetic"], "query_material": f["material"]} for item in f["items"]]

    # Story keys: URL-identified items first, then RSS items adopt a matching publisher+headline.
    by_headline = {}
    for item in items:
        if item["url"]:
            item["story_key"] = f"url:{item['url']}"
            by_headline.setdefault((item["source_id"], normalised_title(item["title"])), set()).add(item["story_key"])
    for item in items:
        if not item["url"]:
            keys = sorted(by_headline.get((item["source_id"], normalised_title(item["title"])), ()))
            item["story_key"] = keys[0] if len(keys) == 1 else f"guid:{item['provider']}:{item['guid']}"

    groups: dict[str, list[dict]] = {}
    for item in items:
        story_id = "story-" + hashlib.sha256(item["story_key"].encode()).hexdigest()[:16]
        content = _sha({"source_id": item["source_id"], "story_id": story_id, "title": item["title"]})
        item.update(story_id=story_id, content_sha256=content, revision_id=f"rev-{content[:16]}")
        groups.setdefault(f"{item['source_id']}:{story_id}:{item['revision_id']}", []).append(item)

    snapshots, duplicates = [], []
    for snapshot_id in sorted(groups):
        members = sorted(groups[snapshot_id], key=lambda i: (i["retrieved_at"], i["provider"]))
        first = members[0]
        seen_per_provider = {}
        for m in members:
            seen_per_provider.setdefault(m["provider"], []).append(m["retrieved_at"])
        for provider, times in sorted(seen_per_provider.items()):
            if len(times) > 1:
                duplicates.append({"snapshot_id": snapshot_id, "provider": provider, "kept_retrieved_at": times[0],
                                   "dropped_retrieved_at": times[1:]})
        published = sorted(m["published_at"] for m in members if m["published_at"])
        provider_seen = sorted(m["provider_first_seen_at"] for m in members if m["provider_first_seen_at"])
        providers = sorted(seen_per_provider)
        entities = {**tag_entities(first["title"], rules), "rules_version": sources["version"]}
        flags = ["embedded_instruction"] if any(p in first["title"].casefold() for p in rules["instruction_patterns"]) \
            else []
        snapshots.append({
            "snapshot_id": snapshot_id, "providers": providers,
            "source": {"source_id": first["source_id"], "publisher": next((m["publisher"] for m in members
                                                                            if m["publisher"]), None),
                       "kind": "news"},
            "story_id": first["story_id"], "revision_id": first["revision_id"],
            "url": next((m["url"] for m in members if m["url"]), None), "title": first["title"],
            "language": first["language"], "first_seen_at": first["retrieved_at"],
            "retrieved_at": sorted({m["retrieved_at"] for m in members}),
            "provider_first_seen_at": provider_seen[0] if provider_seen else None,
            "published_at": published[0] if published else None,
            "published_at_status": "stated" if published else "not_stated",
            "content_sha256": first["content_sha256"],
            "rights": [{"provider": p, "rights_status": sources["providers"][p]["rights_status"],
                        "permitted_uses": list(sources["providers"][p]["permitted_uses"]),
                        "attribution": sources["providers"][p]["attribution"],
                        "terms_note": sources["providers"][p]["terms_note"]} for p in providers],
            "synthetic": any(m["synthetic"] for m in members), "sample": False,
            "query_materials": sorted({m["query_material"] for m in members}),
            "entities": entities, "review_flags": flags, "evidence_status": "candidate", "note": CANDIDATE_NOTE,
        })

    by_title: dict[str, list[dict]] = {}
    for s in snapshots:
        by_title.setdefault(normalised_title(s["title"]), []).append(s)
    for s in snapshots:
        others = [o for o in by_title[normalised_title(s["title"])]
                  if o["source"]["source_id"] != s["source"]["source_id"]]
        known = max([s["first_seen_at"], *([min(o["first_seen_at"] for o in others)] if others else [])])
        s["corroboration"] = {"status": "corroborated" if others else "uncorroborated",
                              "distinct_sources": 1 + len({o["source"]["source_id"] for o in others}),
                              "evidence_refs": sorted(o["snapshot_id"] for o in others), "known_at": known,
                              "rule": CORROBORATION_RULE}
        s["gate_row"], s["gate_row_reason"] = _gate_row(s, rules)

    duplicates.sort(key=lambda d: (d["snapshot_id"], d["provider"]))
    return {"as_of": cutoff, "registry_version": sources["version"], "fetches": states, "rejected": rejected,
            "duplicates": duplicates, "snapshots": snapshots, "note": CANDIDATE_NOTE}


def _gate_row(s: dict, rules: dict) -> tuple[dict | None, str | None]:
    if s["review_flags"]:
        return None, "needs_review_instruction_like_text"
    if not s["source"]["source_id"]:
        return None, "source_unresolved"
    if s["entities"]["event_type_status"] != "resolved":
        return None, f"event_type_{s['entities']['event_type_status']}"
    uses = set.intersection(*(set(r["permitted_uses"]) for r in s["rights"]))
    seen = s["first_seen_at"]
    return {
        "source_id": s["source"]["source_id"], "source_url": s["url"], "story_id": s["story_id"],
        "revision_id": s["revision_id"], "content_hash": s["content_sha256"], "source_kind": "news",
        "event_type": s["entities"]["event_type"],
        "effect_class": rules["effect_class_for_event_type"][s["entities"]["event_type"]],
        "confidence": 0, "synthetic": s["synthetic"], "sample": s["sample"],
        "event_at": None, "effective_at": None, "published_at": s["published_at"], "edited_at": None,
        "first_seen_at": seen,
        "first_seen_witness": {"source_id": s["source"]["source_id"], "revision_id": s["revision_id"],
                               "content_hash": s["content_sha256"]},
        "archive_witnesses": [],
        "permissions": {"known_at": seen, "uses": sorted(uses)},
        "corroboration": {"known_at": s["corroboration"]["known_at"], "status": s["corroboration"]["status"],
                          "evidence_refs": s["corroboration"]["evidence_refs"]},
        "scope": {"known_at": seen, "materials": s["entities"]["materials"], "countries": s["entities"]["countries"],
                  "hs": [], "routes": s["entities"]["routes"], "ports": s["entities"]["ports"], "global": []},
    }, None


def gate_rows(result: dict) -> list[dict]:
    return [s["gate_row"] for s in result["snapshots"] if s["gate_row"] is not None]
