"""Offline rule-based text proposals for the typed voice workspace (#96). No model, no network.

A small fixed grammar turns an English request into one ``compare_receipt_recovery`` call over the host's projection
(receipt ids and arrivals, order ids, period ids). Every clause must match a rule and every id must exist; anything
else is "not understood" with a reason, never a guess. The gateway still validates the call, and the browser still
asks for confirmation before any comparison runs.

Clauses are separated by ``;``, ``,`` or ``and``. Supported clauses (case-insensitive):

- ``receipt r-C arrives 2 hours late`` (also ``is``/``comes``; minutes, hours or days)
- ``delay r-C to 2026-10-10 10:00`` or ``receipt r-C arrives at 2026-10-10T10:00:00Z`` (UTC)
- ``move O-A to P2``, ``O-A in P2``, ``keep O-U in P1`` (every order needs a period: the tool takes a complete
  assignment, and the projection does not carry the baseline, so it is never filled in)

Run as a child process: ``python -m procure.agents.offline_text_proposal`` reads ``{"utterance", "projection"}`` on
stdin and writes only the canonical call JSON on stdout (exit 0). Anything else exits non-zero with no output.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import re
import sys

TOOL = "compare_receipt_recovery"
_MAX_UTTERANCE = 4096
_MAX_INPUT = 64 << 10
_MAX_ROWS = 64
_MAX_SHIFT = timedelta(days=30)
_ID = r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}"
_STAMP = r"(?P<date>\d{4}-\d{2}-\d{2})[ t](?P<time>\d{2}:\d{2})(?::(?P<sec>\d{2}))?\s*(?:z|utc)?"
_RELATIVE = re.compile(rf"^(?:receipt\s+)?(?P<id>{_ID})\s+(?:arrives|is|comes)\s+(?P<n>\d{{1,5}})\s*"
                       r"(?P<unit>minutes?|mins?|hours?|hrs?|days?)\s+late$", re.I)
_ABSOLUTE = re.compile(rf"^(?:(?:delay|move|set)\s+(?:receipt\s+)?(?P<id>{_ID})\s+(?:to|until)|"
                       rf"(?:receipt\s+)?(?P<id2>{_ID})\s+arrives\s+at)\s+{_STAMP}$", re.I)
_ASSIGN = re.compile(rf"^(?:(?:move|put|assign|run|keep|schedule)\s+)?(?:order\s+)?(?P<id>{_ID})\s+"
                     rf"(?:to|in|on|into)\s+(?:period\s+)?(?P<period>{_ID})$", re.I)
_NOTHING = re.compile(r"^(?:no changes?|nothing changes|no change to anything)$", re.I)
_UNITS = {"m": timedelta(minutes=1), "h": timedelta(hours=1), "d": timedelta(days=1)}
_MESSAGES = {
    "nothing_to_propose": "Nothing to propose: a proposal needs at least one receipt arrival change.",
    "no_arrival_change": "No receipt arrival change was understood, so there is nothing to compare.",
    "missing_assignments": "Say which period each order runs in, for example 'O-A in P2'.",
    "unknown_receipt": "That receipt is not in this snapshot.",
    "unknown_order": "That order is not in this snapshot.",
    "unknown_period": "That period is not in this snapshot.",
    "conflicting_statements": "The request says two different things about the same receipt or order.",
    "out_of_range": "That date or delay is not valid or is out of range.",
    "not_understood": "Not understood.",
    "invalid_projection": "The snapshot projection is incomplete.",
}


def _refuse(reason: str, **extra) -> dict:
    return {"status": "not_understood", "reason": reason, **extra,
            "message": f"Offline rule-based proposal: {_MESSAGES[reason]} Use the typed comparison form."}


def _instant(text: object) -> datetime | None:
    if not isinstance(text, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", text):
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _ids(rows: object, key: str, extra: str | None = None) -> dict[str, dict] | None:
    if not isinstance(rows, list) or not 1 <= len(rows) <= _MAX_ROWS:
        return None
    out = {}
    for row in rows:
        keys = {key} | ({extra} if extra else set())
        if not isinstance(row, dict) or set(row) != keys or not isinstance(row[key], str) \
                or not re.fullmatch(_ID, row[key]) or row[key].lower() in out:
            return None
        if extra and _instant(row[extra]) is None:
            return None
        out[row[key].lower()] = row
    return out


def _format(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def propose(utterance: object, projection: object) -> dict:
    """``{"status": "proposed", "call": ...}`` or ``{"status": "not_understood", "reason", "message"}``."""
    if not isinstance(projection, dict):
        return _refuse("invalid_projection")
    receipts = _ids(projection.get("receipts"), "receipt_id", "arrival_at")
    orders = _ids(projection.get("orders"), "order_id")
    periods = _ids(projection.get("periods"), "period_id")
    if receipts is None or orders is None or periods is None:
        return _refuse("invalid_projection")
    if not isinstance(utterance, str) or not utterance.strip() or len(utterance.encode("utf-8")) > _MAX_UTTERANCE:
        return _refuse("not_understood")
    text = " ".join(utterance.split())
    if _NOTHING.match(text.strip(" .")):
        return _refuse("nothing_to_propose")
    clauses = [c.strip(" .") for c in re.split(r"[;,]|\band\b", text, flags=re.I) if c.strip(" .")]
    if not clauses or len(clauses) > 2 * _MAX_ROWS:
        return _refuse("not_understood")
    arrivals: dict[str, str] = {}
    assignments: dict[str, str] = {}
    for clause in clauses:
        if (match := _RELATIVE.match(clause)):
            receipt = receipts.get(match["id"].lower())
            if receipt is None:
                return _refuse("unknown_receipt")
            shift = int(match["n"]) * _UNITS[match["unit"][0].lower()]
            if shift <= timedelta(0) or shift > _MAX_SHIFT:
                return _refuse("out_of_range")
            new = _format(_instant(receipt["arrival_at"]) + shift)
        elif (match := _ABSOLUTE.match(clause)):
            receipt = receipts.get((match["id"] or match["id2"]).lower())
            if receipt is None:
                return _refuse("unknown_receipt")
            try:
                moment = datetime.strptime(f"{match['date']} {match['time']}:{match['sec'] or '00'}",
                                           "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            except ValueError:
                return _refuse("out_of_range")
            if abs(moment - _instant(receipt["arrival_at"])) > _MAX_SHIFT:
                return _refuse("out_of_range")
            new = _format(moment)
        elif (match := _ASSIGN.match(clause)):
            order = orders.get(match["id"].lower())
            if order is None:
                return _refuse("unknown_order")
            period = periods.get(match["period"].lower())
            if period is None:
                return _refuse("unknown_period")
            previous = assignments.setdefault(order["order_id"], period["period_id"])
            if previous != period["period_id"]:
                return _refuse("conflicting_statements")
            continue
        else:
            return _refuse("not_understood")
        if arrivals.setdefault(receipt["receipt_id"], new) != new:
            return _refuse("conflicting_statements")
    changes = {rid: at for rid, at in arrivals.items() if at != receipts[rid.lower()]["arrival_at"]}
    if not changes:
        return _refuse("no_arrival_change")
    missing = [row["order_id"] for row in projection["orders"] if row["order_id"] not in assignments]
    if missing:
        return _refuse("missing_assignments", missing=missing)
    return {"status": "proposed", "call": {"name": TOOL, "arguments": {
        "arrival_replacements": [{"receipt_id": row["receipt_id"], "expected_arrival_at": row["arrival_at"],
                                  "arrival_at": changes[row["receipt_id"]]}
                                 for row in projection["receipts"] if row["receipt_id"] in changes],
        "assignments": [{"order_id": row["order_id"], "period_id": assignments[row["order_id"]]}
                        for row in projection["orders"]]}}}


def main() -> int:
    try:
        raw = sys.stdin.buffer.read(_MAX_INPUT + 1)
        if len(raw) > _MAX_INPUT:
            return 3
        body = json.loads(raw.decode("utf-8"))
        if not isinstance(body, dict) or set(body) != {"utterance", "projection"}:
            return 3
        result = propose(body["utterance"], body["projection"])
    except Exception:  # noqa: BLE001 - fail closed with no output
        return 3
    if result["status"] != "proposed":
        return 2
    sys.stdout.write(json.dumps(result["call"], separators=(",", ":"), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
