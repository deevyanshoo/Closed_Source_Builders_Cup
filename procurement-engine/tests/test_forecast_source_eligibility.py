"""Pure synthetic metadata checks; no observations, sources, fitter or runner imports."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import unittest

MODULE=Path(__file__).parents[1]/'src/procure/evaluation/source_eligibility.py'
PROTOCOL=Path(__file__).parents[1]/'evaluation/forecast/protocol.json'
ROOT_OFFLINE_ACCEPTANCE='5401146e11cebf9060763c11c6cec86d5e1fbb5c64b6584b63023cc823414cab'

def digest(value): return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
def production():
    assert MODULE.is_file(),'missing pure metadata eligibility implementation'
    spec=importlib.util.spec_from_file_location('offline_eligibility_under_test',MODULE)
    m=importlib.util.module_from_spec(spec); sys.modules[spec.name]=m; spec.loader.exec_module(m); return m

def fixtures():
    # Independent literal monthly grid: exactly September2016 through August2026.
    months=[f'{y:04d}-{m:02d}' for y in range(2016,2027) for m in range(1,13) if '2016-09'<=f'{y:04d}-{m:02d}'<='2026-08']
    rows={}; receipts={}
    for name in ('copper','aluminum'):
        rows[name]={'source_id':name,'original_sha256':('a' if name=='copper' else 'b')*64,'is_synthetic':True,'unit':'USD/metric tonne','coverage':'complete','months':[{'month':m,'complete':True} for m in months]}
        receipts[name]={'schema_version':'offline-source-receipt.v1','source_id':name,'synthetic':True,'status':'complete','original_sha256':rows[name]['original_sha256'],'metadata_sha256':digest(rows[name]),'rights':'synthetic_only','vintage':'synthetic_latest_vintage','retrieved_at':'2026-10-08T00:00:00Z'}
    raw=PROTOCOL.read_text()
    protocol={'raw_json':raw,'sha256':hashlib.sha256(raw.encode()).hexdigest()}
    cohort={'kind':'native_subset','series_ids':['copper','aluminum'],'decision_sha256':ROOT_OFFLINE_ACCEPTANCE}
    return receipts,rows,cohort,'2026-10-08T12:00:00Z',protocol

class EligibilityTests(unittest.TestCase):
    def setUp(self): self.m=production(); self.args=list(fixtures())
    def run_case(self): return self.m.inspect_source_eligibility(*self.args)
    def rebind(self,name): self.args[0][name]['metadata_sha256']=digest(self.args[1][name])
    def assert_unavailable(self, code):
        result=self.run_case(); self.assertEqual(result['status'],'UNAVAILABLE'); self.assertIn(code,result['reasons']); self.assertFalse(result['execution_authorized']); return result
    def test_exact_frozen_windows_cutoffs_and_leads_without_fitting(self):
        r=self.run_case()
        self.assertEqual(r['status'],'OFFLINE_METADATA_COMPATIBLE'); self.assertFalse(r['execution_authorized']); self.assertFalse(r['point_in_time_proven'])
        self.assertEqual(r['common_cutoff'],'2026-08')
        self.assertEqual(r['training_start'],'2016-09')
        self.assertEqual((len(r['validation']),r['validation'][0],r['validation'][-1]),(24,'2021-09','2023-08'))
        self.assertEqual(r['purged'],['2023-09','2023-10','2023-11','2023-12','2024-01','2024-02'])
        self.assertEqual((len(r['test']),r['test'][0],r['test'][-1]),(24,'2024-03','2026-02'))
        self.assertEqual(r['required_source_months'],120); self.assertEqual(r['model_leads'],[2,4,7]); self.assertTrue(r['runner_compatible'])
    def test_full_joined_cohort_absent_fx_has_null_cutoff(self):
        self.args[2]={'kind':'joined','series_ids':['copper','aluminum','ecb_usd_eur','ecb_inr_eur'],'decision_sha256':ROOT_OFFLINE_ACCEPTANCE}
        r=self.assert_unavailable('SOURCE_MISSING'); self.assertIsNone(r['common_cutoff'])
    def test_hole_rejects_without_origin_shifting(self):
        self.args[1]['copper']['months']=[x for x in self.args[1]['copper']['months'] if x['month']!='2025-01']; self.rebind('copper')
        r=self.assert_unavailable('MISSING_SUPPORT_MONTH'); self.assertEqual(r['common_cutoff'],'2026-08'); self.assertEqual(r['test'],[])
    def test_59_training_months_rejects(self):
        self.args[1]['aluminum']['months'].pop(0); self.rebind('aluminum'); self.assert_unavailable('MISSING_SUPPORT_MONTH')
    def test_late_source_cutoff_does_not_shift_fixed_protocol(self):
        self.args[1]['aluminum']['months'].pop(); self.rebind('aluminum'); self.assert_unavailable('RUNNER_WINDOW_INCOMPATIBLE')
    def test_future_september_ignored_under_proxy(self):
        for name in ('copper','aluminum'):
            self.args[1][name]['months'].append({'month':'2026-09','complete':True}); self.rebind(name)
        r=self.run_case(); self.assertEqual(r['common_cutoff'],'2026-08'); self.assertEqual(r['status'],'OFFLINE_METADATA_COMPATIBLE')
    def test_future_receipt_rejects(self):
        self.args[0]['copper']['retrieved_at']='2026-10-09T00:00:00Z'; self.assert_unavailable('FUTURE_RECEIPT')
    def test_aware_utc_calendar_and_fixed_asof_required(self):
        for value,code in [('2026-10-08','ASOF_INVALID'),('2026-02-30T00:00:00Z','ASOF_INVALID'),('2026-10-08T12:00:00+05:30','ASOF_INVALID'),('2026-10-09T00:00:00Z','RUNNER_ASOF_INCOMPATIBLE')]:
            with self.subTest(value=value): self.args[3]=value; self.assert_unavailable(code)
    def test_hash_missing_mismatch_or_tamper_rejects(self):
        for field in ('metadata_sha256','original_sha256'):
            for value in (None,'z'*64,'c'*64):
                with self.subTest(field=field,value=value):
                    self.args=list(fixtures()); self.args[0]['copper'][field]=value; self.assert_unavailable('HASH_BINDING')
    def test_unapproved_units_rights_coverage_and_vintage_reject(self):
        for where,field,value,code in [(1,'unit','USD/dmtu','UNIT_HOLD'),(1,'coverage','unknown','COVERAGE_HOLD'),(0,'rights','HOLD','RIGHTS_HOLD'),(0,'vintage','unknown','VINTAGE_HOLD')]:
            with self.subTest(field=field):
                self.args=list(fixtures()); self.args[where]['copper'][field]=value
                if where==1: self.rebind('copper')
                self.assert_unavailable(code)
    def test_observed_metadata_cannot_earn_execution_permission(self):
        self.args[0]['copper']['synthetic']=False; self.assert_unavailable('OBSERVED_METADATA_HOLD')
    def test_native_subset_needs_exact_offline_root_decision(self):
        self.args[2]['decision_sha256']='f'*64; self.assert_unavailable('COHORT_DECISION_HOLD')
    def test_protocol_document_hash_and_fixed_lead_binding(self):
        self.args[4]['sha256']='f'*64; self.assert_unavailable('PROTOCOL_BINDING')
        self.args=list(fixtures()); p=json.loads(self.args[4]['raw_json']); p['model_leads_from_last_training_month']=[1,3,6]
        raw=json.dumps(p); self.args[4]={'raw_json':raw,'sha256':hashlib.sha256(raw.encode()).hexdigest()}; self.assert_unavailable('PROTOCOL_BINDING')
    def test_duplicate_invalid_incomplete_months_are_denied(self):
        for row,code in [({'month':'2020-02','complete':True},'MONTH_METADATA_INVALID'),({'month':'2020-13','complete':True},'MONTH_METADATA_INVALID'),({'month':'2020-02','complete':False},'MONTH_METADATA_INVALID')]:
            with self.subTest(row=row):
                self.args=list(fixtures()); self.args[1]['copper']['months'].append(row); self.rebind('copper'); self.assert_unavailable(code)
    def test_unknown_value_rows_are_not_a_metadata_interface(self):
        self.args[1]['copper']['months'][0]['value']='100.1'; self.rebind('copper'); self.assert_unavailable('MONTH_METADATA_INVALID')
    def test_delayed_history_cannot_enter_an_earlier_training_origin(self):
        row=next(x for x in self.args[1]['copper']['months'] if x['month']=='2024-02')
        row['available_at']='2024-04-01T00:00:00Z'; self.rebind('copper')
        self.assert_unavailable('TRAINING_AVAILABILITY_INVALID')
    def test_delayed_validation_target_cannot_calibrate_first_test(self):
        row=next(x for x in self.args[1]['aluminum']['months'] if x['month']=='2024-02')
        row['available_at']='2024-04-01T00:00:00Z'; self.rebind('aluminum')
        self.assert_unavailable('VALIDATION_AVAILABILITY_INVALID')
    def test_origin_is_utc_month_end_with_previous_month_training_cutoff(self):
        r=self.run_case()
        self.assertEqual(r['origin_instant_basis'],'UTC_END_OF_ORIGIN_MONTH')
        self.assertEqual(r['training_cutoffs']['2024-03'],'2024-02')
        self.assertEqual(r['targets']['2024-03'],['2024-04','2024-06','2024-09'])
    def test_no_inputs_are_mutated(self):
        before=copy.deepcopy(self.args); self.run_case(); self.assertEqual(self.args,before)

if __name__=='__main__': unittest.main()
