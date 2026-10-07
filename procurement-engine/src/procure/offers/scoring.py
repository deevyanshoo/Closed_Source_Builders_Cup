"""Score an offer extraction against the synthetic manifest answer key.

Per field the result is correct or one of:
  wrong_value      a value was extracted but differs from the key
  not_extracted    the key has a value; the extraction left it missing, unverified or conflict
  invented_value   the key says missing; the extraction produced a value
  missed_conflict  the key says the document states two values; the extraction picked one
Amounts compare as numbers (2615 equals 2615.00) and text compares case-insensitively.
"""
from __future__ import annotations

from collections import defaultdict

from .evidence import canonical
from .normalize import squash

OFFER_FIELDS = ("supplier_name", "origin_country", "incoterm", "settlement_currency", "payment_terms", "valid_until",
                "lead_time_days", "min_order", "capacity", "shipments")
ITEM_FIELDS = ("material", "description", "grade", "quantity", "pricing")


def _same(name: str, got, want, extraction) -> bool:
    if name == "origin_country":
        return extraction.normalized.get("origin_country_code") == want
    if isinstance(want, str):
        # "[SYNTHETIC]" marks fixture suppliers; it is a test label, not part of the name
        return isinstance(got, str) and squash(got.replace("[SYNTHETIC]", "")) == squash(want.replace("[SYNTHETIC]", ""))
    if name.endswith("pricing") and want.get("type") == "index_linked":
        required = {"type", "index", "averaging_period", "premium", "per_unit"}
        expected_keys = required | ({"fx_basis"} if "fx_basis" in want else set())
        text_keys = ("index", "averaging_period") + (("fx_basis",) if "fx_basis" in want else ())
        return (isinstance(got, dict) and set(want) == set(got) == expected_keys
                and got["type"] == "index_linked" and got["per_unit"] == want["per_unit"]
                and canonical(got["premium"]) == canonical(want["premium"])
                and all(isinstance(got[key], str) and isinstance(want[key], str)
                        and squash(got[key]) == squash(want[key])
                        for key in text_keys))
    return canonical(got) == canonical(want)


def _field(name: str, got, want: dict, extraction) -> dict:
    expected, status = want["status"], (got.status if got else "absent")
    has_value = status in ("extracted", "corrected")
    if expected == "present":
        if has_value:
            correct = _same(name, got.value, want["value"], extraction)
            error = None if correct else "wrong_value"
        else:
            correct, error = False, "not_extracted"
    elif expected == "missing":
        correct = status in ("missing", "absent")
        error = None if correct else ("invented_value" if has_value else "wrong_status")
    else:  # conflict
        correct = status == "conflict"
        error = None if correct else ("missed_conflict" if has_value else "wrong_status")
    return {"field": name, "expected_status": expected, "got_status": status, "correct": correct, "error": error}


def score_quote(extraction, entry: dict) -> dict:
    expected = entry["expected"]
    results = [_field(n, extraction.fields.get(n), expected[n], extraction) for n in OFFER_FIELDS]
    for i, want_item in enumerate(expected["items"]):
        got_item = extraction.items[i] if i < len(extraction.items) else {}
        for key in ITEM_FIELDS:
            want = ({"status": "present", "value": want_item["material"]} if key == "material" else want_item[key])
            results.append(_field(f"items[{i}].{key}", got_item.get(key), want, extraction))
    extra_items = max(0, len(extraction.items) - len(expected["items"]))

    flags = {f["flag"] for f in extraction.review_flags}
    expired_expected = "expired" in entry["cases"]
    score_flags = {
        "missing": {"expected": sum(r["expected_status"] == "missing" for r in results),
                    "flagged": sum(r["expected_status"] == "missing" and r["correct"] for r in results)},
        "conflict": {"expected": sum(r["expected_status"] == "conflict" for r in results),
                     "flagged": sum(r["expected_status"] == "conflict" and r["correct"] for r in results)},
        "expired": {"expected": int(expired_expected), "flagged": int(expired_expected and "expired" in flags),
                    "false_positive": int(not expired_expected and "expired" in flags)},
        "embedded_instruction": {"expected": int("embedded_instruction" in entry["cases"]),
                                 "flagged": int("embedded_instruction" in entry["cases"]
                                                and "embedded_instruction" in flags)},
    }
    correct = sum(r["correct"] for r in results)
    quote_correct = correct == len(results) and len(extraction.items) == len(expected["items"])
    return {"quote": entry["id"], "split": entry["split"], "format": entry["format"], "extractor": extraction.extractor,
            "fields": results, "correct": correct, "total": len(results), "extra_items": extra_items,
            "accuracy": round(correct / len(results), 4), "quote_correct": quote_correct, "flags": score_flags}


def summarize(scores: list[dict]) -> dict:
    per_field: dict[str, dict] = defaultdict(lambda: {"correct": 0, "total": 0})
    errors: dict[str, int] = defaultdict(int)
    for s in scores:
        for r in s["fields"]:
            key = r["field"].split(".", 1)[-1]  # items[0].pricing -> pricing
            per_field[key]["correct"] += r["correct"]
            per_field[key]["total"] += 1
            if r["error"]:
                errors[r["error"]] += 1
    for v in per_field.values():
        v["accuracy"] = round(v["correct"] / v["total"], 4)
    flags: dict[str, dict] = {}
    for s in scores:
        for name, counts in s["flags"].items():
            agg = flags.setdefault(name, defaultdict(int))
            for k, v in counts.items():
                agg[k] += v
    correct, total = sum(s["correct"] for s in scores), sum(s["total"] for s in scores)
    quotes_correct = sum(s["quote_correct"] for s in scores)
    return {"quotes": len(scores), "correct": correct, "total": total,
            "accuracy": round(correct / total, 4) if total else None, "per_field": dict(per_field),
            "quotes_correct": quotes_correct,
            "quote_accuracy": round(quotes_correct / len(scores), 4) if scores else None,
            "errors": dict(errors), "extra_items": sum(s["extra_items"] for s in scores),
            "flags": {k: dict(v) for k, v in flags.items()}}
