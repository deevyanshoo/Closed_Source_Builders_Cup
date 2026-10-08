"""Bounded synthetic commodity component arithmetic and offline receipts.

This module has no observed-source, network, model, or runner entry point.
Trusted fake fixture receipts bind bytes; they do not authenticate a publisher.
"""
from __future__ import annotations

import calendar
import csv
from datetime import datetime, timezone
from decimal import Context, Decimal, ROUND_HALF_EVEN, localcontext
from fractions import Fraction
from hashlib import sha256
import io
import json
import os
from pathlib import Path
import re
import stat


MAX_BODY = 4 * 1024 * 1024
MAX_RECEIPT = 65536
MAX_MONTHS = 240
MAX_FIELD = 8192
MAX_TOKEN = 64
MAX_BITS = 4096
HEADERS = ["month", "series_id", "unit", "status", "value"]
SERIES = ["copper", "aluminum"]
FLAGS = {"synthetic": True, "source_authenticity": False,
         "observed_eligible": False, "execution_authorized": False,
         "rights": "synthetic_only"}
SCHEMA = {"schema_version": "synthetic-commodity-component.v1", **FLAGS,
          "columns": HEADERS, "series_ids": SERIES, "unit": "USD/metric_tonne"}
RECEIPT_KEYS = {"schema_version", *FLAGS, "run_id", "first_month", "last_month",
                "month_count", "original_bytes", "original_sha256"}
RUN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
MONTH = re.compile(r"[0-9]{4}-[0-9]{2}\Z")
INSTANT = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
                     r"(?:\.[0-9]{1,6})?(?:Z|[+-][0-9]{2}:[0-9]{2})\Z")
NUMBER = re.compile(r"(?:0|[1-9][0-9]*)(?:\.[0-9]+)?\Z")
CODE_SHA256 = sha256(Path(__file__).read_bytes()).hexdigest()


class CommodityComponentError(ValueError):
    """Fixed sanitized failure code, never a source token or filesystem path."""


def _fail(code):
    raise CommodityComponentError(code)


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("ascii")


def _sha(body):
    return sha256(body).hexdigest()


def _flags(value):
    if type(value) is not dict or any(type(value.get(k)) is not type(v) or value.get(k) != v
                                      for k, v in FLAGS.items()):
        _fail("OBSERVED_HOLD")


def _run(value):
    if type(value) is not str or not RUN.fullmatch(value):
        _fail("RUN_INVALID")


def _month(value):
    if type(value) is not str or not MONTH.fullmatch(value):
        _fail("MONTH_INVALID")
    year, number = map(int, value.split("-"))
    if not 1 <= year <= 9998 or not 1 <= number <= 12:
        _fail("MONTH_INVALID")
    return year * 12 + number - 1


def _month_name(index):
    year, zero_month = divmod(index, 12)
    if not 1 <= year <= 9998:
        _fail("MONTH_INVALID")
    return f"{year:04d}-{zero_month + 1:02d}"


def _span(first, last):
    start, end = _month(first), _month(last)
    if end < start or not 1 <= end - start + 1 <= MAX_MONTHS:
        _fail("MONTH_MASK_INVALID")
    return [_month_name(index) for index in range(start, end + 1)]


def _as_of(value):
    if type(value) is not str or not INSTANT.fullmatch(value):
        _fail("AS_OF_INVALID")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.tzinfo is None:
            raise ValueError
        return result.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        _fail("AS_OF_INVALID")


def _proxy(month):
    next_index = _month(month) + 1
    next_year, zero_month = divmod(next_index, 12)
    if not 1 <= next_year <= 9998:
        _fail("MONTH_INVALID")
    next_month = zero_month + 1
    last = calendar.monthrange(next_year, next_month)[1]
    return f"{next_year:04d}-{next_month:02d}-{last:02d}T23:59:59.999999Z"


def _private(fd):
    details = os.fstat(fd)
    if not stat.S_ISDIR(details.st_mode) or details.st_uid != os.getuid() or stat.S_IMODE(details.st_mode) & 0o077:
        _fail("PRIVATE_LAYOUT_INVALID")


def _directory(parent_fd, name):
    return os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)


