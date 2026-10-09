"""Owned XLSX fixtures only; no market original or statistical model fitting."""
import copy
import io
import json
import os
import re
from pathlib import Path
import zipfile

import pytest
from openpyxl import Workbook

from procure.evaluation import observed_admission as admission
from procure.evaluation import observed_runner as runner


def owned_fixture(tmp_path, *, cell=None, archive_edit=None, label_edit=None):
    inputs = tmp_path / "inputs"
    inputs.mkdir(mode=0o700)
    source = inputs / "owned_fake.xlsx"
    book = Workbook()
    sheet = book.active
    sheet.title = "Monthly Prices"
    sheet.cell(1, 1, "OWNED SYNTHETIC TEST FIXTURE")
    # Independent fixture contract from the bound current source manifest,
    # rather than copying the reader's constants into its own affirmative test.
    for col, label, unit in ((65, "Copper", "($/mt)"), (63, "Aluminum", "($/mt)")):
        sheet.cell(5, col, label); sheet.cell(6, col, unit)
    for i in range(801):
        index = 1960 * 12 + i
        sheet.cell(i + 7, 1, f"{index // 12:04d}M{index % 12 + 1:02d}")
        sheet.cell(i + 7, 65, float(100 + i))
        sheet.cell(i + 7, 63, float(200 + i))
    if cell is not None:
        sheet.cell(687, 65, cell)  # first selected month2016-09
    if label_edit:
        label_edit(sheet)
    book.save(source); book.close()
    if type(cell) is int and cell > 2**53:
        # openpyxl's writer rounds large ints. Generate the intended native
        # integer token explicitly; this does not certify real XML decimals.
        def exact_integer(members):
            return [(n, re.sub(rb'(<c r="BM687"[^>]*><v>)[^<]+',
                               lambda m: m[1] + str(cell).encode(), d)
                     if n == "xl/worksheets/sheet1.xml" else d) for n,d in members]
        archive_edit = exact_integer
    if archive_edit:
        raw = source.read_bytes()
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            members = [(m.filename, z.read(m)) for m in z.infolist()]
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            for name, data in archive_edit(members):
                z.writestr(name, data)
        source.write_bytes(out.getvalue())
    raw = source.read_bytes()  # owned generator, not an observed source
    receipt = {"schema_version": admission.SCHEMA, "fixture_id": "owned-fixture",
               "is_synthetic": True, "observed_authenticity": False, "observed_execution_authorized": False,
               "source_sha256": admission.digest(raw), "source_bytes": len(raw),
               "as_of_utc": "2026-10-08T00:00:00.000000Z"}
    receipt_raw = admission.canonical(receipt)
    protocol = {**runner.PROTOCOL_FIELDS, "status": "INFRASTRUCTURE_ONLY_OBSERVED_EXECUTION_HOLD",
                "observed_execution_authorized": False, "source_sha256": receipt["source_sha256"],
                "protocol_sha256": "b" * 64, "snapshot_sha256": "a" * 64}
    manifest = {"is_synthetic": True, "observed_authenticity": False, "observed_execution_authorized": False,
                "sha256": receipt["source_sha256"], "bytes": len(raw), "source_id": "owned_fixture"}
    pp, mp = inputs / "protocol.json", inputs / "manifest.json"
    pp.write_bytes(admission.canonical(protocol)); mp.write_bytes(admission.canonical(manifest))
    binding = admission.register_owned_fixture(source, receipt_raw, controls=(pp, mp))
    run = admission.register_owned_fixture_run(binding, pp, mp, tmp_path / "run")
    return source, receipt_raw, binding, run, pp, mp, tmp_path / "run"


def test_registered_public_reader_hashes_and_parses_same_bytes(tmp_path, monkeypatch):
    source, raw, binding, *_ = owned_fixture(tmp_path)
    import openpyxl
    real = openpyxl.load_workbook
    seen = []
    def traced(stream, **kwargs):
        assert isinstance(stream, io.BytesIO)
        seen.append(admission.digest(stream.getvalue()))
        return real(stream, **kwargs)
    monkeypatch.setattr(openpyxl, "load_workbook", traced)
    with admission.open_observed_native_snapshot(source, raw, expected_admission=binding) as read:
        assert list(read.series["copper"])[0] == "2016-09"
        assert len(read.series["copper"]) == len(read.series["aluminum"]) == 120
        assert read.series["copper"]["2016-09"] == 780.0
        assert read.receipt["full_parent_snapshot_exposed"] is True
        assert read.receipt["physical_test_actuals_untouched"] is False
        assert read.receipt["is_synthetic"] is True
        assert read.receipt["observed_authenticity"] is False
        basis = read.receipt["stored_value_basis"]["copper"]["2016-09"]
        assert basis == {"hex": (780.0).hex(), "numerator": "780", "denominator": "1"}
    assert seen == [json.loads(raw)["source_sha256"]]
    assert read.lifetime.closed


