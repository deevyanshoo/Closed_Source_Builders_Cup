"""Score offer extraction against the synthetic manifest answer key.

    .venv/bin/python scripts/score_offer_extraction.py --mode rules  --split heldout
    .venv/bin/python scripts/score_offer_extraction.py --mode gemini --split heldout   # real calls if a key is set

Writes evaluation/offers/score_<mode>_<split>_<UTC date>.json with per-field accuracy and how many missing,
conflict, expired and embedded-instruction cases were flagged correctly. A Gemini run that falls back to rules
for a quote (no key, offline, failed call) is counted under `extractors_used` and `calls`, never as Gemini.

Inputs are synthetic. The same author wrote the fixtures and the rules extractor, so even held-out scores are
likely optimistic for real supplier documents.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from procure.documents import read_document  # noqa: E402
from procure.offers import scoring  # noqa: E402
from procure.offers.extraction import extract_offer  # noqa: E402
from procure.settings import get_settings, reset_caches  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures" / "offers"


def _revision() -> str:
    try:
        head = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True,
                              check=True).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain", "--", "src", "scripts", "config", "tests"], cwd=ROOT,
                               capture_output=True, text=True, check=True).stdout.strip()
        return f"{head}+uncommitted" if dirty else head
    except Exception:  # noqa: BLE001
        return "unknown"


def run(mode: str = "rules", split: str = "heldout") -> dict:
    manifest = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
    as_of = dt.date.fromisoformat(manifest["as_of"])
    quotes = [q for q in manifest["quotes"] if split == "all" or q["split"] == split]
    scores, details = [], []
    for q in quotes:
        ex = extract_offer(read_document(FIXTURES / q["file"]), mode=mode, as_of=as_of)
        score = scoring.score_quote(ex, q)
        scores.append(score)
        details.append({"quote": q["id"], "extractor": ex.extractor,
                        "call": ex.extractor_detail.get("call", "none"),
                        "fallback_reason": ex.extractor_detail.get("fallback_reason"),
                        "latency_ms": ex.extractor_detail.get("latency_ms"), "accuracy": score["accuracy"],
                        "errors": [f for f in score["fields"] if not f["correct"]]})
    settings = get_settings()
    return {"kind": "offer_extraction_score", "mode": mode, "split": split,
            "run_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(), "revision": _revision(),
            "manifest_as_of": manifest["as_of"], "key_configured": bool(settings.gemini_api_key),
            "offline": settings.offline, "synthetic_inputs": True,
            "extractors_used": dict(Counter(d["extractor"] for d in details)),
            "calls": dict(Counter(d["call"] for d in details)),
            "summary": scoring.summarize(scores), "quotes": details,
            "caveat": "Synthetic documents written by the same author as the rules; scores are likely optimistic."}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--mode", choices=["rules", "gemini", "auto"], default="rules")
    parser.add_argument("--split", choices=["heldout", "train", "all"], default="heldout")
    args = parser.parse_args()
    reset_caches()
    report = run(args.mode, args.split)
    out = ROOT / "evaluation" / "offers" / f"score_{args.mode}_{args.split}_{report['run_at'][:10]}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    s = report["summary"]
    print(json.dumps({"mode": args.mode, "split": args.split, "accuracy": s["accuracy"], "correct": s["correct"],
                      "total": s["total"], "extractors_used": report["extractors_used"], "calls": report["calls"],
                      "flags": s["flags"], "errors": s["errors"]}, indent=2))
    print(f"Saved {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
