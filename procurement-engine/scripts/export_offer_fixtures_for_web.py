"""Export synthetic offer fixtures for the web app's local adapter.

Runs the real Python pipeline (read, rules extraction, evidence check, lifecycle view) on every synthetic quote in
tests/fixtures/offers and writes apps/web/src/offers-fixtures.json. The web page uses this file only until the
gateway serves /v2/offers routes; it is labelled as a local synthetic adapter in the UI.

Run from procurement-engine/:  .venv/bin/python scripts/export_offer_fixtures_for_web.py
"""
from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT.parent / "apps" / "web" / "src" / "offers-fixtures.json"

FIXTURES = ROOT / "tests" / "fixtures" / "offers"


# Scenario inputs computed here by procure.offers.scenario, so the web adapter's mirror can be checked against them.
_EVENTS = {"shipment": "2026-11-05", "bill_of_lading": "2026-11-06", "documents": "2026-11-12",
           "invoice": "2026-11-06", "delivery": "2026-12-10", "arrival": "2026-12-08"}
SCENARIO_EXAMPLES = [
    {"offer_id": "fixture-q01", "decision_date": "2026-10-10", "dates": _EVENTS, "rate_percent": "8",
     "alternative_offer_id": "fixture-q03"},
    {"offer_id": "fixture-q04", "decision_date": "2026-10-26", "dates": _EVENTS, "rate_percent": "7.25",
     "alternative_offer_id": "fixture-q02"},
    {"offer_id": "fixture-q10", "decision_date": "2026-10-10", "dates": {**_EVENTS, "documents": None},
     "rate_percent": None, "alternative_offer_id": "fixture-q09"},
    {"offer_id": "fixture-q17", "decision_date": "2026-10-20", "dates": _EVENTS, "rate_percent": "8",
     "alternative_offer_id": None},
]


def _slim(field: dict) -> dict:
    keep = ("name", "kind", "status", "value", "critical", "source", "reasons")
    out = {k: field.get(k) for k in keep}
    out["evidence"] = [{"locator": e["locator"], "quote": e["quote"], "text_sha256": e["text_sha256"]}
                       for e in field.get("evidence") or []]
    out["candidates"] = [{"value": c["value"], "verified": c["verified"], "problems": c["problems"],
                          "locators": [e["locator"] for e in c.get("evidence") or []]}
                         for c in field.get("candidates") or [] if field["status"] in ("conflict", "unverified")]
    return out


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="offer_export_"))
    os.environ.update({"PROCURE_DATA_DIR": str(tmp), "PROCURE_DB_PATH": str(tmp / "export.duckdb"), "OFFLINE": "1"})
    os.environ.pop("GEMINI_API_KEY", None)
    sys.path.insert(0, str(ROOT / "src"))
    from procure.settings import reset_caches

    reset_caches()
    os.environ.pop("GEMINI_API_KEY", None)  # settings import loads .env; rules only for the export
    reset_caches()
    from procure.offers import store
    from procure.offers.context import OfferContext
    global CONTEXT, SYSTEM_CONTEXT
    CONTEXT = OfferContext("synthetic-reviewer", "synthetic-workspace", "editor")
    SYSTEM_CONTEXT = OfferContext("system", "synthetic-workspace", "system")
    from procure.settings import materials_config

    manifest = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
    as_of = dt.date.fromisoformat(manifest["as_of"])
    offers = []
    for q in manifest["quotes"]:
        path = FIXTURES / q["file"]
        created = store.create_from_upload(path.read_bytes(), path.name, mode="rules", as_of=as_of,
                                           allow_duplicate=True, context=CONTEXT)
        offer = created["offer"]
        segments = store.get_extraction(created["extraction_id"], context=CONTEXT)["segments"]
        offers.append({
            "fixture_id": q["id"], "split": q["split"], "offer_id": f"fixture-{q['id'].lower()}",
            "version": offer["version"], "status": offer["status"], "synthetic": True,
            "document": {k: offer["document"][k] for k in ("document_id", "filename", "media_type", "byte_size")},
            "extractor": offer["extractor"], "as_of": offer["as_of"],
            "fields": {k: _slim(f) for k, f in offer["fields"].items()},
            "items": [{k: _slim(f) for k, f in item.items()} for item in offer["items"]],
            "review_flags": offer["review_flags"], "normalized": offer["normalized"],
            "blocking_reasons": offer["blocking_reasons"],
            "segments": [{k: s[k] for k in ("locator", "text", "flags")} for s in segments],
        })
    from procure.offers.scenario import offer_scenario

    examples = []
    for example in SCENARIO_EXAMPLES:
        target = next(o for o in offers if o["offer_id"] == example["offer_id"])
        examples.append({"inputs": example, "result": offer_scenario(
            target, offers, as_of=as_of, decision_date=dt.date.fromisoformat(example["decision_date"]),
            dates=example["dates"], rate_percent=example["rate_percent"],
            alternative_offer_id=example["alternative_offer_id"])})
    revision = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True,
                              text=True).stdout.strip()
    data = {"synthetic": True, "source": "local_fixture_adapter", "generated_from_revision": revision,
            "as_of": manifest["as_of"], "extractor": "rules",
            "notice": "Synthetic quotes processed by the Python rules pipeline. Not live data and not from the gateway.",
            "materials": list(materials_config()["materials"]), "offers": offers,
            "scenario_examples": examples}
    OUT.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"Wrote {len(offers)} offers to {OUT} ({OUT.stat().st_size // 1024} KiB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
