"""Synthetic exact-to-binary64 conversion, never execution authority."""
from __future__ import annotations
from fractions import Fraction
import math
import os
import stat
import re
import struct
import sys
import calendar
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
from pathlib import Path
from . import panel_adapter as adapter


class ModelInputError(ValueError):
    """Fixed sanitized refusal codes only."""


def _fail(code):
    raise ModelInputError(code) from None


def _fraction(b):
    if type(b) is not dict or set(b) != {'numerator', 'denominator'}:
        _fail('MODEL_INPUT_BASIS_INVALID')
    operands = []
    for s in (b['numerator'], b['denominator']):
        if type(s) is not str or len(s) > 1234 or not re.fullmatch('[1-9][0-9]*', s):
            _fail('MODEL_INPUT_BASIS_INVALID')
        n = int(s)
        if n.bit_length() > 4096:
            _fail('MODEL_INPUT_BASIS_INVALID')
        operands.append(n)
    if math.gcd(*operands) != 1:
        _fail('MODEL_INPUT_BASIS_INVALID')
    return Fraction(*operands)


def _receipt(q):
    if q < 0 or q.numerator.bit_length() > 8192 or q.denominator.bit_length() > 8192:
        _fail('FLOAT_ERROR_BOUND_REFUSED')
    return {'numerator': str(q.numerator), 'denominator': str(q.denominator)}


def _profile():
    info = sys.float_info
    fields = ('radix', 'mant_dig', 'min_exp', 'max_exp', 'rounds')
    if tuple(getattr(info, k) for k in fields) != (2, 53, -1021, 1024, 1):
        _fail('FLOAT_PROFILE_UNSUPPORTED')
    if (struct.calcsize('>d') != 8 or struct.pack('>d', 1.0).hex() != '3ff0000000000000'
            or info.min.hex() != '0x1.0000000000000p-1022'
            or info.max.hex() != '0x1.fffffffffffffp+1023'):
        _fail('FLOAT_PROFILE_UNSUPPORTED')
    return {**{k: getattr(info, k) for k in fields}, 'min_normal_hex': info.min.hex(),
            'max_finite_hex': info.max.hex()}


def _certificate(q, x):
    if type(x) is not float or not math.isfinite(x) or x <= 0:
        _fail('FLOAT_RANGE_REFUSED')
    bits = struct.unpack('>Q', struct.pack('>d', x))[0]
    exponent = (bits >> 52) & 2047
    if not 1 <= exponent <= 2046:
        _fail('FLOAT_RANGE_REFUSED')
    encoded = Fraction.from_float(x)
    error = abs(encoded-q)
    even = not bool(bits & 1)
    neighbors = {}
    for name, direction in [('predecessor', -math.inf), ('successor', math.inf)]:
        neighbor = math.nextafter(x, direction)
        if math.isfinite(neighbor):
            value = Fraction.from_float(neighbor)
            kind, hex_value = 'finite', neighbor.hex()
        else:
            # Only max finite has this successor; this is a comparison point,
            # never an accepted output or an infinity-to-Fraction conversion.
            if name != 'successor' or x != sys.float_info.max:
                _fail('FLOAT_NEAREST_EVEN_REFUSED')
            value, kind, hex_value = Fraction(2**1024), 'overflow_boundary', None
        distance = abs(value-q)
        tied = distance == error
        if distance < error or (tied and not even):
            _fail('FLOAT_NEAREST_EVEN_REFUSED')
        neighbors[name] = {'kind': kind, 'hex': hex_value, 'basis': _receipt(value),
                           'absolute_distance': _receipt(distance), 'tied': tied}
    relative = error/q
    if relative > Fraction(1, 2**53):
        _fail('FLOAT_ERROR_BOUND_REFUSED')
    return {'encoded_basis': _receipt(encoded), 'absolute_error': _receipt(error),
            'relative_error': _receipt(relative), 'nearest_certificate': {
                'rounding_version': 'binary64_fraction_nearest_even_v1',
                'chosen_significand_even': even, **neighbors}}


def _encode_basis(b):
    _profile()
    q = _fraction(b)
    if not Fraction(1, 2**1022) <= q <= Fraction((2**53-1)*2**971):
        _fail('FLOAT_RANGE_REFUSED')
    try:
        x = float(q)  # The only Fraction-to-float conversion, once per row.
    except (ValueError, OverflowError):
        _fail('FLOAT_RANGE_REFUSED')
    return x, {'basis': dict(b), 'float_hex': x.hex(), **_certificate(q, x)}