def _file(parent_fd, name):
    return os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent_fd)


def _identity(details):
    return (details.st_dev, details.st_ino, details.st_size, details.st_mtime_ns,
            details.st_ctime_ns, details.st_mode)


def _read_bound(parent_fd, name, maximum):
    fd = _file(parent_fd, name)
    try:
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.getuid()
                or stat.S_IMODE(before.st_mode) & 0o077 or before.st_size > maximum):
            _fail("FILE_INVALID")
        with os.fdopen(os.dup(fd), "rb") as stream:
            body = stream.read(maximum + 1)
        after = os.fstat(fd)
        path = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if (len(body) > maximum or len(body) != before.st_size
                or _identity(before) != _identity(after)
                or _identity(before) != _identity(path)):
            _fail("FILE_CHANGED")
        return body
    finally:
        os.close(fd)


def load_synthetic_component(original_root, run_id, synthetic_provenance):
    """Read one independently generated fake original and exact private receipt."""
    _flags(synthetic_provenance)  # Observed flags refuse before any path open.
    if set(synthetic_provenance) != set(FLAGS):
        _fail("PROVENANCE_INVALID")
    _run(run_id)
    try:
        root_fd = os.open(original_root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            _private(root_fd)
            objects_fd = _directory(root_fd, "objects")
            try:
                _private(objects_fd)
                run_fd = _directory(objects_fd, run_id)
                try:
                    _private(run_fd)
                    original = _read_bound(run_fd, "original.csv", MAX_BODY)
                finally:
                    os.close(run_fd)
            finally:
                os.close(objects_fd)
            committed_fd = _directory(root_fd, "committed")
            try:
                _private(committed_fd)
                receipt_body = _read_bound(committed_fd, run_id + ".json", MAX_RECEIPT)
            finally:
                os.close(committed_fd)
        finally:
            os.close(root_fd)
    except CommodityComponentError:
        raise
    except OSError:
        _fail("SOURCE_INVALID")
    try:
        receipt = json.loads(receipt_body)
        if (type(receipt) is not dict or set(receipt) != RECEIPT_KEYS
                or _json(receipt) != receipt_body):
            _fail("RECEIPT_INVALID")
        _flags(receipt)
        if (receipt["schema_version"] != "synthetic-commodity-fixture-receipt.v1"
                or receipt["run_id"] != run_id or type(receipt["month_count"]) is not int
                or type(receipt["original_bytes"]) is not int
                or receipt["original_bytes"] != len(original)
                or receipt["original_sha256"] != _sha(original)):
            _fail("RECEIPT_INVALID")
        months = _span(receipt["first_month"], receipt["last_month"])
        if len(months) != receipt["month_count"]:
            _fail("RECEIPT_INVALID")
    except (ValueError, TypeError, KeyError, UnicodeError):
        _fail("RECEIPT_INVALID")
    return {**FLAGS, "archive_root": str(Path(original_root)), "run_id": run_id,
            "receipt_sha256": _sha(receipt_body), "receipt": receipt,
            "original_sha256": _sha(original), "original_bytes": len(original),
            "original_body": original}


def _schema(value):
    _flags(value)
    if type(value) is not dict or _json(value) != _json(SCHEMA):
        _fail("SCHEMA_INVALID")


def _number(token):
    if type(token) is not str or len(token) > MAX_TOKEN or not NUMBER.fullmatch(token):
        _fail("VALUE_INVALID")
    fraction_digits = len(token.partition(".")[2])
    if fraction_digits > 38:
        _fail("VALUE_BOUND")
    significant = token.replace(".", "").lstrip("0")
    if len(significant) > 32:
        _fail("VALUE_BOUND")
    value = Decimal(token)
    if not value.is_finite() or value <= 0 or abs(value.adjusted()) > 6:
        _fail("VALUE_BOUND")
    ratio = Fraction(value)
    _fraction_bound(ratio)
    return ratio


def _fraction_bound(value):
    if value.numerator.bit_length() > MAX_BITS or value.denominator.bit_length() > MAX_BITS:
        _fail("VALUE_BOUND")


def _basis(value):
    return {"numerator": str(value.numerator), "denominator": str(value.denominator)}


def _render(value):
    _fraction_bound(value)
    with localcontext(Context(prec=50, rounding=ROUND_HALF_EVEN)):
        decimal = Decimal(value.numerator) / Decimal(value.denominator)
        if not decimal.is_finite() or decimal <= 0:
            _fail("VALUE_BOUND")
        return format(decimal.normalize(), "f")


def _parse(body, months):
    if type(body) is not bytes or len(body) > MAX_BODY or body.startswith(b"\xef\xbb\xbf"):
        _fail("CSV_INVALID")
    try:
        text = body.decode("utf-8", errors="strict")
        reader = csv.reader(io.StringIO(text, newline=""), strict=True)
        if next(reader) != HEADERS:
            _fail("CSV_INVALID")
        rows = list(reader)
    except (UnicodeError, csv.Error, StopIteration):
        _fail("CSV_INVALID")
    expected = [(month, series) for month in months for series in SERIES]
    if len(rows) != len(expected):
        _fail("CSV_INVALID")
    parsed = []
    for row, (month, series) in zip(rows, expected):
        if (len(row) != len(HEADERS) or any(len(field) > MAX_FIELD for field in row)
                or row[0] != month or row[1] != series or row[2] != "USD/metric_tonne"):
            _fail("CSV_INVALID")
        status, token = row[3], row[4]
        if status == "A":
            value = _number(token)
        elif status == "M" and token == "":
            value = None
        else:
            _fail("CSV_INVALID")
        parsed.append((month, series, status, value, token))
    return parsed


def normalize_commodity_component(verified, synthetic_schema, required_months, as_of):
    _flags(verified)
    _schema(synthetic_schema)
    verified = _fresh(verified)
    if type(required_months) is not list or not 1 <= len(required_months) <= MAX_MONTHS:
        _fail("MONTH_MASK_INVALID")
    try:
        expected = _span(verified["receipt"]["first_month"], verified["receipt"]["last_month"])
    except (KeyError, TypeError):
        _fail("SOURCE_INVALID")
    if required_months != expected:
        _fail("MONTH_MASK_INVALID")
    instant = _as_of(as_of)
    rows = _parse(verified.get("original_body"), expected)
    result = []
    for month, series, status, value, token in rows:
        proxy = _proxy(month)
        if status == "M":
            state, reason = "unavailable", "source_missing"
        elif instant < _as_of(proxy):
            state, reason = "unavailable", "proxy_not_yet_available"
        else:
            state, reason = "complete", None
        native = value if state == "complete" else None
        kg = native / 1000 if native is not None else None
        if kg is not None:
            _fraction_bound(kg)
        result.append({"month": month, "series_id": series, "status": state,
                       "reason": reason, "source_unit": "USD/metric_tonne",
                       "native_unit": "USD/metric_tonne", "derived_unit": "USD/kg",
                       "native_value": _render(native) if native is not None else None,
                       "usd_per_kg": _render(kg) if kg is not None else None,
                       "native_basis": _basis(native) if native is not None else None,
                       "kg_basis": _basis(kg) if kg is not None else None,
                       "token_sha256": _sha(token.encode()) if status == "A" else None,
                       "available_at_proxy": proxy})
    return {"schema_version": "synthetic-commodity-component.v1", **FLAGS,
            "status": "SYNTHETIC_OFFLINE_ONLY" if all(r["status"] == "complete" for r in result)
                      else "SYNTHETIC_OFFLINE_INCOMPLETE",
            "verified_original": verified, "schema": SCHEMA,
            "schema_sha256": _sha(_json(SCHEMA)), "required_months": expected,
            "mask_sha256": _sha(_json(expected)), "as_of": as_of,
            "availability_basis": "end_of_following_month_utc_proxy_not_pit",
            "arithmetic_version": "native_fraction_div1000_decimal50_half_even_v1",
            "months": result}


def _write_new(parent_fd, name, body):
    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                 0o600, dir_fd=parent_fd)
    try:
        remaining = memoryview(body)
        while remaining:
            count = os.write(fd, remaining)
            if count <= 0:
                _fail("PUBLICATION_IO")
            remaining = remaining[count:]
        os.fsync(fd)
    finally:
        os.close(fd)
    if _read_bound(parent_fd, name, MAX_BODY) != body:
        _fail("PUBLICATION_INVALID")