@pytest.mark.parametrize("fake", [None, object(), admission.RootAdmissionBinding("owned-fixture")])
def test_unregistered_authority_refuses_before_any_source_access(tmp_path, monkeypatch, fake):
    def forbidden(*args, **kwargs):
        raise AssertionError("body or metadata access before admission")
    monkeypatch.setattr(os, "lstat", forbidden)
    monkeypatch.setattr(os, "open", forbidden)
    with pytest.raises(ValueError, match="registered.*HOLD"):
        admission.open_observed_native_snapshot(tmp_path / "absent.xlsx", b"{}", expected_admission=fake)


def test_copied_binding_and_altered_receipt_are_not_authority(tmp_path):
    source, raw, binding, *_ = owned_fixture(tmp_path)
    with pytest.raises(ValueError, match="registered"):
        admission.open_observed_native_snapshot(source, raw, expected_admission=copy.copy(binding))
    with pytest.raises(ValueError, match="foreign"):
        admission.open_observed_native_snapshot(source, raw + b" ", expected_admission=binding)


@pytest.mark.parametrize("value", [True, "text", "=1+2", "#VALUE!", 0, -1, 1e-310, 9007199254740993])
def test_required_native_cells_refuse_coercion(tmp_path, value):
    source, raw, binding, *_ = owned_fixture(tmp_path, cell=value)
    with pytest.raises(ValueError, match="native|integer"):
        admission.open_observed_native_snapshot(source, raw, expected_admission=binding)


@pytest.mark.parametrize("edit", [
    lambda s: s.cell(687, 65).__setattr__("value", None),
    lambda s: s.cell(7, 1).__setattr__("value", "1960M02"),
    lambda s: s.cell(5, 65).__setattr__("value", "wrong"),
    lambda s: s.cell(807, 1).__setattr__("value", None),
    lambda s: s.cell(7, 200, "dimension bound"),
])
def test_required_cells_labels_headers_and_dimensions_are_complete(tmp_path, edit):
    source, raw, binding, *_ = owned_fixture(tmp_path, label_edit=edit)
    with pytest.raises(ValueError):
        admission.open_observed_native_snapshot(source, raw, expected_admission=binding)


@pytest.mark.parametrize("edit", [
    lambda m: m + [(m[0][0], m[0][1])],
    lambda m: m + [("../rogue.xml", b"<x/>")],
    lambda m: [(n, b'<!DOCTYPE x [<!ENTITY y "z">]><x/>' if n == "xl/workbook.xml" else d) for n,d in m],
    lambda m: m + [("xl/_rels/evil.rels", b'<Relationships><Relationship TargetMode="External" Target="https://invalid/"/></Relationships>')],
    lambda m: m + [("huge.bin", b"x" * (admission.MAX_MEMBER + 1))],
])
def test_zip_boundaries_reject_unsafe_archives(tmp_path, edit):
    source, raw, binding, *_ = owned_fixture(tmp_path, archive_edit=edit)
    with pytest.raises(ValueError, match="ZIP|XML|external"):
        admission.open_observed_native_snapshot(source, raw, expected_admission=binding)


def test_source_replacement_hardlink_and_control_change_refuse(tmp_path):
    source, raw, binding, _, pp, *_ = owned_fixture(tmp_path)
    original = source.read_bytes()
    other = source.with_name("replacement.xlsx")
    other.write_bytes(original); os.replace(other, source)
    with pytest.raises(ValueError, match="identity"):
        admission.open_observed_native_snapshot(source, raw, expected_admission=binding)
    # Re-registration is a new trusted fixture, not mutation of the old grant.
    binding = admission.register_owned_fixture(source, raw, controls=(pp,))
    os.link(source, source.with_name("hardlink"))
    with pytest.raises(ValueError, match="identity"):
        admission.open_observed_native_snapshot(source, raw, expected_admission=binding)


