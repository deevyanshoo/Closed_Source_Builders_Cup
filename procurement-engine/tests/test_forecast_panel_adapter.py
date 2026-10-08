"""Owned synthetic files and public APIs only; no observed acquisition or models."""
import copy
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from procure.evaluation import commodity_component as commodity
from procure.evaluation import fx_normalization as fx
from procure.evaluation import commodity_fx_join as join
from procure.evaluation import panel_adapter as adapter

FLAGS = dict(synthetic=True, source_authenticity=False, observed_eligible=False,
             execution_authorized=False, rights='synthetic_only')
ASOF = '2026-10-08T00:00:00.000000Z'
MONTHS = ['2026-07', '2026-08']


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


class PanelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='panel-adapter-owned-')
        self.home = Path(self.tmp.name).resolve(); self.serial = 0

    def tearDown(self):
        self.tmp.cleanup()

    def fixture(self, months=MONTHS, missing=None, absent=(), future='1'):
        self.serial += 1
        home = self.home/str(self.serial); home.mkdir(mode=0o700)
        cr, fr, cp, fp = [home/n for n in ('co', 'fo', 'cp', 'fp')]
        run = 'fixture'; layout(cr, run)
        rows = []
        for m in months:
            for sid in ('copper', 'aluminum'):
                value = '1500' if sid == 'aluminum' else ('2000' if m == '2026-07' else '1000')
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

    def load(self,**kw):
        b,p,j=self.fixture(**kw)
        return b,p,j,adapter.load_verified_panel_series(b,p,j)

    def test_public_projection_keeps_exact_units_and_daily_mean(self):
        _,_,_,snapshot=self.load()
        out=json.loads(snapshot.payload)
        self.assertEqual(len(out['series']),5)
        self.assertEqual([s['unit'] for s in out['series']],['USD/metric_tonne','USD/metric_tonne','INR/USD','INR/kg','INR/kg'])
        self.assertEqual([[r['basis'] for r in s['rows']] for s in out['series']], [
            [{'numerator':'2000','denominator':'1'},{'numerator':'1000','denominator':'1'}],
            [{'numerator':'1500','denominator':'1'},{'numerator':'1500','denominator':'1'}],
            [{'numerator':'40','denominator':'1'},{'numerator':'1','denominator':'3'}],
            [{'numerator':'80','denominator':'1'},{'numerator':'1','denominator':'3'}],
            [{'numerator':'60','denominator':'1'},{'numerator':'1','denominator':'2'}]])
        self.assertEqual(out['status'],'SYNTHETIC_OFFLINE_ONLY')
        self.assertFalse(out['execution_authorized'])

    def controls(self):
        root=Path(__file__).resolve().parents[1]/'evaluation/forecast'
        return (root/'protocol.json').read_bytes(),(root/'data_manifest.json').read_bytes()

    def test_readiness_tiny_fixture_retains_fixed_origins_and_all_96_cells(self):
        _,_,_,snapshot=self.load()
        out=adapter.build_panel_readiness(snapshot,*self.controls())
        self.assertEqual(len(out['candidate_ledger']),96)
        self.assertEqual(len(out['expected_months']),120)
        self.assertEqual(out['planned_origins']['validation'][0],'2021-09')
        self.assertEqual(out['planned_origins']['test'][-1],'2026-02')
        self.assertEqual(out['structural_common_origins'],{'validation':[],'test':[]})
        self.assertEqual(len(out['series_readiness'][0]['absent_expected_months']),118)
        for row in out['candidate_ledger']:
            self.assertEqual(row['status'],'not_evaluated'); self.assertIsNone(row['backend'])
            self.assertTrue(set(['READINESS_ONLY_NO_MODEL_RUN','SYNTHETIC_NOT_OBSERVED','OBSERVED_EXECUTION_HOLD'])<=set(row['reasons']))
        for key in ('selection','calibration','metrics'): self.assertIsNone(out[key])

    def test_native_survives_absent_or_refused_companion(self):
        b,p,j=self.fixture()
        b['fx']=None
        out=json.loads(adapter.load_verified_panel_series(b,p).payload)
        self.assertEqual(out['channel_statuses'],{'commodity':'complete_synthetic','fx':'not_supplied','joined':'not_supplied'})
        self.assertEqual(out['series'][0]['rows'][0]['basis'],{'numerator':'2000','denominator':'1'})
        for s in out['series'][2:]:
            self.assertEqual(len(s['rows']),2); self.assertTrue(all(r['basis'] is None for r in s['rows']))
        b,p,j=self.fixture()
        write(Path(b['fx']['panel_root'])/'objects/panel/panel.csv',b'broken')
        out=json.loads(adapter.load_verified_panel_series(b,p,j).payload)
        self.assertEqual(out['channel_statuses']['commodity'],'complete_synthetic')
        self.assertEqual(out['channel_statuses']['fx'],'integrity_refused')
        self.assertIsNone(out['series'][2]['rows'][0]['basis'])

    def test_fx_survives_refused_native(self):
        b,p,j=self.fixture()
        write(Path(b['commodity']['panel_root'])/'objects/panel/panel.csv',b'broken')
        out=json.loads(adapter.load_verified_panel_series(b,p,j).payload)
        self.assertEqual(out['channel_statuses']['fx'],'complete_synthetic')
        self.assertEqual(out['series'][2]['rows'][0]['basis'],{'numerator':'40','denominator':'1'})
        self.assertIsNone(out['series'][0]['rows'][0]['basis'])

    def test_natural_incomplete_public_closure_and_standalone_gap(self):
        b,p,j,snapshot=self.load(missing=('2026-08','copper'))
        out=json.loads(snapshot.payload)
        self.assertEqual(out['channel_statuses']['commodity'],'natural_incomplete_diagnostic')
        self.assertEqual(out['channel_statuses']['joined'],'natural_incomplete_diagnostic')
        self.assertEqual(out['series'][0]['rows'][1]['reasons'],['source_missing'])
        self.assertIsNone(out['series'][3]['rows'][1]['basis'])
        self.assertEqual(out['series'][1]['rows'][1]['basis'],{'numerator':'1500','denominator':'1'})
        b['fx']=None
        out=json.loads(adapter.load_verified_panel_series(b,p).payload)
        self.assertEqual(out['channel_statuses']['commodity'],'incomplete_inspection_unavailable')
        self.assertTrue(all(r['basis'] is None for s in out['series'][:2] for r in s['rows']))
        self.assertIn('NATURAL_INCOMPLETE_PUBLIC_INSPECTOR_UNAVAILABLE',out['series'][0]['rows'][1]['reasons'])

    def test_shared_or_single_missing_fx_day_never_shrinks_denominator(self):
        for sources in [('ecb_usd_eur',),('ecb_usd_eur','ecb_inr_eur')]:
            with self.subTest(sources=sources):
                _,_,_,snapshot=self.load(absent=[(sid,'2026-08-02') for sid in sources])
                out=json.loads(snapshot.payload)
                self.assertIsNone(out['series'][2]['rows'][1]['basis'])
                self.assertIn('EXPECTED_DATE_MISSING',out['series'][2]['rows'][1]['reasons'])
                self.assertIsNone(out['series'][3]['rows'][1]['basis'])

    def test_join_failure_any_entry_priority_and_preservation(self):
        for kind in ('bytes','directory','symlink','fifo'):
            with self.subTest(kind=kind):
                b,p,j=self.fixture(); marker=Path(j['output_root'])/'disqualified/joined.json'
                if kind=='bytes': write(marker,b'invalid failure')
                elif kind=='directory': marker.mkdir(mode=0o700)
                elif kind=='symlink': marker.symlink_to('absent-target')
                else: os.mkfifo(marker,0o600)
                before=marker.lstat()
                # Priority is independent of an unavailable companion.
                b['fx']=None
                out=json.loads(adapter.load_verified_panel_series(b,p,j).payload)
                self.assertEqual(out['channel_statuses']['joined'],'integrity_refused')
                self.assertTrue(all(r['basis'] is None for s in out['series'][3:] for r in s['rows']))
                self.assertEqual(marker.lstat().st_ino,before.st_ino)
                if kind=='bytes': self.assertEqual(marker.read_bytes(),b'invalid failure')

    def test_flags_policy_units_and_extra_keys_refuse_before_opens(self):
        b,p,j=self.fixture()
        variants=[]
        bad=copy.deepcopy(p);bad['execution_authorized']=True;variants.append((b,bad,j))
        bad=copy.deepcopy(p);bad['commodity_schema_sha256']='0'*64;variants.append((b,bad,j))
        bad=copy.deepcopy(p);bad['required_months']=['2026-07','2026-09'];variants.append((b,bad,j))
        bad=copy.deepcopy(b);bad['fx']['provenance']['observed_eligible']=True;variants.append((bad,p,j))
        bad=copy.deepcopy(j);bad['manifest_bytes']=True;variants.append((b,p,bad))
        for args in variants:
            with patch.object(adapter.os,'open',side_effect=AssertionError('must gate before open')):
                with self.assertRaises(adapter.PanelAdapterError): adapter.load_verified_panel_series(*args)

    def test_same_byte_source_replacement_during_public_read_aborts(self):
        b,p,j=self.fixture(); source=Path(b['commodity']['original_root'])/'objects/fixture/original.csv'
        original=commodity.load_synthetic_component; fired=False
        def replace(*args,**kwargs):
            nonlocal fired
            result=original(*args,**kwargs)
            if not fired:
                fired=True; body=source.read_bytes(); source.unlink(); write(source,body)
            return result
        with patch.object(commodity,'load_synthetic_component',side_effect=replace):
            with self.assertRaisesRegex(adapter.PanelAdapterError,'ADAPTER_SOURCE_CHANGED'):
                adapter.load_verified_panel_series(b,p,j)

    def test_final_adapter_close_fault_aborts_instead_of_returning_snapshot(self):
        b,p,j=self.fixture(); real_close=os.close; fired=False
        def close(fd):
            nonlocal fired
            real_close(fd)
            if not fired:
                # Trigger only the adapter's final close, not a public component close.
                import inspect
                if any(frame.function=='close' and frame.filename==adapter.__file__ for frame in inspect.stack()):
                    fired=True; raise OSError('owned close fault')
        with patch.object(adapter.os,'close',side_effect=close):
            with self.assertRaisesRegex(adapter.PanelAdapterError,'ADAPTER_INTEGRITY_UNRESOLVED'):
                adapter.load_verified_panel_series(b,p,j)
        self.assertTrue(fired)

    def test_full_calendar_is_structural_only_with_exact_cutoffs_targets_and_ledger(self):
        months=[f'{y:04d}-{m:02d}' for y in range(2016,2027) for m in range(1,13)
                if '2016-09'<=f'{y:04d}-{m:02d}'<='2026-08']
        _,_,_,snapshot=self.load(months=months)
        out=adapter.build_panel_readiness(snapshot,*self.controls())
        self.assertEqual(out['structural_common_origins'],{
            'validation':[f'{y}-{m:02d}' for y in range(2021,2024) for m in range(1,13) if '2021-09'<=f'{y}-{m:02d}'<='2023-08'],
            'test':[f'{y}-{m:02d}' for y in range(2024,2027) for m in range(1,13) if '2024-03'<=f'{y}-{m:02d}'<='2026-02']})
        origins=out['series_readiness'][0]['origins']
        self.assertEqual((origins['validation'][0]['training_cutoff'],origins['validation'][0]['training_count']),('2021-08',60))
        self.assertEqual((origins['test'][0]['training_cutoff'],origins['test'][0]['training_count']),('2024-02',90))
        self.assertEqual(origins['validation'][-1]['targets_by_horizon']['6'],'2024-02')
        self.assertEqual(origins['test'][-1]['targets_by_horizon']['6'],'2026-08')
        self.assertEqual(out['planned_origins']['purge'],['2023-09','2023-10','2023-11','2023-12','2024-01','2024-02'])
        self.assertEqual([r['model_leads_from_last_training_month'] for r in origins['test']],[[2,4,7]]*24)
        expected=[]
        for sid,unit in [('copper','USD/metric_tonne'),('aluminum','USD/metric_tonne'),('USDINR','INR/USD'),('copper_INR_per_kg','INR/kg'),('aluminum_INR_per_kg','INR/kg')]:
            for model in ('naive','ets','gbm'):
                for split in ('validation','test'):
                    for h in (1,3,6):
                        expected.append({'series_id':sid,'unit':unit,'model':model,'split':split,'horizon':h,
                            'model_lead':h+1,'status':'not_evaluated','backend':None,
                            'reasons':['READINESS_ONLY_NO_MODEL_RUN','SYNTHETIC_NOT_OBSERVED','OBSERVED_EXECUTION_HOLD']})
        for split in ('validation','test'):
            for h in (1,3,6):
                expected.append({'series_id':'iron_ore','unit':'USD/dmtu','model':'all','split':split,'horizon':h,
                    'model_lead':h+1,'status':'not_evaluated','backend':None,
                    'reasons':['READINESS_ONLY_NO_MODEL_RUN','SYNTHETIC_NOT_OBSERVED','OBSERVED_EXECUTION_HOLD','UNIT_HOLD_DMTU']})
        self.assertEqual(out['candidate_ledger'],expected)
        self.assertFalse(out['execution_authorized']);self.assertFalse(out['observed_eligible'])
        self.assertEqual(out['protocol_identity']['raw_json'].encode('ascii'),self.controls()[0])
        def exact_wire(value):
            if isinstance(value,dict):
                for v in value.values(): exact_wire(v)
            elif isinstance(value,list):
                for v in value: exact_wire(v)
            else: self.assertIn(type(value),(str,int,bool,type(None)))
        exact_wire(out)

    def test_119_months_59_training_missing_h6_and_gap_never_choose_subsets(self):
        months=[f'{y:04d}-{m:02d}' for y in range(2016,2027) for m in range(1,13)
                if '2016-09'<=f'{y:04d}-{m:02d}'<='2026-08']
        for mask,missing in ((months[1:],None),(months[:-1],None),(months,('2021-04','copper'))):
            with self.subTest(mask_end=mask[-1],missing=missing):
                _,_,_,snapshot=self.load(months=mask,missing=missing)
                out=adapter.build_panel_readiness(snapshot,*self.controls())
                self.assertEqual(out['structural_common_origins'],{'validation':[],'test':[]})
                self.assertEqual(len(out['planned_origins']['validation']),24)
                copper=out['series_readiness'][0]['origins']
                if mask[0]=='2016-10':
                    self.assertEqual(copper['validation'][0]['training_count'],59)
                    self.assertIn('INSUFFICIENT_CONSECUTIVE_HISTORY',copper['validation'][0]['reasons'])
                if mask[-1]=='2026-07':
                    self.assertEqual(copper['test'][-1]['missing_targets_by_horizon']['6'],['2026-08'])
                if missing: self.assertIn('2021-04',copper['validation'][0]['missing_training_months'])

    def test_consistent_forged_snapshot_copied_identities_cannot_claim_verification(self):
        _,_,_,snapshot=self.load(); payload=json.loads(snapshot.payload)
        # Keep exact internal arithmetic consistent: native3000, FX40, joined120.
        payload['series'][0]['rows'][0]['basis']={'numerator':'3000','denominator':'1'}
        payload['series'][3]['rows'][0]['basis']={'numerator':'120','denominator':'1'}
        forged=adapter.VerifiedPanelSeries(snapshot.request,canon(payload))
        with self.assertRaisesRegex(adapter.PanelAdapterError,'ADAPTER_SNAPSHOT_MISMATCH'):
            adapter.build_panel_readiness(forged,*self.controls())

    def test_snapshot_canonical_keys_units_flags_bases_are_not_constructor_authority(self):
        _,_,_,snapshot=self.load()
        variants=[]
        for mutate in (
            lambda x:x.update(execution_authorized=True),
            lambda x:x.update(extra='caller'),
            lambda x:x['series'][0].update(unit='USD/kg'),
            lambda x:x['series'][0]['rows'][0].update(basis={'numerator':'4000','denominator':'2'}),
            lambda x:x['series'][0]['rows'][0].update(available_at_proxy='2026-08-31T23:59:59Z'),
            lambda x:x['component_identities']['commodity']['manifest'].update(rights='approved'),
        ):
            payload=json.loads(snapshot.payload);mutate(payload);variants.append(canon(payload))
        variants.extend([snapshot.payload[:-1]+b',"synthetic":true}',snapshot.payload.replace(b'"numerator":"2000"',b'"numerator":2.0',1)])
        for raw in variants:
            with self.subTest(raw_size=len(raw)):
                with self.assertRaises(adapter.PanelAdapterError):
                    adapter.build_panel_readiness(adapter.VerifiedPanelSeries(snapshot.request,raw),*self.controls())

    def test_control_retaging_and_mismatched_raw_bytes_refuse_before_io(self):
        _,_,_,snapshot=self.load(); protocol,source=self.controls()
        for controls in ((protocol.replace(b'false',b'true',1),source),(protocol,source+b'\n'),
                         (protocol.replace(b'HOLD',b'PASS',1),source)):
            with patch.object(adapter.os,'open',side_effect=AssertionError('control gate first')):
                with self.assertRaisesRegex(adapter.PanelAdapterError,'ADAPTER_CONTROL_HOLD'):
                    adapter.build_panel_readiness(snapshot,*controls)

    def test_future_token_change_preserves_past_exact_rows_but_changes_snapshot(self):
        _,_,_,first=self.load(future='1');_,_,_,second=self.load(future='2')
        a,b=json.loads(first.payload),json.loads(second.payload)
        for sa,sb in zip(a['series'],b['series']): self.assertEqual(sa['rows'][0],sb['rows'][0])
        self.assertNotEqual(sha(first.payload),sha(second.payload))
        self.assertEqual(b['series'][3]['rows'][1]['basis'],{'numerator':'2','denominator':'3'})

    def test_late_join_failure_during_final_close_aborts_and_preserves_marker(self):
        b,p,j=self.fixture(); marker=Path(j['output_root'])/'disqualified/joined.json'
        real_close=os.close; fired=False
        def close(fd):
            nonlocal fired
            real_close(fd)
            if not fired:
                import inspect
                if any(frame.function=='close' and frame.filename==adapter.__file__ for frame in inspect.stack()):
                    fired=True; write(marker,b'late failure evidence')
        with patch.object(adapter.os,'close',side_effect=close):
            with self.assertRaisesRegex(adapter.PanelAdapterError,'ADAPTER_SOURCE_CHANGED'):
                adapter.load_verified_panel_series(b,p,j)
        self.assertEqual(marker.read_bytes(),b'late failure evidence')

    def test_same_byte_panel_marker_replacement_after_public_verification_aborts(self):
        b,p,j=self.fixture(); marker=Path(b['commodity']['panel_root'])/'complete/panel.json'
        original=commodity.verify_commodity_component; fired=False
        def replace(*args,**kwargs):
            nonlocal fired
            out=original(*args,**kwargs)
            if not fired:
                fired=True; body=marker.read_bytes();marker.unlink();write(marker,body)
            return out
        with patch.object(commodity,'verify_commodity_component',side_effect=replace):
            with self.assertRaisesRegex(adapter.PanelAdapterError,'ADAPTER_SOURCE_CHANGED'):
                adapter.load_verified_panel_series(b,p,j)

    def test_same_byte_ancestor_replacement_aborts(self):
        b,p,j=self.fixture(); root=Path(b['commodity']['original_root']); home=root.parent
        original=commodity.load_synthetic_component;fired=False;old=home.with_name(home.name+'-old')
        def replace(*args,**kwargs):
            nonlocal fired
            out=original(*args,**kwargs)
            if not fired:
                import shutil
                fired=True;home.rename(old);shutil.copytree(old,home,symlinks=True)
                # Preserve panel hardlinks within the replacement without modifying originals.
                for n in ('cp','fp','jp'):
                    directory=home/n
                    for state in ('complete','incomplete'):
                        for marker in (directory/state).glob('*.json'):
                            marker.unlink();os.link(directory/'objects'/marker.stem/'manifest.json',marker)
                m=home/'fo/committed/fixture.json';m.unlink();os.link(home/'fo/objects/fixture/manifest.json',m)
            return out
        with patch.object(commodity,'load_synthetic_component',side_effect=replace):
            with self.assertRaisesRegex(adapter.PanelAdapterError,'ADAPTER_SOURCE_CHANGED'):
                adapter.load_verified_panel_series(b,p,j)

    def test_source_fifo_and_symlink_refuse_without_reading_or_hanging(self):
        for kind in ('fifo','symlink'):
            b,p,j=self.fixture();source=Path(b['commodity']['original_root'])/'objects/fixture/original.csv'
            source.unlink()
            if kind=='fifo':os.mkfifo(source,0o600)
            else:source.symlink_to('absent-target')
            out=json.loads(adapter.load_verified_panel_series(b,p,j).payload)
            self.assertEqual(out['channel_statuses']['commodity'],'integrity_refused')
            self.assertEqual(out['channel_statuses']['fx'],'complete_synthetic')

    def test_readiness_rechecks_sources_after_planning_not_just_at_entry(self):
        b,p,j,snapshot=self.load(); real=adapter.load_verified_panel_series;calls=0
        def load(**kwargs):
            nonlocal calls
            calls+=1
            if calls==2:
                marker=Path(j['output_root'])/'disqualified/joined.json';write(marker,b'changed after planning')
            return real(**kwargs)
        with patch.object(adapter,'load_verified_panel_series',side_effect=load):
            with self.assertRaisesRegex(adapter.PanelAdapterError,'ADAPTER_SNAPSHOT_MISMATCH'):
                adapter.build_panel_readiness(snapshot,*self.controls())
        self.assertEqual(calls,2)

    def test_public_incomplete_closure_refusal_never_becomes_natural_diagnostic(self):
        b,p,j=self.fixture(missing=('2026-08','copper'))
        root=Path(b['commodity']['panel_root']);panel=root/'objects/panel/panel.csv'
        # Caller supplies internally matching hashes for a corrupted publication.
        # Only public reconstruction from original1500 detects the forged1510.
        raw=panel.read_bytes().replace(b'1500',b'1510');write(panel,raw)
        manifest_path=root/'objects/panel/manifest.json'
        manifest=json.loads(manifest_path.read_bytes());manifest['panel_sha256']=sha(raw);manifest['panel_bytes']=len(raw)
        write(manifest_path,canon(manifest))
        b['commodity'].update(panel_sha256=sha(raw),panel_bytes=len(raw),panel_manifest_sha256=sha(canon(manifest)))
        out=json.loads(adapter.load_verified_panel_series(b,p,j).payload)
        self.assertEqual(out['channel_statuses']['commodity'],'integrity_refused')
        self.assertEqual(out['channel_statuses']['fx'],'complete_synthetic')
        self.assertEqual(out['channel_statuses']['joined'],'integrity_refused')
        self.assertTrue(all(r['basis'] is None for s in out['series'][:2] for r in s['rows']))
        self.assertEqual(out['series'][2]['rows'][0]['basis'],{'numerator':'40','denominator':'1'})

    def test_failed_complete_verifier_cannot_be_demoted_to_natural_inspection(self):
        b,p,j=self.fixture()
        with patch.object(commodity,'verify_commodity_component',side_effect=commodity.CommodityComponentError('PUBLICATION_INVALID')):
            out=json.loads(adapter.load_verified_panel_series(b,p,j).payload)
        self.assertEqual(out['channel_statuses']['commodity'],'integrity_refused')
        self.assertEqual(out['channel_statuses']['fx'],'complete_synthetic')
        self.assertTrue(all(r['basis'] is None for s in out['series'][:2] for r in s['rows']))

    def test_reader_never_initializes_missing_join_failure_layout(self):
        b,p,j=self.fixture();directory=Path(j['output_root'])/'disqualified'
        directory.rmdir()
        out=json.loads(adapter.load_verified_panel_series(b,p,j).payload)
        self.assertEqual(out['channel_statuses']['joined'],'integrity_refused')
        self.assertEqual(out['channel_statuses']['commodity'],'complete_synthetic')
        self.assertEqual(out['channel_statuses']['fx'],'complete_synthetic')
        self.assertFalse(directory.exists())

    def test_no_join_publication_means_no_derived_rows_even_with_complete_components(self):
        b,p,j=self.fixture()
        out=json.loads(adapter.load_verified_panel_series(b,p).payload)
        self.assertEqual(out['channel_statuses']['joined'],'not_supplied')
        self.assertIsNone(out['joined_identity'])
        for s in out['series'][3:]:
            for r in s['rows']:
                self.assertIsNone(r['basis']);self.assertEqual(r['reasons'],['JOIN_PANEL_NOT_SUPPLIED'])

    def test_denied_writes_do_not_break_production_loading_or_readiness(self):
        b,p,j=self.fixture()
        def denied(*args,**kw): raise AssertionError('production write attempted')
        real_open=os.open
        def read_only(path,flags,*args,**kwargs):
            if flags&(os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC|os.O_APPEND): denied()
            return real_open(path,flags,*args,**kwargs)
        with patch.object(os,'open',side_effect=read_only),patch.object(os,'write',side_effect=denied),\
             patch.object(os,'mkdir',side_effect=denied),patch.object(os,'link',side_effect=denied),\
             patch.object(os,'unlink',side_effect=denied),patch.object(os,'rename',side_effect=denied),\
             patch.object(os,'fsync',side_effect=denied),\
             patch.object(commodity,'publish_commodity_component',side_effect=denied),\
             patch.object(fx,'publish_fx_panel',side_effect=denied),patch.object(join,'publish_joined_panel',side_effect=denied):
            snapshot=adapter.load_verified_panel_series(b,p,j)
            out=adapter.build_panel_readiness(snapshot,*self.controls())
        self.assertEqual(len(out['candidate_ledger']),96)

    def test_dataclass_owns_canonical_copies_of_mutable_caller_bindings(self):
        b,p,j,snapshot=self.load()
        old=snapshot.request
        p['required_months'].append('2026-09');b['commodity']['original_root']='invalid'
        j['manifest_sha256']='0'*64
        self.assertEqual(snapshot.request,old)
        self.assertEqual(json.loads(snapshot.request)['join_policy']['required_months'],['2026-07','2026-08'])
        with self.assertRaises(AttributeError): snapshot.payload=b'caller upgrade'

    def rewrite_component_receipt(self,bindings,kind,mutate):
        binding=bindings[kind]
        run=binding['fixture_run_id' if kind=='commodity' else 'source_run_id']
        receipt=Path(binding['original_root'])/'committed'/(run+'.json')
        value=json.loads(receipt.read_bytes());mutate(value);raw=canon(value)
        receipt.chmod(0o600);receipt.write_bytes(raw)
        if kind=='fx':receipt.chmod(0o400)
        binding['fixture_receipt_sha256' if kind=='commodity' else 'source_receipt_sha256']=sha(raw)
        return receipt

    def load_with_body_open_trace(self,b,p,j,kind):
        opened=[];real_open=os.open
        names={'original.csv'} if kind=='commodity' else {'ecb_usd_eur.raw','ecb_inr_eur.raw'}
        def traced(path,*args,**kwargs):
            name=Path(os.fsdecode(path)).name
            if name in names:opened.append(name)
            return real_open(path,*args,**kwargs)
        with patch.object(os,'open',side_effect=traced):
            snapshot=adapter.load_verified_panel_series(b,p,j)
        return json.loads(snapshot.payload),opened

    def test_repair_R1_commodity_receipt_gate_precedes_original_open(self):
        b,p,j=self.fixture()
        self.rewrite_component_receipt(b,'commodity',lambda r:r.update(synthetic=False))
        out,opened=self.load_with_body_open_trace(b,p,j,'commodity')
        self.assertEqual(out['channel_statuses']['commodity'],'integrity_refused')
        self.assertEqual(out['channel_statuses']['fx'],'complete_synthetic')
        self.assertEqual(out['series'][2]['rows'][0]['basis'],{'numerator':'40','denominator':'1'})
        self.assertEqual(opened,[], 'Invalid receipt must refuse BEFORE any original body open')

    def test_repair_R1_fx_receipt_gate_precedes_original_open(self):
        b,p,j=self.fixture()
        self.rewrite_component_receipt(b,'fx',lambda r:r.update(execution_authorized=True))
        out,opened=self.load_with_body_open_trace(b,p,j,'fx')
        self.assertEqual(out['channel_statuses']['fx'],'integrity_refused')
        self.assertEqual(out['channel_statuses']['commodity'],'complete_synthetic')
        self.assertEqual(out['series'][0]['rows'][0]['basis'],{'numerator':'2000','denominator':'1'})
        self.assertEqual(opened,[], 'Invalid receipt must refuse BEFORE any original body open')

    def test_repair_R2_unsupported_join_with_either_companion_missing_is_refused(self):
        for missing in ('commodity','fx'):
            with self.subTest(missing=missing):
                b,p,j=self.fixture();manifest=Path(j['output_root'])/'objects/joined/manifest.json'
                value=json.loads(manifest.read_bytes());value['schema_version']='unsupported-joined-receipt.v99'
                raw=canon(value);write(manifest,raw)
                j.update(manifest_sha256=sha(raw),manifest_bytes=len(raw))
                # Confirm the artifact itself is invalid with complete public closure.
                with self.assertRaisesRegex(join.JoinError,'PUBLICATION_INVALID'):
                    join.inspect_joined_panel(j['output_root'],j['run_id'],b,p)
                companion='fx' if missing=='commodity' else 'commodity';b[missing]=None
                out=json.loads(adapter.load_verified_panel_series(b,p,j).payload)
                self.assertEqual(out['channel_statuses'][companion],'complete_synthetic')
                self.assertEqual(out['channel_statuses']['joined'],'integrity_refused')
                self.assertEqual(out['load_ledger'][2]['reasons'],['JOIN_PUBLIC_CLOSURE_UNVERIFIED'])
                self.assertIsNone(out['joined_identity'])
                self.assertTrue(all(r['basis'] is None for s in out['series'][3:] for r in s['rows']))

    def test_repair_R1_metadata_admission_matrix_never_opens_invalid_bodies(self):
        common=[('missing',lambda r:r.pop('schema_version')),('extra',lambda r:r.update(extra='caller')),
                ('schema',lambda r:r.update(schema_version='unsupported.v99')),
                ('run',lambda r:r.update(run_id='other'))]
        commodity_cases=common+[
            ('synthetic',lambda r:r.update(synthetic=False)),('synthetic_type',lambda r:r.update(synthetic=1)),
            ('authenticity',lambda r:r.update(source_authenticity=True)),('observed',lambda r:r.update(observed_eligible=True)),
            ('execution',lambda r:r.update(execution_authorized=True)),('rights',lambda r:r.update(rights='approved')),
            ('first_month',lambda r:r.update(first_month='2026-13')),('last_month',lambda r:r.update(last_month=None)),
            ('reverse_window',lambda r:r.update(first_month='2026-09')),
            ('count_type',lambda r:r.update(month_count=True)),('count_range',lambda r:r.update(month_count=241)),
            ('count_decl',lambda r:r.update(month_count=1)),('bytes_type',lambda r:r.update(original_bytes=True)),
            ('bytes_range',lambda r:r.update(original_bytes=0)),('bytes_decl',lambda r:r.update(original_bytes=1)),
            ('hash_syntax',lambda r:r.update(original_sha256='BAD')),('hash_decl',lambda r:r.update(original_sha256='0'*64))]
        fx_cases=common+[
            ('status',lambda r:r.update(status='OFFLINE_INCOMPLETE')),
            ('offline',lambda r:r.update(offline_only=False)),('offline_type',lambda r:r.update(offline_only=1)),
            ('settled',lambda r:r.update(settled=False)),('settled_type',lambda r:r.update(settled=1)),
            ('capture',lambda r:r.update(capture_verified=False)),('capture_type',lambda r:r.update(capture_verified=1)),
            ('execution',lambda r:r.update(execution_authorized=True)),('execution_type',lambda r:r.update(execution_authorized=0)),
            ('originals_type',lambda r:r.update(originals={})),('missing_original',lambda r:r['originals'].pop()),
            ('duplicate_original',lambda r:r['originals'].__setitem__(1,copy.deepcopy(r['originals'][0]))),
            ('row_extra',lambda r:r['originals'][0].update(extra='caller')),
            ('row_missing',lambda r:r['originals'][0].pop('bytes')),
            ('row_source',lambda r:r['originals'][0].update(source_id='unknown')),
            ('row_path',lambda r:r['originals'][0].update(path='../outside.raw')),
            ('row_bytes_type',lambda r:r['originals'][0].update(bytes=True)),
            ('row_bytes_range',lambda r:r['originals'][0].update(bytes=0)),
            ('row_bytes_decl',lambda r:r['originals'][0].update(bytes=1)),
            ('row_hash_syntax',lambda r:r['originals'][0].update(sha256='BAD')),
            ('row_hash_decl',lambda r:r['originals'][0].update(sha256='0'*64))]
        for kind,cases in [('commodity',commodity_cases),('fx',fx_cases)]:
            for label,mutate in cases:
                with self.subTest(kind=kind,case=label):
                    b,p,j=self.fixture();self.rewrite_component_receipt(b,kind,mutate)
                    out,opened=self.load_with_body_open_trace(b,p,j,kind)
                    self.assertEqual(opened,[])
                    self.assertEqual(out['channel_statuses'][kind],'integrity_refused')
                    companion='fx' if kind=='commodity' else 'commodity'
                    self.assertEqual(out['channel_statuses'][companion],'complete_synthetic')

    def test_repair_R2_valid_supplied_join_without_public_closure_is_explicitly_unverified(self):
        for missing in ('commodity','fx'):
            with self.subTest(missing=missing):
                b,p,j=self.fixture();b[missing]=None
                out=json.loads(adapter.load_verified_panel_series(b,p,j).payload)
                self.assertEqual(out['channel_statuses']['joined'],'integrity_refused')
                self.assertEqual(out['load_ledger'][2]['reasons'],['JOIN_PUBLIC_CLOSURE_UNVERIFIED'])
                self.assertTrue(all(r['basis'] is None for s in out['series'][3:] for r in s['rows']))

    def test_repair_R1_noncanonical_or_nonregular_receipts_refuse_before_bodies(self):
        for kind in ('commodity','fx'):
            for form in ('duplicate','decimal','noncanonical','oversize','fifo','symlink','fx_unlinked_receipt'):
                if form=='fx_unlinked_receipt' and kind!='fx':continue
                with self.subTest(kind=kind,form=form):
                    b,p,j=self.fixture();binding=b[kind]
                    run=binding['fixture_run_id' if kind=='commodity' else 'source_run_id']
                    path=Path(binding['original_root'])/'committed'/(run+'.json');raw=path.read_bytes()
                    if form in ('fifo','symlink','fx_unlinked_receipt'):
                        path.unlink()
                        if form=='fifo':os.mkfifo(path,0o600)
                        elif form=='symlink':path.symlink_to('absent-receipt')
                        else:write(path,raw);path.chmod(0o400)
                    else:
                        if form=='duplicate':raw=raw[:-1]+b',"schema_version":"unsupported"}'
                        elif form=='decimal':raw=raw.replace(b'"execution_authorized":false',b'"execution_authorized":0.0')
                        elif form=='noncanonical':raw+=b'\n'
                        else:raw+=b' '*(65537-len(raw))
                        path.chmod(0o600);path.write_bytes(raw)
                        if kind=='fx':path.chmod(0o400)
                        binding['fixture_receipt_sha256' if kind=='commodity' else 'source_receipt_sha256']=sha(raw)
                    out,opened=self.load_with_body_open_trace(b,p,j,kind)
                    self.assertEqual(opened,[])
                    self.assertEqual(out['channel_statuses'][kind],'integrity_refused')
                    self.assertEqual(out['channel_statuses']['commodity' if kind=='fx' else 'fx'],'complete_synthetic')

    def test_repair_R1_receipt_mutation_after_anchor_aborts_before_body_open(self):
        for kind in ('commodity','fx'):
            with self.subTest(kind=kind):
                b,p,j=self.fixture();binding=b[kind]
                run=binding['fixture_run_id' if kind=='commodity' else 'source_run_id']
                receipt=Path(binding['original_root'])/'committed'/(run+'.json')
                real_read=adapter._ReadLifetime.read;real_open=os.open;fired=False;opened=[]
                names={'original.csv'} if kind=='commodity' else {'ecb_usd_eur.raw','ecb_inr_eur.raw'}
                def traced_open(path,*args,**kwargs):
                    if Path(os.fsdecode(path)).name in names:opened.append(os.fsdecode(path))
                    return real_open(path,*args,**kwargs)
                def mutate_after_read(guard,parent,name,*args,**kwargs):
                    nonlocal fired
                    result=real_read(guard,parent,name,*args,**kwargs)
                    if not fired and guard.path(parent)+'/'+name==str(receipt):
                        fired=True;value=json.loads(result[0]);value['execution_authorized']=True
                        receipt.chmod(0o600);receipt.write_bytes(canon(value))
                        if kind=='fx':receipt.chmod(0o400)
                    return result
                with patch.object(adapter._ReadLifetime,'read',new=mutate_after_read),patch.object(os,'open',side_effect=traced_open):
                    with self.assertRaisesRegex(adapter.PanelAdapterError,'ADAPTER_SOURCE_CHANGED'):
                        adapter.load_verified_panel_series(b,p,j)
                self.assertTrue(fired);self.assertEqual(opened,[])

    def test_repair_R1_rechecks_admitted_receipt_before_public_body_loader(self):
        for kind in ('commodity','fx'):
            with self.subTest(kind=kind):
                b,p,j=self.fixture();binding=b[kind]
                run=binding['fixture_run_id' if kind=='commodity' else 'source_run_id']
                receipt=Path(binding['original_root'])/'committed'/(run+'.json')
                panel=Path(binding['panel_root'])/'objects/panel/panel.csv'
                real_read=adapter._ReadLifetime.read;real_open=os.open;fired=False;late_opens=[]
                names={'original.csv'} if kind=='commodity' else {'ecb_usd_eur.raw','ecb_inr_eur.raw'}
                def traced_open(path,*args,**kwargs):
                    if fired and Path(os.fsdecode(path)).name in names:late_opens.append(os.fsdecode(path))
                    return real_open(path,*args,**kwargs)
                def mutate_after_panel(guard,parent,name,*args,**kwargs):
                    nonlocal fired
                    result=real_read(guard,parent,name,*args,**kwargs)
                    if not fired and guard.path(parent)+'/'+name==str(panel):
                        fired=True;value=json.loads(receipt.read_bytes());value['execution_authorized']=True
                        receipt.chmod(0o600);receipt.write_bytes(canon(value))
                        if kind=='fx':receipt.chmod(0o400)
                    return result
                with patch.object(adapter._ReadLifetime,'read',new=mutate_after_panel),patch.object(os,'open',side_effect=traced_open):
                    with self.assertRaisesRegex(adapter.PanelAdapterError,'ADAPTER_SOURCE_CHANGED'):
                        adapter.load_verified_panel_series(b,p,j)
                self.assertTrue(fired);self.assertEqual(late_opens,[])
