"""Focused owned synthetic model-input tests; no model/runner/source acquisition."""
from fractions import Fraction
import unittest
from procure.evaluation import model_input as subject


def basis(q):
    return {'numerator': str(q.numerator), 'denominator': str(q.denominator)}


class NumericTests(unittest.TestCase):
    # Catches loss of exact receipts and substitution of decimal/display arithmetic.
    def test_one_third_exact_receipts(self):
        x, r = subject._encode_basis({'numerator': '1', 'denominator': '3'})
        self.assertEqual(x.hex(), '0x1.5555555555555p-2')
        self.assertEqual(r['encoded_basis'], {'numerator': '6004799503160661', 'denominator': '18014398509481984'})
        self.assertEqual(r['absolute_error'], {'numerator': '1', 'denominator': '54043195528445952'})
        self.assertEqual(r['relative_error'], {'numerator': '1', 'denominator': '18014398509481984'})

    # Catches admitting values outside exact normal range even when rounding normalizes them.
    def test_exact_range_refuses_roundback(self):
        maximum = Fraction((2**53-1)*2**971)
        for q in [Fraction(1, 2**1074), Fraction(1, 2**1022)-Fraction(1, 2**1075), maximum+1]:
            with self.subTest(q_kind='exact-range'):
                with self.assertRaisesRegex(subject.ModelInputError, '^FLOAT_RANGE_REFUSED$'):
                    subject._encode_basis(basis(q))

    def test_invalid_basis_is_not_a_float_or_unreduced_fraction(self):
        for b in [None, 1.0, {'numerator': '2', 'denominator': '6'},
                  {'numerator': '0', 'denominator': '1'}, {'numerator': '-1', 'denominator': '1'},
                  {'numerator': 'NaN', 'denominator': '1'}, {'numerator': True, 'denominator': '1'},
                  {'numerator': str(2**4096), 'denominator': '1'}]:
            with self.subTest(kind=str(type(b))):
                with self.assertRaisesRegex(subject.ModelInputError, '^MODEL_INPUT_BASIS_INVALID$'):
                    subject._encode_basis(b)

    def test_both_tie_parities_and_normal_endpoints(self):
        for q, h, tied in [
            (Fraction(2**53+1, 2**53), '0x1.0000000000000p+0', 'successor'),
            (Fraction(2**53+3, 2**53), '0x1.0000000000002p+0', 'predecessor'),
            (Fraction(1, 2**1022), '0x1.0000000000000p-1022', None),
            (Fraction((2**53-1)*2**971), '0x1.fffffffffffffp+1023', None)]:
            with self.subTest(h=h):
                x, r = subject._encode_basis(basis(q))
                self.assertEqual(x.hex(), h)
                if tied:
                    self.assertTrue(r['nearest_certificate']['chosen_significand_even'])
                    self.assertTrue(r['nearest_certificate'][tied]['tied'])
                else:
                    self.assertEqual(r['absolute_error'], {'numerator':'0','denominator':'1'})
        self.assertEqual(r['nearest_certificate']['successor']['kind'], 'overflow_boundary')
        self.assertIsNone(r['nearest_certificate']['successor']['hex'])

    # Catches trusting a candidate rather than checking exact adjacent distances/parity.
    def test_wrong_candidate_and_wrong_tie_refuse(self):
        for q, x in [(Fraction(1,3), 0.5), (Fraction(2**53+1,2**53), float.fromhex('0x1.0000000000001p+0'))]:
            with self.subTest(q=q):
                with self.assertRaisesRegex(subject.ModelInputError, '^FLOAT_NEAREST_EVEN_REFUSED$'):
                    subject._certificate(q, x)


import copy
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
from unittest.mock import patch
from procure.evaluation import commodity_component as commodity
from procure.evaluation import fx_normalization as fx
from procure.evaluation import commodity_fx_join as join
from procure.evaluation import panel_adapter as adapter

FLAGS = dict(synthetic=True, source_authenticity=False, observed_eligible=False,
             execution_authorized=False, rights="synthetic_only")
ASOF = "2026-10-08T00:00:00.000000Z"
MONTHS = ["2026-07", "2026-08"]

def canon(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True,
                      allow_nan=False).encode('ascii')

def sha(body):
    return hashlib.sha256(body).hexdigest()

