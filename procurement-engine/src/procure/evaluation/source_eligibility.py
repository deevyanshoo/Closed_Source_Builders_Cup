"""Pure stdlib, synthetic metadata-only eligibility against the delivered protocol.

No files, observations, normalizers, models or runner are imported/read. The exact
ROOT acceptance permits offline synthetic metadata only. Observed metadata remains
HOLD, even if a caller changes booleans or supplies plausible rights. This function
never authorizes execution and never establishes historical publication/PIT.
"""
from __future__ import annotations

import calendar
import datetime as dt
import hashlib
import json
import re

PROTOCOL_SHA256 = 'ac970b1dace90dec6882913f32c146f9fd592f1502125c0e63da460f5c874f66'
OFFLINE_ROOT_ACCEPTANCE_SHA256 = '5401146e11cebf9060763c11c6cec86d5e1fbb5c64b6584b63023cc823414cab'
NATIVE_COHORT = ['copper','aluminum']
UNITS = {'copper':'USD/metric tonne','aluminum':'USD/metric tonne',
         'ecb_usd_eur':'USD/EUR','ecb_inr_eur':'INR/EUR'}


def _digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def _hash(value):
    return type(value) is str and re.fullmatch(r'[0-9a-f]{64}',value) is not None


def _utc(value):
    if type(value) is not str or not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|\+00:00)',value):
        raise ValueError()
    parsed=dt.datetime.fromisoformat(value.replace('Z','+00:00'))
    if parsed.utcoffset()!=dt.timedelta(0): raise ValueError()
    return parsed


def _index(value):
    if type(value) is not str or not re.fullmatch(r'\d{4}-(?:0[1-9]|1[0-2])',value): raise ValueError()
    year,month=map(int,value.split('-'))
    if not 1 <= year <= 9998: raise ValueError()
    return year*12+month-1


def _month(index):
    if not 12 <= index <= 9998*12+11: raise ValueError()
    return f'{index//12:04d}-{index%12+1:02d}'


def _month_end(index):
    value=_month(index); year,month=map(int,value.split('-'))
    return dt.datetime(year,month,calendar.monthrange(year,month)[1],23,59,59,999999,tzinfo=dt.timezone.utc)


