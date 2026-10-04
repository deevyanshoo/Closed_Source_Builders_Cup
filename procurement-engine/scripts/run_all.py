"""Start the API (uvicorn) and the UI (Streamlit) together. Works on macOS, Linux and Windows.

Usage:
    python scripts/run_all.py              # API on :8000, UI on :8501
    python scripts/run_all.py --no-ui      # API only
Press Ctrl+C to stop both.
"""
from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]


def wait_for(url: str, timeout: float, proc: subprocess.Popen) -> bool:
    start = time.time()
    while time.time() - start < timeout:
        if proc.poll() is not None:
            return False
        try:
            if httpx.get(url, timeout=3).status_code == 200:
                return True
        except httpx.HTTPError:
            pass
        time.sleep(1)
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api-port", type=int, default=8000)
    ap.add_argument("--ui-port", type=int, default=8501)
    ap.add_argument("--no-ui", action="store_true")
    args = ap.parse_args()

    env = {**os.environ, "PROCURE_API_URL": f"http://localhost:{args.api_port}"}
    procs: list[subprocess.Popen] = []
    api = subprocess.Popen([sys.executable, "-m", "uvicorn", "procure.api:app", "--host", "127.0.0.1",
                            "--port", str(args.api_port)], cwd=ROOT, env=env)
    procs.append(api)
    print(f"[run_all] starting API on http://localhost:{args.api_port} (first start ingests data, up to ~2 min)")
    if not wait_for(f"http://localhost:{args.api_port}/health", 600, api):
        print("[run_all] API failed to start")
        api.terminate()
        return 1
    print(f"[run_all] API ready. Docs: http://localhost:{args.api_port}/docs")

    if not args.no_ui:
        ui = subprocess.Popen([sys.executable, "-m", "streamlit", "run", str(ROOT / "ui" / "app.py"),
                               "--server.port", str(args.ui_port), "--server.headless", "true",
                               "--browser.gatherUsageStats", "false"], cwd=ROOT, env=env)
        procs.append(ui)
        if wait_for(f"http://localhost:{args.ui_port}/_stcore/health", 120, ui):
            print(f"[run_all] UI ready: http://localhost:{args.ui_port}")
        else:
            print("[run_all] UI failed to start")

    def stop(*_):
        for p in procs:
            if p.poll() is None:
                p.terminate()
        for p in procs:
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()
        sys.exit(0)

    signal.signal(signal.SIGINT, stop)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, stop)
    print("[run_all] press Ctrl+C to stop")
    while all(p.poll() is None for p in procs):
        time.sleep(1)
    stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