def test_parse_time_source_or_control_replacement_prevents_result(tmp_path, monkeypatch):
    source, raw, binding, _, pp, *_ = owned_fixture(tmp_path)
    real = admission._parse_native
    def changed(data):
        parsed = real(data)
        pp.write_bytes(pp.read_bytes() + b" ")
        return parsed
    monkeypatch.setattr(admission, "_parse_native", changed)
    with pytest.raises(ValueError, match="identity|close"):
        admission.open_observed_native_snapshot(source, raw, expected_admission=binding)


def test_first_fstat_failure_still_closes_owned_fd_once(tmp_path, monkeypatch):
    source, raw, binding, *_ = owned_fixture(tmp_path)
    real_fstat, real_close = os.fstat, os.close
    seen, closed = [], []
    def broken(fd):
        seen.append(fd)
        raise OSError("owned injected first fstat failure")
    def close(fd):
        closed.append(fd); real_close(fd)
    monkeypatch.setattr(os, "fstat", broken)
    monkeypatch.setattr(os, "close", close)
    with pytest.raises(OSError):
        admission.open_observed_native_snapshot(source, raw, expected_admission=binding)
    assert closed == seen and len(closed) == 1
    monkeypatch.setattr(os, "fstat", real_fstat)


def test_reader_close_uncertainty_never_retries_fd(tmp_path, monkeypatch):
    source, raw, binding, *_ = owned_fixture(tmp_path)
    read = admission.open_observed_native_snapshot(source, raw, expected_admission=binding)
    real_close = os.close
    closed = []
    def injected(fd):
        closed.append(fd); real_close(fd)
        if len(closed) == 1:
            raise OSError("injected close uncertainty")
    monkeypatch.setattr(os, "close", injected)
    with pytest.raises(ValueError, match="close/postclose"):
        read.close()
    assert len(closed) == len(set(closed)) == len(read.lifetime.fds)


def test_registration_never_accepts_observed_flags(tmp_path):
    source, raw, *_ = owned_fixture(tmp_path)
    grant = json.loads(raw)
    grant.update(is_synthetic=False, observed_authenticity=True, observed_execution_authorized=True)
    with pytest.raises(ValueError, match="only synthetic"):
        admission.register_owned_fixture(source, admission.canonical(grant))


def test_actual_xml_rows_cannot_hide_behind_small_dimension_metadata(tmp_path):
    def hidden(members):
        return [(name, data.replace(b"</sheetData>",
                    b'<row r="1100"><c r="A1100" t="inlineStr"><is><t>hidden</t></is></c></row></sheetData>')
                 if name == "xl/worksheets/sheet1.xml" else data) for name, data in members]
    source, raw, binding, *_ = owned_fixture(tmp_path, archive_edit=hidden)
    with pytest.raises(ValueError, match="actual.*row|expansion"):
        admission.open_observed_native_snapshot(source, raw, expected_admission=binding)


def test_named_observed_bytes_cannot_be_relabelled_fixture_before_access(tmp_path, monkeypatch):
    source, raw, *_ = owned_fixture(tmp_path)
    grant = json.loads(raw)
    grant["source_sha256"] = "ea1c350827878ea3bbe30e3cda16a13fd3bd5b409b8458940dc94a36b5a33154"
    def forbidden(*args, **kwargs):
        raise AssertionError("observed original accessed")
    monkeypatch.setattr(os, "open", forbidden)
    with pytest.raises(ValueError, match="observed original"):
        admission.register_owned_fixture(source, admission.canonical(grant))


def test_source_hash_mismatch_refuses_before_parser(tmp_path, monkeypatch):
    source, raw, *_ = owned_fixture(tmp_path)
    grant = json.loads(raw); grant["source_sha256"] = "0" * 64
    raw = admission.canonical(grant)
    binding = admission.register_owned_fixture(source, raw)
    def forbidden(*args, **kwargs):
        raise AssertionError("hash mismatch reached parser")
    monkeypatch.setattr(admission, "_parse_native", forbidden)
    with pytest.raises(ValueError, match="hash mismatch"):
        admission.open_observed_native_snapshot(source, raw, expected_admission=binding)


