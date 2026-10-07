"""Export an existing compatible DuckDB cache to a separate, pinned snapshot.

This operator tool performs no ingestion, evaluation, provider access, or auto-fetch.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from procure.service_cache import CacheUnavailable, provenance_labels, validate_database  # noqa: E402


def _no_symlink(path: Path) -> None:
    for part in (path, *path.parents):
        if part.is_symlink():
            raise CacheUnavailable(f"symlink in export path: {part}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def export_snapshot(source: Path, output: Path, source_revision: str,
                    synthetic: bool, sample: bool, materials: tuple[str, ...]) -> str:
    """Validate and copy to a new snapshot directory; return the pinned digest."""
    source, output = Path(source), Path(output)
    if not source.is_absolute() or not output.is_absolute():
        raise CacheUnavailable("source and output must be absolute paths")
    if not re.fullmatch(r"[0-9a-f]{40}", source_revision):
        raise CacheUnavailable("source revision must be a lowercase 40-digit commit hash")
    if not isinstance(synthetic, bool) or not isinstance(sample, bool):
        raise CacheUnavailable("synthetic and sample labels must be boolean")
    _no_symlink(source)
    _no_symlink(output)
    source, output = source.resolve(), output.resolve()
    if (output == source.parent or source.parent in output.parents
            or output in source.parents or output == source):
        raise CacheUnavailable("snapshot output and source paths overlap")
    if output.exists():
        raise CacheUnavailable("snapshot output already exists")
    validate_database(source, materials)
    if provenance_labels(source) != (synthetic, sample):
        raise CacheUnavailable("requested provenance labels do not match source rows")
    source_hash = _sha256(source)
    output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temp = Path(tempfile.mkdtemp(prefix=".snapshot-", dir=output.parent))
    try:
        copied = temp / "app.duckdb"
        with source.open("rb") as input_stream, copied.open("xb") as output_stream:
            os.chmod(copied, 0o600)
            shutil.copyfileobj(input_stream, output_stream, length=1024 * 1024)
            output_stream.flush()
            os.fsync(output_stream.fileno())
        if _sha256(copied) != source_hash or _sha256(source) != source_hash:
            raise CacheUnavailable("source changed during snapshot export")
        validate_database(copied, materials)
        if provenance_labels(copied) != (synthetic, sample):
            raise CacheUnavailable("copied provenance labels do not match source rows")
        manifest = {"format": "closed-source-cache-v1", "engine": "duckdb",
                    "file": "app.duckdb", "sha256": source_hash,
                    "source_revision": source_revision, "synthetic": synthetic,
                    "sample": sample}
        manifest_path = temp / "manifest.json"
        with manifest_path.open("x", encoding="utf-8") as handle:
            os.chmod(manifest_path, 0o600)
            json.dump(manifest, handle, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        if output.exists():
            raise CacheUnavailable("snapshot output already exists")
        os.rename(temp, output)
    except Exception:
        shutil.rmtree(temp, ignore_errors=True)
        raise
    return source_hash


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True,
                        help="Absolute path to an already-existing task-owned DuckDB cache")
    parser.add_argument("--output", type=Path, required=True,
                        help="New, separate snapshot directory (must not exist)")
    parser.add_argument("--source-revision", required=True)
    parser.add_argument("--synthetic", choices=("true", "false"), required=True)
    parser.add_argument("--sample", choices=("true", "false"), required=True)
    parser.add_argument("--material", action="append", required=True,
                        help="Configured material; repeat for every served material")
    args = parser.parse_args(argv)
    try:
        digest = export_snapshot(args.source, args.output, args.source_revision,
                                 args.synthetic == "true", args.sample == "true",
                                 tuple(args.material))
    except (CacheUnavailable, OSError) as exc:
        parser.exit(2, f"snapshot export unavailable: {exc}\n")
    print(digest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