FLAGS = {'synthetic': True, 'source_authenticity': False, 'observed_eligible': False,
         'execution_authorized': False, 'rights': 'synthetic_only'}
MAX_BODY = 4194304
REGISTRY = (('commodity', 'copper', 'USD/metric_tonne', 'synthetic-commodity-component'),
            ('commodity', 'aluminum', 'USD/metric_tonne', 'synthetic-commodity-component'),
            ('fx', 'USDINR', 'INR/USD', 'synthetic-ecb-daily-cross'),
            ('joined', 'copper_INR_per_kg', 'INR/kg', 'synthetic-commodity-fx-join'),
            ('joined', 'aluminum_INR_per_kg', 'INR/kg', 'synthetic-commodity-fx-join'))
TRUSTED_CODE = {
    'commodity': ('commodity_component.py', '8196147a7dfa626efbef04990763c52a85eaa214829c493947f7b436d4ce2763'),
    'fx': ('fx_normalization.py', '872c9ff5b0b614e136f27a87ae62cc24c07aa83a1953a7326aaf3968c79a71c6'),
    'transport': ('source_transport.py', 'f41ed298ffc938d38749413168dfbed080b7a9d881ae54f642ec7275e3c8e060'),
    'join': ('commodity_fx_join.py', '990be562463697e6a095e8aeac771e1daabf7a89a2e7c7e528eae4ca886eb3d2'),
    'adapter': ('panel_adapter.py', '40b003dacab1ef32e5d169ad538eff1245bc0116bf6413b456955484f9bd752c')}
# Source change identity, not an executing-code attestation.
_SELF_RAW = Path(__file__).read_bytes()
CODE_SHA256 = hashlib.sha256(_SELF_RAW).hexdigest()
CODE_BYTES = len(_SELF_RAW)
del _SELF_RAW


@dataclass(frozen=True, slots=True)
class ModelInputSlice:
    """Local historical verification; constructor gives no phase/fit permission."""
    request: bytes
    payload: bytes
    values: tuple[float, ...]


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _canonical(value):
    remaining = 60000
    def visit(v, depth=0):
        nonlocal remaining
        remaining -= 1
        if remaining < 0 or depth > 12:
            _fail('MODEL_INPUT_BINDING_INVALID')
        if type(v) is dict:
            if any(type(k) is not str or len(k) > 128 for k in v):
                _fail('MODEL_INPUT_BINDING_INVALID')
            for item in v.values(): visit(item, depth+1)
        elif type(v) is list:
            for item in v: visit(item, depth+1)
        elif type(v) is str:
            if len(v) > 8192: _fail('MODEL_INPUT_BINDING_INVALID')
        elif type(v) is int:
            if v.bit_length() > 32: _fail('MODEL_INPUT_BINDING_INVALID')
        elif v is not None and type(v) is not bool:
            _fail('MODEL_INPUT_BINDING_INVALID')
    visit(value)
    raw = json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True,
                     allow_nan=False).encode('ascii')
    if len(raw) > MAX_BODY: _fail('MODEL_INPUT_BINDING_INVALID')
    return raw


def _decode(raw):
    if type(raw) is not bytes or not 1 <= len(raw) <= MAX_BODY:
        _fail('MODEL_INPUT_BINDING_INVALID')
    def pairs(items):
        out = {}
        for k, v in items:
            if k in out: _fail('MODEL_INPUT_BINDING_INVALID')
            out[k] = v
        return out
    def forbidden(_): _fail('MODEL_INPUT_BINDING_INVALID')
    try:
        result = json.loads(raw, object_pairs_hook=pairs, parse_float=forbidden, parse_constant=forbidden)
        if _canonical(result) != raw: _fail('MODEL_INPUT_BINDING_INVALID')
        return result
    except (ValueError, TypeError, UnicodeError, RecursionError):
        _fail('MODEL_INPUT_BINDING_INVALID')


def _keys(value, keys):
    if type(value) is not dict or set(value) != set(keys):
        _fail('MODEL_INPUT_BINDING_INVALID')


