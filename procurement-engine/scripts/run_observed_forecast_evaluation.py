"""Offline observed forecast runner; current frozen protocol holds execution."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from procure.evaluation.observed_runner import run_offline_evaluation  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol", required=True, type=Path)
    parser.add_argument("--source-manifest", required=True, type=Path)
    parser.add_argument("--source-file", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--as-of", required=True)
    args = parser.parse_args()
    try:
        run_offline_evaluation(args.protocol, args.source_manifest, args.source_file, args.output_dir, args.as_of)
    except (ValueError, OSError, RuntimeError) as exc:
        parser.exit(2, f"offline forecast runner: {exc}\n")
    print(f"Completed {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
