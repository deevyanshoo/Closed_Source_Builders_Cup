"""CLI for the pure offline forecast scorecard."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from procure.evaluation.forecast_scorecard import write_run  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--bootstrap-replicates", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()
    try:
        manifest = write_run(args.input, args.output_dir, bootstrap_replicates=args.bootstrap_replicates, seed=args.seed)
    except (OSError, ValueError) as exc:
        parser.exit(2, f"forecast scorecard: {exc}\n")
    print(f"Created {args.output_dir} (input SHA-256 {manifest['input_sha256']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