def _disqualify(root_fd, run_id):
    body = _json({"status": "SYNTHETIC_OFFLINE_INCOMPLETE", "run_id": run_id,
                  **FLAGS, "reason": "POSTLINK_FINALIZATION_FAILED"})
    name = run_id + ".json"
    try:
        incomplete_fd = _directory(root_fd, "incomplete")
        try:
            _private(incomplete_fd)
            marker_fd = os.open(name, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                0o600, dir_fd=incomplete_fd)
            try:
                created = os.fstat(marker_fd)
                if (not stat.S_ISREG(created.st_mode) or created.st_uid != os.getuid()
                        or stat.S_IMODE(created.st_mode) & 0o077):
                    _fail("PUBLICATION_CLEANUP_UNRESOLVED")
                remaining = memoryview(body)
                while remaining:
                    count = os.write(marker_fd, remaining)
                    if count <= 0:
                        _fail("PUBLICATION_CLEANUP_UNRESOLVED")
                    remaining = remaining[count:]
                os.fsync(marker_fd)
                os.lseek(marker_fd, 0, os.SEEK_SET)
                if os.read(marker_fd, len(body) + 1) != body:
                    _fail("PUBLICATION_CLEANUP_UNRESOLVED")
                expected = (created.st_dev, created.st_ino, len(body))
                for details in (os.fstat(marker_fd),
                                os.stat(name, dir_fd=incomplete_fd, follow_symlinks=False)):
                    if (details.st_dev, details.st_ino, details.st_size) != expected:
                        _fail("PUBLICATION_CLEANUP_UNRESOLVED")
                if _read_bound(incomplete_fd, name, MAX_RECEIPT) != body:
                    _fail("PUBLICATION_CLEANUP_UNRESOLVED")
                os.fsync(incomplete_fd)
                final = os.stat(name, dir_fd=incomplete_fd, follow_symlinks=False)
                if (final.st_dev, final.st_ino, final.st_size) != expected:
                    _fail("PUBLICATION_CLEANUP_UNRESOLVED")
            finally:
                os.close(marker_fd)
        finally:
            os.close(incomplete_fd)
    except BaseException:
        _fail("PUBLICATION_CLEANUP_UNRESOLVED")


