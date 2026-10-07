"""Score offer extraction against the synthetic manifest answer key.

    .venv/bin/python scripts/score_offer_extraction.py --mode rules  --split heldout
    .venv/bin/python scripts/score_offer_extraction.py --mode gemini --split heldout   # real calls if a key is set

Writes a unique evaluation/offers/score_<mode>_<split>_<UTC date>_<run id>.json with field accuracy, whole-quote
accuracy, and how many missing,
conflict, expired and embedded-instruction cases were flagged correctly. A Gemini run that falls back to rules
for a quote (no key, offline, failed call) is counted under `extractors_used` and `calls`, never as Gemini.

Inputs are synthetic. The same author wrote the fixtures and the rules extractor, so even held-out scores are
likely optimistic for real supplier documents.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import uuid
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
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True,
                              check=True).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain", "--", "src", "scripts", "config", "tests"], cwd=ROOT,
                               capture_output=True, text=True, check=True).stdout.strip()
        return f"{head}+uncommitted" if dirty else head
    except Exception:  # noqa: BLE001
        return "unknown"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _source_hashes() -> dict[str, str]:
    paths = [ROOT / "scripts" / "score_offer_extraction.py"]
    paths += sorted((ROOT / "src" / "procure" / "offers").glob("*.py"))
    paths += sorted((ROOT / "src" / "procure" / "documents").glob("*.py"))
    paths += sorted((ROOT / "config").glob("*.yaml"))
    paths += sorted((ROOT / "config").glob("*.csv"))
    return {str(path.relative_to(ROOT)): _sha256(path) for path in paths if path.is_file()}


def run(mode: str = "rules", split: str = "heldout") -> dict:
    manifest = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
    as_of = dt.date.fromisoformat(manifest["as_of"])
    quotes = [q for q in manifest["quotes"] if split == "all" or q["split"] == split]
    input_hashes = {q["id"]: _sha256(FIXTURES / q["file"]) for q in quotes}
    scores, details = [], []
    for q in quotes:
        ex = extract_offer(read_document(FIXTURES / q["file"]), mode=mode, as_of=as_of)
        score = scoring.score_quote(ex, q)
        scores.append(score)
        detail = {"quote": q["id"], "extractor": ex.extractor,
                        "call": ex.extractor_detail.get("call", "none"),
                        "fallback_reason": ex.extractor_detail.get("fallback_reason"),
                        "error_kind": ex.extractor_detail.get("error_kind"),
                        "latency_ms": ex.extractor_detail.get("latency_ms"),
                        "field_accuracy": score["accuracy"], "quote_correct": score["quote_correct"],
                        "extra_items": score["extra_items"],
                        "errors": [f for f in score["fields"] if not f["correct"]]}
        for key in ("provider", "model", "prompt_tokens", "output_tokens", "request_id", "provider_request_id"):
            if ex.extractor_detail.get(key) is not None:
                detail[key] = ex.extractor_detail[key]
        details.append(detail)
    settings = get_settings()
    used = Counter(d["extractor"] for d in details)
    status = "gemini_not_used" if mode in ("gemini", "auto") and not used.get("gemini") else "completed"
    if status == "completed" and mode in ("gemini", "auto") and used.get("rules"):
        status = "partly_fell_back_to_rules"
    summary = scoring.summarize(scores)
    summary["field_accuracy"] = summary["accuracy"]
    return {"kind": "offer_extraction_score", "status": status, "mode": mode, "split": split,
            "run_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(), "revision": _revision(),
            "manifest_as_of": manifest["as_of"], "manifest_sha256": _sha256(FIXTURES / "manifest.json"),
            "input_sha256": input_hashes, "source_sha256": _source_hashes(),
            "key_configured": bool(settings.gemini_api_key),
            "offline": settings.offline, "synthetic_inputs": True,
            "extractors_used": dict(used),
            "calls": dict(Counter(d["call"] for d in details)),
            "summary": summary, "quotes": details,
            "caveat": "Synthetic documents written by the same author as the rules; scores are likely optimistic."}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--mode", choices=["rules", "gemini", "auto"], default="rules")
    parser.add_argument("--split", choices=["heldout", "train", "all"], default="heldout")
    args = parser.parse_args()
    reset_caches()
    report = run(args.mode, args.split)
    out = ROOT / "evaluation" / "offers" / f"score_{args.mode}_{args.split}_{report['run_at'][:10]}_{uuid.uuid4().hex}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    # A same-day rerun must never replace a previous record. Link a completed
    # temporary file into its unique destination atomically and create-only.
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=out.parent, prefix=".score-",
                                     suffix=".tmp", delete=False) as temporary:
        temp_path = Path(temporary.name)
        json.dump(report, temporary, indent=2, ensure_ascii=False)
        temporary.write("\n")
        temporary.flush()
        os.fsync(temporary.fileno())
    try:
        os.link(temp_path, out)
    finally:
        temp_path.unlink()
    s = report["summary"]
    print(json.dumps({"status": report["status"], "mode": args.mode, "split": args.split,
                      "field_accuracy": s["field_accuracy"], "fields_correct": s["correct"],
                      "fields_total": s["total"], "quote_accuracy": s["quote_accuracy"],
                      "quotes_correct": s["quotes_correct"], "quotes": s["quotes"],
                      "extractors_used": report["extractors_used"], "calls": report["calls"],
                      "flags": s["flags"], "errors": s["errors"]}, indent=2))
    print(f"Saved {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
