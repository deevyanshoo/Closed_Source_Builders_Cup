"""Bounded synthetic-only FX arithmetic and offline panel receipts.

This module never acquires data. Its inputs are trusted synthetic fixtures, not
source-authentication evidence or permission to use observed rates.
"""
from __future__ import annotations

import calendar
import csv
from contextlib import ExitStack
from datetime import date, datetime, timezone
from decimal import Context, Decimal, ROUND_HALF_EVEN, localcontext
from fractions import Fraction
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat

from . import source_transport

MAX_BODY = 4 * 1024 * 1024
MAX_ROWS = 8192
MAX_MONTHS = 240
HEADERS = ("KEY", "FREQ", "CURRENCY", "CURRENCY_DENOM", "EXR_TYPE",
           "EXR_SUFFIX", "TIME_PERIOD", "OBS_VALUE", "UNIT", "UNIT_MULT", "OBS_STATUS")
SCHEMA = {
    "schema_version": "synthetic-ecb-schema.v1", "synthetic": True,
    "columns": list(HEADERS), "frequency": "D", "denominator": "EUR",
    "exr_type": "SP00", "exr_suffix": "A", "unit": "SYNTHETIC_QUOTE",
    "unit_mult": "0", "obs_status": "A",
    "pairs": {
        "ecb_usd_eur": {"currency": "USD", "key": "D.USD.EUR.SP00.A", "meaning": "USD_per_EUR"},
        "ecb_inr_eur": {"currency": "INR", "key": "D.INR.EUR.SP00.A", "meaning": "INR_per_EUR"},
    },
}
SOURCE_IDS = tuple(sorted(source_transport.SOURCES))
DAY = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}\Z")
MONTH = re.compile(r"[0-9]{4}-[0-9]{2}\Z")
INSTANT = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]{1,6})?(?:Z|[+-][0-9]{2}:[0-9]{2})\Z"
)
NUMBER = re.compile(r"(?:0|[1-9][0-9]*)(?:\.[0-9]+)?\Z")
RUN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
CODE_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


class FxPanelError(ValueError):
    """A fixed code; never interpolate original values, paths, or OS errors."""


def _fail(code):
    raise FxPanelError(code)


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                      allow_nan=False).encode("ascii")


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _synthetic(value):
    if (type(value) is not dict or value.get("synthetic") is not True
            or value.get("source_authenticity") is not False
            or value.get("observed_eligible") is not False
            or value.get("execution_authorized") is not False):
        _fail("OBSERVED_HOLD")


def _provenance(value):
    _synthetic(value)
    if (set(value) != {"synthetic", "source_authenticity", "observed_eligible",
                       "execution_authorized", "rights", "retrieved_at"}
            or value["rights"] != "synthetic_only"
            or type(value["retrieved_at"]) is not str
            or len(value["retrieved_at"]) > 64):
        _fail("PROVENANCE_INVALID")
    try:
        instant = datetime.fromisoformat(value["retrieved_at"].replace("Z", "+00:00"))
        if instant.tzinfo is None or instant.utcoffset().total_seconds() != 0:
            raise ValueError
    except (ValueError, OverflowError):
        _fail("PROVENANCE_INVALID")


def _run(value):
    if type(value) is not str or not RUN.fullmatch(value):
        _fail("RUN_INVALID")


def _read_fd(fd, maximum):
    before = os.fstat(fd)
    if not stat.S_ISREG(before.st_mode) or before.st_size > maximum:
        _fail("FILE_INVALID")
    with os.fdopen(os.dup(fd), "rb") as stream:
        body = stream.read(maximum + 1)
    after = os.fstat(fd)
    if (len(body) > maximum or before.st_size != len(body)
            or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
            != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)):
        _fail("FILE_CHANGED")
    return body


def _directory(parent_fd, name):
    return os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)


def _file(parent_fd, name):
    return os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent_fd)


def _require_private(fd):
    details = os.fstat(fd)
    if details.st_uid != os.getuid() or stat.S_IMODE(details.st_mode) & 0o077:
        _fail("PUBLICATION_LAYOUT")


