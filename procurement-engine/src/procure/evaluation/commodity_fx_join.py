"""Synthetic-only offline composition. No acquisition, runner, or model entry point.

Integrity verification is not source authentication/rights/PIT evidence. Private
files are checked against anchored identities, not contained against same-UID code.
"""
from __future__ import annotations

import copy
import calendar
import csv
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Context, Decimal, ROUND_HALF_EVEN, localcontext
from fractions import Fraction
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat

from . import commodity_component as commodity
from . import fx_normalization as fx

MAX_BODY = 4 * 1024 * 1024
MAX_META = 65536
MAX_BITS = 4096
FLAGS = {"synthetic": True, "source_authenticity": False, "observed_eligible": False,
         "execution_authorized": False, "rights": "synthetic_only"}
COMPLETE = "SYNTHETIC_OFFLINE_ONLY"
INCOMPLETE = "SYNTHETIC_OFFLINE_INCOMPLETE"
ARITHMETIC = "component_fraction_product_v1"
ROUNDING = "decimal50_half_even_normalized_plain_v1"
AVAILABILITY = "end_of_following_month_utc_proxy_not_pit"
TRUSTED = {
    "commodity": "8196147a7dfa626efbef04990763c52a85eaa214829c493947f7b436d4ce2763",
    "fx": "872c9ff5b0b614e136f27a87ae62cc24c07aa83a1953a7326aaf3968c79a71c6",
    "transport": "f41ed298ffc938d38749413168dfbed080b7a9d881ae54f642ec7275e3c8e060",
}
CODE_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
RUN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
HASH = re.compile(r"[0-9a-f]{64}\Z")
INSTANT = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
                     r"(?:\.[0-9]{1,6})?(?:Z|[+-][0-9]{2}:[0-9]{2})\Z")
POLICY_KEYS = {"schema_version", *FLAGS, "series_ids", "required_months", "as_of",
               "commodity_schema_sha256", "fx_schema_sha256", "fx_coverage_sha256",
               "arithmetic_version", "rounding_version"}
COMMON_BINDING = {"original_root", "panel_root", "panel_run_id", "panel_manifest_sha256",
                  "panel_sha256", "panel_bytes", "schema_sha256", "code_sha256"}
COMMODITY_BINDING = COMMON_BINDING | {"fixture_run_id", "fixture_receipt_sha256",
                                     "original_sha256", "original_bytes"}
FX_BINDING = COMMON_BINDING | {"source_run_id", "provenance", "source_receipt_sha256",
                              "source_originals", "coverage_sha256", "coverage"}


class JoinError(ValueError):
    """Fixed sanitized code; never source tokens, paths, or OS errors."""


def _fail(code):
    raise JoinError(code)


def _sha(body):
    return hashlib.sha256(body).hexdigest()


def _json(value, maximum=MAX_BODY):
    # Bound traversal before encoding: all wire numbers are integer counts or strings.
    remaining = 60000
    def visit(v, depth=0):
        nonlocal remaining
        remaining -= 1
        if remaining < 0 or depth > 12:
            _fail("BINDING_INVALID")
        if type(v) is dict:
            if any(type(k) is not str or len(k) > 128 for k in v):_fail("BINDING_INVALID")
            for item in v.values():visit(item, depth + 1)
        elif type(v) is list:
            for item in v:visit(item, depth + 1)
        elif type(v) is str:
            if len(v) > 8192:_fail("BINDING_INVALID")
        elif type(v) is int:
            if v.bit_length() > 32:_fail("BINDING_INVALID")
        elif v is not None and type(v) is not bool:
            _fail("BINDING_INVALID")
    visit(value)
    body = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                      allow_nan=False).encode("ascii")
    if len(body) > maximum:_fail("BINDING_INVALID")
    return body


def _decode(body, maximum=MAX_META):
    if type(body) is not bytes or len(body) > maximum:_fail("PUBLICATION_INVALID")
    def pairs(items):
        out = {}
        for key, value in items:
            if key in out:_fail("PUBLICATION_INVALID")
            out[key] = value
        return out
    try:
        value = json.loads(body, object_pairs_hook=pairs,
                           parse_constant=lambda _: _fail("PUBLICATION_INVALID"),
                           parse_float=lambda _: _fail("PUBLICATION_INVALID"))
        if _json(value, maximum) != body:_fail("PUBLICATION_INVALID")
        return value
    except (ValueError, TypeError, UnicodeError, RecursionError):
        _fail("PUBLICATION_INVALID")


def _exact(value, keys):
    if type(value) is not dict or set(value) != keys:_fail("BINDING_INVALID")


def _flags(value):
    if type(value) is not dict or any(type(value.get(k)) is not type(v) or value.get(k) != v
                                     for k, v in FLAGS.items()):_fail("OBSERVED_HOLD")


def _instant(token):
    if type(token) is not str or len(token) > 35 or not INSTANT.fullmatch(token):_fail("AS_OF_INVALID")
    try:
        parsed = datetime.fromisoformat(token.replace("Z", "+00:00"))
        if parsed.tzinfo is None:_fail("AS_OF_INVALID")
        return parsed.astimezone(timezone.utc)
    except (ValueError, OverflowError):_fail("AS_OF_INVALID")


def _canonical_instant(token):
    parsed = _instant(token)
    if parsed.isoformat(timespec="microseconds").replace("+00:00", "Z") != token:_fail("AS_OF_INVALID")
    return parsed


def _run(token):
    if type(token) is not str or not RUN.fullmatch(token):_fail("BINDING_INVALID")


def _path(value):
    try:token = os.fspath(value)
    except TypeError:_fail("BINDING_INVALID")
    if (type(token) is not str or len(token) > 4096 or not token.startswith("/")
            or any(p in ("", ".", "..") for p in token.split("/")[1:])):_fail("BINDING_INVALID")
    return token


