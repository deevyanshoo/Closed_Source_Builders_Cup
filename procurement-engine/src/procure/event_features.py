"""Pure point-in-time projection of normalized immutable event assertions.

project_events(records, *, as_of, scope, purpose, allow_demo=False) accepts
one mapping per complete source/story snapshot. Each row needs source_id or
source_url, story_id, revision_id, 64-character content_hash, source_kind
(news/official/social/other), event_type, effect_class, confidence in [0,1],
synthetic and sample booleans, and explicit event_at/effective_at/published_at/
edited_at/first_seen_at keys. Those dates can be None; known dates are aware
instants. first_seen_witness binds the chosen source identity, revision_id,
and hash; archive_witnesses additionally need at and evidence_ref. Complete
permissions {known_at, uses}, corroboration {known_at, status, evidence_refs},
and scope {known_at, materials, countries, hs, routes, ports, global} snapshots
are required. HS entries and query scope use {code, revision}; global contains
dimension names and only fills empty dimensions. purpose is training/planning.
Row confidence is source-attributed and belongs to that row's dated
corroboration snapshot; updating it requires a new complete dated snapshot.

Projection has deterministic features with per-source provenance and reason-
coded exclusions. No source text is verified and no numerical effect or merged
confidence is inferred. The earliest valid exact-content witness determines
availability; publication/edit or metadata dates cannot backdate it. An exact
content witness earlier than a known publication/edit is contradictory and
quarantined. Event/effective dates never gate availability.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
import re

DIMENSIONS = {"material": "materials", "country": "countries", "route": "routes", "port": "ports", "hs": "hs"}
PURPOSES = {"training", "planning"}
EFFECTS = {"price", "fx", "landed_cost", "delay", "policy", "export_restriction"}
SOURCE_KINDS = {"news", "official", "social", "other"}
HASH = re.compile(r"[0-9a-fA-F]{64}\Z")


@dataclass(frozen=True)
class Projection:
    features: tuple[dict, ...]
    exclusions: tuple[dict, ...]


class Invalid(Exception):
    def __init__(self, reason):
        self.reason = reason


def instant(value, optional=False):
    if value is None and optional:
        return None
    try:
        if isinstance(value, datetime):
            result = value
        elif isinstance(value, str) and "T" in value:
            result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        else:
            raise ValueError
        if result.tzinfo is None or result.utcoffset() is None:
            raise ValueError
        return result.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError) as exc:
        raise Invalid("invalid_timestamp") from exc


def nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def strings(value):
    if not isinstance(value, (tuple, list)) or any(not nonempty(x) for x in value):
        raise Invalid("invalid_record")
    return tuple(sorted(set(value)))


def metadata(value, kind):
    if not isinstance(value, Mapping):
        raise Invalid("metadata_unavailable")
    if value.get("known_at") is None:
        raise Invalid("metadata_unavailable")
    known = instant(value.get("known_at"))
    if kind == "permissions":
        uses = strings(value.get("uses"))
        if set(uses) - PURPOSES:
            raise Invalid("invalid_record")
        return known, uses
    status = value.get("status")
    if not isinstance(status, str) or status not in {"corroborated", "uncorroborated", "retracted"}:
        raise Invalid("invalid_record")
    refs = strings(value.get("evidence_refs"))
    if status == "corroborated" and not refs:
        raise Invalid("corroboration_unavailable")
    return known, {"status": status, "evidence_refs": refs}


def scope_snapshot(value):
    if not isinstance(value, Mapping):
        raise Invalid("metadata_unavailable")
    if value.get("known_at") is None:
        raise Invalid("metadata_unavailable")
    known = instant(value.get("known_at"))
    dimensions = {name: strings(value.get(key)) for name, key in DIMENSIONS.items() if name != "hs"}
    hs = value.get("hs")
    if not isinstance(hs, (tuple, list)):
        raise Invalid("invalid_record")
    pairs = []
    for entry in hs:
        if not isinstance(entry, Mapping) or not nonempty(entry.get("code")) or not nonempty(entry.get("revision")):
            raise Invalid("invalid_record")
        pairs.append((entry["code"], entry["revision"]))
    dimensions["hs"] = tuple(sorted(set(pairs)))
    dimensions["global"] = strings(value.get("global"))
    if set(dimensions["global"]) - DIMENSIONS.keys():
        raise Invalid("invalid_record")
    return known, dimensions


def witness(entry, row, archive=False):
    identity_key = "source_id" if nonempty(row.get("source_id")) else "source_url"
    if not isinstance(entry, Mapping) or any(entry.get(k) != row[k] for k in (identity_key, "revision_id", "content_hash")):
        raise Invalid("witness_mismatch")
    if archive:
        if not nonempty(entry.get("evidence_ref")):
            raise Invalid("witness_mismatch")
        return instant(entry.get("at"))


def normalize(row):
    if not isinstance(row, Mapping):
        raise Invalid("invalid_record")
    if any(row.get(key) is not None and not nonempty(row.get(key))
           for key in ("source_id", "source_url")):
        raise Invalid("invalid_record")
    if not nonempty(row.get("source_id")) and not nonempty(row.get("source_url")):
        raise Invalid("invalid_record")
    for key in ("story_id", "revision_id", "source_kind", "event_type", "effect_class"):
        if not nonempty(row.get(key)):
            raise Invalid("invalid_record")
    content_hash = row.get("content_hash")
    if (not isinstance(content_hash, str) or not HASH.fullmatch(content_hash)
            or row["effect_class"] not in EFFECTS or row["source_kind"] not in SOURCE_KINDS):
        raise Invalid("invalid_record")
    confidence = row.get("confidence")
    if (isinstance(confidence, bool) or not isinstance(confidence, (int, float))
            or not 0 <= confidence <= 1
            or (isinstance(confidence, float) and not math.isfinite(confidence))):
        raise Invalid("invalid_record")
    if not isinstance(row.get("synthetic"), bool) or not isinstance(row.get("sample"), bool):
        raise Invalid("invalid_record")
    date_keys = ("event_at", "effective_at", "published_at", "edited_at", "first_seen_at")
    if any(k not in row for k in date_keys):
        raise Invalid("invalid_record")
    dates = {k: instant(row[k], optional=True) for k in date_keys}
    if dates["published_at"] and dates["edited_at"] and dates["edited_at"] < dates["published_at"]:
        raise Invalid("contradictory_chronology")
    seen = []
    if dates["first_seen_at"] is not None:
        witness(row.get("first_seen_witness"), row)
        seen.append(dates["first_seen_at"])
    archives = row.get("archive_witnesses")
    if not isinstance(archives, (tuple, list)):
        raise Invalid("invalid_record")
    normalized_archives = []
    for entry in archives:
        archived_at = witness(entry, row, archive=True)
        seen.append(archived_at)
        normalized_archives.append({
            "at": archived_at.isoformat(), "evidence_ref": entry["evidence_ref"],
            "revision_id": entry["revision_id"], "content_hash": entry["content_hash"],
            "source_id" if nonempty(row.get("source_id")) else "source_url":
                row["source_id"] if nonempty(row.get("source_id")) else row["source_url"],
        })
    if not seen:
        raise Invalid("unknown_availability")
    available = min(seen)
    if any(dates[k] is not None and available < dates[k] for k in ("published_at", "edited_at")):
        raise Invalid("contradictory_chronology")
    content_known = max([available] + [dates[k] for k in ("published_at", "edited_at") if dates[k] is not None])
    permissions_at, uses = metadata(row.get("permissions"), "permissions")
    corroboration_at, corroboration = metadata(row.get("corroboration"), "corroboration")
    scope_at, scoped = scope_snapshot(row.get("scope"))
    known = max(content_known, permissions_at, corroboration_at, scope_at)
    provenance = {k: row[k] for k in ("revision_id", "content_hash", "source_kind", "event_type", "confidence")}
    identity_kind = "source_id" if nonempty(row.get("source_id")) else "source_url"
    provenance["source_id"] = row[identity_kind]
    provenance["source_identity_kind"] = identity_kind
    provenance["source_url"] = row.get("source_url")
    provenance.update({k: v.isoformat() if v else None for k, v in dates.items()})
    provenance.update(content_available_at=available.isoformat(), known_at=known.isoformat(),
                      corroboration=corroboration["status"], evidence_refs=corroboration["evidence_refs"])
    identity_key = identity_kind
    provenance["first_seen_witness"] = (
        {identity_key: row[identity_key], "revision_id": row["revision_id"],
         "content_hash": row["content_hash"]} if dates["first_seen_at"] is not None else None
    )
    provenance["archive_witnesses"] = tuple(sorted(normalized_archives, key=lambda x: (x["at"], x["evidence_ref"])))
    provenance["permissions"] = {"known_at": permissions_at.isoformat(), "uses": uses}
    provenance["corroboration_snapshot"] = {
        "known_at": corroboration_at.isoformat(), "status": corroboration["status"],
        "evidence_refs": corroboration["evidence_refs"],
    }
    provenance["scope_known_at"] = scope_at.isoformat()
    return {"source_id": row[identity_kind], "source_identity": (identity_kind, row[identity_kind]),
            "story_id": row["story_id"], "known": known,
            "effect_class": row["effect_class"], "scope": scoped, "uses": uses,
            "corroboration": corroboration["status"], "synthetic": row["synthetic"],
            "sample": row["sample"], "provenance": provenance}


def query_scope(value):
    if not isinstance(value, Mapping) or not value or set(value) - DIMENSIONS.keys():
        raise ValueError("scope must use known dimensions")
    result = {}
    for dimension, item in value.items():
        if dimension == "hs":
            if not isinstance(item, Mapping) or not nonempty(item.get("code")) or not nonempty(item.get("revision")):
                raise ValueError("HS scope requires code and revision")
            result[dimension] = (item["code"], item["revision"])
        elif not nonempty(item):
            raise ValueError("scope values must be nonempty strings")
        else:
            result[dimension] = item
    return result


def matches(scoped, query):
    for dimension, item in query.items():
        choices = scoped[dimension]
        if choices and item not in choices:
            return False
        if not choices and dimension not in scoped["global"]:
            return False
    return True


def diagnostic(row, reason):
    if not isinstance(row, Mapping):
        row = {}
    source_id = row.get("source_id") if nonempty(row.get("source_id")) else None
    source_url = row.get("source_url") if nonempty(row.get("source_url")) else None
    kind = "source_id" if source_id is not None else ("source_url" if source_url is not None else None)
    return {"source_id": source_id, "source_url": source_url,
            "source_identity_kind": kind,
            "story_id": row.get("story_id") if nonempty(row.get("story_id")) else None,
            "reason": reason}


def selected_diagnostic(item, reason):
    provenance = item["provenance"]
    return diagnostic({
        "source_id": provenance["source_id"] if provenance["source_identity_kind"] == "source_id" else None,
        "source_url": provenance["source_url"], "story_id": item["story_id"],
    }, reason)


def project_events(records, *, as_of, scope, purpose, allow_demo=False) -> Projection:
    """Select latest known source snapshots, then gate use and canonical dedup."""
    try:
        origin = instant(as_of)
    except Invalid as exc:
        raise ValueError("as_of must be a timezone-aware instant") from exc
    if purpose not in PURPOSES:
        raise ValueError("purpose must be training or planning")
    query = query_scope(scope)
    if not isinstance(allow_demo, bool):
        raise ValueError("allow_demo must be bool")
    exclusions, by_source = [], {}
    for row in records:
        try:
            item = normalize(row)
        except Invalid as exc:
            exclusions.append(diagnostic(row, exc.reason))
            continue
        if item["known"] > origin:
            exclusions.append(diagnostic(row, "after_origin"))
            continue
        by_source.setdefault((item["source_identity"], item["story_id"]), []).append(item)
    selected = []
    for key, entries in by_source.items():
        newest = max(x["known"] for x in entries)
        tied = [x for x in entries if x["known"] == newest]
        signatures = {json.dumps({k: v for k, v in x.items() if k != "known"}, sort_keys=True, default=str) for x in tied}
        if len(signatures) != 1:
            identity_kind, identity_value = key[0]
            exclusions.append(diagnostic({
                "source_id": identity_value if identity_kind == "source_id" else None,
                "source_url": identity_value if identity_kind == "source_url" else None,
                "story_id": key[1],
            }, "ambiguous_revision"))
            continue
        item = tied[0]
        if purpose not in item["uses"]:
            reason = "permission_denied"
        elif item["corroboration"] == "retracted":
            reason = "retracted"
        elif purpose == "training" and item["provenance"]["source_kind"] == "social" and item["corroboration"] != "corroborated":
            reason = "social_uncorroborated"
        elif (item["synthetic"] or item["sample"]) and not allow_demo:
            reason = "demo_excluded"
        elif not matches(item["scope"], query):
            reason = "scope_mismatch"
        else:
            reason = None
        if reason:
            exclusions.append(selected_diagnostic(item, reason))
        else:
            selected.append(item)
    features_by_key = {}
    for item in selected:
        scoped = item["scope"]
        identity = (item["story_id"], item["effect_class"], json.dumps(scoped, sort_keys=True), item["synthetic"], item["sample"])
        feature = features_by_key.setdefault(identity, {"story_id": item["story_id"], "effect_class": item["effect_class"],
                                                        "scope": scoped, "synthetic": item["synthetic"],
                                                        "sample": item["sample"], "provenance": []})
        feature["provenance"].append(item["provenance"])
    features = []
    for key in sorted(features_by_key):
        feature = features_by_key[key]
        feature["provenance"] = tuple(sorted(
            feature["provenance"],
            key=lambda x: (x["source_identity_kind"], x["source_id"],
                           x["revision_id"], x["content_hash"]),
        ))
        features.append(feature)
    exclusions.sort(key=lambda x: (
        str(x["source_identity_kind"]), str(x["source_id"]), str(x["source_url"]),
        str(x["story_id"]), x["reason"],
    ))
    return Projection(tuple(features), tuple(exclusions))