def csv_body(header, rows):
    s = io.StringIO(newline=''); w = csv.writer(s, lineterminator='\n')
    w.writerow(header); w.writerows(rows)
    return s.getvalue().encode('ascii')

def write(path, body):
    path.write_bytes(body); path.chmod(0o600)

def layout(root, run):
    for p in (root, root/'objects', root/'objects'/run, root/'committed'):
        p.mkdir(mode=0o700, exist_ok=True)

class PublicTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="model-input-owned-")
        self.home = Path(self.tmp.name).resolve(); self.serial = 0

    def tearDown(self):
        self.tmp.cleanup()

    def fixture(self, months=MONTHS, missing=None, absent=(), future='1', copper_values=None, fx_values=None):
            self.serial += 1
            home = self.home/str(self.serial); home.mkdir(mode=0o700)
            cr, fr, cp, fp = [home/n for n in ('co', 'fo', 'cp', 'fp')]
            run = 'fixture'; layout(cr, run)
            rows = []
            for m in months:
                for sid in ('copper', 'aluminum'):
                    value = '1500' if sid == 'aluminum' else ('2000' if m == '2026-07' else '1000')
                    if sid == 'copper' and copper_values is not None: value = copper_values[months.index(m)]
                    rows.append([m, sid, 'USD/metric_tonne', 'M' if (m,sid)==missing else 'A',
                                 '' if (m,sid)==missing else value])
            body = csv_body(['month','series_id','unit','status','value'], rows)
            receipt = {'schema_version':'synthetic-commodity-fixture-receipt.v1', **FLAGS,
                       'run_id':run,'first_month':months[0],'last_month':months[-1],
                       'month_count':len(months),'original_bytes':len(body),'original_sha256':sha(body)}
            write(cr/'objects'/run/'original.csv', body); write(cr/'committed'/(run+'.json'),canon(receipt))
            cv = commodity.load_synthetic_component(cr,run,FLAGS)
            ca = commodity.normalize_commodity_component(cv,commodity.SCHEMA,months,ASOF)
            cm = commodity.publish_commodity_component(ca,cv,cp,'panel')
            dates = {m: [m+'-01',m+'-02'] for m in months}
            layout(fr,run); (fr/'incomplete').mkdir(mode=0o700)
            originals=[]
            for sid,currency in [('ecb_usd_eur','USD'),('ecb_inr_eur','INR')]:
                rows=[]
                for m in months:
                    values = (['2','4'] if currency=='USD' else ['100','120']) if m=='2026-07' else (['3','3'] if currency=='USD' else [future,future])
                    if fx_values is not None: values = fx_values[currency][months.index(m)]
                    for day,value in zip(dates[m],values):
                        if (sid,day) not in absent:
                            rows.append([f'D.{currency}.EUR.SP00.A','D',currency,'EUR','SP00','A',day,value,'SYNTHETIC_QUOTE','0','A'])
                body=csv_body(fx.HEADERS,rows); write(fr/'objects'/run/(sid+'.raw'),body)
                originals.append({'source_id':sid,'path':f'objects/{run}/{sid}.raw','bytes':len(body),'sha256':sha(body)})
            sr={'schema_version':'offline-source-publication.v1','run_id':run,'status':'OFFLINE_COMPLETE',
                'offline_only':True,'execution_authorized':False,'originals':sorted(originals,key=lambda r:r['source_id']),
                'settled':True,'capture_verified':True}
            write(fr/'objects'/run/'manifest.json',canon(sr)); os.link(fr/'objects'/run/'manifest.json',fr/'committed'/(run+'.json'))
            for item in (fr/'objects'/run).iterdir(): item.chmod(0o400)
            (fr/'objects'/run).chmod(0o500)
            provenance={**FLAGS,'retrieved_at':ASOF}
            coverage={'schema_version':'synthetic-date-mask.v2','synthetic':True,'as_of':ASOF,
                      'required_months':list(months),'dates_by_month':dates}
            fv=fx.load_verified_originals(fr,run,provenance); fa=fx.normalize_ecb_pair(fv,fx.SCHEMA,coverage)
            fm=fx.publish_fx_panel(fa,provenance,fp,'panel')
            def binding(root,manifest):
                return {'panel_root':str(root),'panel_run_id':'panel',
                        'panel_manifest_sha256':sha((root/'objects/panel/manifest.json').read_bytes()),
                        **{k:manifest[k] for k in ('panel_sha256','panel_bytes','schema_sha256','code_sha256')}}
            cb={**binding(cp,cm),'original_root':str(cr),'fixture_run_id':run,
                'fixture_receipt_sha256':cv['receipt_sha256'],'original_sha256':cv['original_sha256'],'original_bytes':cv['original_bytes']}
            fb={**binding(fp,fm),'original_root':str(fr),'source_run_id':run,'provenance':provenance,
                'source_receipt_sha256':fv['receipt_sha256'],'source_originals':fm['source_originals'],
                'coverage_sha256':fm['coverage_sha256'],'coverage':coverage}
            policy={'schema_version':'synthetic-commodity-fx-join.v1',**FLAGS,'series_ids':['copper','aluminum'],
                    'required_months':list(months),'as_of':ASOF,'commodity_schema_sha256':sha(canon(commodity.SCHEMA)),
                    'fx_schema_sha256':sha(canon(fx.SCHEMA)),'fx_coverage_sha256':sha(canon(coverage)),
                    'arithmetic_version':'component_fraction_product_v1','rounding_version':'decimal50_half_even_normalized_plain_v1'}
            b={'commodity':cb,'fx':fb}
            jp=home/'jp'; candidate=join.compose_joined_panel(join.load_join_inputs(b,policy),policy)
            jm=join.publish_joined_panel(candidate,b,policy,jp,'joined')
            raw=(jp/'objects/joined/manifest.json').read_bytes()
            ref={'output_root':str(jp),'run_id':'joined','manifest_sha256':sha(raw),'manifest_bytes':len(raw),
                 'panel_sha256':jm['panel_sha256'],'panel_bytes':jm['panel_bytes']}
            return b,policy,ref

    def load(self, **kw):
        b, p, j = self.fixture(**kw)
        return b, p, j, adapter.load_verified_panel_series(b, p, j)

    def convert(self, snap, sid='copper_INR_per_kg', first='2026-07', last='2026-08'):
        return subject.prepare_binary64_model_slice(snap, sid, first, last)

    def test_public_exact_values_and_one_third(self):
        b, p, j, snap = self.load()
        out = self.convert(snap)
        self.assertEqual(out.values, (80.0, 1/3))
        r = json.loads(out.payload)
        self.assertEqual(r['rows'][0]['basis'], {'numerator':'80','denominator':'1'})
        self.assertEqual(r['rows'][0]['float_hex'], '0x1.4000000000000p+6')
        self.assertEqual(r['rows'][1]['relative_error'], {'numerator':'1','denominator':'18014398509481984'})
        aluminum = self.convert(snap, 'aluminum_INR_per_kg', last='2026-07')
        self.assertEqual(aluminum.values, (60.0,))
        self.assertEqual(out.request, snap.request)
        for k, v in FLAGS.items(): self.assertEqual(r[k], v)
        self.assertEqual(r['snapshot_identity']['payload_sha256'], sha(snap.payload))
        self.assertEqual(r['snapshot_identity']['code_identities'], json.loads(snap.payload)['code_identities'])
        self.assertEqual(r['slice_identity']['months'], MONTHS)
        self.assertNotIn('phase', r)

    # Two unequal real public commodity tokens collapse to one model encoding.
    def test_public_distinct_collision_refuses_whole_slice(self):
        _, _, _, snap = self.load(copper_values=['1', '1.00000000000000001'])
        with self.assertRaisesRegex(subject.ModelInputError, '^FLOAT_DISTINCT_VALUE_COLLISION$'):
            self.convert(snap, 'copper')

    # A same-byte replacement is invisible to a byte-only final reload.
    def test_same_byte_source_replacement_during_conversion_refuses(self):
        b, _, _, snap = self.load()
        source = Path(b['commodity']['original_root'])/'objects/fixture/original.csv'
        encode = subject._encode_basis; fired = False
        def replace(value):
            nonlocal fired
            result = encode(value)
            if not fired:
                fired = True
                other = source.with_name('replacement.csv'); write(other, source.read_bytes())
                other.replace(source)
            return result
        with patch.object(subject, '_encode_basis', side_effect=replace):
            with self.assertRaisesRegex(subject.ModelInputError, '^MODEL_INPUT_SOURCE_CHANGED$'):
                self.convert(snap)

    def test_public_both_tie_parities_via_exact_daily_crosses(self):
        usd = ['900719.9254740992']*2
        _, _, _, snap = self.load(fx_values={'USD': [usd, usd], 'INR': [
            ['900719.9254740993']*2, ['900719.9254740995']*2]})
        out = self.convert(snap, 'USDINR'); r = json.loads(out.payload)['rows']
        self.assertEqual([x.hex() for x in out.values], ['0x1.0000000000000p+0','0x1.0000000000002p+0'])
        self.assertEqual(r[0]['basis'], {'numerator':'9007199254740993','denominator':'9007199254740992'})
        self.assertEqual(r[1]['basis'], {'numerator':'9007199254740995','denominator':'9007199254740992'})
        self.assertTrue(r[0]['nearest_certificate']['successor']['tied'])
        self.assertTrue(r[1]['nearest_certificate']['predecessor']['tied'])
        self.assertTrue(all(row['nearest_certificate']['chosen_significand_even'] for row in r))

    def test_identical_exact_repeats_are_allowed(self):
        _, _, _, snap = self.load(copper_values=['1','1'])
        self.assertEqual(self.convert(snap, 'copper').values, (1.0,1.0))

    def test_forged_payload_copied_identities_is_not_attestation(self):
        _, _, _, snap = self.load()
        out = json.loads(snap.payload)
        variants = []
        for change in ['price','unit','proxy','flags','rows','extra','code']:
            bad = copy.deepcopy(out)
            if change == 'price': bad['series'][0]['rows'][0]['basis']['numerator'] = '1999'
            elif change == 'unit': bad['series'][0]['unit'] = 'INR/kg'
            elif change == 'proxy': bad['series'][0]['rows'][0]['available_at_proxy'] = ASOF
            elif change == 'flags': bad['observed_eligible'] = True
            elif change == 'rows': bad['series'][0]['rows'].reverse()
            elif change == 'extra': bad['grant'] = True
            else: bad['code_identities']['adapter'] = '0'*64
            variants.append(adapter.VerifiedPanelSeries(snap.request, canon(bad)))
        for fake in variants:
            with self.subTest(identity=sha(fake.payload)):
                with self.assertRaises(subject.ModelInputError): self.convert(fake, 'copper')

    def test_invalid_flags_refuse_before_any_open(self):
        _, _, _, snap = self.load(); data = json.loads(snap.payload); data['execution_authorized'] = True
        fake = adapter.VerifiedPanelSeries(snap.request, canon(data))
        with patch.object(subject.os, 'open', side_effect=AssertionError('must refuse before open')):
            with self.assertRaisesRegex(subject.ModelInputError, '^MODEL_INPUT_BINDING_INVALID$'):
                self.convert(fake)

    def test_noncanonical_duplicate_nonfinite_and_limits_refuse(self):
        _, _, _, snap = self.load()
        for raw in [b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":1.0}', b' '+snap.payload,
                    b'['*15+b'0'+b']'*15, b'x'*(4194304+1)]:
            with self.subTest(length=len(raw)):
                fake = adapter.VerifiedPanelSeries(snap.request, raw)
                with self.assertRaisesRegex(subject.ModelInputError, '^MODEL_INPUT_BINDING_INVALID$'):
                    self.convert(fake)

    def test_bounds_and_iron_refuse_without_truncation(self):
        _, _, _, snap = self.load()
        for first, last in [('2026-08','2026-07'), ('2026-06','2026-07'), ('2026-07','2026-09'),
                            ('2026-7','2026-08'), ('0000-01','2026-08'), ('2026-07',True)]:
            with self.subTest(first=first, last=last):
                with self.assertRaisesRegex(subject.ModelInputError, '^MODEL_INPUT_MASK_INVALID$'):
                    self.convert(snap, first=first, last=last)
        with self.assertRaisesRegex(subject.ModelInputError, '^UNIT_HOLD_DMTU$'): self.convert(snap, 'iron_ore')
        with self.assertRaisesRegex(subject.ModelInputError, '^MODEL_INPUT_SERIES_UNSUPPORTED$'): self.convert(snap, 'gold')

    def test_native_complete_with_absent_and_refused_companion(self):
        b, p, j = self.fixture(); b['fx'] = None
        snap = adapter.load_verified_panel_series(b, p)
        self.assertEqual(self.convert(snap, 'copper').values, (2000.0,1000.0))
        with self.assertRaisesRegex(subject.ModelInputError, '^MODEL_INPUT_PUBLICATION_NOT_COMPLETE$'):
            self.convert(snap)
        b, p, j = self.fixture(); write(Path(b['fx']['panel_root'])/'objects/panel/panel.csv', b'broken')
        snap = adapter.load_verified_panel_series(b,p,j)
        out = self.convert(snap, 'copper')
        self.assertEqual(out.values, (2000.0,1000.0))
        self.assertIsNone(json.loads(out.payload)['snapshot_identity']['component_identities']['fx'])

    def test_natural_incomplete_is_not_rescued_by_complete_prefix(self):
        _, _, _, snap = self.load(missing=('2026-08','copper'))
        with self.assertRaisesRegex(subject.ModelInputError, '^MODEL_INPUT_PUBLICATION_NOT_COMPLETE$'):
            self.convert(snap, 'copper', last='2026-07')
        self.assertEqual(self.convert(snap, 'USDINR').values, (40.0,1/3))
        _, _, _, snap = self.load(absent=[('ecb_usd_eur','2026-08-02')])
        with self.assertRaisesRegex(subject.ModelInputError, '^MODEL_INPUT_PUBLICATION_NOT_COMPLETE$'):
            self.convert(snap, 'USDINR', last='2026-07')

    def test_receipt_rebound_observed_lane_never_opens_original(self):
        b, p, j = self.fixture()
        receipt = Path(b['commodity']['original_root'])/'committed/fixture.json'
        data = json.loads(receipt.read_bytes()); data['observed_eligible'] = True
        write(receipt, canon(data)); b['commodity']['fixture_receipt_sha256'] = sha(receipt.read_bytes())
        snap = adapter.load_verified_panel_series(b,p,j)
        opened = []; original_open = os.open
        def trace(path, *args, **kwargs):
            if str(path).endswith('original.csv'): opened.append(str(path))
            return original_open(path, *args, **kwargs)
        with patch.object(subject.os, 'open', side_effect=trace):
            out = self.convert(snap, 'USDINR')
        self.assertEqual(opened, [])
        self.assertEqual(out.values, (40.0,1/3))

    def test_changed_source_after_snapshot_refuses(self):
        b, _, _, snap = self.load()
        source = Path(b['commodity']['original_root'])/'objects/fixture/original.csv'
        raw = source.read_bytes().replace(b'2000',b'2001'); write(source,raw)
        with self.assertRaisesRegex(subject.ModelInputError, '^MODEL_INPUT_SNAPSHOT_MISMATCH$'):
            self.convert(snap)

    def test_final_public_reload_compares_whole_snapshot(self):
        _, _, _, snap = self.load(); original = adapter.load_verified_panel_series; calls = []
        def load(*args, **kwargs):
            fresh = original(*args, **kwargs); calls.append(fresh)
            if len(calls) == 2:
                data = json.loads(fresh.payload); data['series'][1]['rows'][0]['basis']['numerator'] = '1499'
                return adapter.VerifiedPanelSeries(fresh.request, canon(data))
            return fresh
        with patch.object(adapter,'load_verified_panel_series',side_effect=load):
            with self.assertRaisesRegex(subject.ModelInputError, '^MODEL_INPUT_SNAPSHOT_MISMATCH$'):
                self.convert(snap)
        self.assertEqual(len(calls),2)

    def test_late_any_type_disqualifier_preserved(self):
        for kind in ('bytes','directory','symlink','fifo'):
            with self.subTest(kind=kind):
                _, _, j, snap = self.load(); marker = Path(j['output_root'])/'disqualified/joined.json'
                encode = subject._encode_basis; fired = False; inode = None
                def add(value):
                    nonlocal fired, inode
                    result = encode(value)
                    if not fired:
                        fired = True
                        if kind == 'bytes': write(marker,b'failed-publication')
                        elif kind == 'directory': marker.mkdir(mode=0o700)
                        elif kind == 'symlink': marker.symlink_to('absent')
                        else: os.mkfifo(marker,0o600)
                        inode = marker.lstat().st_ino
                    return result
                with patch.object(subject,'_encode_basis',side_effect=add):
                    with self.assertRaisesRegex(subject.ModelInputError, '^MODEL_INPUT_SOURCE_CHANGED$'):
                        self.convert(snap)
                self.assertEqual(marker.lstat().st_ino,inode)
                if kind == 'bytes': self.assertEqual(marker.read_bytes(),b'failed-publication')

    def test_own_code_change_during_conversion_refuses(self):
        _, _, _, snap = self.load(); code = Path(subject.__file__); raw = code.read_bytes()
        encode = subject._encode_basis; fired = False
        def change(value):
            nonlocal fired
            result = encode(value)
            if not fired:
                fired = True; code.write_bytes(raw+b'\n# owned fault test\n')
            return result
        try:
            with patch.object(subject,'_encode_basis',side_effect=change):
                with self.assertRaisesRegex(subject.ModelInputError, '^MODEL_INPUT_CODE_CHANGED$'):
                    self.convert(snap)
        finally: code.write_bytes(raw)
        self.assertEqual(code.read_bytes(),raw)

    def test_same_byte_root_ancestor_and_hardlink_replacements_refuse(self):
        import shutil
        for kind in ('root','ancestor','marker'):
            with self.subTest(kind=kind):
                b, _, j, snap = self.load(); encode = subject._encode_basis; fired = False
                target = (Path(b['commodity']['original_root']) if kind == 'root' else
                          Path(b['commodity']['original_root']).parent if kind == 'ancestor' else
                          Path(j['output_root'])/'complete/joined.json')
                def replace(value):
                    nonlocal fired
                    result = encode(value)
                    if not fired:
                        fired = True
                        moved = target.with_name(target.name+'-old'); target.rename(moved)
                        if kind == 'marker': write(target,moved.read_bytes())
                        else: shutil.copytree(moved,target)
                    return result
                with patch.object(subject,'_encode_basis',side_effect=replace):
                    with self.assertRaisesRegex(subject.ModelInputError, '^MODEL_INPUT_SOURCE_CHANGED$'):
                        self.convert(snap)

    def capture_guard(self):
        guards = []; init = subject._Lifetime.__init__
        def capture(guard): init(guard); guards.append(guard)
        return guards, patch.object(subject._Lifetime, '__init__', capture)

    def assert_closed(self, guard):
        for fd in guard.fds:
            with self.assertRaises(OSError): os.fstat(fd)

    def test_close_uncertainty_aborts_and_releases_owned_fds(self):
        _, _, _, snap = self.load(); guards, capture = self.capture_guard()
        close = subject._Lifetime.close
        def uncertain(guard):
            target = guard.fds[-1]; real = os.close; fired = False
            def fail(fd):
                nonlocal fired
                real(fd)
                if fd == target and not fired:
                    fired = True; raise OSError('controlled private close failure')
            with patch.object(subject.os, 'close',side_effect=fail): close(guard)
        with capture, patch.object(subject._Lifetime,'close',uncertain):
            with self.assertRaisesRegex(subject.ModelInputError, '^MODEL_INPUT_INTEGRITY_UNRESOLVED$'):
                self.convert(snap)
        self.assert_closed(guards[0])

    def test_source_change_on_final_close_refuses_after_successful_close(self):
        b, _, _, snap = self.load(); guards, capture = self.capture_guard()
        source = Path(b['commodity']['original_root'])/'objects/fixture/original.csv'
        close = subject._Lifetime.close
        def after_close(guard):
            real = os.close; target = guard.fds[0]; fired = False
            def change(fd):
                nonlocal fired
                real(fd)
                if fd == target and not fired:
                    fired = True
                    replacement = source.with_name('late.csv'); write(replacement,source.read_bytes()); replacement.replace(source)
            with patch.object(subject.os,'close',side_effect=change): close(guard)
        with capture, patch.object(subject._Lifetime,'close',after_close):
            with self.assertRaisesRegex(subject.ModelInputError, '^MODEL_INPUT_SOURCE_CHANGED$'):
                self.convert(snap)
        self.assert_closed(guards[0])

    def test_wrapper_never_reads_original_bodies_and_success_fds_close(self):
        _, _, _, snap = self.load(); guards, capture = self.capture_guard(); read = os.read
        def trace(fd, n):
            for guard in guards:
                for _, (owned, _, directory, code) in guard.records.items():
                    if owned == fd and not directory and not code:
                        raise AssertionError('wrapper must not read admitted source body')
            return read(fd,n)
        with capture, patch.object(subject.os,'read',side_effect=trace):
            out = self.convert(snap)
        self.assertEqual(out.values,(80.0,1/3)); self.assert_closed(guards[0])

    def test_future_only_valid_snapshot_changes_identity_not_earlier_values(self):
        _, _, _, first = self.load(); _, _, _, second = self.load(future='2')
        a = self.convert(first,last='2026-07'); b = self.convert(second,last='2026-07')
        self.assertEqual(a.values,b.values)
        ar, br = json.loads(a.payload),json.loads(b.payload)
        self.assertEqual(ar['rows'],br['rows'])
        self.assertEqual(ar['slice_identity']['exact_rows_sha256'],br['slice_identity']['exact_rows_sha256'])
        self.assertNotEqual(ar['snapshot_identity']['payload_sha256'],br['snapshot_identity']['payload_sha256'])

    def test_final_public_reload_code_change_refuses(self):
        _, _, _, snap = self.load(); real = adapter.load_verified_panel_series; calls = 0
        code = Path(subject.__file__); raw = code.read_bytes()
        def reload(*args, **kwargs):
            nonlocal calls
            fresh = real(*args, **kwargs); calls += 1
            if calls == 2: code.write_bytes(raw+b'\n# final public return code fault\n')
            return fresh
        try:
            with patch.object(adapter,'load_verified_panel_series',side_effect=reload):
                with self.assertRaisesRegex(subject.ModelInputError, '^MODEL_INPUT_CODE_CHANGED$'):
                    self.convert(snap)
        finally: code.write_bytes(raw)
        self.assertEqual(calls,2)

    def test_acquired_fd_is_released_when_initial_fstat_fails(self):
        _, _, _, snap = self.load(); guards, capture = self.capture_guard(); real = os.fstat; fired = False
        def fail(fd):
            nonlocal fired
            if not fired and guards and fd in guards[0].fds:
                fired = True; raise OSError('owned initial fstat fault')
            return real(fd)
        with capture, patch.object(subject.os,'fstat',side_effect=fail):
            with self.assertRaisesRegex(subject.ModelInputError, '^MODEL_INPUT_INTEGRITY_UNRESOLVED$'):
                self.convert(snap)
        self.assertTrue(fired); self.assert_closed(guards[0])

    def test_fraction_conversion_is_once_per_public_row(self):
        _, _, _, snap = self.load(); real = Fraction.__float__; exact_inputs = []
        def once(q): exact_inputs.append(q); return real(q)
        with patch.object(Fraction,'__float__',once): out = self.convert(snap)
        self.assertEqual(exact_inputs,[Fraction(80),Fraction(1,3)])
        self.assertEqual(out.values,(80.0,1/3))


