"""End-to-end smoke test against a running API. Calls every endpoint with realistic inputs
and asserts sane outputs. Exits non-zero on the first failure.

Usage: python scripts/smoke_test.py [--api http://localhost:8000] [--refresh]
"""
from __future__ import annotations

import argparse
import sys
import time

import httpx

# Plausible INR per kg ranges, wide enough for any market since 2010.
INR_RANGE = {"copper": (200, 4000), "aluminium": (50, 800), "iron_ore": (1, 40)}
SHOCKS = [
    ("copper", "Major copper mine strike halts 10% of global supply"),
    ("aluminium", "Major aluminium smelter outage halts 10% of global supply"),
    ("iron_ore", "Major iron ore mine closure halts 10% of global supply"),
]
FAILURES: list[str] = []
TIMINGS: dict[str, float] = {}


def check(cond: bool, msg: str) -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {msg}")
    if not cond:
        FAILURES.append(msg)


def call(c: httpx.Client, method: str, path: str, **kw):
    t = time.perf_counter()
    r = c.request(method, path, **kw)
    TIMINGS[f"{method} {path.split('?')[0]}"] = round(time.perf_counter() - t, 2)
    check(r.status_code == 200, f"{method} {path} -> {r.status_code}")
    return r.json() if r.status_code == 200 else None


def now_share(rec: dict) -> float:
    return sum(s["percent"] for s in rec["schedule"] if s["month"] == 0)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://localhost:8000")
    ap.add_argument("--refresh", action="store_true", help="also call POST /ingest/refresh")
    args = ap.parse_args()
    c = httpx.Client(base_url=args.api, timeout=600)

    print("== health and config")
    h = call(c, "GET", "/health")
    for name, s in h["sources"].items():
        print(f"     source {name:<16} used={s['used']}")
    check(h["status"] == "ok", "health status ok")
    mats = [m["key"] for m in call(c, "GET", "/materials")]
    check(mats == ["copper", "aluminium", "iron_ore"], "three materials configured")
    if args.refresh:
        rep = call(c, "POST", "/ingest/refresh")
        check(len(rep["sources"]) >= 5, "refresh reports every source")

    for m in mats:
        print(f"== {m}")
        p = call(c, "GET", f"/prices/{m}")
        last = p["points"][-1]["inr_per_kg"]
        lo, hi = INR_RANGE[m]
        check(len(p["points"]) >= 60, f"{m}: at least 5 years of monthly history ({len(p['points'])})")
        check(lo <= last <= hi, f"{m}: latest INR/kg {last:.2f} in plausible range {lo}-{hi}")
        f = call(c, "GET", f"/forecast/{m}?horizon=6")
        check(len(f["points"]) == 7 and all(x["p10"] <= x["p50"] <= x["p90"] for x in f["points"]),
              f"{m}: forecast has ordered P10 <= P50 <= P90 for 6 months")
        check(len(f["metrics"]) == 18, f"{m}: metrics for 3 models x 6 horizons")
        n = call(c, "GET", f"/news/{m}")
        check(-1 <= n["news_index"] <= 1 and len(n["items"]) > 0, f"{m}: news index {n['news_index']:+.2f} in range")
        rec = call(c, "POST", "/recommend", json={"material": m, "quantity_kg": 50000, "need_by_month": 3,
                                                  "current_stock_kg": 20000, "monthly_consumption_kg": 15000,
                                                  "supplier_lead_time_months": 1, "risk_appetite": "medium"})
        total = sum(s["percent"] for s in rec["schedule"])
        check(abs(total - 100) < 1e-6, f"{m}: schedule percents sum to 100 ({rec['summary']})")
        check(rec["action"] in {"BUY NOW", "SPLIT BUY", "WAIT"}, f"{m}: action {rec['action']}")
        check(len(rec["why"]) >= 8 and all(w["value"] for w in rec["why"]), f"{m}: why-panel has computed values")
        check(len(rec["comparison"]) == 4, f"{m}: best plan plus three baselines")
        bt = call(c, "GET", f"/backtest/{m}")
        s = bt["summary"]
        check(bt["months"] >= 12 and len(bt["rows"]) == bt["months"], f"{m}: backtest over {bt['months']} months")
        print(f"     backtest: saved vs JIT INR {s['total_saved_vs_jit_inr']:,} ({s['avg_saving_vs_jit_pct']:+.2f}% avg), "
              f"beat JIT {s['share_months_beat_jit'] * 100:.0f}% of months")

    print("== headline to impact")
    changed = []
    for m, headline in SHOCKS:
        out = call(c, "POST", "/headline-impact", json={"material": m, "headline": headline,
                                                       "request": {"material": m}})
        sig = out["signal"]
        check(sig["relevant"] and sig["direction"] == "up", f"{m}: shock read as relevant, price up (severity {sig['severity']})")
        b, a = out["before"], out["after"]
        check(a["news_index"] >= b["news_index"], f"{m}: news index {b['news_index']:+.2f} -> {a['news_index']:+.2f}")
        check(a["forecast"]["mean"][2] >= b["forecast"]["mean"][2], f"{m}: expected price did not fall")
        moved = now_share(a) != now_share(b) or a["summary"] != b["summary"]
        print(f"     {m}: '{b['summary']}' -> '{a['summary']}'")
        print(f"     {out['diff']}")
        if moved:
            changed.append(m)
    check(len(changed) >= 1, f"a strong supply shock visibly changes at least one plan (changed: {changed})")
    calm = call(c, "POST", "/headline-impact", json={"headline": "Gold prices rise on safe haven demand",
                                                    "request": {"material": "copper"}})
    check(not calm["signal"]["relevant"], "unrelated headline is not treated as price-moving")

    print("== actions and audit")
    rec = call(c, "POST", "/recommend", json={"material": "copper"})
    a1 = call(c, "POST", "/actions/rfq", json={"recommendation_id": rec["id"]})
    check(a1["status"] == "draft" and "Supplier team" in a1["payload"]["body"], "RFQ draft created")
    check("[" not in a1["payload"]["body"], "RFQ has no placeholders")
    check(call(c, "POST", f"/actions/{a1['id']}/approve", json={"reason": "smoke test"})["status"] == "approved", "approve")
    a2 = call(c, "POST", "/actions/rfq", json={"recommendation_id": rec["id"]})
    check(call(c, "POST", f"/actions/{a2['id']}/reject", json={"reason": "smoke test reject"})["status"] == "rejected", "reject")
    audit = call(c, "GET", "/audit")
    events = {(e["event"], e["id"]) for e in audit}
    check(("rfq_approved", a1["id"]) in events and ("rfq_rejected", a2["id"]) in events
          and ("recommend", rec["id"]) in events, "audit log has recommendation, approval and rejection")
    call(c, "GET", "/actions")
    call(c, "GET", "/overview")
    call(c, "GET", "/metrics")
    check(c.get("/prices/zinc").status_code == 404, "unknown material returns 404")

    print("== timings (s)")
    for k, v in TIMINGS.items():
        print(f"     {k:<36} {v}")
    print(f"\nSMOKE TEST {'PASSED' if not FAILURES else 'FAILED'} ({len(FAILURES)} failures)")
    for f in FAILURES:
        print("  -", f)
    return 0 if not FAILURES else 1


if __name__ == "__main__":
    sys.exit(main())