def load_verified_originals(archive_root, run_id, provenance):
    """Revalidate the exact two published fake originals, then read pinned inodes."""
    _provenance(provenance)
    _run(run_id)
    try:
        root = Path(archive_root)
        with ExitStack() as stack:
            root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            stack.callback(os.close, root_fd)
            objects_fd = _directory(root_fd, "objects")
            stack.callback(os.close, objects_fd)
            run_fd = _directory(objects_fd, run_id)
            stack.callback(os.close, run_fd)
            committed_fd = _directory(root_fd, "committed")
            stack.callback(os.close, committed_fd)
            receipt_fd = _file(committed_fd, run_id + ".json")
            stack.callback(os.close, receipt_fd)
            source_fds = {}
            before = {}
            for source_id in SOURCE_IDS:
                fd = _file(run_fd, source_id + ".raw")
                stack.callback(os.close, fd)
                source_fds[source_id] = fd
                before[source_id] = os.fstat(fd)
            receipt_before = os.fstat(receipt_fd)
            receipt = source_transport.verify_publication(root, run_id)
            rows = {row["source_id"]: row for row in receipt["originals"]}
            if set(rows) != set(SOURCE_IDS):
                _fail("SOURCE_INVALID")
            originals = {}
            for source_id in SOURCE_IDS:
                fd = source_fds[source_id]
                body = _read_fd(fd, MAX_BODY)
                after = os.fstat(fd)
                path_st = os.stat(source_id + ".raw", dir_fd=run_fd, follow_symlinks=False)
                for check in (after, path_st):
                    if (before[source_id].st_dev, before[source_id].st_ino,
                            before[source_id].st_size, before[source_id].st_mtime_ns,
                            before[source_id].st_ctime_ns) != (
                            check.st_dev, check.st_ino, check.st_size,
                            check.st_mtime_ns, check.st_ctime_ns):
                        _fail("FILE_CHANGED")
                if len(body) != rows[source_id]["bytes"] or _sha(body) != rows[source_id]["sha256"]:
                    _fail("SOURCE_INVALID")
                originals[source_id] = {"body": body, "bytes": len(body), "sha256": _sha(body)}
            receipt_body = _read_fd(receipt_fd, source_transport.MAX_METADATA)
            receipt_after = os.fstat(receipt_fd)
            receipt_path = os.stat(run_id + ".json", dir_fd=committed_fd, follow_symlinks=False)
            for check in (receipt_after, receipt_path):
                if (receipt_before.st_dev, receipt_before.st_ino, receipt_before.st_size,
                        receipt_before.st_mtime_ns, receipt_before.st_ctime_ns) != (
                        check.st_dev, check.st_ino, check.st_size,
                        check.st_mtime_ns, check.st_ctime_ns):
                    _fail("FILE_CHANGED")
            if json.loads(receipt_body) != receipt:
                _fail("SOURCE_INVALID")
        return {"synthetic": True, "source_authenticity": False,
                "observed_eligible": False, "execution_authorized": False,
                "archive_root": root, "run_id": run_id,
                "receipt_sha256": _sha(receipt_body), "originals": originals}
    except FxPanelError:
        raise
    except (OSError, ValueError, KeyError, TypeError, source_transport.TransportError):
        _fail("SOURCE_INVALID")


def _iso_day(token):
    if type(token) is not str or not DAY.fullmatch(token):
        _fail("DATE_INVALID")
    try:
        parsed = date.fromisoformat(token)
    except ValueError:
        _fail("DATE_INVALID")
    if parsed.isoformat() != token:
        _fail("DATE_INVALID")
    return parsed


