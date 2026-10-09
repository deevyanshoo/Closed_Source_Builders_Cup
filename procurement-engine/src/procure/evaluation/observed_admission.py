"""Fixture-only host admission and native binary64 workbook snapshots.

There is deliberately no observed registration API. The registry is an in-process
host configuration seam, not a signature or protection against hostile same-UID
Python. Product requests cannot mint bindings by copying fields or APPROVED text.
The parent sees the entire snapshot; this is not physical test-data isolation.
"""
from __future__ import annotations

import hashlib
import io
import json
import math
import os
import re
import stat
import sys
import weakref
import zipfile
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from xml.etree import ElementTree

MAX_SOURCE = 12 * 1024 * 1024
MAX_MEMBER = 16 * 1024 * 1024
MAX_EXPANSION = 64 * 1024 * 1024
SCHEMA = "observed-admission-fixture.v1"
SERIES = {"copper": (65, "Copper", "($/mt)"), "aluminum": (63, "Aluminum", "($/mt)")}
OFFLINE_ACCEPTANCE_SHA256 = "ea92d939b06e9c48a25bd03becc0039135e98af90efe380aff6adf41bdee4983"
_HELD_OBSERVED_SHA256 = "ea1c350827878ea3bbe30e3cda16a13fd3bd5b409b8458940dc94a36b5a33154"
_REGISTRY = weakref.WeakKeyDictionary()
_RUNS = weakref.WeakKeyDictionary()
_RUN_LIVES = weakref.WeakKeyDictionary()


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _pairs(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise ValueError("duplicate JSON key")
        out[key] = value
    return out


def decode(raw):
    def invalid(_):
        raise ValueError("nonfinite JSON")
    value = json.loads(raw, object_pairs_hook=_pairs, parse_constant=invalid)
    canonical(value)
    return value


def _absolute(path):
    path = Path(path)
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("absolute canonical local path required")
    return path


def _identity(s, directory=False):
    base = (s.st_dev, s.st_ino, s.st_mode, s.st_uid)
    # Ancestor content timestamps change when an owned sibling output is created.
    # Anchor directory identity/mode/owner, not unrelated directory contents.
    return base if directory else (*base, s.st_nlink, s.st_size, s.st_mtime_ns, s.st_ctime_ns)


class Lifetime:
    """Own every descriptor immediately, including failures at the first fstat."""

    def __init__(self):
        self.fds = []
        self.records = []
        self.closed = False

    def open(self, path, *, private=False, limit=MAX_SOURCE):
        path = _absolute(path)
        parent = None
        for index, part in enumerate(path.parts):
            if index == 0:
                fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
                at = Path("/")
            else:
                at = at / part
                directory = index < len(path.parts) - 1
                flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
                if directory:
                    flags |= os.O_DIRECTORY
                fd = os.open(part, flags, dir_fd=parent)
            self.fds.append(fd)  # before fstat, never retry close
            info = os.fstat(fd)
            directory = index < len(path.parts) - 1
            if directory and not stat.S_ISDIR(info.st_mode):
                raise ValueError("ancestor not directory")
            if not directory:
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > limit:
                    raise ValueError("regular single-link bounded file required")
                if private:
                    pinfo = os.fstat(parent)
                    if info.st_uid != os.getuid() or pinfo.st_uid != os.getuid() or pinfo.st_mode & 0o077:
                        raise ValueError("source/control root must be private")
            self.records.append((at, fd, _identity(info, directory), directory))
            parent = fd
        return fd

    def check(self, *, postclose=False):
        for path, fd, expected, directory in self.records:
            info = os.lstat(path)
            if _identity(info, directory) != expected:
                raise ValueError("path identity changed")
            if not postclose and _identity(os.fstat(fd), directory) != expected:
                raise ValueError("descriptor identity changed")

    def read(self, fd, limit):
        chunks, total = [], 0
        while True:
            chunk = os.read(fd, min(65536, limit + 1 - total))
            if not chunk:
                break
            chunks.append(chunk); total += len(chunk)
            if total > limit:
                raise ValueError("bounded read exceeded")
        self.check()
        return b"".join(chunks)

    def close(self):
        if self.closed:
            raise ValueError("lifetime already closed")
        self.closed = True
        failed = False
        for fd in reversed(self.fds):
            try:
                os.close(fd)
            except OSError:
                failed = True
        try:
            self.check(postclose=True)
        except (OSError, ValueError):
            failed = True
        if failed:
            raise ValueError("close/postclose identity unverified")


@dataclass(frozen=True, eq=False)
class RootAdmissionBinding:
    """Opaque identity; constructing this type does not register authority."""
    fixture_id: str


@dataclass(frozen=True, eq=False)
class RootObservedRunBinding:
    fixture_id: str


def _registered(binding, registry):
    try:
        value = registry.get(binding)
    except TypeError:
        value = None
    if value is None:
        raise ValueError("registered fixture binding required; observed execution HOLD")
    return value


def register_owned_fixture(source_file, admission_raw, *, controls=()):
    """Trusted test-host setup ONLY. No grant for real originals or real fitting.

    Controls are owning-host paths, never product request data. Hashing the owned
    fixture happens in its generating test, not here before permission checking.
    """
    grant = decode(admission_raw)
    required = {"schema_version", "fixture_id", "is_synthetic", "observed_authenticity",
                "observed_execution_authorized", "source_sha256", "source_bytes", "as_of_utc"}
    if (set(grant) != required or grant["schema_version"] != SCHEMA
            or not isinstance(grant["fixture_id"], str) or not 0 < len(grant["fixture_id"]) <= 128):
        raise ValueError("fixture grant shape mismatch")
    if (grant["is_synthetic"] is not True or grant["observed_authenticity"] is not False
            or grant["observed_execution_authorized"] is not False):
        raise ValueError("only synthetic fixture registration; observed execution HOLD")
    if grant["as_of_utc"] != "2026-10-08T00:00:00.000000Z":
        raise ValueError("fixture UTC proxy mismatch")
    sha = grant["source_sha256"]
    if not isinstance(sha, str) or len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha):
        raise ValueError("fixture source hash invalid")
    if sha == _HELD_OBSERVED_SHA256 or Path(source_file).name == "worldbank_pinksheet_monthly.xlsx":
        raise ValueError("named observed original is not a fixture; observed execution HOLD")
    if type(grant["source_bytes"]) is not int or not 0 < grant["source_bytes"] <= MAX_SOURCE:
        raise ValueError("fixture source size invalid")
    life = Lifetime()
    try:
        life.open(source_file, private=True)
        pins = []
        for path in controls:
            fd = life.open(path, private=True, limit=1024 * 1024)
            pins.append((str(_absolute(path)), digest(life.read(fd, 1024 * 1024))))
        code = []
        for path in (Path(__file__), Path(__file__).with_name("observed_runner.py"),
                     Path(__file__).with_name("forecast_scorecard.py"),
                     Path(__file__).parents[1] / "forecast/models.py"):
            fd = life.open(path, limit=1024 * 1024)
            code.append((str(path), digest(life.read(fd, 1024 * 1024))))
        identities = tuple((str(p), identity, directory) for p, _, identity, directory in life.records)
        life.check()
    finally:
        life.close()
    binding = RootAdmissionBinding(grant["fixture_id"])
    from importlib.metadata import version
    runtime = {"python": sys.version.split()[0], "executable": str(Path(sys.executable).resolve()),
               "packages": {name: version(name) for name in ("openpyxl", "numpy", "pandas")}}
    _REGISTRY[binding] = MappingProxyType({"source": str(_absolute(source_file)),
                                         "raw": bytes(admission_raw), "grant": MappingProxyType(grant),
                                         "controls": tuple(pins), "code": tuple(code), "identities": identities,
                                         "runtime_raw": canonical(runtime), "acceptance_sha256": OFFLINE_ACCEPTANCE_SHA256})
    return binding


