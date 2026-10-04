"""Headless UI check: run the Streamlit app with AppTest against a running API and click
through every tab's main action. Fails on any exception or error box.

Usage: python scripts/ui_check.py   (the API must be running, see scripts/run_all.py)
"""
from __future__ import annotations

import sys
from pathlib import Path

from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parents[1] / "ui" / "app.py")


def check(at: AppTest, step: str) -> None:
    problems = [f"exception: {e.value}" for e in at.exception] + [f"error: {e.value}" for e in at.error]
    status = "ok" if not problems else "FAIL"
    print(f"[ui] {step:<34} {status}")
    if problems:
        for p in problems:
            print("     ", p)
        sys.exit(1)


def button(at: AppTest, label_start: str):
    matches = [b for b in at.button if b.label.startswith(label_start)]
    if not matches:
        print(f"[ui] button '{label_start}' not found")
        sys.exit(1)
    return matches[0]


def main() -> None:
    at = AppTest.from_file(APP, default_timeout=600).run()
    check(at, "initial load (all tabs render)")
    button(at, "Get recommendation").click().run()
    check(at, "plan a purchase")
    button(at, "Draft RFQ").click().run()
    check(at, "draft RFQ")
    button(at, "Major copper mine strike").click().run()
    button(at, "Show impact").click().run()
    check(at, "headline to impact")
    print("     ", at.info[-1].value if at.info else "(no diff shown)")
    at.run()
    approve = [b for b in at.button if b.label == "Approve"]
    if approve:
        approve[0].click().run()
        check(at, "approve RFQ")
    reject = [b for b in at.button if b.label == "Reject"]
    if reject:
        reject[0].click().run()
        check(at, "reject RFQ")
    for mat in ("aluminium", "iron_ore"):
        [s for s in at.selectbox if s.label == "Material" and s.key == "bt_mat"][0].set_value(mat).run()
        check(at, f"regret meter {mat}")
    [s for s in at.selectbox if s.label == "Risk measure"][0].set_value("overshoot").run()
    check(at, "regret meter overshoot measure")
    print("[ui] all checks passed")


if __name__ == "__main__":
    main()