def _utc_instant(token):
    if type(token) is not str or len(token) > 35 or not INSTANT.fullmatch(token):
        _fail("COVERAGE_INVALID")
    try:
        parsed = datetime.fromisoformat(token.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            _fail("COVERAGE_INVALID")
        return parsed.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        _fail("COVERAGE_INVALID")


def _coverage(value):
    if type(value) is not dict or set(value) != {
            "schema_version", "synthetic", "as_of", "required_months", "dates_by_month"
    } or value["schema_version"] != "synthetic-date-mask.v2" or value["synthetic"] is not True:
        _fail("COVERAGE_INVALID")
    if (type(value["dates_by_month"]) is not dict
            or any(type(key) is not str for key in value["dates_by_month"])):
        _fail("COVERAGE_INVALID")
    as_of = _utc_instant(value["as_of"])
    months = value["required_months"]
    masks = value["dates_by_month"]
    if (type(months) is not list or not 1 <= len(months) <= MAX_MONTHS
            or any(type(month) is not str for month in months)
            or type(masks) is not dict or set(months) != set(masks)
            or len(set(months)) != len(months) or months != sorted(months)):
        _fail("COVERAGE_INVALID")
    total = 0
    previous = None
    for month in months:
        if type(month) is not str or not MONTH.fullmatch(month):
            _fail("COVERAGE_INVALID")
        try:
            year, number = map(int, month.split("-"))
            date(year, number, 1)
        except ValueError:
            _fail("COVERAGE_INVALID")
        if year == 9999 and number == 12:
            _fail("COVERAGE_INVALID")
        ordinal = year * 12 + number
        if previous is not None and ordinal != previous + 1:
            _fail("COVERAGE_INVALID")
        previous = ordinal
        days = masks[month]
        if (type(days) is not list or not 1 <= len(days) <= 31
                or any(type(day) is not str for day in days)
                or days != sorted(days) or len(set(days)) != len(days)):
            _fail("COVERAGE_INVALID")
        for day in days:
            if _iso_day(day).strftime("%Y-%m") != month:
                _fail("COVERAGE_INVALID")
        total += len(days)
    if total > MAX_ROWS:
        _fail("COVERAGE_LIMIT")
    try:
        encoded = _json(value)
    except (TypeError, ValueError):
        _fail("COVERAGE_INVALID")
    if len(encoded) > 65536:
        _fail("COVERAGE_LIMIT")
    return as_of, masks


def _schema(value):
    if type(value) is not dict:
        _fail("SCHEMA_INVALID")
    try:
        if _json(value) != _json(SCHEMA):
            _fail("SCHEMA_INVALID")
    except (TypeError, ValueError):
        _fail("SCHEMA_INVALID")


def _number(token):
    if type(token) is not str or len(token) > 64 or not NUMBER.fullmatch(token):
        _fail("RATE_INVALID")
    parts = token.split(".")
    if len(parts) == 2 and len(parts[1]) > 38:
        _fail("RATE_LIMIT")
    digits = "".join(parts).lstrip("0").rstrip("0")
    if len(digits) > 32:
        _fail("RATE_LIMIT")
    number = Decimal(token)
    if not number.is_finite() or number <= 0 or abs(number.adjusted()) > 6:
        _fail("RATE_INVALID")
    rational = Fraction(number)
    _fraction_bound(rational)
    return rational


def _fraction_bound(value):
    if value.numerator.bit_length() > 4096 or value.denominator.bit_length() > 4096:
        _fail("ARITHMETIC_LIMIT")
    return value


def _parse(body, source_id, masks):
    if type(body) is not bytes or len(body) > MAX_BODY:
        _fail("SOURCE_INVALID")
    try:
        text = body.decode("utf-8", errors="strict")
    except UnicodeError:
        _fail("CSV_INVALID")
    try:
        reader = csv.reader(io.StringIO(text, newline=""), strict=True)
        if tuple(next(reader)) != HEADERS:
            _fail("CSV_HEADER")
        expected = SCHEMA["pairs"][source_id]
        allowed = {day for days in masks.values() for day in days}
        found = {}
        previous = ""
        for row in reader:
            if (len(row) != len(HEADERS) or any(len(field) > 8192 or
                    "\r" in field or "\n" in field for field in row)):
                _fail("CSV_INVALID")
            if len(found) >= MAX_ROWS:
                _fail("CSV_LIMIT")
            key, freq, currency, denom, kind, suffix, day, value, unit, mult, status = row
            if (key != expected["key"] or freq != "D" or currency != expected["currency"]
                    or denom != "EUR" or kind != "SP00" or suffix != "A"
                    or unit != "SYNTHETIC_QUOTE" or mult != "0" or status != "A"):
                _fail("CSV_METADATA")
            _iso_day(day)
            if day not in allowed or day <= previous:
                _fail("CSV_DATE_SET")
            previous = day
            found[day] = (value, _number(value))
        return found
    except (csv.Error, StopIteration):
        _fail("CSV_INVALID")


def _proxy(month):
    year, number = map(int, month.split("-"))
    next_year, next_month = (year + 1, 1) if number == 12 else (year, number + 1)
    last = calendar.monthrange(next_year, next_month)[1]
    return f"{next_year:04d}-{next_month:02d}-{last:02d}T23:59:59.999999Z"


def normalize_ecb_pair(verified_originals, approved_schema, approved_date_coverage):
    _synthetic(verified_originals)
    _schema(approved_schema)
    as_of, masks = _coverage(approved_date_coverage)
    originals = verified_originals.get("originals")
    if type(originals) is not dict or set(originals) != set(SOURCE_IDS):
        _fail("SOURCE_INVALID")
    parsed = {}
    for sid in SOURCE_IDS:
        row = originals[sid]
        if (type(row) is not dict or type(row.get("body")) is not bytes
                or type(row.get("bytes")) is not int or row["bytes"] != len(row["body"])
                or row.get("sha256") != _sha(row["body"])):
            _fail("SOURCE_INVALID")
        parsed[sid] = _parse(row["body"], sid, masks)
    months = []
    for month in approved_date_coverage["required_months"]:
        expected = masks[month]
        usd = {day for day in parsed["ecb_usd_eur"] if day.startswith(month)}
        inr = {day for day in parsed["ecb_inr_eur"] if day.startswith(month)}
        reasons = []
        if usd != inr:
            reasons.append("DATE_SET_MISMATCH")
        if usd != set(expected) or inr != set(expected):
            reasons.append("EXPECTED_DATE_MISSING")
        proxy = _proxy(month)
        if _utc_instant(proxy) > as_of:
            reasons.append("PROXY_AFTER_ASOF")
        value = None
        daily_hash = None
        tokens = None
        if not reasons:
            total = Fraction(0)
            tokens = []
            for day in expected:
                usd_token, usd_rate = parsed["ecb_usd_eur"][day]
                inr_token, inr_rate = parsed["ecb_inr_eur"][day]
                total = _fraction_bound(total + _fraction_bound(inr_rate / usd_rate))
                tokens.append([day, usd_token, inr_token])
            result = _fraction_bound(total / len(expected))
            with localcontext(Context(prec=50, rounding=ROUND_HALF_EVEN)):
                rendered = Decimal(result.numerator) / Decimal(result.denominator)
            if not rendered.is_finite() or rendered <= 0:
                _fail("ARITHMETIC_INVALID")
            value = format(rendered, "f")
            if Decimal(value) == 0:
                _fail("ARITHMETIC_INVALID")
            daily_hash = _sha(_json(tokens))
        months.append({"month": month, "status": "complete" if not reasons else "unavailable",
                       "value": value, "unit": "INR/USD", "matched_days": len(usd & inr),
                       "expected_days": len(expected), "first_date": expected[0],
                       "last_date": expected[-1], "available_at_proxy": proxy,
                       "daily_basis_sha256": daily_hash, "daily_tokens": tokens,
                       "arithmetic_version": "daily_cross_fraction_decimal_v1",
                       "rounding_version": "decimal50_half_even_plain_v1",
                       "reasons": reasons})
    return {"synthetic": True, "source_authenticity": False, "observed_eligible": False,
            "execution_authorized": False, "status": "SYNTHETIC_OFFLINE_ONLY",
            "verified_originals": verified_originals,
            "schema_sha256": _sha(_json(SCHEMA)),
            "coverage_sha256": _sha(_json(approved_date_coverage)),
            "schema": SCHEMA, "coverage": approved_date_coverage, "months": months}


def _publish_incomplete_disqualifier(root_fd, run_id):
    """Create independent failure evidence; never delete any publication marker."""
    name = run_id + ".json"
    body = _json({"status": "SYNTHETIC_OFFLINE_INCOMPLETE",
                  "run_id": run_id, "synthetic": True,
                  "execution_authorized": False,
                  "reason": "POSTLINK_FINALIZATION_FAILED"})
    try:
        incomplete_fd = _directory(root_fd, "incomplete")
        try:
            fd = os.open(name, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600, dir_fd=incomplete_fd)
            try:
                created = os.fstat(fd)
                if not stat.S_ISREG(created.st_mode):
                    _fail("PUBLICATION_CLEANUP_UNRESOLVED")
                remaining = memoryview(body)
                while remaining:
                    count = os.write(fd, remaining)
                    if count <= 0:
                        _fail("PUBLICATION_CLEANUP_UNRESOLVED")
                    remaining = remaining[count:]
                os.fsync(fd)
                os.lseek(fd, 0, os.SEEK_SET)
                if _read_fd(fd, 4096) != body:
                    _fail("PUBLICATION_CLEANUP_UNRESOLVED")
                for entry in (os.fstat(fd),
                              os.stat(name, dir_fd=incomplete_fd, follow_symlinks=False)):
                    if (entry.st_dev, entry.st_ino, entry.st_size) != (
                            created.st_dev, created.st_ino, len(body)):
                        _fail("PUBLICATION_CLEANUP_UNRESOLVED")
                os.fsync(incomplete_fd)
                final = os.stat(name, dir_fd=incomplete_fd, follow_symlinks=False)
                if (final.st_dev, final.st_ino, final.st_size) != (
                        created.st_dev, created.st_ino, len(body)):
                    _fail("PUBLICATION_CLEANUP_UNRESOLVED")
            finally:
                os.close(fd)
        finally:
            os.close(incomplete_fd)
    except BaseException:
        _fail("PUBLICATION_CLEANUP_UNRESOLVED")


def publish_fx_panel(candidate, provenance_decision, output_root, run_id):
    _provenance(provenance_decision)
    _run(run_id)
    _synthetic(candidate)
    pair = candidate.get("verified_originals")
    _synthetic(pair)
    _schema(candidate.get("schema"))
    _coverage(candidate.get("coverage"))
    fresh = load_verified_originals(pair["archive_root"], pair["run_id"], provenance_decision)
    if fresh != pair or normalize_ecb_pair(fresh, SCHEMA, candidate["coverage"]) != candidate:
        _fail("CANDIDATE_CHANGED")
    if _sha(Path(__file__).read_bytes()) != CODE_SHA256:
        _fail("CODE_CHANGED")
    months = candidate["months"]
    lines = io.StringIO(newline="")
    writer = csv.writer(lines, lineterminator="\n")
    writer.writerow(("month", "status", "value", "unit", "matched_days", "expected_days",
                     "first_date", "last_date", "available_at_proxy",
                     "daily_basis_sha256", "reasons"))
    for row in months:
        writer.writerow((row["month"], row["status"], row["value"] or "", row["unit"],
                         row["matched_days"], row["expected_days"], row["first_date"],
                         row["last_date"], row["available_at_proxy"],
                         row["daily_basis_sha256"] or "", ";".join(row["reasons"])))
    panel = lines.getvalue().encode("utf-8")
    complete = all(row["status"] == "complete" for row in months)
    status = "SYNTHETIC_OFFLINE_ONLY" if complete else "SYNTHETIC_OFFLINE_INCOMPLETE"
    manifest = {
        "status": status, "run_id": run_id, "synthetic": True,
        "source_authenticity": False, "observed_eligible": False,
        "execution_authorized": False, "rights": "synthetic_only",
        "synthetic_fixture_timestamp": provenance_decision["retrieved_at"],
        "source_run_id": pair["run_id"], "source_receipt_sha256": pair["receipt_sha256"],
        "source_originals": {sid: {"bytes": pair["originals"][sid]["bytes"],
                                   "sha256": pair["originals"][sid]["sha256"]} for sid in SOURCE_IDS},
        "schema_sha256": candidate["schema_sha256"],
        "coverage_sha256": candidate["coverage_sha256"],
        "code_sha256": CODE_SHA256, "panel_sha256": _sha(panel),
        "panel_bytes": len(panel), "month_count": len(months),
        "availability_basis": "end_of_following_month_utc_proxy_not_pit",
        "arithmetic_version": "daily_cross_fraction_decimal_v1",
        "rounding_version": "decimal50_half_even_plain_v1",
    }
    manifest_body = _json(manifest)
    try:
        root = Path(output_root)
        root.mkdir(mode=0o700)
    except FileExistsError:
        pass
    except OSError:
        _fail("PUBLICATION_IO")
    try:
        root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            _require_private(root_fd)
            for name in ("objects", "complete", "incomplete"):
                try:
                    os.mkdir(name, 0o700, dir_fd=root_fd)
                except FileExistsError:
                    pass
                fd = _directory(root_fd, name)
                try:
                    _require_private(fd)
                finally:
                    os.close(fd)
            objects_fd = _directory(root_fd, "objects")
            try:
                # An existing object, including a failed attempt, is never replaced.
                os.mkdir(run_id, 0o700, dir_fd=objects_fd)
                object_fd = _directory(objects_fd, run_id)
                try:
                    _require_private(object_fd)
                    for name, body in (("panel.csv", panel), ("manifest.json", manifest_body)):
                        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                     0o600, dir_fd=object_fd)
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
                        fd = _file(object_fd, name)
                        try:
                            if _read_fd(fd, MAX_BODY) != body:
                                _fail("PUBLICATION_IO")
                        finally:
                            os.close(fd)
                    os.fsync(object_fd)
                finally:
                    os.close(object_fd)
                os.fsync(objects_fd)
            finally:
                os.close(objects_fd)
            os.fsync(root_fd)
            staged = _verify_panel(root, run_id, fresh, SCHEMA, candidate["coverage"],
                                   require_marker=False, expected_complete=complete)
            if staged != manifest:
                _fail("PUBLICATION_INVALID")
            marker_name = "complete" if complete else "incomplete"
            marker_fd = _directory(root_fd, marker_name)
            try:
                linked = False
                try:
                    os.link(f"objects/{run_id}/manifest.json", run_id + ".json",
                            src_dir_fd=root_fd, dst_dir_fd=marker_fd, follow_symlinks=False)
                    linked = True
                    os.fsync(marker_fd)
                    if complete:
                        verified = verify_fx_panel(root, run_id, fresh, SCHEMA,
                                                   candidate["coverage"])
                        if verified != manifest:
                            _fail("PUBLICATION_INVALID")
                except BaseException:
                    if linked:
                        if marker_name != "complete":
                            _fail("PUBLICATION_CLEANUP_UNRESOLVED")
                        _publish_incomplete_disqualifier(root_fd, run_id)
                        _fail("PUBLICATION_INCOMPLETE")
                    raise
            finally:
                os.close(marker_fd)
        finally:
            os.close(root_fd)
    except FileExistsError:
        _fail("PUBLICATION_EXISTS")
    except FxPanelError:
        raise
    except OSError:
        _fail("PUBLICATION_IO")
    return manifest