def _policy(value):
    _flags(value)
    _exact(value, POLICY_KEYS)
    if value["schema_version"] != "synthetic-commodity-fx-join.v1":_fail("BINDING_INVALID")
    if type(value["series_ids"]) is not list or value["series_ids"] != ["copper", "aluminum"]:_fail("UNIT_HOLD")
    if value["arithmetic_version"] != ARITHMETIC or value["rounding_version"] != ROUNDING:_fail("BINDING_INVALID")
    _canonical_instant(value["as_of"])
    months = value["required_months"]
    if type(months) is not list or not 1 <= len(months) <= 240:_fail("MASK_MISMATCH")
    previous = None
    for month in months:
        if type(month) is not str or not re.fullmatch(r"[0-9]{4}-(?:0[1-9]|1[0-2])", month):_fail("MASK_MISMATCH")
        year, number = map(int, month.split("-"))
        if not 1 <= year <= 9998:_fail("MASK_MISMATCH")
        ordinal = year * 12 + number
        if previous is not None and ordinal != previous + 1:_fail("MASK_MISMATCH")
        previous = ordinal
    if value["commodity_schema_sha256"] != _sha(_json(commodity.SCHEMA)) or value["fx_schema_sha256"] != _sha(_json(fx.SCHEMA)):_fail("BINDING_INVALID")
    _json(value, MAX_META)


def _bindings(bindings, policy):
    _policy(policy)
    _exact(bindings, {"commodity", "fx"})
    for kind, keys in (("commodity", COMMODITY_BINDING), ("fx", FX_BINDING)):
        row = bindings[kind]
        if kind == "fx":_flags(row.get("provenance") if type(row) is dict else None)
        _exact(row, keys)
        for key, val in row.items():
            if key.endswith("sha256"):
                if type(val) is not str or not HASH.fullmatch(val):_fail("BINDING_INVALID")
            elif key.endswith("bytes"):
                if type(val) is not int or not 1 <= val <= MAX_BODY:_fail("BINDING_INVALID")
            elif key.endswith("run_id"):_run(val)
            elif key.endswith("root"):_path(val)
        if row["code_sha256"] != TRUSTED[kind]:_fail("CODE_CHANGED")
        if row["schema_sha256"] != policy[kind + "_schema_sha256"]:_fail("BINDING_INVALID")
    row = bindings["fx"]
    _exact(row["provenance"], {*FLAGS, "retrieved_at"})
    if _instant(row["provenance"]["retrieved_at"]).utcoffset().total_seconds() != 0:_fail("BINDING_INVALID")
    _exact(row["source_originals"], {"ecb_usd_eur", "ecb_inr_eur"})
    for source in row["source_originals"].values():
        _exact(source, {"bytes", "sha256"})
        if type(source["bytes"]) is not int or not 1 <= source["bytes"] <= MAX_BODY:_fail("BINDING_INVALID")
        if type(source["sha256"]) is not str or not HASH.fullmatch(source["sha256"]):_fail("BINDING_INVALID")
    coverage = row["coverage"]
    _exact(coverage, {"schema_version", "synthetic", "as_of", "required_months", "dates_by_month"})
    if coverage["synthetic"] is not True:_fail("OBSERVED_HOLD")
    if coverage["required_months"] != policy["required_months"]:_fail("MASK_MISMATCH")
    if _instant(coverage["as_of"]) != _instant(policy["as_of"]):_fail("AS_OF_MISMATCH")
    if row["coverage_sha256"] != policy["fx_coverage_sha256"] or row["coverage_sha256"] != _sha(_json(coverage, MAX_META)):_fail("BINDING_INVALID")
    _json(bindings)


def _directory_identity(s):
    return (s.st_dev, s.st_ino, s.st_mode, s.st_uid)


def _file_identity(s):
    return (*_directory_identity(s), s.st_size, s.st_mtime_ns, s.st_ctime_ns)


def _private(s):
    if s.st_uid != os.getuid() or stat.S_IMODE(s.st_mode) & 0o077:_fail("BINDING_INVALID")