def register_owned_fixture_run(admission, protocol_path, manifest_path, output_dir):
    record = _registered(admission, _REGISTRY)
    paths = tuple(str(_absolute(p)) for p in (protocol_path, manifest_path))
    if paths != tuple(p for p, _ in record["controls"]):
        raise ValueError("foreign control paths")
    output = _absolute(output_dir)
    if output.parent != Path(record["source"]).parent.parent or output.name in {"inputs", "source"}:
        raise ValueError("output must be a distinct owned fixture-root child")
    if os.path.lexists(output):
        raise ValueError("output already has an entry")
    parent = os.lstat(output.parent)
    if not stat.S_ISDIR(parent.st_mode) or parent.st_uid != os.getuid() or parent.st_mode & 0o077:
        raise ValueError("output parent must be private")
    binding = RootObservedRunBinding(admission.fixture_id)
    _RUNS[binding] = MappingProxyType({"admission": admission, "protocol": paths[0], "manifest": paths[1],
                                     "output": str(output), "parent_identity": _identity(parent, True)})
    return binding


def fixture_run_record(binding):
    return _registered(binding, _RUNS)


def fixture_admission_record(binding):
    return _registered(binding, _REGISTRY)


def _begin_fixture_run(binding):
    _registered(binding, _RUNS)
    if binding in _RUN_LIVES:
        raise ValueError("fixture run already attempted; no marker reset")
    token = object()
    _RUN_LIVES[binding] = {"token": token, "completion_sha256": None, "failed": False,
                          "created_identity": None, "sealed_identity": None}
    return token