def _flags(value):
    if type(value) is not dict or any(type(value.get(k)) is not type(v) or value.get(k) != v for k, v in FLAGS.items()):
        _fail('MODEL_INPUT_BINDING_INVALID')


def _month(value):
    if type(value) is not str or not re.fullmatch('[0-9]{4}-(0[1-9]|1[0-2])', value):
        _fail('MODEL_INPUT_MASK_INVALID')
    y, m = map(int, value.split('-'))
    if not 1 <= y <= 9998: _fail('MODEL_INPUT_MASK_INVALID')
    return y*12+m-1


def _months(first, last):
    start, stop = _month(first), _month(last)
    if not 1 <= stop-start+1 <= 240: _fail('MODEL_INPUT_MASK_INVALID')
    return [f'{n//12:04d}-{n%12+1:02d}' for n in range(start, stop+1)]


def _instant(value):
    if type(value) is not str or not re.fullmatch('[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\\.[0-9]{6}Z', value):
        _fail('MODEL_INPUT_BINDING_INVALID')
    instant = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if instant.isoformat(timespec='microseconds').replace('+00:00', 'Z') != value:
        _fail('MODEL_INPUT_BINDING_INVALID')
    return instant


def _proxy(month):
    n = _month(month)+1
    y, m = n//12, n%12+1
    return f'{y:04d}-{m:02d}-{calendar.monthrange(y,m)[1]:02d}T23:59:59.999999Z'


def _payload_shape(snapshot, request, payload):
    _keys(request, {'component_bindings', 'join_policy', 'joined_publication'})
    _keys(request['component_bindings'], {'commodity', 'fx'})
    _flags(request['join_policy'])
    _keys(payload, {'schema_version', *FLAGS, 'status', 'as_of', 'required_months', 'mask_sha256',
                    'request_sha256', 'channel_statuses', 'component_identities', 'joined_identity',
                    'code_identities', 'series', 'load_ledger'})
    _flags(payload)
    months = payload['required_months']
    if type(months) is not list or not months or months != _months(months[0], months[-1]):
        _fail('MODEL_INPUT_MASK_INVALID')
    if (payload['schema_version'] != 'synthetic-panel-series.v1'
            or payload['status'] not in ('SYNTHETIC_OFFLINE_ONLY', 'SYNTHETIC_OFFLINE_INCOMPLETE')
            or payload['as_of'] != request['join_policy']['as_of']
            or months != request['join_policy']['required_months']
            or payload['mask_sha256'] != _sha(_canonical(months))
            or payload['request_sha256'] != _sha(snapshot.request)
            or _instant(payload['as_of']).date().isoformat() != '2026-10-08'):
        _fail('MODEL_INPUT_BINDING_INVALID')
    _keys(payload['channel_statuses'], {'commodity', 'fx', 'joined'})
    _keys(payload['component_identities'], {'commodity', 'fx'})
    if payload['code_identities'] != {k: v[1] for k, v in TRUSTED_CODE.items()}:
        _fail('MODEL_INPUT_CODE_CHANGED')
    if type(payload['series']) is not list or len(payload['series']) != len(REGISTRY):
        _fail('MODEL_INPUT_BINDING_INVALID')
    for s, (kind, sid, unit, source) in zip(payload['series'], REGISTRY):
        _keys(s, {'series_kind', 'series_id', 'unit', 'source_id', 'is_synthetic', 'publication_status', 'rows'})
        if (s['series_kind'], s['series_id'], s['unit'], s['source_id']) != (kind, sid, unit, source) or s['is_synthetic'] is not True:
            _fail('MODEL_INPUT_BINDING_INVALID')
        if s['publication_status'] != payload['channel_statuses'][kind]: _fail('MODEL_INPUT_BINDING_INVALID')
        if type(s['rows']) is not list or len(s['rows']) != len(months): _fail('MODEL_INPUT_MASK_INVALID')
        for r, m in zip(s['rows'], months):
            _keys(r, {'month', 'status', 'reasons', 'basis', 'available_at_proxy'})
            if r['month'] != m or r['available_at_proxy'] != _proxy(m): _fail('MODEL_INPUT_MASK_INVALID')
            if type(r['reasons']) is not list or any(type(v) is not str or not v for v in r['reasons']) or len(set(r['reasons'])) != len(r['reasons']):
                _fail('MODEL_INPUT_BINDING_INVALID')
            if r['status'] == 'complete':
                if r['reasons'] or r['basis'] is None or _instant(r['available_at_proxy']) > _instant(payload['as_of']):
                    _fail('MODEL_INPUT_ROW_UNAVAILABLE')
                _fraction(r['basis'])
            elif r['status'] != 'unavailable' or r['basis'] is not None or not r['reasons']:
                _fail('MODEL_INPUT_ROW_UNAVAILABLE')