def test_private_root_and_code_identity_changes_refuse_before_body(tmp_path, monkeypatch):
    source, raw, binding, *_ = owned_fixture(tmp_path)
    source.parent.chmod(0o755)
    with pytest.raises(ValueError, match="identity"):
        admission.open_observed_native_snapshot(source, raw, expected_admission=binding)
    source.parent.chmod(0o700)  # owned fixture cleanup, never source marker deletion
    binding = admission.register_owned_fixture(source, raw)
    real_stat = os.lstat
    def changed(path, *args, **kwargs):
        info = real_stat(path, *args, **kwargs)
        if Path(path) == Path(admission.__file__):
            from types import SimpleNamespace
            return SimpleNamespace(st_dev=info.st_dev, st_ino=info.st_ino, st_mode=info.st_mode, st_uid=info.st_uid,
                                   st_nlink=info.st_nlink, st_size=info.st_size, st_mtime_ns=info.st_mtime_ns + 1,
                                   st_ctime_ns=info.st_ctime_ns)
        return info
    monkeypatch.setattr(os, "lstat", changed)
    with pytest.raises(ValueError, match="identity"):
        admission.open_observed_native_snapshot(source, raw, expected_admission=binding)


def test_utf16_dtd_is_refused(tmp_path):
    def edit(members):
        return [(n, '<?xml version="1.0" encoding="utf-16"?><!DOCTYPE x [<!ENTITY y "z">]><x/>'.encode("utf-16")
                 if n == "xl/workbook.xml" else data) for n,data in members]
    source, raw, binding, *_ = owned_fixture(tmp_path, archive_edit=edit)
    with pytest.raises(ValueError, match="DTD/entity"):
        admission.open_observed_native_snapshot(source, raw, expected_admission=binding)