def verify_fx_panel(output_root, run_id, verified_originals, approved_schema, approved_date_coverage):
    return _verify_panel(output_root, run_id, verified_originals, approved_schema,
                         approved_date_coverage, require_marker=True, expected_complete=True)


def _verify_panel(output_root, run_id, verified_originals, approved_schema,
                  approved_date_coverage, *, require_marker, expected_complete):
    _run(run_id)
    _synthetic(verified_originals)
    _schema(approved_schema)
    _coverage(approved_date_coverage)
    if _sha(Path(__file__).read_bytes()) != CODE_SHA256:
        _fail("CODE_CHANGED")
    try:
        root = Path(output_root)
        root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            marker = None
            marker_stat = None
            for marker_kind in ("incomplete", "complete"):
                marker_dir_fd = _directory(root_fd, marker_kind)
                try:
                    try:
                        marker_path_stat = os.stat(run_id + ".json", dir_fd=marker_dir_fd,
                                                   follow_symlinks=False)
                    except FileNotFoundError:
                        if require_marker and marker_kind == "complete":
                            _fail("PUBLICATION_INVALID")
                        continue
                    if marker_kind == "incomplete" or not require_marker:
                        _fail("PUBLICATION_INCOMPLETE")
                    marker_fd = _file(marker_dir_fd, run_id + ".json")
                    try:
                        marker_stat = os.fstat(marker_fd)
                        if (marker_stat.st_dev, marker_stat.st_ino) != (
                                marker_path_stat.st_dev, marker_path_stat.st_ino):
                            _fail("PUBLICATION_INVALID")
                        marker = _read_fd(marker_fd, 65536)
                    finally:
                        os.close(marker_fd)
                finally:
                    os.close(marker_dir_fd)
            objects_fd = _directory(root_fd, "objects")
            try:
                object_fd = _directory(objects_fd, run_id)
                try:
                    manifest_fd = _file(object_fd, "manifest.json")
                    try:
                        if require_marker and (
                                os.fstat(manifest_fd).st_dev, os.fstat(manifest_fd).st_ino) != (
                                marker_stat.st_dev, marker_stat.st_ino):
                            _fail("PUBLICATION_INVALID")
                        manifest_body = _read_fd(manifest_fd, 65536)
                        if require_marker and manifest_body != marker:
                            _fail("PUBLICATION_INVALID")
                    finally:
                        os.close(manifest_fd)
                    panel_fd = _file(object_fd, "panel.csv")
                    try:
                        panel = _read_fd(panel_fd, MAX_BODY)
                    finally:
                        os.close(panel_fd)
                finally:
                    os.close(object_fd)
            finally:
                os.close(objects_fd)
        finally:
            os.close(root_fd)
        manifest = json.loads(manifest_body)
        if type(manifest) is not dict or _json(manifest) != manifest_body:
            _fail("PUBLICATION_INVALID")
        fresh = load_verified_originals(verified_originals["archive_root"],
                                        verified_originals["run_id"],
                                        {"synthetic": True, "source_authenticity": False,
                                         "observed_eligible": False, "execution_authorized": False,
                                         "rights": "synthetic_only",
                                         "retrieved_at": manifest["synthetic_fixture_timestamp"]})
        if fresh != verified_originals:
            _fail("SOURCE_INVALID")
        recomputed = normalize_ecb_pair(fresh, SCHEMA, approved_date_coverage)
        expected_lines = io.StringIO(newline="")
        writer = csv.writer(expected_lines, lineterminator="\n")
        writer.writerow(("month", "status", "value", "unit", "matched_days", "expected_days",
                         "first_date", "last_date", "available_at_proxy",
                         "daily_basis_sha256", "reasons"))
        for row in recomputed["months"]:
            writer.writerow((row["month"], row["status"], row["value"] or "", row["unit"],
                             row["matched_days"], row["expected_days"], row["first_date"],
                             row["last_date"], row["available_at_proxy"],
                             row["daily_basis_sha256"] or "", ";".join(row["reasons"])))
        if panel != expected_lines.getvalue().encode("utf-8"):
            _fail("PUBLICATION_INVALID")
        if (manifest.get("status") != ("SYNTHETIC_OFFLINE_ONLY" if expected_complete
                                     else "SYNTHETIC_OFFLINE_INCOMPLETE")
                or all(row["status"] == "complete" for row in recomputed["months"])
                    is not expected_complete
                or manifest.get("synthetic") is not True
                or manifest.get("source_authenticity") is not False
                or manifest.get("observed_eligible") is not False
                or manifest.get("execution_authorized") is not False
                or manifest.get("rights") != "synthetic_only"
                or manifest.get("run_id") != run_id
                or manifest.get("source_run_id") != fresh["run_id"]
                or manifest.get("source_receipt_sha256") != fresh["receipt_sha256"]
                or manifest.get("source_originals") != {
                    sid: {"bytes": fresh["originals"][sid]["bytes"],
                          "sha256": fresh["originals"][sid]["sha256"]} for sid in SOURCE_IDS}
                or manifest.get("schema_sha256") != _sha(_json(SCHEMA))
                or manifest.get("coverage_sha256") != _sha(_json(approved_date_coverage))
                or manifest.get("code_sha256") != CODE_SHA256
                or manifest.get("panel_sha256") != _sha(panel)
                or manifest.get("panel_bytes") != len(panel)
                or manifest.get("month_count") != len(recomputed["months"])
                or manifest.get("availability_basis") !=
                    "end_of_following_month_utc_proxy_not_pit"
                or manifest.get("arithmetic_version") != "daily_cross_fraction_decimal_v1"
                or manifest.get("rounding_version") != "decimal50_half_even_plain_v1"):
            _fail("PUBLICATION_INVALID")
        return manifest
    except FxPanelError:
        raise
    except (OSError, ValueError, TypeError, KeyError, UnicodeError):
        _fail("PUBLICATION_INVALID")