class _RunDirectoryAnchor(Lifetime):
    """Discrete run identity check; own directory FDs through verification/close.

    The inherited file reader is unchanged. This anchor opens directories only,
    without following any ancestor, and uses the same close-once/postclose guard.
    """

    def __init__(self, output, identities):
        super().__init__()
        output = _absolute(output)
        parent = None
        try:
            for index, part in enumerate(output.parts):
                at = Path("/") if index == 0 else at / part
                flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK
                fd = os.open("/" if index == 0 else part, flags, dir_fd=parent)
                self.fds.append(fd)  # ownership before even the first fstat
                info = os.fstat(fd)
                if not stat.S_ISDIR(info.st_mode):
                    raise ValueError("run directory identity/type changed")
                identity = _identity(info, True)
                self.records.append((at, fd, identity, True))
                if at == output.parent and identity != identities[0]:
                    raise ValueError("run parent identity changed")
                if at == output and identity != identities[1]:
                    raise ValueError("run output identity changed")
                parent = fd
            self.check()
        except BaseException:
            self.close()
            raise

    def __enter__(self):
        try:
            self.check()
        except BaseException:
            self.close()
            raise
        return self

    def __exit__(self, *_):
        self.close()


def _capture_fixture_output(binding, token, created_identity):
    """Owning host only: capture the mkdir identity with its issued run token."""
    run = _registered(binding, _RUNS)
    state = _registered(binding, _RUN_LIVES)
    if (state["token"] is not token or state["failed"] or state["created_identity"] is not None
            or state["completion_sha256"] is not None):
        raise ValueError("foreign/stale host output token")
    identities = (run["parent_identity"], tuple(created_identity))
    with _RunDirectoryAnchor(run["output"], identities):
        pass
    state["created_identity"] = identities


def _seal_fixture_run(binding, token, completion_raw):
    run = _registered(binding, _RUNS)
    state = _registered(binding, _RUN_LIVES)
    if state["token"] is not token or state["failed"] or state["completion_sha256"] is not None:
        raise ValueError("foreign/stale host closure token")
    identities = state["created_identity"]
    if identities is None or identities[0] != run["parent_identity"]:
        raise ValueError("host-created output identity absent")
    with _RunDirectoryAnchor(run["output"], identities):
        pass
    # Issue both commitments only after the anchor's close/postclose succeeds.
    state.update(completion_sha256=digest(completion_raw), sealed_identity=identities)


def _fail_fixture_run(binding, token):
    state = _registered(binding, _RUN_LIVES)
    if state["token"] is not token:
        raise ValueError("foreign host token")
    state["failed"] = True