def _native_identity(path, directory=False):
    s = os.lstat(path)
    keys = ('dev', 'ino', 'mode', 'uid') if directory else ('dev', 'ino', 'mode', 'uid', 'nlink', 'size', 'mtime_ns', 'ctime_ns')
    values = (s.st_dev, s.st_ino, s.st_mode, s.st_uid) if directory else (
        s.st_dev, s.st_ino, s.st_mode, s.st_uid, s.st_nlink, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
    return dict(zip(keys, values))


def _native_ancestors(path):
    return [{'path': str(p), 'identity': _native_identity(p, True)} for p in reversed(Path(path).parents)]


def _native_pin(path):
    import hashlib
    path = Path(path)
    raw = path.read_bytes()
    return {'path': str(path), 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest(),
            'identity': _native_identity(path), 'ancestors': _native_ancestors(path)}


def native_owned_fixture(tmp_path, monkeypatch, *, mutate=None, register=True):
    """Only this owning test utility configures isolated synthetic host state."""
    import hashlib, sys, weakref
    from datetime import datetime, timedelta, timezone
    root = tmp_path.resolve()
    source, _, _, _, pp, mp, output = owned_fixture(root)
    for name in ('_NATIVE_HOSTS', '_NATIVE_RUNS', '_NATIVE_LIVES'):
        monkeypatch.setattr(admission, name, weakref.WeakKeyDictionary())
    monkeypatch.setattr(admission, '_NATIVE_ACTIVE', None)
    project = Path(__file__).parents[2]
    code = []
    for rel in ('procurement-engine/src/procure/evaluation/observed_admission.py',
                'procurement-engine/src/procure/evaluation/observed_runner.py',
                'procurement-engine/src/procure/evaluation/forecast_scorecard.py',
                'procurement-engine/src/procure/forecast/models.py'):
        pin = _native_pin(project / rel)
        raw = (project / rel).read_bytes()
        code.append({'repository_path': rel, 'file': pin,
                     'git_blob': hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()})
    from importlib.metadata import distribution
    from importlib.machinery import PathFinder
    packages = []
    for name in ('numpy', 'pandas', 'openpyxl', 'scikit-learn', 'statsmodels', 'lightgbm'):
        dist = distribution(name)
        metadata = Path(dist._path) / 'METADATA'
        origin = PathFinder.find_spec('sklearn' if name == 'scikit-learn' else name).origin
        packages.append({'distribution': name, 'version': dist.version, 'metadata': _native_pin(metadata), 'module_origin': origin})
    aliases = []; current = Path(sys.executable)
    while current.is_symlink():
        target = os.readlink(current)
        aliases.append({'path': str(current), 'target': target, 'identity': _native_identity(current), 'ancestors': _native_ancestors(current)})
        next_path = Path(target) if Path(target).is_absolute() else current.parent / target
        current = next_path.parent.resolve() / next_path.name
    now = datetime.now(timezone.utc)
    utc = lambda d: d.strftime('%Y-%m-%dT%H:%M:%S.%fZ')
    root_control = source.parent / 'root-native-test-control.json'
    control = {'schema_version': 'root-native-run-control.v1', 'status': 'ROOT_ACCEPTED_NATIVE_FIXTURE_TEST',
               'profile': 'native_fixture_test', 'acceptance_id': 'owning-test-authority', 'run_id': 'owning-test-run',
               'integrated_head': '1' * 40, 'integrated_tree': '2' * 40,
               'integration_receipt_sha256': '3' * 64, 'design_acceptance_sha256': '4' * 64,
               'numerical_review_sha256': '5' * 64, 'runtime_review_sha256': '6' * 64,
               'issued_at_utc': utc(now - timedelta(seconds=10)), 'expires_at_utc': utc(now + timedelta(hours=1)),
               'analysis_as_of_utc': '2026-10-08T12:00:00.000000Z',
               'source': {'file': _native_pin(source), 'source_id': 'owned_fixture', 'source_url': 'synthetic:owned-test',
                          'retrieved_utc': '2026-10-08T00:00:00.000000Z', 'rights_disposition_sha256': '7' * 64,
                          'sheet': 'Monthly Prices', 'month_range': '1960M01-2026M09',
                          'series': {'copper': {'column': 'BM', 'index': 65, 'label': 'Copper', 'native_unit': '($/mt)', 'unit': 'USD/metric tonne', 'currency': 'USD'},
                                     'aluminum': {'column': 'BK', 'index': 63, 'label': 'Aluminum', 'native_unit': '($/mt)', 'unit': 'USD/metric tonne', 'currency': 'USD'}},
                          'representation': 'openpyxl_native_binary64', 'full_parent_snapshot_exposed': True,
                          'physical_test_actuals_untouched': False, 'vintage_status': 'latest_vintage_only_historical_publication_unknown',
                          'availability_proxy': 'month_m_available_at_end_of_m_plus_1; not historical publication proof'},
               'controls': {'protocol': _native_pin(pp), 'manifest': _native_pin(mp), 'root_acceptance_path': str(root_control)},
               'code': code, 'test_artifact_blobs': {str(p.relative_to(project)): '8' * 40 for p in (Path(__file__), Path(__file__).with_name('test_observed_forecast_runner.py'))},
               'runtime': {'executable_alias': sys.executable, 'resolved_executable': _native_pin(Path(sys.executable).resolve()),
                           'alias_chain': aliases, 'python_version': sys.version.split()[0], 'packages': packages,
                           'model_backends': {'naive': 'naive', 'ets': 'ets', 'gbm': 'sklearn'}},
               'calendar': json.loads(admission.canonical(admission.NATIVE_CALENDAR)),
               'outputs': {'path': str(output), 'parent': str(output.parent), 'parent_identity': _native_identity(output.parent, True),
                           'owner_uid': os.getuid(), 'create_mode': 448, 'absent_required': True, 'overwrite': False},
               'budgets': json.loads(admission.canonical(admission.NATIVE_BUDGETS)),
               'permissions': {'local_source_body_read': True, 'local_statistical_execution': False, 'observed_execution': False,
                               'synthetic_test_execution': True, 'network': False, 'provider': False, 'source_download': False, 'sql': False}}
    if mutate:
        mutate(control)
    raw = admission.canonical(control); root_control.write_bytes(raw)
    authority = admission._issue_root_native_host_authority(acceptance_id='owning-test-authority', accepted_control=_native_pin(root_control),
                                                           integrated_head='1' * 40, integrated_tree='2' * 40, profile='native_fixture_test')
    if not register:
        return authority, control, raw, source, pp, mp, output
    binding = admission.register_root_accepted_native_run(authority, raw, source_file=source, protocol_path=pp, manifest_path=mp, output_dir=output)
    return source, raw, authority, binding, pp, mp, output


@pytest.mark.parametrize('change', ['permission','calendar','budget','source','unit','expiry','code','output',
                                     'package','origin','alias','head','unknown'])
def test_native_host_control_refusal_precedes_source_body(tmp_path,monkeypatch,change):
    def mutate(c):
        if change=='permission':c['permissions']['observed_execution']=True
        if change=='calendar':c['calendar']['minimum_training_months']=59
        if change=='budget':c['budgets']['retries']=1
        if change=='source':c['source']['file']['sha256']=admission._HELD_OBSERVED_SHA256
        if change=='unit':c['source']['series']['copper']['currency']='INR'
        if change=='expiry':c['expires_at_utc']='2026-01-01T00:00:00.000000Z'
        if change=='code':c['code'][0]['file']['sha256']='0'*64
        if change=='output':c['outputs']['path']=str(Path(c['source']['file']['path']).parent/'run')
        if change=='package':c['runtime']['packages'][0]['metadata']['sha256']='0'*64
        if change=='origin':c['runtime']['packages'][0]['module_origin']='/missing/synthetic-module.py'
        if change=='alias':c['runtime']['alias_chain'][0]['target']='synthetic-wrong-target'
        if change=='head':c['integrated_head']='0'*40
        if change=='unknown':c['unexpected_permission']=True
    authority,c,raw,source,pp,mp,output=native_owned_fixture(tmp_path.resolve(),monkeypatch,mutate=mutate,register=False)
    seen=[]; original=admission._NativeLifetime.read; identity=os.lstat(source)
    def read(self,fd,limit):
        if os.fstat(fd).st_ino==identity.st_ino and os.fstat(fd).st_dev==identity.st_dev:
            seen.append(fd)
        return original(self,fd,limit)
    monkeypatch.setattr(admission._NativeLifetime,'read',read)
    with pytest.raises(ValueError):admission.register_root_accepted_native_run(authority,raw,source_file=source,protocol_path=pp,manifest_path=mp,output_dir=output)
    assert seen==[] and not output.exists()


def test_native_host_unissued_copied_cross_mode_no_source_effect(tmp_path,monkeypatch):
    import dataclasses
    fixture=native_owned_fixture(tmp_path.resolve(),monkeypatch)
    source,raw,authority,binding,pp,mp,output=fixture
    monkeypatch.setattr(admission,'_parse_native',lambda _: (_ for _ in ()).throw(AssertionError('body parse forbidden')))
    for unissued in (object(),admission.RootNativeRunBinding(binding.run_id),copy.copy(binding),dataclasses.replace(binding)):
        with pytest.raises(ValueError):runner.run_offline_evaluation(pp,mp,source,output,'2026-10-08',accepted_run=unissued)
    for wrong in (admission.RootNativeHostAuthority(authority.acceptance_id),copy.copy(authority),object()):
        with pytest.raises(ValueError):admission.register_root_accepted_native_run(wrong,raw,source_file=source,protocol_path=pp,manifest_path=mp,output_dir=output)
    with pytest.raises(ValueError):admission.open_root_native_snapshot(binding)
    assert not output.exists()


def test_native_host_reservation_survives_failed_registration(tmp_path,monkeypatch):
    authority,c,raw,source,pp,mp,output=native_owned_fixture(tmp_path.resolve(),monkeypatch,register=False)
    output.mkdir()
    with pytest.raises(ValueError):admission.register_root_accepted_native_run(authority,raw,source_file=source,protocol_path=pp,manifest_path=mp,output_dir=output)
    # Removing a test-created empty directory does not reset a consumed authority.
    output.rmdir()
    with pytest.raises(ValueError,match='consumed'):admission.register_root_accepted_native_run(authority,raw,source_file=source,protocol_path=pp,manifest_path=mp,output_dir=output)
    with pytest.raises(ValueError,match='configured'):admission._issue_root_native_host_authority(acceptance_id='again',accepted_control=_native_pin(source.parent/'root-native-test-control.json'),integrated_head='1'*40,integrated_tree='2'*40,profile='native_fixture_test')


@pytest.mark.parametrize('fault',['first_fstat','close','postclose'])
def test_native_host_lifetime_owns_and_closes_once_under_fault(tmp_path,monkeypatch,fault):
    path=tmp_path.resolve()/'fake-control';path.write_bytes(b'owned synthetic control');life=admission._NativeLifetime()
    real_fstat,real_close=os.fstat,os.close;closed=[]
    if fault=='first_fstat':
        monkeypatch.setattr(admission.os,'fstat',lambda fd: (_ for _ in ()).throw(OSError('synthetic fstat fault')))
        try:
            with pytest.raises(OSError):life.open(path,private=True)
        finally:
            monkeypatch.setattr(admission.os,'fstat',real_fstat)
    else:life.open(path,private=True)
    def close(fd):
        closed.append(fd);real_close(fd)
        if fault=='close':raise OSError('synthetic ambiguous close')
    monkeypatch.setattr(admission.os,'close',close)
    if fault=='postclose':
        original=life.check
        def check(*,postclose=False):
            if postclose:raise ValueError('synthetic postclose fault')
            return original(postclose=postclose)
        monkeypatch.setattr(life,'check',check)
    if fault=='first_fstat':life.close()
    else:
        with pytest.raises(ValueError,match='close'):life.close()
    assert len(closed)==len(set(closed))==len(life.fds)
    with pytest.raises(ValueError,match='closed'):life.close()


def test_native_host_every_new_fd_first_fstat_and_close_failure_owns_once(tmp_path,monkeypatch):
    path=tmp_path.resolve()/'owned-control';path.write_bytes(b'synthetic')
    real_open,real_fstat,real_close=os.open,os.fstat,os.close
    for phase in ('first_fstat','close'):
        for target in range(1,len(path.parts)+1):
            life=admission._NativeLifetime();opened=[];closed=[];seen=set()
            def opening(*args,**kwargs):
                fd=real_open(*args,**kwargs);opened.append(fd);return fd
            def statting(fd):
                if fd not in seen:
                    seen.add(fd)
                    if phase=='first_fstat' and len(opened)==target:raise OSError('synthetic first fstat')
                return real_fstat(fd)
            def closing(fd):
                closed.append(fd);real_close(fd)
                if phase=='close' and fd==opened[target-1]:raise OSError('synthetic ambiguous close')
            with monkeypatch.context() as patch:
                patch.setattr(admission.os,'open',opening);patch.setattr(admission.os,'fstat',statting);patch.setattr(admission.os,'close',closing)
                if phase=='first_fstat':
                    with pytest.raises(OSError):life.open(path,private=True)
                    life.close()
                else:
                    life.open(path,private=True)
                    with pytest.raises(ValueError,match='close'):life.close()
            assert len(closed)==len(set(closed))==len(opened)==len(life.fds)


def test_native_host_shared_prefix_capacity_and_read_failure_close_all(tmp_path,monkeypatch):
    path=tmp_path.resolve()/'owned-control';path.write_bytes(b'synthetic');life=admission._NativeLifetime()
    for _ in range(128-len(path.parts)+1):life.open(path,private=True)
    assert len(life.fds)==128
    with pytest.raises(ValueError,match='capacity'):life.open(path,private=True)
    life.close()
    life=admission._NativeLifetime();fd=life.open(path,private=True)
    with monkeypatch.context() as patch:
        patch.setattr(admission.os,'read',lambda *_:(_ for _ in ()).throw(OSError('synthetic bounded read fault')))
        with pytest.raises(OSError):life.read(fd,9)
    life.close();assert life.closed


def test_native_host_strong_reservation_survives_weak_binding_collection(tmp_path,monkeypatch):
    import gc,weakref
    fixture=native_owned_fixture(tmp_path.resolve(),monkeypatch)
    source,raw,authority,binding,pp,mp,output=fixture;ref=weakref.ref(binding)
    del fixture,binding;gc.collect();assert ref() is None
    with pytest.raises(ValueError,match='consumed'):
        admission.register_root_accepted_native_run(authority,raw,source_file=source,protocol_path=pp,manifest_path=mp,output_dir=output)
    with pytest.raises(ValueError):admission.native_run_record(admission.RootNativeRunBinding('owning-test-run'))
    assert not output.exists()


def test_native_host_expiry_after_registration_refuses_before_body(tmp_path,monkeypatch):
    from datetime import timedelta
    fixture=native_owned_fixture(tmp_path.resolve(),monkeypatch);original=admission.datetime
    class ExpiredClock(original):
        @classmethod
        def now(cls,tz=None):return original.now(tz)+timedelta(hours=2)
    monkeypatch.setattr(admission,'datetime',ExpiredClock)
    monkeypatch.setattr(admission,'_parse_native',lambda _:(_ for _ in ()).throw(AssertionError('expired grant reached body')))
    monkeypatch.setattr(runner,'fit_in_owned_child',lambda *_a,**_k:(_ for _ in ()).throw(AssertionError('expired grant reached fitter')))
    source,raw,authority,binding,pp,mp,output=fixture
    with pytest.raises(ValueError,match='expired'):runner.run_offline_evaluation(pp,mp,source,output,'2026-10-08',accepted_run=binding)
    assert not output.exists()