def _protocol(identity):
    if type(identity) is not dict or set(identity)!={'raw_json','sha256'}: raise ValueError()
    raw=identity['raw_json']
    if type(raw) is not str or len(raw.encode())>16384 or identity['sha256']!=PROTOCOL_SHA256 or hashlib.sha256(raw.encode()).hexdigest()!=PROTOCOL_SHA256: raise ValueError()
    def pairs(rows):
        out={}
        for key,value in rows:
            if key in out: raise ValueError()
            out[key]=value
        return out
    document=json.loads(raw,object_pairs_hook=pairs,parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    # Hash binds all existing settings, including source identity and permission.
    if document['observed_execution_authorized'] is not False or document['training_cutoff']!='origin_minus_1' or document['horizons']!=[1,3,6] or document['model_leads_from_last_training_month']!=[2,4,7]: raise ValueError()
    return document


def inspect_source_eligibility(receipts, monthly_metadata, cohort, as_of, protocol_identity):
    """Project source-month windows from metadata; return fixed denial codes.

    Receipt fields bind the original hash and canonical full metadata hash. This
    offline acceptance is the only recognized native-subset decision. Hashes prove
    binding, not origin/rights/authenticity; synthetic status remains explicit.
    """
    out={'schema_version':'offline-source-eligibility.v1','status':'UNAVAILABLE',
         'execution_authorized':False,'point_in_time_proven':False,'offline_only':True,
         'availability_basis':'month_plus_one_end_PROXY_not_publication_proof',
         'origin_instant_basis':'UTC_END_OF_ORIGIN_MONTH',
         'common_cutoff':None,'training_start':None,'validation':[],'purged':[],'test':[],
         'training_cutoffs':{},'targets':{},'model_leads':[2,4,7],
         'required_source_months':120,'runner_compatible':False,'reasons':[],
         'series':{},'protocol_sha256':PROTOCOL_SHA256}
    def deny(reason):
        if reason not in out['reasons']: out['reasons'].append(reason)
    try: instant=_utc(as_of)
    except (ValueError,TypeError,OverflowError): deny('ASOF_INVALID'); return out
    try: protocol=_protocol(protocol_identity)
    except (ValueError,TypeError,KeyError,UnicodeError): deny('PROTOCOL_BINDING'); return out
    if instant.date().isoformat()!=protocol['as_of']:
        deny('RUNNER_ASOF_INCOMPATIBLE'); return out
    if type(receipts) is not dict or type(monthly_metadata) is not dict or type(cohort) is not dict or set(cohort)!={'kind','series_ids','decision_sha256'}:
        deny('METADATA_INVALID'); return out
    names=cohort['series_ids']
    if type(names) is not list or not names or any(type(x) is not str or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',x) for x in names) or len(set(names))!=len(names):
        deny('COHORT_INVALID'); return out
    if cohort['decision_sha256']!=OFFLINE_ROOT_ACCEPTANCE_SHA256:
        deny('COHORT_DECISION_HOLD'); return out
    compatible_cohort=cohort['kind']=='native_subset' and names==NATIVE_COHORT
    if not compatible_cohort: deny('RUNNER_COHORT_INCOMPATIBLE')
    if set(receipts)-set(names) or set(monthly_metadata)-set(names):
        deny('COHORT_UNDECLARED_INPUT'); return out
    available={}
    availability_by_source={}
    metadata_keys={'source_id','original_sha256','is_synthetic','unit','coverage','months'}
    receipt_keys={'schema_version','source_id','synthetic','status','original_sha256','metadata_sha256','rights','vintage','retrieved_at'}
    for name in names:
        reasons=[]
        def bad(reason):
            if reason not in reasons: reasons.append(reason)
            deny(reason)
        if name not in receipts or name not in monthly_metadata:
            bad('SOURCE_MISSING'); out['series'][name]={'reasons':reasons}; continue
        record, metadata=receipts[name],monthly_metadata[name]
        if type(record) is not dict or type(metadata) is not dict or set(record)!=receipt_keys or set(metadata)!=metadata_keys:
            bad('METADATA_INVALID'); out['series'][name]={'reasons':reasons}; continue
        if record['source_id']!=name or metadata['source_id']!=name or record['schema_version']!='offline-source-receipt.v1' or record['status']!='complete': bad('RECEIPT_INVALID')
        if record['synthetic'] is not True or metadata['is_synthetic'] is not True: bad('OBSERVED_METADATA_HOLD')
        if record['rights']!='synthetic_only': bad('RIGHTS_HOLD')
        if record['vintage']!='synthetic_latest_vintage': bad('VINTAGE_HOLD')
        if metadata['coverage']!='complete': bad('COVERAGE_HOLD')
        if name not in UNITS or metadata['unit']!=UNITS[name] or name=='iron_ore': bad('UNIT_HOLD')
        try:
            retrieved=_utc(record['retrieved_at'])
            if retrieved>instant: bad('FUTURE_RECEIPT')
        except (ValueError,TypeError,OverflowError): bad('RECEIPT_TIME_INVALID')
        try:
            if not _hash(record['original_sha256']) or not _hash(record['metadata_sha256']) or not _hash(metadata['original_sha256']) or record['original_sha256']!=metadata['original_sha256'] or record['metadata_sha256']!=_digest(metadata): bad('HASH_BINDING')
        except (ValueError,TypeError,UnicodeError,OverflowError): bad('HASH_BINDING')
        seen,usable=set(),set()
        timestamps={}
        if type(metadata['months']) is not list or not metadata['months']:
            bad('MONTH_METADATA_INVALID')
        else:
            for row in metadata['months']:
                try:
                    if type(row) is not dict or set(row) not in ({'month','complete'},{'month','complete','available_at'}) or row['complete'] is not True: raise ValueError()
                    index=_index(row['month'])
                    if index in seen: raise ValueError()
                    seen.add(index)
                    earliest=_month_end(index+1)
                    availability=_utc(row['available_at']) if 'available_at' in row else earliest
                    if availability<earliest: raise ValueError()
                    timestamps[index]=availability
                    if availability<=instant: usable.add(index)
                except (ValueError,TypeError,OverflowError,KeyError): bad('MONTH_METADATA_INVALID')
        out['series'][name]={'reasons':reasons,'metadata_sha256':record['metadata_sha256'],
                             'original_sha256':record['original_sha256'],'unit':metadata['unit'],
                             'usable_month_count':len(usable),'future_or_unavailable_month_count':len(seen-usable)}
        if not reasons:
            available[name]=usable; availability_by_source[name]=timestamps
    # Any absent/invalid source member denies a full-cohort cutoff. Never default to WB max.
    if len(available)!=len(names): return out
    common=set.intersection(*(available[name] for name in names))
    if not common: deny('NO_COMMON_ELIGIBLE_MONTH'); return out
    last=max(common); out['common_cutoff']=_month(last)
    test_last=last-6; test_first=test_last-23; validation_last=test_first-7; validation_first=validation_last-23; training_start=validation_first-60
    try:
        required=set(range(training_start,last+1))
        validation=list(range(validation_first,validation_last+1))
        tests=list(range(test_first,test_last+1)); purge=list(range(test_first-6,test_first))
        for name in names:
            missing=sorted(required-available[name])
            if missing:
                deny('MISSING_SUPPORT_MONTH'); out['series'][name]['missing_support_months']=[_month(m) for m in missing]
        computed={'training_start':_month(training_start),'validation_start':_month(validation_first),'validation_end':_month(validation_last),'purge_start':_month(test_first-6),'purge_end':_month(test_first-1),'test_start':_month(test_first),'test_end':_month(test_last),'latest_eligible_actual':_month(last)}
        if any(protocol[k]!=v for k,v in computed.items()): deny('RUNNER_WINDOW_INCOMPATIBLE')
        if len(required)!=120 or len(validation)!=24 or len(purge)!=6 or len(tests)!=24: deny('PROTOCOL_COUNTS_INVALID')
        first_test_instant=_month_end(test_first)
        for name in names:
            stamps=availability_by_source[name]
            if any(stamps.get(origin+h,instant+dt.timedelta(days=1))>first_test_instant for origin in validation for h in (1,3,6)):
                deny('VALIDATION_AVAILABILITY_INVALID')
            if any(stamps.get(month,instant+dt.timedelta(days=1))>_month_end(origin) for origin in validation+tests for month in range(training_start,origin)):
                deny('TRAINING_AVAILABILITY_INVALID')
        if any(origin+h not in common for origin in validation+tests for h in (1,3,6)): deny('TARGET_MONTH_MISSING')
    except (ValueError,OverflowError,KeyError): deny('MONTH_METADATA_INVALID'); return out
    if out['reasons']: return out
    out.update({'status':'OFFLINE_METADATA_COMPATIBLE','runner_compatible':True,
                'training_start':_month(training_start),'required_support_months':[_month(x) for x in sorted(required)],
                'validation':[_month(x) for x in validation],'purged':[_month(x) for x in purge],'test':[_month(x) for x in tests],
                'training_cutoffs':{_month(x):_month(x-1) for x in validation+tests},
                'targets':{_month(x):[_month(x+h) for h in (1,3,6)] for x in validation+tests}})
    return out