class NumericContractTests(unittest.TestCase):
    # These are helper-unit tests, not public extreme-price fixture claims.
    def test_unsupported_runtime_profile_refuses(self):
        from types import SimpleNamespace
        import sys
        info = SimpleNamespace(**{k:getattr(sys.float_info,k) for k in ('radix','mant_dig','min_exp','max_exp','rounds','min','max')})
        info.radix = 10
        with patch.object(subject.sys,'float_info',info):
            with self.assertRaisesRegex(subject.ModelInputError, '^FLOAT_PROFILE_UNSUPPORTED$'):
                subject._encode_basis({'numerator':'80','denominator':'1'})

    def test_nonfinite_nonpositive_and_subnormal_candidates_refuse(self):
        import math
        for x in (math.nan,math.inf,-math.inf,0.0,-1.0,float.fromhex('0x0.0000000000001p-1022')):
            with self.subTest(value=str(x)):
                with self.assertRaisesRegex(subject.ModelInputError, '^FLOAT_RANGE_REFUSED$'):
                    subject._certificate(Fraction(1),x)

    def test_overflow_and_missing_basis_refuse_before_conversion(self):
        for q in (Fraction(2**1024),Fraction(2**4095)):
            with self.assertRaisesRegex(subject.ModelInputError, '^FLOAT_RANGE_REFUSED$'):
                subject._encode_basis(basis(q))
        for b in ({'numerator':'1','denominator':'0'}, {'numerator':'01','denominator':'1'},
                  {'numerator':'1e0','denominator':'1'}, {'numerator':'1','denominator':True}):
            with self.assertRaisesRegex(subject.ModelInputError, '^MODEL_INPUT_BASIS_INVALID$'):
                subject._encode_basis(b)

    def test_receipt_resource_limit_refuses_instead_of_truncation(self):
        with self.assertRaisesRegex(subject.ModelInputError, '^FLOAT_ERROR_BOUND_REFUSED$'):
            subject._receipt(Fraction(2**8192))