class _Guard:
    """Keep each ancestor/child descriptor and each checked path binding alive."""
    def __init__(self):
        self.records = []
        self.absences = []
        self.closed = False

    def root(self, path, *, private=True, create=False):
        path = _path(path)
        fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        self.records.append((None, None, fd, "/", _directory_identity(os.fstat(fd)), True))
        walked = ""
        parts = path.split("/")[1:]
        for index, part in enumerate(parts):
            if create and index == len(parts) - 1:
                try:os.mkdir(part, 0o700, dir_fd=fd)
                except FileExistsError:pass
            walked += "/" + part
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            details = os.fstat(child)
            self.records.append((fd, part, child, walked, _directory_identity(details), True))
            fd = child
        if private:_private(os.fstat(fd))
        return fd

    def directory(self, parent, name, *, create=False, exclusive=False):
        if create:
            try:os.mkdir(name, 0o700, dir_fd=parent)
            except FileExistsError:
                if exclusive:raise
        fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        details = os.fstat(fd)
        parent_path = next(r[3] for r in self.records if r[2] == parent)
        self.records.append((parent, name, fd, parent_path + "/" + name, _directory_identity(details), True))
        _private(details)
        return fd

    def read(self, parent, name, maximum=MAX_BODY, *, private=True):
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        details = os.fstat(fd)
        parent_path = next(r[3] for r in self.records if r[2] == parent)
        self.records.append((parent, name, fd, parent_path + "/" + name, _file_identity(details), False))
        if not stat.S_ISREG(details.st_mode) or not 0 <= details.st_size <= maximum:_fail("BINDING_INVALID")
        if private:_private(details)
        chunks, left = [], maximum + 1
        while left:
            part = os.read(fd, min(left, 65536))
            if not part:break
            chunks.append(part); left -= len(part)
        body = b"".join(chunks)
        if len(body) != details.st_size or len(body) > maximum:_fail("SOURCE_CHANGED")
        self.check()
        return body, details

    def absent(self, parent, name, code):
        try:os.stat(name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:pass
        else:_fail(code)
        self.absences.append((parent, name, code))

    def check(self):
        for parent, name, fd, path, identity, directory in self.records:
            key = _directory_identity if directory else _file_identity
            if key(os.fstat(fd)) != identity:_fail("SOURCE_CHANGED")
            current = os.stat(name, dir_fd=parent, follow_symlinks=False) if parent is not None else os.stat(path, follow_symlinks=False)
            if key(current) != identity:_fail("SOURCE_CHANGED")
        for parent, name, code in self.absences:
            try:os.stat(name, dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:continue
            _fail(code)

    def close(self):
        if self.closed:return
        self.closed = True
        error = None
        for _, _, fd, _, _, _ in reversed(self.records):
            try:os.close(fd)
            except BaseException as exc:
                if error is None:error = exc
        # Path identity must survive descriptor closing too.
        for _, _, _, path, identity, directory in self.records:
            try:
                key = _directory_identity if directory else _file_identity
                if key(os.stat(path, follow_symlinks=False)) != identity:_fail("SOURCE_CHANGED")
            except BaseException as exc:
                if error is None:error = exc
        for parent, name, code in self.absences:
            path = next(r[3] for r in self.records if r[2] == parent) + "/" + name
            try:
                try:os.stat(path, follow_symlinks=False)
                except FileNotFoundError:continue
                _fail(code)
            except BaseException as exc:
                if error is None:error = exc
        if error is not None:raise error


def _code():
    guard = _Guard()
    try:
        fd = guard.root(str(Path(__file__).parent), private=False)
        for kind, name in (("commodity", "commodity_component.py"), ("fx", "fx_normalization.py"),
                           ("transport", "source_transport.py"), ("join", "commodity_fx_join.py")):
            raw, _ = guard.read(fd, name, private=False)
            if _sha(raw) != (CODE_SHA256 if kind == "join" else TRUSTED[kind]):_fail("CODE_CHANGED")
        guard.check()
    finally:guard.close()
    return {**TRUSTED, "join": CODE_SHA256}


def _csv(header, rows):
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(header); writer.writerows(rows)
    return stream.getvalue().encode("utf-8")


def _commodity_artifacts(candidate, verified, run):
    rows = []
    for r in candidate["months"]:
        n, k = r["native_basis"], r["kg_basis"]
        rows.append([r["month"], r["series_id"], r["status"], r["reason"] or "",
                     r["native_value"] or "", r["native_unit"], r["usd_per_kg"] or "", r["derived_unit"],
                     r["available_at_proxy"], n["numerator"] if n else "", n["denominator"] if n else "",
                     k["numerator"] if k else "", k["denominator"] if k else ""])
    panel = _csv(["month", "series_id", "status", "reason", "native_value", "native_unit", "usd_per_kg",
                  "derived_unit", "available_at_proxy", "native_numerator", "native_denominator", "kg_numerator", "kg_denominator"], rows)
    manifest = {"schema_version": "synthetic-commodity-panel-receipt.v1", **FLAGS,
                "status": candidate["status"], "run_id": run, "fixture_run_id": verified["run_id"],
                "fixture_receipt_sha256": verified["receipt_sha256"], "original_sha256": verified["original_sha256"],
                "original_bytes": verified["original_bytes"], "schema_sha256": candidate["schema_sha256"],
                "mask_sha256": candidate["mask_sha256"], "as_of": candidate["as_of"], "code_sha256": TRUSTED["commodity"],
                "panel_sha256": _sha(panel), "panel_bytes": len(panel), "month_count": len(candidate["required_months"]),
                "availability_basis": candidate["availability_basis"], "arithmetic_version": candidate["arithmetic_version"]}
    return panel, manifest


def _fx_artifacts(candidate, verified, provenance, run):
    panel = _csv(["month", "status", "value", "unit", "matched_days", "expected_days", "first_date",
                  "last_date", "available_at_proxy", "daily_basis_sha256", "reasons"],
                 [[r["month"], r["status"], r["value"] or "", r["unit"], r["matched_days"], r["expected_days"],
                   r["first_date"], r["last_date"], r["available_at_proxy"], r["daily_basis_sha256"] or "",
                   ";".join(r["reasons"])] for r in candidate["months"]])
    status = COMPLETE if all(r["status"] == "complete" for r in candidate["months"]) else INCOMPLETE
    manifest = {"status": status, "run_id": run, **FLAGS,
                "synthetic_fixture_timestamp": provenance["retrieved_at"], "source_run_id": verified["run_id"],
                "source_receipt_sha256": verified["receipt_sha256"],
                "source_originals": {sid: {"bytes": r["bytes"], "sha256": r["sha256"]} for sid, r in verified["originals"].items()},
                "schema_sha256": candidate["schema_sha256"], "coverage_sha256": candidate["coverage_sha256"],
                "code_sha256": TRUSTED["fx"], "panel_sha256": _sha(panel), "panel_bytes": len(panel),
                "month_count": len(candidate["months"]), "availability_basis": AVAILABILITY,
                "arithmetic_version": "daily_cross_fraction_decimal_v1", "rounding_version": "decimal50_half_even_plain_v1"}
    return panel, manifest


def _pin_original(guard, kind, binding):
    root = guard.root(binding["original_root"])
    objects = guard.directory(root, "objects")
    run = binding["fixture_run_id"] if kind == "commodity" else binding["source_run_id"]
    obj = guard.directory(objects, run)
    committed = guard.directory(root, "committed")
    receipt, _ = guard.read(committed, run + ".json", MAX_META)
    if _sha(receipt) != binding["fixture_receipt_sha256" if kind == "commodity" else "source_receipt_sha256"]:_fail("SOURCE_CHANGED")
    metadata = _decode(receipt)
    if kind == "commodity":
        _flags(metadata)
        _exact(metadata, {"schema_version", *FLAGS, "run_id", "first_month", "last_month", "month_count", "original_bytes", "original_sha256"})
        if (metadata["schema_version"] != "synthetic-commodity-fixture-receipt.v1" or metadata["run_id"] != run
                or type(metadata["month_count"]) is not int or not 1 <= metadata["month_count"] <= 240
                or type(metadata["original_bytes"]) is not int or metadata["original_bytes"] != binding["original_bytes"]
                or metadata["original_sha256"] != binding["original_sha256"]):_fail("SOURCE_CHANGED")
        raw, _ = guard.read(obj, "original.csv")
        if len(raw) != binding["original_bytes"] or _sha(raw) != binding["original_sha256"]:_fail("SOURCE_CHANGED")
    else:
        if type(metadata) is not dict or metadata.get("offline_only") is not True or metadata.get("execution_authorized") is not False:_fail("OBSERVED_HOLD")
        _exact(metadata, {"schema_version", "run_id", "status", "offline_only", "execution_authorized", "originals", "settled", "capture_verified"})
        expected = [{"source_id": sid, "path": f"objects/{run}/{sid}.raw", **binding["source_originals"][sid]}
                    for sid in sorted(binding["source_originals"])]
        if (metadata["schema_version"] != "offline-source-publication.v1" or metadata["run_id"] != run
                or metadata["status"] != "OFFLINE_COMPLETE" or metadata["settled"] is not True
                or metadata["capture_verified"] is not True or _json(metadata["originals"]) != _json(expected)):_fail("SOURCE_CHANGED")
        incomplete = guard.directory(root, "incomplete")
        guard.absent(incomplete, run + ".json", "COMPONENT_DISQUALIFIED")
        for sid, expected in binding["source_originals"].items():
            raw, _ = guard.read(obj, sid + ".raw")
            if len(raw) != expected["bytes"] or _sha(raw) != expected["sha256"]:_fail("SOURCE_CHANGED")
    return receipt


def _pin_panel(guard, binding):
    root = guard.root(binding["panel_root"])
    objects = guard.directory(root, "objects")
    obj = guard.directory(objects, binding["panel_run_id"])
    raw_manifest, mstat = guard.read(obj, "manifest.json", MAX_META)
    if _sha(raw_manifest) != binding["panel_manifest_sha256"]:_fail("COMPONENT_INVALID")
    manifest = _decode(raw_manifest)
    _flags(manifest)
    raw_panel, _ = guard.read(obj, "panel.csv")
    if _sha(raw_panel) != binding["panel_sha256"] or len(raw_panel) != binding["panel_bytes"]:_fail("COMPONENT_INVALID")
    complete = manifest.get("status") == COMPLETE
    if not complete and manifest.get("status") != INCOMPLETE:_fail("COMPONENT_INVALID")
    positive = guard.directory(root, "complete")
    negative = guard.directory(root, "incomplete")
    guard.absent(negative if complete else positive, binding["panel_run_id"] + ".json", "COMPONENT_DISQUALIFIED")
    marker, s = guard.read(positive if complete else negative, binding["panel_run_id"] + ".json", MAX_META)
    if marker != raw_manifest or (s.st_dev, s.st_ino) != (mstat.st_dev, mstat.st_ino):_fail("COMPONENT_INVALID")
    return raw_panel, raw_manifest, manifest


def _bounded(value, *, zero=False):
    if (value < 0 or (not zero and value == 0) or value.numerator.bit_length() > MAX_BITS
            or value.denominator.bit_length() > MAX_BITS):_fail("ARITHMETIC_LIMIT")
    return value


def _basis(value):
    return {"numerator": str(value.numerator), "denominator": str(value.denominator)}


def _fraction(basis):
    _exact(basis, {"numerator", "denominator"})
    for val in basis.values():
        if type(val) is not str or len(val) > 1234 or not re.fullmatch(r"[1-9][0-9]*", val):_fail("ARITHMETIC_LIMIT")
    value = _bounded(Fraction(int(basis["numerator"]), int(basis["denominator"])))
    if _basis(value) != basis:_fail("ARITHMETIC_LIMIT")
    return value


def _render(value):
    _bounded(value)
    with localcontext(Context(prec=50, rounding=ROUND_HALF_EVEN)):
        rendered = Decimal(value.numerator) / Decimal(value.denominator)
        if not rendered.is_finite() or rendered <= 0:_fail("ARITHMETIC_LIMIT")
        return format(rendered.normalize(), "f")


@dataclass(frozen=True)
class VerifiedJoinInputs:
    """Owned copied immutable bytes; never an authorization/permission assertion."""
    policy: bytes
    payload: bytes


def _snapshot(payload, policy):
    """A Python dataclass is not evidence that its reconstructed payload is valid."""
    _exact(payload, {"rows", "component_identities", "component_statuses"})
    identities, statuses = payload["component_identities"], payload["component_statuses"]
    _exact(identities, {"commodity", "fx"})
    _exact(statuses, {"commodity_USD", "FX_INR_USD"})
    ckeys = {"fixture_run_id", "panel_run_id", "fixture_receipt_sha256", "original_sha256", "original_bytes",
             "panel_manifest_sha256", "panel_manifest_bytes", "panel_sha256", "panel_bytes", "schema_sha256",
             "mask_sha256", "as_of", "code_sha256", "arithmetic_version", "availability_basis", "rights"}
    fkeys = {"source_run_id", "panel_run_id", "source_receipt_sha256", "source_originals", "panel_manifest_sha256",
             "panel_manifest_bytes", "panel_sha256", "panel_bytes", "schema_sha256", "coverage_sha256", "as_of",
             "code_sha256", "arithmetic_version", "rounding_version", "synthetic_fixture_timestamp", "availability_basis", "rights"}
    for kind, keys in (("commodity", ckeys), ("fx", fkeys)):
        identity = identities[kind]
        _exact(identity, keys)
        for key, value in identity.items():
            if key.endswith("sha256"):
                if type(value) is not str or not HASH.fullmatch(value):_fail("BINDING_INVALID")
            elif key.endswith("bytes"):
                if type(value) is not int or not 1 <= value <= (MAX_META if key == "panel_manifest_bytes" else MAX_BODY):_fail("BINDING_INVALID")
            elif key.endswith("run_id"):_run(value)
        if identity["code_sha256"] != TRUSTED[kind]:_fail("CODE_CHANGED")
        if identity["schema_sha256"] != policy[kind + "_schema_sha256"]:_fail("BINDING_INVALID")
        if identity["rights"] != "synthetic_only":_fail("OBSERVED_HOLD")
        if identity["availability_basis"] != AVAILABILITY:_fail("BINDING_INVALID")
        if _instant(identity["as_of"]) != _instant(policy["as_of"]):_fail("AS_OF_MISMATCH")
    if identities["commodity"]["mask_sha256"] != _sha(_json(policy["required_months"])):_fail("MASK_MISMATCH")
    if identities["fx"]["coverage_sha256"] != policy["fx_coverage_sha256"]:_fail("BINDING_INVALID")
    if identities["commodity"]["arithmetic_version"] != "native_fraction_div1000_decimal50_half_even_v1":_fail("BINDING_INVALID")
    if identities["fx"]["arithmetic_version"] != "daily_cross_fraction_decimal_v1" or identities["fx"]["rounding_version"] != "decimal50_half_even_plain_v1":_fail("BINDING_INVALID")
    _instant(identities["fx"]["synthetic_fixture_timestamp"])
    sources = identities["fx"]["source_originals"]
    _exact(sources, {"ecb_usd_eur", "ecb_inr_eur"})
    for source in sources.values():
        _exact(source, {"bytes", "sha256"})
        if type(source["bytes"]) is not int or not 1 <= source["bytes"] <= MAX_BODY:_fail("BINDING_INVALID")
        if type(source["sha256"]) is not str or not HASH.fullmatch(source["sha256"]):_fail("BINDING_INVALID")
    rows = payload["rows"]
    expected = [(m, s) for m in policy["required_months"] for s in ("copper", "aluminum")]
    if type(rows) is not list or len(rows) != len(expected):_fail("MASK_MISMATCH")
    cfields = {"status", "reason", "source_unit", "native_value", "native_unit", "native_basis", "usd_per_kg", "derived_unit", "kg_basis", "token_sha256", "available_at_proxy"}
    ffields = {"status", "reasons", "value", "unit", "matched_days", "expected_days", "first_date", "last_date", "daily_basis_sha256", "available_at_proxy", "basis"}
    previous_fx = None
    for r, (month, series) in zip(rows, expected):
        _exact(r, {"month", "series_id", "commodity", "fx"})
        if r["month"] != month or r["series_id"] != series:_fail("MASK_MISMATCH")
        c, f = r["commodity"], r["fx"]
        _exact(c, cfields); _exact(f, ffields)
        if c["source_unit"] != "USD/metric_tonne" or c["native_unit"] != "USD/metric_tonne" or c["derived_unit"] != "USD/kg" or f["unit"] != "INR/USD":_fail("UNIT_HOLD")
        year, number = map(int, month.split("-"))
        year, number = (year + 1, 1) if number == 12 else (year, number + 1)
        proxy = f"{year:04d}-{number:02d}-{calendar.monthrange(year, number)[1]:02d}T23:59:59.999999Z"
        if c["available_at_proxy"] != proxy or f["available_at_proxy"] != proxy:_fail("BINDING_INVALID")
        available = _instant(proxy) <= _instant(policy["as_of"])
        if c["status"] == "complete":
            if c["reason"] is not None or not available:_fail("COMPONENT_INVALID")
            native, kg = _fraction(c["native_basis"]), _fraction(c["kg_basis"])
            if native / 1000 != kg or c["native_value"] != _render(native) or c["usd_per_kg"] != _render(kg):_fail("COMPONENT_INVALID")
        elif c["status"] == "unavailable":
            if c["reason"] not in ("source_missing", "proxy_not_yet_available") or (c["reason"] == "proxy_not_yet_available" and available):_fail("COMPONENT_INVALID")
            if any(c[k] is not None for k in ("native_value", "usd_per_kg", "native_basis", "kg_basis")):_fail("COMPONENT_INVALID")
        else:_fail("COMPONENT_INVALID")
        if c["token_sha256"] is not None and (type(c["token_sha256"]) is not str or not HASH.fullmatch(c["token_sha256"])):_fail("BINDING_INVALID")
        if (type(f["reasons"]) is not list or any(type(reason) is not str for reason in f["reasons"])
                or len(set(f["reasons"])) != len(f["reasons"])
                or any(reason not in ("DATE_SET_MISMATCH", "EXPECTED_DATE_MISSING", "PROXY_AFTER_ASOF") for reason in f["reasons"])):_fail("COMPONENT_INVALID")
        for key in ("matched_days", "expected_days"):
            if type(f[key]) is not int or not 0 <= f[key] <= 31:_fail("COMPONENT_INVALID")
        if not 1 <= f["expected_days"] or f["matched_days"] > f["expected_days"]:_fail("COMPONENT_INVALID")
        for key in ("first_date", "last_date"):
            token = f[key]
            if type(token) is not str or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", token) or token[:7] != month:_fail("COMPONENT_INVALID")
            try:datetime.strptime(token, "%Y-%m-%d")
            except ValueError:_fail("COMPONENT_INVALID")
        if f["first_date"] > f["last_date"]:_fail("COMPONENT_INVALID")
        if f["status"] == "complete":
            if f["reasons"] or not available or f["matched_days"] != f["expected_days"]:_fail("COMPONENT_INVALID")
            ratio = _fraction(f["basis"])
            with localcontext(Context(prec=50, rounding=ROUND_HALF_EVEN)):
                if f["value"] != format(Decimal(ratio.numerator) / Decimal(ratio.denominator), "f"):_fail("COMPONENT_INVALID")
            if type(f["daily_basis_sha256"]) is not str or not HASH.fullmatch(f["daily_basis_sha256"]):_fail("COMPONENT_INVALID")
        elif f["status"] == "unavailable":
            if not f["reasons"] or any(f[k] is not None for k in ("value", "basis", "daily_basis_sha256")):_fail("COMPONENT_INVALID")
        else:_fail("COMPONENT_INVALID")
        if series == "aluminum" and f != previous_fx:_fail("COMPONENT_INVALID")
        previous_fx = f
    for kind, field in (("commodity", "commodity_USD"), ("fx", "FX_INR_USD")):
        expected_status = COMPLETE if all(r[kind]["status"] == "complete" for r in rows) else INCOMPLETE
        if statuses[field] != expected_status:_fail("COMPONENT_INVALID")


def load_join_inputs(component_bindings: dict, join_policy: dict) -> VerifiedJoinInputs:
    _bindings(component_bindings, join_policy)  # Permission/type gates before opens.
    bindings, policy = copy.deepcopy(component_bindings), copy.deepcopy(join_policy)
    guard = _Guard()
    try:
        _code()
        c, f = bindings["commodity"], bindings["fx"]
        cp, cmraw, cm = _pin_panel(guard, c)
        fp, fmraw, fm = _pin_panel(guard, f)
        _pin_original(guard, "commodity", c); _pin_original(guard, "fx", f)
        if _instant(cm["as_of"]) != _instant(policy["as_of"]):_fail("AS_OF_MISMATCH")
        cv = commodity.load_synthetic_component(c["original_root"], c["fixture_run_id"], FLAGS)
        ca = commodity.normalize_commodity_component(cv, commodity.SCHEMA, policy["required_months"], cm["as_of"])
        fv = fx.load_verified_originals(f["original_root"], f["source_run_id"], f["provenance"])
        fa = fx.normalize_ecb_pair(fv, fx.SCHEMA, f["coverage"])
        expected_cp, expected_cm = _commodity_artifacts(ca, cv, c["panel_run_id"])
        expected_fp, expected_fm = _fx_artifacts(fa, fv, f["provenance"], f["panel_run_id"])
        if cp != expected_cp or cmraw != _json(expected_cm) or fp != expected_fp or fmraw != _json(expected_fm):_fail("COMPONENT_INVALID")
        if cm["status"] == COMPLETE:
            if commodity.verify_commodity_component(c["panel_root"], c["panel_run_id"], cv, commodity.SCHEMA, policy["required_months"], cm["as_of"]) != cm:_fail("COMPONENT_INVALID")
        if fm["status"] == COMPLETE:
            if fx.verify_fx_panel(f["panel_root"], f["panel_run_id"], fv, fx.SCHEMA, f["coverage"]) != fm:_fail("COMPONENT_INVALID")
        fx_rows = {}
        for r in fa["months"]:
            row = {k: copy.deepcopy(r[k]) for k in ("status", "reasons", "value", "unit", "matched_days", "expected_days", "first_date", "last_date", "daily_basis_sha256", "available_at_proxy")}
            basis = None
            if r["status"] == "complete":
                if r["unit"] != "INR/USD":_fail("UNIT_HOLD")
                if _sha(_json(r["daily_tokens"])) != r["daily_basis_sha256"]:_fail("COMPONENT_INVALID")
                total = Fraction(0)
                for _, usd, inr in r["daily_tokens"]:
                    total = _bounded(total + _bounded(Fraction(Decimal(inr)) / Fraction(Decimal(usd))))
                basis = _basis(_bounded(total / r["expected_days"]))
            row["basis"] = basis
            fx_rows[r["month"]] = row
        rows = []
        for r in ca["months"]:
            if r["source_unit"] != "USD/metric_tonne" or r["derived_unit"] != "USD/kg":_fail("UNIT_HOLD")
            cr = {k: copy.deepcopy(r[k]) for k in ("status", "reason", "source_unit", "native_value", "native_unit", "native_basis", "usd_per_kg", "derived_unit", "kg_basis", "token_sha256", "available_at_proxy")}
            rows.append({"month": r["month"], "series_id": r["series_id"], "commodity": cr, "fx": copy.deepcopy(fx_rows[r["month"]])})
        ci = {k: cm[k] for k in ("fixture_run_id", "fixture_receipt_sha256", "original_sha256", "original_bytes", "schema_sha256", "mask_sha256", "as_of", "code_sha256", "arithmetic_version", "availability_basis", "rights", "panel_sha256", "panel_bytes")}
        fi = {k: fm[k] for k in ("source_run_id", "source_receipt_sha256", "source_originals", "schema_sha256", "coverage_sha256", "code_sha256", "arithmetic_version", "rounding_version", "synthetic_fixture_timestamp", "availability_basis", "rights", "panel_sha256", "panel_bytes")}
        for identity, raw, binding in ((ci, cmraw, c), (fi, fmraw, f)):
            identity.update(panel_run_id=binding["panel_run_id"], panel_manifest_sha256=_sha(raw), panel_manifest_bytes=len(raw))
        fi["as_of"] = f["coverage"]["as_of"]
        payload = {"rows": rows, "component_identities": {"commodity": ci, "fx": fi},
                   "component_statuses": {"commodity_USD": cm["status"], "FX_INR_USD": fm["status"]}}
        # Fresh bytes/receipts and path bindings after all public computations.
        if commodity.load_synthetic_component(c["original_root"], c["fixture_run_id"], FLAGS) != cv:_fail("SOURCE_CHANGED")
        if fx.load_verified_originals(f["original_root"], f["source_run_id"], f["provenance"]) != fv:_fail("SOURCE_CHANGED")
        guard.check(); _code()
        snapshot = VerifiedJoinInputs(_json(policy, MAX_META), _json(payload))
    except JoinError:raise
    except (OSError, ValueError, TypeError, KeyError, OverflowError):_fail("COMPONENT_INVALID")
    finally:
        try:guard.close()
        except (OSError, ValueError):_fail("SOURCE_CHANGED")
    return snapshot


def compose_joined_panel(inputs: VerifiedJoinInputs, join_policy: dict) -> dict:
    _policy(join_policy)
    if type(inputs) is not VerifiedJoinInputs or inputs.policy != _json(join_policy, MAX_META):_fail("BINDING_INVALID")
    payload = _decode(inputs.payload, MAX_BODY)
    _snapshot(payload, join_policy)
    instant = _instant(join_policy["as_of"])
    rows = payload["rows"]
    for r in rows:
        c, f = r["commodity"], r["fx"]
        reasons = (["commodity:" + c["reason"]] if c["reason"] else []) + ["fx:" + reason for reason in f["reasons"]]
        value, basis = None, None
        available = max(_instant(c["available_at_proxy"]), _instant(f["available_at_proxy"]))
        if not reasons and c["status"] == f["status"] == "complete" and available <= instant:
            native = _fraction(c["native_basis"])
            kg = _fraction(c["kg_basis"])
            if _bounded(native / 1000) != kg:_fail("ARITHMETIC_LIMIT")
            result = _bounded(kg * _fraction(f["basis"]))
            basis, value = _basis(result), _render(result)
        elif not reasons:_fail("COMPONENT_INVALID")
        r["joined"] = {"status": "complete" if value is not None else "unavailable", "reasons": reasons,
                       "value": value, "unit": "INR/kg", "basis": basis,
                       "available_at_proxy": available.isoformat(timespec="microseconds").replace("+00:00", "Z")}
    complete = all(r["joined"]["status"] == "complete" for r in rows)
    status = COMPLETE if complete else INCOMPLETE
    return {"schema_version": "synthetic-commodity-fx-join.v1", **FLAGS, "status": status,
            "as_of": join_policy["as_of"], "required_months": copy.deepcopy(join_policy["required_months"]),
            "mask_sha256": _sha(_json(join_policy["required_months"])), "policy_sha256": _sha(inputs.policy),
            "component_identities": payload["component_identities"],
            "component_statuses": {**payload["component_statuses"], "joined_INR_kg": status},
            "availability_basis": AVAILABILITY, "arithmetic_version": ARITHMETIC,
            "rounding_version": ROUNDING, "rows": rows}


def _manifest(candidate, run, panel):
    return {"schema_version": "synthetic-commodity-fx-join-receipt.v1", **FLAGS, "run_id": run,
            "status": candidate["status"], "policy_sha256": candidate["policy_sha256"],
            "mask_sha256": candidate["mask_sha256"], "as_of": candidate["as_of"],
            "month_count": len(candidate["required_months"]), "row_count": len(candidate["rows"]),
            "component_identities": candidate["component_identities"], "code_identities": _code(),
            "panel_sha256": _sha(panel), "panel_bytes": len(panel), "availability_basis": AVAILABILITY,
            "arithmetic_version": ARITHMETIC, "rounding_version": ROUNDING}


def _separate(output_root, bindings):
    output = _path(output_root)
    for binding in bindings.values():
        for key in ("original_root", "panel_root"):
            source = _path(binding[key])
            if output == source or output.startswith(source + "/") or source.startswith(output + "/"):
                _fail("BINDING_INVALID")
    return output


def _write_new(guard, parent, name, body):
    fd = os.open(name, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
    try:
        created = os.fstat(fd)
        _private(created)
        remaining = memoryview(body)
        while remaining:
            count = os.write(fd, remaining)
            if count <= 0:_fail("PUBLICATION_IO")
            remaining = remaining[count:]
        os.fsync(fd)
        current = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if (current.st_dev, current.st_ino) != (created.st_dev, created.st_ino):_fail("SOURCE_CHANGED")
    finally:os.close(fd)
    readback, checked = guard.read(parent, name)
    if (checked.st_dev, checked.st_ino) != (created.st_dev, created.st_ino):_fail("SOURCE_CHANGED")
    if readback != body:_fail("PUBLICATION_INVALID")


def _verify(output_root, run, bindings, policy, *, diagnostic=False, staged=False):
    _bindings(bindings, policy); _run(run)
    output_root = _separate(output_root, bindings)
    guard = _Guard()
    try:
        root = guard.root(output_root)
        positive = guard.directory(root, "complete")
        negative = guard.directory(root, "incomplete")
        disqualified = guard.directory(root, "disqualified")
        guard.absent(disqualified, run + ".json", "PUBLICATION_INCOMPLETE")
        if staged:
            guard.absent(positive, run + ".json", "PUBLICATION_EXISTS")
            guard.absent(negative, run + ".json", "PUBLICATION_EXISTS")
        elif not diagnostic:guard.absent(negative, run + ".json", "PUBLICATION_INCOMPLETE")
        else:
            # Inspection may also return complete, but never accept both entries.
            try:os.stat(run + ".json", dir_fd=negative, follow_symlinks=False)
            except FileNotFoundError:
                diagnostic = False
                guard.absent(negative, run + ".json", "PUBLICATION_INCOMPLETE")
            else:guard.absent(positive, run + ".json", "PUBLICATION_INCOMPLETE")
        objects = guard.directory(root, "objects")
        obj = guard.directory(objects, run)
        manifest_raw, manifest_stat = guard.read(obj, "manifest.json", MAX_META)
        panel, _ = guard.read(obj, "panel.json")
        actual = _decode(manifest_raw)
        if not staged:
            marker, marker_stat = guard.read(negative if diagnostic else positive, run + ".json", MAX_META)
            if marker != manifest_raw or (marker_stat.st_dev, marker_stat.st_ino) != (manifest_stat.st_dev, manifest_stat.st_ino):_fail("PUBLICATION_INCOMPLETE")
        fresh = compose_joined_panel(load_join_inputs(bindings, policy), policy)
        expected = _manifest(fresh, run, _json(fresh))
        if actual != expected or manifest_raw != _json(expected) or panel != _json(fresh):_fail("PUBLICATION_INVALID")
        if not staged and actual["status"] != (INCOMPLETE if diagnostic else COMPLETE):_fail("PUBLICATION_INCOMPLETE")
        guard.check(); _code()
    except JoinError:raise
    except (OSError, ValueError, TypeError, KeyError):_fail("PUBLICATION_INVALID")
    finally:
        try:guard.close()
        except JoinError:raise
        except (OSError, ValueError):_fail("PUBLICATION_INVALID")
    return actual


def verify_joined_panel(output_root, run_id: str, component_bindings: dict, join_policy: dict) -> dict:
    return _verify(output_root, run_id, component_bindings, join_policy)


def inspect_joined_panel(output_root, run_id: str, component_bindings: dict, join_policy: dict) -> dict:
    return _verify(output_root, run_id, component_bindings, join_policy, diagnostic=True)


def _recovery_root(guard, ancestors):
    """Reopen only the original ancestor chain, checking each before traversal."""
    parent = None
    for path, identity in ancestors:
        name = path.rsplit("/", 1)[-1] if parent is not None else None
        current = os.stat(name, dir_fd=parent, follow_symlinks=False) if parent is not None else os.stat(path, follow_symlinks=False)
        if _directory_identity(current) != identity:_fail("PUBLICATION_CLEANUP_UNRESOLVED")
        fd = os.open(name if parent is not None else path,
                     os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        guard.records.append((parent, name, fd, path, identity, True))
        if _directory_identity(os.fstat(fd)) != identity:_fail("PUBLICATION_CLEANUP_UNRESOLVED")
        parent = fd
    _private(os.fstat(parent))
    guard.check()
    return parent


def _disqualify(output_root, root_identity, directory_identity, ancestors, run):
    """Independent creation-bound evidence; never delete any marker pathname."""
    guard = _Guard()
    try:
        root = _recovery_root(guard, ancestors)
        if (os.fstat(root).st_dev, os.fstat(root).st_ino) != root_identity:_fail("PUBLICATION_CLEANUP_UNRESOLVED")
        original = os.stat("disqualified", dir_fd=root, follow_symlinks=False)
        if (original.st_dev, original.st_ino) != directory_identity:_fail("PUBLICATION_CLEANUP_UNRESOLVED")
        directory = guard.directory(root, "disqualified")
        if (os.fstat(directory).st_dev, os.fstat(directory).st_ino) != directory_identity:_fail("PUBLICATION_CLEANUP_UNRESOLVED")
        # Verify all captured ancestors before writing through the pinned directory.
        guard.check()
        body = _json({"schema_version": "synthetic-commodity-fx-join-failure.v1", **FLAGS,
                      "run_id": run, "status": INCOMPLETE, "reason": "PUBLICATION_LINK_OR_FINALIZATION_FAILED"})
        _write_new(guard, directory, run + ".json", body)
        os.fsync(directory)
        guard.check()
    except BaseException:_fail("PUBLICATION_CLEANUP_UNRESOLVED")
    finally:
        try:guard.close()
        except BaseException:_fail("PUBLICATION_CLEANUP_UNRESOLVED")


def publish_joined_panel(candidate: dict, component_bindings: dict, join_policy: dict,
                         output_root, run_id: str) -> dict:
    _bindings(component_bindings, join_policy); _flags(candidate); _run(run_id)
    output_root = _separate(output_root, component_bindings)
    fresh = compose_joined_panel(load_join_inputs(component_bindings, join_policy), join_policy)
    if _json(candidate) != _json(fresh):_fail("PUBLICATION_INVALID")
    panel = _json(fresh)
    manifest = _manifest(fresh, run_id, panel)
    guard = _Guard()
    link_attempted = False
    root_identity = None
    directory_identity = None
    ancestors = ()
    error = None
    try:
        root = guard.root(output_root, create=True)
        root_identity = (os.fstat(root).st_dev, os.fstat(root).st_ino)
        ancestors = tuple((record[3], record[4]) for record in guard.records)
        objects = guard.directory(root, "objects", create=True)
        positive = guard.directory(root, "complete", create=True)
        negative = guard.directory(root, "incomplete", create=True)
        disqualified = guard.directory(root, "disqualified", create=True)
        directory_identity = (os.fstat(disqualified).st_dev, os.fstat(disqualified).st_ino)
        guard.absent(disqualified, run_id + ".json", "PUBLICATION_INCOMPLETE")
        # Preflight only; these absences change intentionally at our own link.
        for fd in (positive, negative):
            try:os.stat(run_id + ".json", dir_fd=fd, follow_symlinks=False)
            except FileNotFoundError:pass
            else:_fail("PUBLICATION_EXISTS")
        obj = guard.directory(objects, run_id, create=True, exclusive=True)
        _write_new(guard, obj, "panel.json", panel)
        _write_new(guard, obj, "manifest.json", _json(manifest))
        for fd in (obj, objects, root):os.fsync(fd)
        if _verify(output_root, run_id, component_bindings, join_policy, staged=True) != manifest:_fail("PUBLICATION_INVALID")
        complete = manifest["status"] == COMPLETE
        marker_dir = positive if complete else negative
        guard.check()
        # A raised call can have an ambiguous side effect; intent covers both statuses.
        link_attempted = True
        os.link("manifest.json", run_id + ".json", src_dir_fd=obj, dst_dir_fd=marker_dir, follow_symlinks=False)
        # Linking changes manifest ctime; refresh only our creation-bound inode.
        for i, row in enumerate(guard.records):
            parent, name, fd, path, ident, directory = row
            if parent == obj and name == "manifest.json":
                updated = os.fstat(fd)
                if (updated.st_dev, updated.st_ino) != ident[:2]:_fail("SOURCE_CHANGED")
                guard.records[i] = (parent, name, fd, path, _file_identity(updated), directory)
        marker, marker_stat = guard.read(marker_dir, run_id + ".json", MAX_META)
        manifest_stat = os.stat("manifest.json", dir_fd=obj, follow_symlinks=False)
        if marker != _json(manifest) or (marker_stat.st_dev, marker_stat.st_ino) != (manifest_stat.st_dev, manifest_stat.st_ino):_fail("PUBLICATION_INVALID")
        guard.absent(negative if complete else positive, run_id + ".json", "PUBLICATION_INCOMPLETE")
        os.fsync(marker_dir)
        if _verify(output_root, run_id, component_bindings, join_policy, diagnostic=not complete) != manifest:_fail("PUBLICATION_INVALID")
        guard.check()
    except BaseException as exc:error = exc
    try:guard.close()
    except BaseException as exc:
        if error is None:error = exc
    if error is not None:
        if link_attempted:
            _disqualify(output_root, root_identity, directory_identity, ancestors, run_id)
            _fail("PUBLICATION_INCOMPLETE")
        if isinstance(error, JoinError):raise error
        if isinstance(error, FileExistsError):_fail("PUBLICATION_EXISTS")
        _fail("PUBLICATION_IO")
    return manifest