def _panel(candidate):
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(("month", "series_id", "status", "reason", "native_value",
                     "native_unit", "usd_per_kg", "derived_unit", "available_at_proxy",
                     "native_numerator", "native_denominator", "kg_numerator", "kg_denominator"))
    for row in candidate["months"]:
        writer.writerow((row["month"], row["series_id"], row["status"], row["reason"] or "",
                         row["native_value"] or "", row["native_unit"], row["usd_per_kg"] or "",
                         row["derived_unit"], row["available_at_proxy"],
                         row["native_basis"]["numerator"] if row["native_basis"] else "",
                         row["native_basis"]["denominator"] if row["native_basis"] else "",
                         row["kg_basis"]["numerator"] if row["kg_basis"] else "",
                         row["kg_basis"]["denominator"] if row["kg_basis"] else ""))
    return stream.getvalue().encode("utf-8")


def _fresh(verified):
    _flags(verified)
    try:
        root, run_id = verified["archive_root"], verified["run_id"]
    except (KeyError, TypeError):
        _fail("SOURCE_CHANGED")
    fresh = load_synthetic_component(root, run_id, FLAGS)
    if fresh != verified:
        _fail("SOURCE_CHANGED")
    return fresh


def _check_final_incomplete(output_root, run_id):
    """Catch a disqualifier published during verification before reporting complete."""
    root_fd = os.open(output_root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        _private(root_fd)
        incomplete_fd = _directory(root_fd, "incomplete")
        try:
            _private(incomplete_fd)
            try:
                os.stat(run_id + ".json", dir_fd=incomplete_fd, follow_symlinks=False)
            except FileNotFoundError:
                return
            _fail("PUBLICATION_INCOMPLETE")
        finally:
            os.close(incomplete_fd)
    finally:
        os.close(root_fd)


def _manifest(candidate, verified, run_id, panel):
    return {"schema_version": "synthetic-commodity-panel-receipt.v1", **FLAGS,
            "status": candidate["status"], "run_id": run_id,
            "fixture_run_id": verified["run_id"],
            "fixture_receipt_sha256": verified["receipt_sha256"],
            "original_sha256": verified["original_sha256"],
            "original_bytes": verified["original_bytes"],
            "schema_sha256": candidate["schema_sha256"],
            "mask_sha256": candidate["mask_sha256"],
            "as_of": candidate["as_of"],
            "code_sha256": CODE_SHA256,
            "panel_sha256": _sha(panel), "panel_bytes": len(panel),
            "month_count": len(candidate["required_months"]),
            "availability_basis": candidate["availability_basis"],
            "arithmetic_version": candidate["arithmetic_version"]}


def _ensure_layout(output_root):
    root = Path(output_root)
    try:
        root.mkdir(mode=0o700)
    except FileExistsError:
        pass
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        _private(root_fd)
        for name in ("objects", "complete", "incomplete"):
            try:
                os.mkdir(name, 0o700, dir_fd=root_fd)
            except FileExistsError:
                pass
            fd = _directory(root_fd, name)
            try:
                _private(fd)
            finally:
                os.close(fd)
        return root_fd
    except BaseException:
        os.close(root_fd)
        raise


def publish_commodity_component(candidate, verified, output_root, run_id):
    _flags(candidate)
    _flags(verified)
    _run(run_id)
    if _sha(Path(__file__).read_bytes()) != CODE_SHA256:
        _fail("CODE_CHANGED")
    fresh = _fresh(verified)
    if candidate != normalize_commodity_component(fresh, SCHEMA,
                                                   candidate.get("required_months"),
                                                   candidate.get("as_of")):
        _fail("CANDIDATE_CHANGED")
    panel = _panel(candidate)
    manifest = _manifest(candidate, fresh, run_id, panel)
    manifest_body = _json(manifest)
    try:
        root_fd = _ensure_layout(output_root)
        linked_complete = False
        disqualified = False
        try:
            original_root = os.fstat(root_fd)
            original_root_identity = (original_root.st_dev, original_root.st_ino)
            objects_fd = _directory(root_fd, "objects")
            try:
                os.mkdir(run_id, 0o700, dir_fd=objects_fd)
                object_fd = _directory(objects_fd, run_id)
                try:
                    _private(object_fd)
                    _write_new(object_fd, "panel.csv", panel)
                    _write_new(object_fd, "manifest.json", manifest_body)
                    os.fsync(object_fd)
                finally:
                    os.close(object_fd)
                os.fsync(objects_fd)
            finally:
                os.close(objects_fd)
            os.fsync(root_fd)
            if _verify(output_root, run_id, fresh, SCHEMA, candidate["required_months"],
                       candidate["as_of"], require_marker=False) != manifest:
                _fail("PUBLICATION_INVALID")
            kind = "complete" if manifest["status"] == "SYNTHETIC_OFFLINE_ONLY" else "incomplete"
            marker_fd = _directory(root_fd, kind)
            linked = False
            try:
                try:
                    os.link(f"objects/{run_id}/manifest.json", run_id + ".json",
                            src_dir_fd=root_fd, dst_dir_fd=marker_fd, follow_symlinks=False)
                    linked = True
                    linked_complete = kind == "complete"
                    os.fsync(marker_fd)
                    if kind == "complete" and verify_commodity_component(
                            output_root, run_id, fresh, SCHEMA, candidate["required_months"],
                            candidate["as_of"]) != manifest:
                        _fail("PUBLICATION_INVALID")
                    closing_fd, marker_fd = marker_fd, None
                    os.close(closing_fd)
                except BaseException:
                    if linked:
                        if kind != "complete":
                            _fail("PUBLICATION_CLEANUP_UNRESOLVED")
                        _disqualify(root_fd, run_id)
                        disqualified = True
                        _fail("PUBLICATION_INCOMPLETE")
                    raise
            finally:
                if marker_fd is not None:
                    closing_fd, marker_fd = marker_fd, None
                    try:
                        os.close(closing_fd)
                    except BaseException:
                        if linked_complete and not disqualified:
                            _disqualify(root_fd, run_id)
                            disqualified = True
                        if linked_complete and disqualified:
                            _fail("PUBLICATION_INCOMPLETE")
                        raise
        finally:
            try:
                os.close(root_fd)
            except BaseException:
                if linked_complete and not disqualified:
                    try:
                        recovery_fd = os.open(output_root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                        try:
                            _private(recovery_fd)
                            recovered = os.fstat(recovery_fd)
                            if (recovered.st_dev, recovered.st_ino) != original_root_identity:
                                _fail("PUBLICATION_CLEANUP_UNRESOLVED")
                            _disqualify(recovery_fd, run_id)
                            disqualified = True
                        finally:
                            os.close(recovery_fd)
                    except BaseException:
                        _fail("PUBLICATION_CLEANUP_UNRESOLVED")
                if linked_complete and disqualified:
                    _fail("PUBLICATION_INCOMPLETE")
                raise
    except FileExistsError:
        _fail("PUBLICATION_EXISTS")
    except CommodityComponentError:
        raise
    except OSError:
        _fail("PUBLICATION_IO")
    return manifest


def verify_commodity_component(output_root, run_id, verified, synthetic_schema,
                               required_months, as_of):
    return _verify(output_root, run_id, verified, synthetic_schema, required_months,
                   as_of, require_marker=True)


def _verify(output_root, run_id, verified, synthetic_schema, required_months,
            as_of, *, require_marker):
    _flags(verified)
    _schema(synthetic_schema)
    _run(run_id)
    if _sha(Path(__file__).read_bytes()) != CODE_SHA256:
        _fail("CODE_CHANGED")
    try:
        root_fd = os.open(output_root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            _private(root_fd)
            incomplete_fd = _directory(root_fd, "incomplete")
            try:
                _private(incomplete_fd)
                try:
                    os.stat(run_id + ".json", dir_fd=incomplete_fd, follow_symlinks=False)
                except FileNotFoundError:
                    pass
                else:
                    _fail("PUBLICATION_INCOMPLETE")
            finally:
                os.close(incomplete_fd)
            marker = None
            marker_stat = None
            if require_marker:
                complete_fd = _directory(root_fd, "complete")
                try:
                    _private(complete_fd)
                    marker = _read_bound(complete_fd, run_id + ".json", MAX_RECEIPT)
                    marker_stat = os.stat(run_id + ".json", dir_fd=complete_fd,
                                          follow_symlinks=False)
                finally:
                    os.close(complete_fd)
            objects_fd = _directory(root_fd, "objects")
            try:
                _private(objects_fd)
                object_fd = _directory(objects_fd, run_id)
                try:
                    _private(object_fd)
                    manifest_body = _read_bound(object_fd, "manifest.json", MAX_RECEIPT)
                    manifest_stat = os.stat("manifest.json", dir_fd=object_fd,
                                            follow_symlinks=False)
                    panel = _read_bound(object_fd, "panel.csv", MAX_BODY)
                finally:
                    os.close(object_fd)
            finally:
                os.close(objects_fd)
            if require_marker and (marker != manifest_body or
                                   (marker_stat.st_dev, marker_stat.st_ino) !=
                                   (manifest_stat.st_dev, manifest_stat.st_ino)):
                _fail("PUBLICATION_INVALID")
        finally:
            os.close(root_fd)
        manifest = json.loads(manifest_body)
        if type(manifest) is not dict or _json(manifest) != manifest_body:
            _fail("PUBLICATION_INVALID")
        fresh = _fresh(verified)
        candidate = normalize_commodity_component(fresh, SCHEMA, required_months, as_of)
        if (panel != _panel(candidate) or manifest != _manifest(candidate, fresh, run_id, panel)
                or (require_marker and manifest["status"] != "SYNTHETIC_OFFLINE_ONLY")):
            _fail("PUBLICATION_INVALID")
        if require_marker:
            _check_final_incomplete(output_root, run_id)
        return manifest
    except CommodityComponentError:
        raise
    except (OSError, ValueError, TypeError, KeyError, UnicodeError):
        _fail("PUBLICATION_INVALID")
