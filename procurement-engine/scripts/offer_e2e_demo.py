"""Record one end-to-end run: synthetic quote -> reviewed, saved offer -> planning input -> existing recommend()
-> existing local RFQ draft. Nothing is sent.

Runs offline in a temporary data directory (synthetic market data and sample news, labelled as such), so it
never touches data/app.duckdb and never calls a provider.

Run from procurement-engine/:  .venv/bin/python scripts/offer_e2e_demo.py
Output: evaluation/offers/e2e_demo_<UTC date>.json
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
FIXTURES = ROOT / "tests" / "fixtures" / "offers"
BUYER = {"need_by_month": 3, "current_stock_kg": "20000", "monthly_consumption_kg": "15000", "risk_appetite": "medium"}


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="offer_e2e_"))
    os.environ.update({"PROCURE_DATA_DIR": str(tmp), "PROCURE_DB_PATH": str(tmp / "demo.duckdb"), "OFFLINE": "1"})
    sys.path.insert(0, str(ROOT / "src"))
    from procure.settings import reset_caches

    os.environ.pop("GEMINI_API_KEY", None)
    reset_caches()
    from procure.ingest.refresh import refresh_all
    from procure.offers import store

    refresh = refresh_all(include_news=True, evaluate_models=True)
    manifest = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
    today = dt.date.fromisoformat(manifest["as_of"])
    q01 = next(q for q in manifest["quotes"] if q["id"] == "Q01")
    path = FIXTURES / q01["file"]

    steps = []
    offer = store.create_from_upload(path.read_bytes(), path.name, mode="rules", as_of=today, actor="buyer")["offer"]
    steps.append({"step": "extracted", "status": offer["status"], "blocking_reasons": offer["blocking_reasons"]})
    offer = store.correct_field(offer["offer_id"], field="settlement_currency", value="USD",
                                evidence=[{"locator": "p1:l10", "quote": "USD"}], expected_version=offer["version"],
                                actor="reviewer", idempotency_key="demo-correct-0001", today=today)
    steps.append({"step": "corrected", "status": offer["status"], "field": "settlement_currency",
                  "evidence_status": offer["fields"]["settlement_currency"]["evidence_status"]})
    offer = store.save_offer(offer["offer_id"], expected_version=offer["version"], actor="reviewer",
                             idempotency_key="demo-save-0001", today=today)
    steps.append({"step": "saved", "status": offer["status"], "version": offer["version"]})
    plan = store.planning_input([offer["offer_id"]], buyer=BUYER, actor="planner",
                                idempotency_key="demo-plan-0001", today=today)
    steps.append({"step": "planning_input", "offer": plan["offers"][0]["items"][0],
                  "legacy_request": plan["legacy_recommend_requests"][0], "limitations": plan["limitations"]})
    rfq = store.draft_rfq(offer["offer_id"], buyer=BUYER, actor="planner", idempotency_key="demo-rfq-0001",
                          today=today)
    steps.append({"step": "rfq_drafted", "status": rfq["offer"]["status"], "recommendation": rfq["recommendation"],
                  "rfq": rfq["rfq"], "sent": rfq["sent"]})
    events = store.get_offer(offer["offer_id"], include_events=True, today=today)["events"]

    revision = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True,
                              text=True).stdout.strip()
    record = {"kind": "offer_end_to_end_demo", "run_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
              .isoformat(), "revision": revision, "synthetic_inputs": True, "offline": True,
              "market_data": {s["source"]: s["used"] for s in refresh["sources"]},
              "note": "Synthetic quote and offline synthetic market data. A local RFQ draft only; nothing was sent.",
              "steps": steps, "audit_events": events}
    out = ROOT / "evaluation" / "offers" / f"e2e_demo_{record['run_at'][:10]}.json"
    out.write_text(json.dumps(record, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    print(json.dumps([{k: s[k] for k in s if k in ("step", "status", "sent")} for s in steps], indent=1))
    print(f"Saved {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
