"""Owned synthetic archives and public APIs; no models or observed acquisition."""
import calendar
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
from procure.evaluation import consumer_boundary as consumer

FLAGS = {'synthetic': True, 'source_authenticity': False, 'observed_eligible': False,
         'execution_authorized': False, 'rights': 'synthetic_only'}
ASOF = '2026-10-08T00:00:00.000000Z'
ENGINE = Path(__file__).resolve().parents[1]


def canon(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True,
                      allow_nan=False).encode('ascii')


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def months(first, last):
    y, m = map(int, first.split('-')); a = 12*y+m-1
    y, m = map(int, last.split('-')); b = 12*y+m-1
    return [f'{i//12:04d}-{i%12+1:02d}' for i in range(a, b+1)]


def body(header, rows):
    stream = io.StringIO(newline=''); writer = csv.writer(stream, lineterminator='\n')
    writer.writerow(header); writer.writerows(rows)
    return stream.getvalue().encode('ascii')


def write(path, raw):
    path.write_bytes(raw); path.chmod(0o600)


def original_layout(root):
    for p in (root, root/'objects', root/'objects/fixture', root/'committed'):
        p.mkdir(mode=0o700, exist_ok=True)


class ConsumerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='consumer-readiness-owned-')
        self.home = Path(self.tmp.name).resolve(); self.serial = 0
        self.controls = tuple((ENGINE/'evaluation/forecast'/p).read_bytes()
                              for p in ('protocol.json', 'data_manifest.json'))

    def tearDown(self):
        self.tmp.cleanup()

    def fixture(self, labels=None, missing=None, future='1000', collision=False):
        labels = labels or months('2016-09', '2026-08')
        self.serial += 1
        home = self.home/str(self.serial); home.mkdir(mode=0o700)
        cr, fr, cp, fp, jp = [home/n for n in ('commodity', 'fx', 'commodity_panel', 'fx_panel', 'joined')]
        original_layout(cr)
        rows = []
        for m in labels:
            for sid in ('copper', 'aluminum'):
                value = '1500' if sid == 'aluminum' else (future if m == '2026-08' else '1000')
                if collision and sid == 'copper':
                    value = '1000.000000000000000001' if m == '2016-10' else '1000'
                unavailable = (m, sid) == missing
                rows.append([m, sid, 'USD/metric_tonne', 'M' if unavailable else 'A', '' if unavailable else value])
        raw = body(['month','series_id','unit','status','value'], rows)
        receipt = {'schema_version':'synthetic-commodity-fixture-receipt.v1', **FLAGS,
                   'run_id':'fixture', 'first_month':labels[0], 'last_month':labels[-1],
                   'month_count':len(labels), 'original_bytes':len(raw), 'original_sha256':sha(raw)}
        write(cr/'objects/fixture/original.csv',raw); write(cr/'committed/fixture.json',canon(receipt))
        cv=commodity.load_synthetic_component(cr,'fixture',FLAGS)
        ca=commodity.normalize_commodity_component(cv,commodity.SCHEMA,labels,ASOF)
        cm=commodity.publish_commodity_component(ca,cv,cp,'panel')
        original_layout(fr); (fr/'incomplete').mkdir(mode=0o700)
        dates={m:[m+'-01'] for m in labels}; originals=[]
        for sid,currency,value in [('ecb_usd_eur','USD','2'),('ecb_inr_eur','INR','160')]:
            raw=body(fx.HEADERS,[[f'D.{currency}.EUR.SP00.A','D',currency,'EUR','SP00','A',m+'-01',value,'SYNTHETIC_QUOTE','0','A'] for m in labels])
            write(fr/'objects/fixture'/(sid+'.raw'),raw)
            originals.append({'source_id':sid,'path':f'objects/fixture/{sid}.raw','bytes':len(raw),'sha256':sha(raw)})
        receipt={'schema_version':'offline-source-publication.v1','run_id':'fixture','status':'OFFLINE_COMPLETE',
                 'offline_only':True,'execution_authorized':False,'originals':sorted(originals,key=lambda r:r['source_id']),
                 'settled':True,'capture_verified':True}
        write(fr/'objects/fixture/manifest.json',canon(receipt))
        os.link(fr/'objects/fixture/manifest.json',fr/'committed/fixture.json')
        for item in (fr/'objects/fixture').iterdir(): item.chmod(0o400)
        (fr/'objects/fixture').chmod(0o500)
        provenance={**FLAGS,'retrieved_at':ASOF}
        coverage={'schema_version':'synthetic-date-mask.v2','synthetic':True,'as_of':ASOF,
                  'required_months':labels,'dates_by_month':dates}
        fv=fx.load_verified_originals(fr,'fixture',provenance)
        fa=fx.normalize_ecb_pair(fv,fx.SCHEMA,coverage); fm=fx.publish_fx_panel(fa,provenance,fp,'panel')
        def panel_binding(root, manifest):
            return {'panel_root':str(root),'panel_run_id':'panel',
                    'panel_manifest_sha256':sha((root/'objects/panel/manifest.json').read_bytes()),
                    **{k:manifest[k] for k in ('panel_sha256','panel_bytes','schema_sha256','code_sha256')}}
        cb={**panel_binding(cp,cm),'original_root':str(cr),'fixture_run_id':'fixture',
            'fixture_receipt_sha256':cv['receipt_sha256'],'original_sha256':cv['original_sha256'],'original_bytes':cv['original_bytes']}
        fb={**panel_binding(fp,fm),'original_root':str(fr),'source_run_id':'fixture','provenance':provenance,
            'source_receipt_sha256':fv['receipt_sha256'],'source_originals':fm['source_originals'],
            'coverage_sha256':fm['coverage_sha256'],'coverage':coverage}
        policy={'schema_version':'synthetic-commodity-fx-join.v1',**FLAGS,'series_ids':['copper','aluminum'],
                'required_months':labels,'as_of':ASOF,'commodity_schema_sha256':sha(canon(commodity.SCHEMA)),
                'fx_schema_sha256':sha(canon(fx.SCHEMA)),'fx_coverage_sha256':sha(canon(coverage)),
                'arithmetic_version':'component_fraction_product_v1','rounding_version':'decimal50_half_even_normalized_plain_v1'}
        bindings={'commodity':cb,'fx':fb}
        composed=join.compose_joined_panel(join.load_join_inputs(bindings,policy),policy)
        jm=join.publish_joined_panel(composed,bindings,policy,jp,'joined')
        raw=(jp/'objects/joined/manifest.json').read_bytes()
        publication={'output_root':str(jp),'run_id':'joined','manifest_sha256':sha(raw),'manifest_bytes':len(raw),
                     'panel_sha256':jm['panel_sha256'],'panel_bytes':jm['panel_bytes']}
        return bindings,policy,publication,adapter.load_verified_panel_series(bindings,policy,publication)

    def build(self,snapshot):
        return json.loads(consumer.build_consumer_boundary_readiness(snapshot,*self.controls).payload)

    def test_full_archive_plans_fixed_windows_without_execution(self):
        # Missing consumer calendar or a shortened/favorable cohort must fail.
        _,_,_,snapshot=self.fixture()
        result=self.build(snapshot)
        self.assertEqual(len(result.get('planned_windows',{}).get('origins',[])),48)
        self.assertEqual(result['planned_windows']['purged_origin_labels'],months('2023-09','2024-02'))
        windows=result['planned_windows']['origins']
        for index,origin,count,last,targets in [
            (0,'2021-09',60,'2021-08',['2021-10','2021-12','2022-03']),
            (23,'2023-08',83,'2023-07',['2023-09','2023-11','2024-02']),
            (24,'2024-03',90,'2024-02',['2024-04','2024-06','2024-09']),
            (47,'2026-02',113,'2026-01',['2026-03','2026-05','2026-08'])]:
            w=windows[index]; self.assertEqual((w['origin'],w['training_mask']['count'],w['training_mask']['last']),(origin,count,last))
            self.assertEqual([t['month'] for t in w['targets']],targets)
            self.assertEqual([t['model_lead'] for t in w['targets']],[2,4,7])
        # Independent calendar expectations for EVERY descriptor, not production helpers.
        self.assertEqual([w['origin'] for w in windows],months('2021-09','2023-08')+months('2024-03','2026-02'))
        for w in windows:
            y,m=map(int,w['origin'].split('-')); ordinal=y*12+m-1
            cutoff=f'{(ordinal-1)//12:04d}-{(ordinal-1)%12+1:02d}'
            training=months('2016-09',cutoff)
            self.assertEqual(w['training_mask']['sha256_of_ordered_month_list'],sha(canon(training)))
            self.assertEqual(w['training_mask']['count'],len(training))
            self.assertEqual(w['split'],'validation' if w['origin']<='2023-08' else 'test')
            instant=f'{w["origin"]}-{calendar.monthrange(y,m)[1]:02d}T23:59:59.999999Z'
            self.assertEqual(w['evaluated_at_proxy_utc'],instant)
            self.assertEqual(w['training_cutoff_available_at_proxy_utc'],instant)
            for target,horizon in zip(w['targets'],(1,3,6)):
                t=ordinal+horizon; py,pm=divmod(t+1,12); pm+=1
                self.assertEqual(target['month'],f'{t//12:04d}-{t%12+1:02d}')
                self.assertEqual(target['available_at_proxy_utc'],f'{py:04d}-{pm:02d}-{calendar.monthrange(py,pm)[1]:02d}T23:59:59.999999Z')
        self.assertEqual(len(result['planned_windows']['expected_months']),120)
        self.assertEqual(len(result['candidate_ledger']),96)
        for r in result['candidate_ledger']:
            self.assertEqual(r['status'],'not_evaluated'); self.assertIsNone(r['backend'])
        for k in ('selection','calibration','metrics'): self.assertIsNone(result[k])
        self.assertFalse(result['execution_authorized'])

    def test_metadata_identity_and_exposure_are_truthful(self):
        # Dropping public reasons/identities or claiming untouched actuals must fail.
        _,_,_,snapshot=self.fixture(labels=['2026-07','2026-08'])
        public=adapter.build_panel_readiness(snapshot,*self.controls)
        value=consumer.build_consumer_boundary_readiness(snapshot,*self.controls)
        result=json.loads(value.payload)
        self.assertEqual(value.request,snapshot.request)
        self.assertEqual(result.get('schema_version'),'synthetic-consumer-boundary-readiness.v1')
        self.assertEqual(result['snapshot_identity'],public['snapshot_identity'])
        self.assertEqual(result['readiness_identity']['sha256'],sha(canon(public)))
        self.assertEqual(result['readiness_identity']['bytes'],len(canon(public)))
        self.assertEqual(result['readiness_identity']['all_models_not_evaluated'],public['all_models_not_evaluated'])
        self.assertEqual(result['exposure'],{'parent_full_snapshot_exposed':True,'fitter_values_released':False,
            'scorer_actuals_released':False,'physical_test_actuals_untouched':False,'publication_authorized':False})
        self.assertEqual(set(result),{'schema_version',*FLAGS,'status','control_identity','snapshot_identity',
            'readiness_identity','consumer_code_identity','exposure','planned_windows','series_readiness',
            'candidate_ledger','phase_dependencies','selection','calibration','metrics'})
        for k,v in FLAGS.items(): self.assertIs(type(result[k]),type(v)); self.assertEqual(result[k],v)
        self.assertEqual(result['phase_dependencies'],['NO_HOST_PHASE_AUTHORITY','FIT_SCORER_SEPARATION_NOT_IMPLEMENTED',
            'ACTUAL_ACCESS_GUARANTEE_NOT_ACCEPTED','PHYSICAL_STAGED_SOURCE_READER_UNAVAILABLE'])
        for old,new in zip(public['candidate_ledger'],result['candidate_ledger']):
            for k,v in old.items():
                if k!='reasons': self.assertEqual(new[k],v)
            self.assertEqual(new['reasons'][:len(old['reasons'])],old['reasons'])
            self.assertTrue(set(['CONSUMER_READINESS_ONLY','PHASE_EXECUTION_HELD','CONVERSION_NOT_CHECKED'])<=set(new['reasons']))
        for old,new in zip(public['series_readiness'],result['series_readiness']):
            for split in ('validation','test'):
                for a,b in zip(old['origins'][split],new['origins'][split]):
                    self.assertEqual({k:b[k] for k in a},a)
                    self.assertEqual(b['conversion_readiness'],'not_checked'); self.assertEqual(b['phase_execution'],'held')
        for k in ('basis','values','rows','p10','p50','p90','actual','float_hex'):
            self.assertNotIn('"'+k+'":',value.payload.decode())
        with self.assertRaises(AttributeError): value.payload=b'forged'

    def test_incomplete_and_gap_do_not_shorten_planned_cohort(self):
        # Returning any alternative full common cohort from missing evidence fails.
        for labels,missing in [(months('2016-09','2026-07'),None),
                               (None,('2018-01','copper'))]:
            with self.subTest(missing=missing):
                _,_,_,snapshot=self.fixture(labels=labels,missing=missing)
                out=self.build(snapshot)
                self.assertEqual(len(out.get('planned_windows',{}).get('origins',[])),48)
                self.assertEqual(out['planned_windows']['structural_common_origins'],{'validation':[],'test':[]})
                self.assertTrue(out['series_readiness'][0]['split_reasons']['test'])
                self.assertEqual(len(out['candidate_ledger']),96)

    def test_native_survives_absent_and_refused_companions(self):
        # A refused companion must not hide independent native diagnostics.
        b,p,j,_=self.fixture(labels=['2026-07','2026-08'])
        for companion in (None,{**b['fx'],'source_receipt_sha256':'0'*64}):
            with self.subTest(companion=companion is None):
                request={'commodity':b['commodity'],'fx':companion}
                snapshot=adapter.load_verified_panel_series(request,p,None)
                out=self.build(snapshot)
                self.assertEqual(out['series_readiness'][0]['publication_status'],'complete_synthetic')
                self.assertIn(out['series_readiness'][2]['publication_status'],('not_supplied','integrity_refused'))
                self.assertEqual(out['planned_windows']['structural_common_origins'],{'validation':[],'test':[]})
                self.assertEqual(out['readiness_identity']['all_models_not_evaluated'][-1]['series_id'],'provider_dl')

    def test_exact_microsecond_mask_proxy_and_extra_source_identity(self):
        # Date truncation, off-by-one leads or discarded extra identity fails.
        _,_,_,snapshot=self.fixture(labels=months('2016-08','2026-08'))
        out=self.build(snapshot); w=out['planned_windows']['origins']
        self.assertEqual(w[0]['evaluated_at_proxy_utc'],'2021-09-30T23:59:59.999999Z')
        self.assertEqual(w[0]['training_cutoff_available_at_proxy_utc'],w[0]['evaluated_at_proxy_utc'])
        self.assertEqual(w[-1]['targets'][-1]['available_at_proxy_utc'],'2026-09-30T23:59:59.999999Z')
        self.assertEqual(w[0]['training_mask']['first'],'2016-09')
        self.assertEqual(out['snapshot_identity']['mask_sha256'],sha(canon(months('2016-08','2026-08'))))
        self.assertEqual(out['planned_windows']['expected_months'][0],'2016-09')

    def test_future_only_valid_snapshot_changes_identity_not_earlier_masks(self):
        # Full identity must change despite an unchanged past training mask.
        _,_,_,a=self.fixture(future='1000'); _,_,_,b=self.fixture(future='1001')
        old,new=self.build(a),self.build(b)
        self.assertNotEqual(old['snapshot_identity'],new['snapshot_identity'])
        self.assertNotEqual(old['readiness_identity']['sha256'],new['readiness_identity']['sha256'])
        self.assertEqual(old['planned_windows'],new['planned_windows'])
        self.assertEqual(json.loads(a.payload)['series'][0]['rows'][:60],json.loads(b.payload)['series'][0]['rows'][:60])

    def test_colliding_exact_tokens_remain_unconverted(self):
        # Claiming conversion readiness from structural availability fails.
        _,_,_,snapshot=self.fixture(collision=True)
        out=self.build(snapshot)
        self.assertEqual(len(out['planned_windows']['structural_common_origins']['validation']),24)
        self.assertEqual(len(out['planned_windows']['structural_common_origins']['test']),24)
        for s in out['series_readiness']:
            for split in ('validation','test'):
                self.assertTrue(all(r['structurally_ready'] and r['conversion_readiness']=='not_checked' for r in s['origins'][split]))

    def assert_refusal(self,snapshot,code=None,controls=None):
        with self.assertRaises(consumer.ConsumerBoundaryError) as caught:
            consumer.build_consumer_boundary_readiness(snapshot,*(controls or self.controls))
        error=caught.exception
        self.assertIn(str(error),{'CONSUMER_BINDING_INVALID','CONSUMER_CONTROL_HOLD','CONSUMER_SNAPSHOT_MISMATCH',
            'CONSUMER_SOURCE_CHANGED','CONSUMER_CODE_CHANGED','CONSUMER_INTEGRITY_UNRESOLVED'})
        self.assertTrue(error.__suppress_context__)
        if code: self.assertEqual(str(error),code)
        return error

    def test_original_same_byte_replacement_during_readiness_refuses(self):
        # Fresh hashes alone miss this inode replacement across PUBLIC calls.
        b,_,_,snapshot=self.fixture(labels=['2026-07','2026-08'])
        original=Path(b['commodity']['original_root'])/'objects/fixture/original.csv'
        real=consumer.build_panel_readiness
        def mutate(*args):
            readiness=real(*args)
            replacement=original.with_name('replacement.csv'); write(replacement,original.read_bytes())
            os.replace(replacement,original)
            return readiness
        with patch.object(consumer,'build_panel_readiness',side_effect=mutate):
            self.assert_refusal(snapshot,'CONSUMER_SOURCE_CHANGED')

    def test_controls_and_bad_flags_refuse_before_any_open(self):
        # An invalid envelope/control reaching even archive admission is a bug.
        _,_,_,snapshot=self.fixture(labels=['2026-07','2026-08'])
        bad=[]
        for key,value in [('synthetic',1),('execution_authorized',True),('rights','observed')]:
            p=json.loads(snapshot.payload); p[key]=value
            bad.append(adapter.VerifiedPanelSeries(snapshot.request,canon(p)))
        r=json.loads(snapshot.request); r['component_bindings']['fx']['provenance']['source_authenticity']=True
        bad.append(adapter.VerifiedPanelSeries(canon(r),snapshot.payload))
        p=json.loads(snapshot.payload); p['series'][0]['is_synthetic']=False
        bad.append(adapter.VerifiedPanelSeries(snapshot.request,canon(p)))
        with patch.object(os,'open',side_effect=AssertionError('invalid input reached open')):
            for value in bad: self.assert_refusal(value,'CONSUMER_BINDING_INVALID')
            self.assert_refusal(snapshot,'CONSUMER_CONTROL_HOLD',(self.controls[0]+b' ',self.controls[1]))
            modified=self.controls[0].replace(b'false',b'true')
            self.assert_refusal(snapshot,'CONSUMER_CONTROL_HOLD',(modified,self.controls[1]))

    def test_canonical_limits_and_shapes_refuse_before_open(self):
        # Accepting duplicates/nonfinite/float/deep/large/extra keys is a bug.
        _,_,_,snapshot=self.fixture(labels=['2026-07','2026-08'])
        raw_cases=[snapshot.request+b' ',b'{"x":1,"x":2}',b'{"x":1.5}',b'{"x":NaN}',
                   b'['*14+b'0'+b']'*14,b'"'+b'a'*8193+b'"',b'a'*(4194304+1)]
        p=json.loads(snapshot.payload); p['unexpected']=True
        cases=[adapter.VerifiedPanelSeries(raw,snapshot.payload) for raw in raw_cases]
        cases.append(adapter.VerifiedPanelSeries(snapshot.request,canon(p)))
        cases.append(adapter.VerifiedPanelSeries(bytearray(snapshot.request),snapshot.payload))
        cases.append(object())
        with patch.object(os,'open',side_effect=AssertionError('invalid input reached open')):
            for value in cases: self.assert_refusal(value,'CONSUMER_BINDING_INVALID')

    def test_forged_exact_values_and_proxy_fail_public_binding(self):
        # Reconstructing PUBLIC source must defeat copied self-consistent metadata.
        _,_,_,snapshot=self.fixture(labels=['2026-07','2026-08'])
        p=json.loads(snapshot.payload); p['series'][0]['rows'][0]['basis']['numerator']='1001'
        self.assert_refusal(adapter.VerifiedPanelSeries(snapshot.request,canon(p)),'CONSUMER_SNAPSHOT_MISMATCH')
        p=json.loads(snapshot.payload); p['series'][0]['rows'][0]['available_at_proxy']='2026-09-01T00:00:00.000000Z'
        with patch.object(os,'open',side_effect=AssertionError('forged proxy reached open')):
            self.assert_refusal(adapter.VerifiedPanelSeries(snapshot.request,canon(p)),'CONSUMER_BINDING_INVALID')

    def test_public_full_parent_exposure_not_wrapper_body_read(self):
        # Source read by wrapper or an untouched-parent claim is a bug.
        b,_,_,snapshot=self.fixture(labels=['2026-07','2026-08'])
        original=Path(b['commodity']['original_root'])/'objects/fixture/original.csv'
        opened={}; readers=[]; real_open=os.open; real_read=os.read; real_close=os.close
        import traceback
        def opened_file(path,*args,**kwargs):
            fd=real_open(path,*args,**kwargs); opened[fd]=str(path); return fd
        def read_file(fd,*args):
            if opened.get(fd)=='original.csv':
                stack=traceback.extract_stack()
                readers.append(stack[-2].filename)
            return real_read(fd,*args)
        def close_file(fd):
            try: return real_close(fd)
            finally: opened.pop(fd,None)
        with patch.object(os,'open',side_effect=opened_file),patch.object(os,'read',side_effect=read_file),patch.object(os,'close',side_effect=close_file):
            out=self.build(snapshot)
        self.assertTrue(readers)
        self.assertTrue(all(not p.endswith('consumer_boundary.py') for p in readers))
        self.assertTrue(out['exposure']['parent_full_snapshot_exposed'])
        self.assertFalse(out['exposure']['physical_test_actuals_untouched'])

    def test_refused_original_not_opened_by_consumer_metadata(self):
        # Metadata-only wrapper must not promote a PUBLIC refused lane.
        b,p,_,_=self.fixture(labels=['2026-07','2026-08'])
        b['fx']['source_receipt_sha256']='0'*64
        snapshot=adapter.load_verified_panel_series(b,p,None)
        real=consumer._Lifetime.file; wrapper_originals=[]
        def record(guard,parent,name,*args,**kwargs):
            if name.endswith('.raw'): wrapper_originals.append(name)
            return real(guard,parent,name,*args,**kwargs)
        with patch.object(consumer._Lifetime,'file',record): out=self.build(snapshot)
        self.assertEqual(wrapper_originals,[])
        self.assertEqual(out['series_readiness'][2]['publication_status'],'integrity_refused')
        self.assertEqual(out['series_readiness'][0]['publication_status'],'complete_synthetic')

    def test_receipt_permission_refusal_does_not_read_original_body(self):
        # PUBLIC receipt permission refusal must precede source body access.
        b,p,_,_=self.fixture(labels=['2026-07','2026-08'])
        receipt_path=Path(b['commodity']['original_root'])/'committed/fixture.json'
        receipt=json.loads(receipt_path.read_bytes()); receipt['source_authenticity']=True
        write(receipt_path,canon(receipt)); b['commodity']['fixture_receipt_sha256']=sha(canon(receipt))
        real=os.open; original_opens=[]
        def spy(path,*args,**kwargs):
            if str(path)=='original.csv': original_opens.append(path)
            return real(path,*args,**kwargs)
        with patch.object(os,'open',side_effect=spy):
            snapshot=adapter.load_verified_panel_series(b,p,None); out=self.build(snapshot)
        self.assertEqual(original_opens,[])
        self.assertEqual(out['series_readiness'][0]['publication_status'],'integrity_refused')
        self.assertEqual(out['series_readiness'][2]['publication_status'],'complete_synthetic')
        self.assertTrue(receipt_path.exists())

    def test_existing_failure_marker_all_entry_types_preserved(self):
        # A bytes marker, dangling link, directory or FIFO always disqualifies.
        for kind in ('regular','dangling','directory','fifo'):
            with self.subTest(kind=kind):
                b,p,j,_=self.fixture(labels=['2026-07','2026-08'])
                marker=Path(j['output_root'])/'disqualified/joined.json'
                if kind=='regular': write(marker,b'failure evidence')
                elif kind=='dangling': marker.symlink_to('missing-target')
                elif kind=='directory': marker.mkdir(mode=0o700)
                else: os.mkfifo(marker,0o600)
                before=marker.lstat()
                snapshot=adapter.load_verified_panel_series(b,p,j)
                out=self.build(snapshot)
                after=marker.lstat()
                self.assertEqual((before.st_dev,before.st_ino,before.st_mode),(after.st_dev,after.st_ino,after.st_mode))
                self.assertEqual(out['series_readiness'][3]['publication_status'],'integrity_refused')
                self.assertEqual(out['series_readiness'][0]['publication_status'],'complete_synthetic')
                if kind=='regular': self.assertEqual(marker.read_bytes(),b'failure evidence')
                if kind=='dangling': self.assertEqual(os.readlink(marker),'missing-target')

    def test_late_join_failure_preserves_marker_and_refuses_return(self):
        # An any-type disqualification appearing inside the held lifetime aborts.
        _,_,j,snapshot=self.fixture(labels=['2026-07','2026-08'])
        marker=Path(j['output_root'])/'disqualified/joined.json'; real=consumer.build_panel_readiness
        def mutate(*args):
            out=real(*args); marker.symlink_to('missing-target'); return out
        with patch.object(consumer,'build_panel_readiness',side_effect=mutate):
            self.assert_refusal(snapshot,'CONSUMER_SOURCE_CHANGED')
        self.assertTrue(marker.is_symlink()); self.assertEqual(os.readlink(marker),'missing-target')

    def test_late_opposite_and_fx_source_failure_refuse(self):
        # Neither a panel opposite marker nor FX incomplete fact may appear late.
        for kind in ('panel_opposite','fx_original_incomplete'):
            with self.subTest(kind=kind):
                b,_,_,snapshot=self.fixture(labels=['2026-07','2026-08'])
                marker=(Path(b['commodity']['panel_root'])/'incomplete/panel.json' if kind=='panel_opposite'
                        else Path(b['fx']['original_root'])/'incomplete/fixture.json')
                real=consumer.build_panel_readiness
                def mutate(*args):
                    out=real(*args); write(marker,b'preserved failure'); return out
                with patch.object(consumer,'build_panel_readiness',side_effect=mutate):
                    self.assert_refusal(snapshot,'CONSUMER_SOURCE_CHANGED')
                self.assertEqual(marker.read_bytes(),b'preserved failure')

    def test_original_ancestor_replacement_with_same_files_refuses(self):
        # Identity must include directories, not only their leaf files.
        b,_,_,snapshot=self.fixture(labels=['2026-07','2026-08'])
        obj=Path(b['commodity']['original_root'])/'objects/fixture'
        real=consumer.build_panel_readiness
        def mutate(*args):
            out=real(*args); old=obj.with_name('preserved-fixture'); obj.rename(old)
            obj.mkdir(mode=0o700); os.link(old/'original.csv',obj/'original.csv'); return out
        with patch.object(consumer,'build_panel_readiness',side_effect=mutate):
            self.assert_refusal(snapshot,'CONSUMER_SOURCE_CHANGED')

    def test_code_read_mismatch_refuses_without_source_open(self):
        # Wrong pinned code bytes must abort before any archive read.
        _,_,_,snapshot=self.fixture(labels=['2026-07','2026-08'])
        real=consumer.os.read; source_calls=[]; public=consumer.load_verified_panel_series
        def changed(fd,size):
            raw=real(fd,size)
            return (b'X'+raw[1:]) if raw else raw
        def record(*args,**kwargs): source_calls.append(True); return public(*args,**kwargs)
        with patch.object(consumer.os,'read',side_effect=changed),patch.object(consumer,'load_verified_panel_series',side_effect=record):
            self.assert_refusal(snapshot,'CONSUMER_CODE_CHANGED')
        self.assertEqual(source_calls,[])

    def test_runner_code_read_mismatch_refuses_before_source_open(self):
        # The observed runner is a later pin; a bad read there must still stop
        # before any original or public panel is loaded.
        _,_,_,snapshot=self.fixture(labels=['2026-07','2026-08'])
        real_open=consumer.os.open; real_read=consumer.os.read; public=consumer.load_verified_panel_series
        runner_fds=set(); source_calls=[]
        def opened(path,*args,**kwargs):
            fd=real_open(path,*args,**kwargs)
            if path=='observed_runner.py': runner_fds.add(fd)
            return fd
        def changed(fd,size):
            raw=real_read(fd,size)
            return b'X'+raw[1:] if fd in runner_fds and raw else raw
        def record(*args,**kwargs):
            source_calls.append(True)
            return public(*args,**kwargs)
        with patch.object(consumer.os,'open',side_effect=opened),\
             patch.object(consumer.os,'read',side_effect=changed),\
             patch.object(consumer,'load_verified_panel_series',side_effect=record):
            self.assert_refusal(snapshot,'CONSUMER_CODE_CHANGED')
        self.assertTrue(runner_fds)
        self.assertEqual(source_calls,[])

    def test_hardlink_metadata_mismatch_refuses(self):
        # Hardlink equality is checked using anchored descriptors, not byte hashes.
        _,_,_,snapshot=self.fixture(labels=['2026-07','2026-08'])
        real=consumer._Lifetime.hardlink; real_fstat=os.fstat
        def altered(guard,first,second):
            def mismatch(fd):
                out=real_fstat(fd)
                if fd==second:
                    fields=list(out); fields[1]+=1; return os.stat_result(fields)
                return out
            with patch.object(os,'fstat',side_effect=mismatch): return real(guard,first,second)
        with patch.object(consumer._Lifetime,'hardlink',altered):
            self.assert_refusal(snapshot,'CONSUMER_SOURCE_CHANGED')

    def test_first_fstat_failure_releases_every_acquired_descriptor(self):
        # Owning a descriptor after fstat would leak this first acquisition.
        _,_,_,snapshot=self.fixture(labels=['2026-07','2026-08'])
        real_open=os.open; real_close=os.close; acquired=[]; closed=[]
        def acquire(*args,**kwargs):
            fd=real_open(*args,**kwargs); acquired.append(fd); return fd
        def close(fd): closed.append(fd); return real_close(fd)
        with (patch.object(os,'open',side_effect=acquire),
              patch.object(os,'fstat',side_effect=OSError('private fault')),
              patch.object(os,'close',side_effect=close)):
            self.assert_refusal(snapshot,'CONSUMER_INTEGRITY_UNRESOLVED')
        self.assertEqual(acquired,closed); self.assertEqual(len(acquired),1)
        with self.assertRaises(OSError): os.fstat(acquired[0])

    def test_success_close_fault_no_result_and_no_fd_reuse(self):
        # A real close followed by a reported fault must never become success.
        _,_,_,snapshot=self.fixture(labels=['2026-07','2026-08'])
        real_close=os.close; calls=[]; original=consumer._Lifetime.close
        def failure(guard):
            target=guard.fds[-1]
            def injected(fd):
                calls.append(fd); real_close(fd)
                if fd==target: raise OSError('private close fault')
            with patch.object(os,'close',side_effect=injected): return original(guard)
        with patch.object(consumer._Lifetime,'close',failure):
            self.assert_refusal(snapshot,'CONSUMER_INTEGRITY_UNRESOLVED')
        self.assertEqual(len(calls),len(set(calls)))
        for fd in calls:
            with self.assertRaises(OSError): os.fstat(fd)

    def test_postclose_path_change_refuses(self):
        # Successful OS closes do not excuse late pathname/absence changes.
        _,_,j,snapshot=self.fixture(labels=['2026-07','2026-08'])
        marker=Path(j['output_root'])/'disqualified/joined.json'
        real_close=os.close; original=consumer._Lifetime.close
        def change_after_close(guard):
            target=guard.fds[0]
            def close(fd):
                real_close(fd)
                if fd==target: write(marker,b'late-close failure')
            with patch.object(os,'close',side_effect=close): return original(guard)
        with patch.object(consumer._Lifetime,'close',change_after_close):
            self.assert_refusal(snapshot,'CONSUMER_SOURCE_CHANGED')
        self.assertEqual(marker.read_bytes(),b'late-close failure')

    def test_final_public_reload_prevents_late_source_payload_change(self):
        # Returning a planned result without the final PUBLIC reload fails.
        b,_,_,snapshot=self.fixture(labels=['2026-07','2026-08'])
        path=Path(b['commodity']['original_root'])/'objects/fixture/original.csv'
        real=consumer.load_verified_panel_series; calls=[]
        def reload(*args,**kwargs):
            calls.append(True)
            if len(calls)==2:
                write(path,path.read_bytes().replace(b'1000',b'1001'))
            return real(*args,**kwargs)
        with patch.object(consumer,'load_verified_panel_series',side_effect=reload): self.assert_refusal(snapshot)
        self.assertEqual(len(calls),2)

    def test_extra_source_mask_129_months_retains_full_public_identity(self):
        # A key-name limit must not truncate the accepted240-month date-key map.
        for first in ('2015-12','2006-09'):
            with self.subTest(first=first):
                labels=months(first,'2026-08')
                _,_,_,snapshot=self.fixture(labels=labels)
                try: out=self.build(snapshot)
                except consumer.ConsumerBoundaryError as exc: self.fail('Valid PUBLIC bounded snapshot refused: '+str(exc))
                self.assertEqual(out['snapshot_identity']['mask_sha256'],sha(canon(labels)))
                self.assertEqual(len(out['planned_windows']['expected_months']),120)
                self.assertEqual(out['planned_windows']['origins'][0]['training_mask']['count'],60)

    def test_consumer_call_cannot_write_import_models_or_make_network_calls(self):
        # A consumer side effect/prohibited import must fail, even with valid inputs.
        import builtins
        import socket
        _,_,_,snapshot=self.fixture(labels=['2026-07','2026-08'])
        real_open=os.open; real_import=builtins.__import__
        files={str(p.relative_to(self.home)):(p.lstat().st_ino,sha(p.read_bytes())) for p in self.home.rglob('*') if p.is_file()}
        def read_only(path,flags,*args,**kwargs):
            self.assertEqual(flags&(os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC|os.O_APPEND),0)
            return real_open(path,flags,*args,**kwargs)
        def safe_import(name,*args,**kwargs):
            if name.startswith(('numpy','pandas','procure.forecast','procure.evaluation.model_input',
                                'procure.evaluation.observed_runner','procure.evaluation.forecast_scorecard')):
                raise AssertionError('prohibited consumer import')
            return real_import(name,*args,**kwargs)
        with (patch.object(os,'open',side_effect=read_only),
              patch.object(builtins,'__import__',side_effect=safe_import),
              patch.object(socket,'getaddrinfo',side_effect=AssertionError('DNS forbidden')),
              patch.object(socket,'create_connection',side_effect=AssertionError('network forbidden'))):
            out=self.build(snapshot)
        after={str(p.relative_to(self.home)):(p.lstat().st_ino,sha(p.read_bytes())) for p in self.home.rglob('*') if p.is_file()}
        self.assertEqual(after,files)
        self.assertEqual(out['status'],'SYNTHETIC_CONSUMER_READINESS_ONLY')
        for relative,identity in out['consumer_code_identity'].items():
            raw=(ENGINE/'src/procure'/relative).read_bytes()
            self.assertEqual((identity['bytes'],identity['sha256']),(len(raw),sha(raw)))