def _fixture_completion_sha(binding):
    state = _registered(binding, _RUN_LIVES)
    if state["failed"] or state["completion_sha256"] is None:
        raise ValueError("host-issued closure absent or failed")
    return state["completion_sha256"]


def _fixture_completion_identity(binding):
    run = _registered(binding, _RUNS)
    _fixture_completion_sha(binding)
    state = _registered(binding, _RUN_LIVES)
    identities = state["sealed_identity"]
    if (identities is None or identities != state["created_identity"]
            or identities[0] != run["parent_identity"]):
        raise ValueError("host-sealed output identity absent or stale")
    return identities


def _zip_guard(raw):
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        members = archive.infolist()
        if len(members) > 1024 or len({m.filename for m in members}) != len(members):
            raise ValueError("ZIP member count or duplicate")
        total = 0
        for member in members:
            path = PurePosixPath(member.filename)
            if path.is_absolute() or ".." in path.parts or "\\" in member.filename or member.flag_bits & 1:
                raise ValueError("ZIP traversal/encryption")
            if member.file_size > MAX_MEMBER or stat.S_ISLNK(member.external_attr >> 16):
                raise ValueError("ZIP member limit/type")
            actual = 0; chunks = []
            with archive.open(member) as stream:
                while True:
                    chunk = stream.read(65536)
                    if not chunk:
                        break
                    actual += len(chunk); total += len(chunk)
                    if actual > MAX_MEMBER or total > MAX_EXPANSION:
                        raise ValueError("ZIP expanded byte limit")
                    if member.filename.endswith((".xml", ".rels")):
                        chunks.append(chunk)
            if actual != member.file_size:
                raise ValueError("ZIP expansion metadata mismatch")
            if chunks:
                data = b"".join(chunks)
                markup = data.replace(b"\x00", b"").upper()  # include UTF16/32 markup
                if b"<!DOCTYPE" in markup or b"<!ENTITY" in markup:
                    raise ValueError("XML DTD/entity forbidden")
                root = ElementTree.fromstring(data)
                if member.filename.startswith("xl/worksheets/"):
                    xml_rows = [node for node in root.iter() if node.tag.rsplit("}", 1)[-1] == "row"]
                    cells = [node for node in root.iter() if node.tag.rsplit("}", 1)[-1] == "c"]
                    row_ids = [int(node.attrib.get("r", "0")) for node in xml_rows]
                    if (len(xml_rows) > 1006 or len(cells) > 100000 or row_ids != sorted(set(row_ids))
                            or any(index < 1 or index > 1006 for index in row_ids)):
                        raise ValueError("actual XML row/cell expansion bound")
                    seen, max_row, max_col = set(), 0, 0
                    for cell in cells:
                        ref = cell.attrib.get("r", "")
                        matched = re.fullmatch(r"([A-Z]+)([1-9][0-9]*)", ref)
                        if matched is None or ref in seen:
                            raise ValueError("actual XML duplicate/invalid cell")
                        seen.add(ref)
                        col = 0
                        for letter in matched[1]:
                            col = col * 26 + ord(letter) - 64
                        max_col, max_row = max(max_col, col), max(max_row, int(matched[2]))
                    if max_row > 1006 or max_row * max_col > 100000:
                        raise ValueError("actual XML cell expansion bound")
                if member.filename.endswith(".rels") and any(
                        node.attrib.get("TargetMode", "").lower() == "external" for node in root):
                    raise ValueError("external workbook relationship")


def _native(cell):
    value = cell.value
    if cell.data_type in {"f", "e"} or type(value) not in {int, float}:
        raise ValueError("required native numeric cell invalid")
    try:
        converted = float(value)
    except (OverflowError, ValueError) as exc:
        raise ValueError("native numeric overflow") from exc
    if not math.isfinite(converted) or converted < sys.float_info.min:
        raise ValueError("native numeric nonpositive/nonfinite/subnormal")
    if type(value) is int and Fraction(value) != Fraction.from_float(converted):
        raise ValueError("lossy integer conversion")
    return converted