def _fresh(snapshot, request):
    fresh = adapter.load_verified_panel_series(**request)
    if type(fresh) is not adapter.VerifiedPanelSeries or fresh.request != snapshot.request or fresh.payload != snapshot.payload:
        _fail('MODEL_INPUT_SNAPSHOT_MISMATCH')


def _identity(details, directory):
    base = (details.st_dev, details.st_ino, details.st_mode, details.st_uid)
    return base if directory else (*base, details.st_size, details.st_mtime_ns, details.st_ctime_ns)


class _Lifetime:
    """Own read-only identities; source descriptors are metadata-only after admission.

    Bounded interleavings, not hostile same-UID containment or later liveness.
    Every acquired fd is owned immediately, even if its first fstat fails.
    """
    def __init__(self):
        self.fds = []
        self.records = {}
        self.absences = []
        self.closed = False

    def _open(self, path, directory, private, *, parent=None, name=None, code=False, maximum=MAX_BODY):
        if path in self.records:
            fd, identity, was_directory, was_code = self.records[path]
            if was_directory != directory or was_code != code:
                _fail('MODEL_INPUT_INTEGRITY_UNRESOLVED')
            self.check()
            details = os.fstat(fd)
        else:
            flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
            if directory: flags |= os.O_DIRECTORY
            fd = os.open(path if parent is None else name, flags, dir_fd=parent)
            self.fds.append(fd)
            details = os.fstat(fd)
            self.records[path] = (fd, _identity(details, directory), directory, code)
        if directory:
            valid = stat.S_ISDIR(details.st_mode)
        else:
            valid = stat.S_ISREG(details.st_mode) and 1 <= details.st_size <= maximum
        if not valid or (private and (details.st_uid != os.getuid() or stat.S_IMODE(details.st_mode) & 0o077)):
            _fail('MODEL_INPUT_CODE_CHANGED' if code else 'MODEL_INPUT_SOURCE_CHANGED')
        return fd

    def root(self, path, private=True, code=False):
        if (type(path) is not str or len(path) > 4096 or not path.startswith('/')
                or any(p in ('', '.', '..') for p in path.split('/')[1:])):
            _fail('MODEL_INPUT_BINDING_INVALID')
        label = self.records['/'][3] if '/' in self.records else code
        fd = self._open('/', True, False, code=label)
        current = ''
        for part in path.split('/')[1:]:
            current += '/'+part
            # Shared ancestry can already be pinned for code or a fixture root.
            # Retain the original label when reusing it.
            label = self.records[current][3] if current in self.records else code
            fd = self._open(current, True, private and current == path,
                            parent=fd, name=part, code=label)
        return fd

    def path(self, fd):
        return next(p for p, r in self.records.items() if r[0] == fd)

    def directory(self, parent, name):
        return self._open(self.path(parent)+'/'+name, True, True, parent=parent, name=name)

    def file(self, parent, name, maximum=MAX_BODY, code=False):
        return self._open(self.path(parent)+'/'+name, False, not code, parent=parent,
                          name=name, code=code, maximum=maximum)

    def absent(self, parent, name):
        path = self.path(parent)+'/'+name
        try:
            os.stat(name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            self.absences.append(path)
            return
        _fail('MODEL_INPUT_SOURCE_CHANGED')  # any type; never open or delete it

    def hardlink(self, first, second):
        a, b = os.fstat(first), os.fstat(second)
        if (a.st_dev, a.st_ino) != (b.st_dev, b.st_ino):
            _fail('MODEL_INPUT_SOURCE_CHANGED')

    def check(self, descriptors=True):
        for path, (fd, identity, directory, code) in self.records.items():
            failure = 'MODEL_INPUT_CODE_CHANGED' if code else 'MODEL_INPUT_SOURCE_CHANGED'
            try:
                if descriptors and _identity(os.fstat(fd), directory) != identity: _fail(failure)
                if _identity(os.stat(path, follow_symlinks=False), directory) != identity: _fail(failure)
            except OSError:
                _fail(failure)
        for path in self.absences:
            try: os.stat(path, follow_symlinks=False)
            except FileNotFoundError: continue
            except OSError: _fail('MODEL_INPUT_SOURCE_CHANGED')
            _fail('MODEL_INPUT_SOURCE_CHANGED')

    def close(self):
        if self.closed: return
        self.closed = True
        failure = None
        try: self.check()
        except BaseException as exc: failure = exc
        for fd in reversed(self.fds):
            try: os.close(fd)
            except BaseException:
                failure = ModelInputError('MODEL_INPUT_INTEGRITY_UNRESOLVED')
        try: self.check(descriptors=False)
        except BaseException as exc:
            if failure is None: failure = exc
        if failure is not None: raise failure from None


def _pin_code(guard):
    root = guard.root(str(Path(__file__).parent), private=False, code=True)
    for name, digest in [*TRUSTED_CODE.values(), ('model_input.py', CODE_SHA256)]:
        fd = guard.file(root, name, code=True)
        chunks, remaining = [], MAX_BODY+1
        while remaining:
            part = os.read(fd, min(65536, remaining))  # code files only, never source bodies
            if not part: break
            chunks.append(part); remaining -= len(part)
        raw = b''.join(chunks)
        if len(raw) != os.fstat(fd).st_size or _sha(raw) != digest:
            _fail('MODEL_INPUT_CODE_CHANGED')
    guard.check()


def _pin_panel_metadata(guard, b, complete, joined=False):
    root = guard.root(b['output_root'] if joined else b['panel_root'])
    run = b['run_id'] if joined else b['panel_run_id']
    if joined:
        failure = guard.directory(root, 'disqualified'); guard.absent(failure, run+'.json')
    objects = guard.directory(root, 'objects'); obj = guard.directory(objects, run)
    manifest = guard.file(obj, 'manifest.json', 65536)
    panel = guard.file(obj, 'panel.json' if joined else 'panel.csv')
    if os.fstat(panel).st_size != b['panel_bytes']: _fail('MODEL_INPUT_SOURCE_CHANGED')
    if joined and os.fstat(manifest).st_size != b['manifest_bytes']: _fail('MODEL_INPUT_SOURCE_CHANGED')
    positive = guard.directory(root, 'complete'); negative = guard.directory(root, 'incomplete')
    guard.absent(negative if complete else positive, run+'.json')
    marker = guard.file(positive if complete else negative, run+'.json', 65536)
    guard.hardlink(manifest, marker)


def _pin_admitted_metadata(guard, request, payload):
    # Called ONLY after full public reconstruction/equality. Refused lanes are not
    # opened here. No receipt, source body or panel body is read by this guard.
    for kind in ('commodity', 'fx'):
        channel = payload['channel_statuses'][kind]
        if channel not in ('complete_synthetic', 'natural_incomplete_diagnostic'): continue
        if payload['component_identities'][kind] is None: _fail('MODEL_INPUT_INTEGRITY_UNRESOLVED')
        b = request['component_bindings'][kind]
        root = guard.root(b['original_root']); objects = guard.directory(root, 'objects')
        run = b['fixture_run_id'] if kind == 'commodity' else b['source_run_id']
        obj = guard.directory(objects, run); committed = guard.directory(root, 'committed')
        receipt = guard.file(committed, run+'.json', 65536)
        if kind == 'commodity':
            original = guard.file(obj, 'original.csv')
            if os.fstat(original).st_size != b['original_bytes']: _fail('MODEL_INPUT_SOURCE_CHANGED')
        else:
            negative = guard.directory(root, 'incomplete'); guard.absent(negative, run+'.json')
            manifest = guard.file(obj, 'manifest.json', 65536); guard.hardlink(manifest, receipt)
            for sid, expected in b['source_originals'].items():
                original = guard.file(obj, sid+'.raw')
                if os.fstat(original).st_size != expected['bytes']: _fail('MODEL_INPUT_SOURCE_CHANGED')
        _pin_panel_metadata(guard, b, channel == 'complete_synthetic')
    channel = payload['channel_statuses']['joined']
    if channel in ('complete_synthetic', 'natural_incomplete_diagnostic'):
        if payload['joined_identity'] is None: _fail('MODEL_INPUT_INTEGRITY_UNRESOLVED')
        _pin_panel_metadata(guard, request['joined_publication'], channel == 'complete_synthetic', joined=True)
    guard.check()


def prepare_binary64_model_slice(snapshot: adapter.VerifiedPanelSeries, series_id: str,
                                 start_month: str, end_month: str) -> ModelInputSlice:
    guard = _Lifetime()
    try:
        if type(snapshot) is not adapter.VerifiedPanelSeries or type(series_id) is not str:
            _fail('MODEL_INPUT_BINDING_INVALID')
        months = _months(start_month, end_month)
        if series_id == 'iron_ore': _fail('UNIT_HOLD_DMTU')
        if series_id not in {r[1] for r in REGISTRY}: _fail('MODEL_INPUT_SERIES_UNSUPPORTED')
        request, payload = _decode(snapshot.request), _decode(snapshot.payload)
        _payload_shape(snapshot, request, payload)
        profile = _profile()
        _pin_code(guard)
        _fresh(snapshot, request)
        _pin_admitted_metadata(guard, request, payload)
        selected = next(s for s in payload['series'] if s['series_id'] == series_id)
        if selected['publication_status'] != 'complete_synthetic': _fail('MODEL_INPUT_PUBLICATION_NOT_COMPLETE')
        if any(m not in payload['required_months'] for m in months): _fail('MODEL_INPUT_MASK_INVALID')
        rows = [r for r in selected['rows'] if start_month <= r['month'] <= end_month]
        if [r['month'] for r in rows] != months: _fail('MODEL_INPUT_MASK_INVALID')
        values, converted, seen = [], [], {}
        for r in rows:
            if r['status'] != 'complete' or r['reasons'] or r['basis'] is None: _fail('MODEL_INPUT_ROW_UNAVAILABLE')
            x, receipt = _encode_basis(r['basis'])
            exact = _fraction(r['basis'])
            if receipt['float_hex'] in seen and seen[receipt['float_hex']] != exact:
                _fail('FLOAT_DISTINCT_VALUE_COLLISION')
            seen[receipt['float_hex']] = exact
            values.append(x)
            converted.append({'month': r['month'], 'available_at_proxy': r['available_at_proxy'], **receipt})
        guard.check()
        _fresh(snapshot, request)
        guard.check()
        if _profile() != profile: _fail('FLOAT_PROFILE_UNSUPPORTED')
        identity = {'request_sha256': _sha(snapshot.request), 'request_bytes': len(snapshot.request),
                    'payload_sha256': _sha(snapshot.payload), 'payload_bytes': len(snapshot.payload),
                    **{k: payload[k] for k in ('as_of', 'required_months', 'mask_sha256', 'component_identities', 'joined_identity', 'code_identities')}}
        out = {'schema_version': 'synthetic-binary64-model-slice.v1', **FLAGS, 'status': 'SYNTHETIC_MODEL_INPUT_ONLY',
               'series_identity': {k: v for k, v in selected.items() if k != 'rows'}, 'snapshot_identity': identity,
               'slice_identity': {'start_month': start_month, 'end_month': end_month, 'months': months, 'count': len(months),
                    'mask_sha256': _sha(_canonical(months)), 'exact_rows_sha256': _sha(_canonical([
                        {k: r[k] for k in ('month', 'available_at_proxy', 'basis')} for r in rows]))},
               'conversion_identity': {'version': 'binary64_fraction_nearest_even_v1', 'converter_sha256': CODE_SHA256,
                    'converter_bytes': CODE_BYTES, 'adapter_sha256': TRUSTED_CODE['adapter'][1], 'runtime_profile': profile,
                    'relative_error_bound': {'numerator': '1', 'denominator': '9007199254740992'}}, 'rows': converted}
        result = ModelInputSlice(snapshot.request, _canonical(out), tuple(values))
    except ModelInputError:
        raise
    except (adapter.PanelAdapterError, OSError, ValueError, TypeError, KeyError, OverflowError, RecursionError):
        _fail('MODEL_INPUT_INTEGRITY_UNRESOLVED')
    finally:
        guard.close()
    return result