def _parse_native(raw):
    from openpyxl import load_workbook
    _zip_guard(raw)
    stream = io.BytesIO(raw)
    book = load_workbook(stream, read_only=True, data_only=False, keep_links=False)
    try:
        sheet = book["Monthly Prices"]
        if not sheet.max_row or not sheet.max_column or sheet.max_row * sheet.max_column > 100000:
            raise ValueError("sheet cell bound")
        if sheet.max_row - 6 > 1000:
            raise ValueError("sheet month-row bound")
        for _, (col, label, unit) in SERIES.items():
            if sheet.cell(5, col).value != label or sheet.cell(6, col).value != unit:
                raise ValueError("native header/unit mismatch")
        series = {key: {} for key in SERIES}
        labels = []
        cells_seen = 0
        for row in sheet.iter_rows(min_row=7, max_col=sheet.max_column):
            cells_seen += len(row)
            if cells_seen > 100000 or len(labels) >= 1000:
                raise ValueError("actual sheet expansion bound")
            label = row[0].value
            if label is None:
                raise ValueError("blank source month")
            expected_index = 1960 * 12 + len(labels)
            expected = f"{expected_index // 12:04d}M{expected_index % 12 + 1:02d}"
            if label != expected:
                raise ValueError("source month labels must be unique consecutive")
            labels.append(label)
            month = label[:4] + "-" + label[5:]
            if "2016-09" <= month <= "2026-08":
                for key, (col, _, _) in SERIES.items():
                    series[key][month] = _native(row[col - 1])
        if len(labels) != 801 or labels[-1] != "2026M09" or any(len(s) != 120 for s in series.values()):
            raise ValueError("exact 801 labels/120 model months required")
        return series
    finally:
        book.close()
        stream.close()


class ObservedNativeRead:
    def __init__(self, lifetime, series, receipt):
        self.lifetime = lifetime
        self.series = MappingProxyType({key: MappingProxyType(values) for key, values in series.items()})
        self.receipt = MappingProxyType(receipt)

    def check(self):
        self.lifetime.check()

    def close(self):
        self.lifetime.close()

    def __enter__(self):
        self.check()
        return self

    def __exit__(self, *_):
        self.close()


def open_observed_native_snapshot(source_file: Path, admission_raw: bytes, *, expected_admission: RootAdmissionBinding):
    record = _registered(expected_admission, _REGISTRY)  # before any body/metadata access
    if str(_absolute(source_file)) != record["source"] or admission_raw != record["raw"]:
        raise ValueError("foreign admission/source binding")
    for path, identity, directory in record["identities"]:
        if _identity(os.lstat(path), directory) != identity:
            raise ValueError("registered identity changed")
    life = Lifetime()
    try:
        for path, sha in record["controls"] + record["code"]:
            fd = life.open(path, private=(path, sha) in record["controls"], limit=1024 * 1024)
            if digest(life.read(fd, 1024 * 1024)) != sha:
                raise ValueError("control/code hash changed")
        fd = life.open(source_file, private=True)
        raw = life.read(fd, MAX_SOURCE)
        grant = record["grant"]
        if len(raw) != grant["source_bytes"] or digest(raw) != grant["source_sha256"]:
            raise ValueError("anchored source size/hash mismatch")
        values = _parse_native(raw)  # same bytes; never reopen the path
        life.check()
        basis = {key: {month: {"hex": value.hex(), "numerator": str(Fraction.from_float(value).numerator),
                              "denominator": str(Fraction.from_float(value).denominator)}
                       for month, value in series.items()} for key, series in values.items()}
        receipt = {**grant, "full_parent_snapshot_exposed": True, "physical_test_actuals_untouched": False,
                   "numeric_policy": "native-cell binary64; no XML decimal certificate",
                   "source_labels": 801, "required_months": 120, "stored_value_basis_sha256": digest(canonical(basis)),
                   "stored_value_basis": basis, "code_sha256": dict(record["code"]),
                   "control_sha256": dict(record["controls"]), "runtime": decode(record["runtime_raw"]),
                   "offline_acceptance_sha256": record["acceptance_sha256"]}
        return ObservedNativeRead(life, values, receipt)
    except BaseException:
        life.close()
        raise
