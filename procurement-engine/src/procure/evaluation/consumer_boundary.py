"""Synthetic consumer metadata, never fit, phase or publication authority.

Public reconstruction reads the FULL parent, including future synthetic values.
Identity checks cover bounded local interleavings, not hostile same-UID code,
executing-code attestation or liveness after return.
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat

from .panel_adapter import VerifiedPanelSeries, PanelAdapterError
from .panel_adapter import load_verified_panel_series, build_panel_readiness

FLAGS = {'synthetic': True, 'source_authenticity': False, 'observed_eligible': False,
         'execution_authorized': False, 'rights': 'synthetic_only'}
MAX_BODY = 4194304
PHASE_HOLDS = ['NO_HOST_PHASE_AUTHORITY', 'FIT_SCORER_SEPARATION_NOT_IMPLEMENTED',
               'ACTUAL_ACCESS_GUARANTEE_NOT_ACCEPTED', 'PHYSICAL_STAGED_SOURCE_READER_UNAVAILABLE']
EXTRA_REASONS = ['CONSUMER_READINESS_ONLY', 'PHASE_EXECUTION_HELD', 'CONVERSION_NOT_CHECKED']
EXPOSURE = {'parent_full_snapshot_exposed': True, 'fitter_values_released': False,
            'scorer_actuals_released': False, 'physical_test_actuals_untouched': False,
            'publication_authorized': False}
CONTROL_BINDINGS = [(1198, 'ac970b1dace90dec6882913f32c146f9fd592f1502125c0e63da460f5c874f66'),
                    (1264, '6230bc824373c4378dc4c4f3dde945c5ebb6bac9df73218e37c0f810ac3feea7')]
REGISTRY = [('commodity','copper','USD/metric_tonne','synthetic-commodity-component'),
            ('commodity','aluminum','USD/metric_tonne','synthetic-commodity-component'),
            ('fx','USDINR','INR/USD','synthetic-ecb-daily-cross'),
            ('joined','copper_INR_per_kg','INR/kg','synthetic-commodity-fx-join'),
            ('joined','aluminum_INR_per_kg','INR/kg','synthetic-commodity-fx-join')]


class ConsumerBoundaryError(ValueError):
    """Only fixed sanitized refusal codes; no private source/OS text."""


@dataclass(frozen=True, slots=True)
class ConsumerBoundaryReadiness:
    """Owned local bytes; constructing/copying this object is never authority."""
    request: bytes
    payload: bytes


def _fail(code='CONSUMER_BINDING_INVALID'):
    raise ConsumerBoundaryError(code) from None


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


# Exact accepted inspected-source identities, never imported execution authority.
TRUSTED_CODE = {'evaluation/panel_adapter.py': {'sha256': '40b003dacab1ef32e5d169ad538eff1245bc0116bf6413b456955484f9bd752c',
                                 'bytes': 38844},
 'forecast/models.py': {'sha256': '59f28f8be0858a2ac24c36c4a70e7cbc136918e654f05dc358601dd43a28f170',
                        'bytes': 6178},
 'evaluation/observed_runner.py': {'sha256': '7ab4babca441e0b5a955ae360d6f6cf1c4677d64dee56a2be65e1cb78cba7cfb',
                                   'bytes': 47492},
 'evaluation/source_transport.py': {'sha256': 'f41ed298ffc938d38749413168dfbed080b7a9d881ae54f642ec7275e3c8e060',
                                    'bytes': 28303},
 'evaluation/fx_normalization.py': {'sha256': '872c9ff5b0b614e136f27a87ae62cc24c07aa83a1953a7326aaf3968c79a71c6',
                                    'bytes': 32013},
 'evaluation/commodity_component.py': {'sha256': '8196147a7dfa626efbef04990763c52a85eaa214829c493947f7b436d4ce2763',
                                       'bytes': 26931},
 'evaluation/commodity_fx_join.py': {'sha256': '990be562463697e6a095e8aeac771e1daabf7a89a2e7c7e528eae4ca886eb3d2',
                                     'bytes': 47191},
 'evaluation/model_input.py': {'sha256': '45f5883f6aa2afa356a5c610bdb73c84104a9e95f45e1418ffcdc910a7b4c99e',
                               'bytes': 24180},
 'evaluation/forecast_scorecard.py': {'sha256': '897f3bd129d0ac0f6110176ee672e8260e594794e6a948db1e7abf76f8a4f15b',
                                      'bytes': 17841},
 'forecast/select.py': {'sha256': '2804c6c0d284958c539137c97d2a19233c3b36be9bcc23d1525916204e564c8c',
                        'bytes': 6266},
 'forecast/simulate.py': {'sha256': '353bd3232d55522076a9ba161d0b9c0ec461093bb528a205bc78e840c52b90ea',
                          'bytes': 6303}}
_SELF_RAW = Path(__file__).read_bytes()
CODE_SHA256, CODE_BYTES = _sha(_SELF_RAW), len(_SELF_RAW)
del _SELF_RAW

def _canonical(value):
    remaining = 60000
    def visit(v, depth=0):
        nonlocal remaining
        remaining -= 1
        if remaining < 0 or depth > 12: _fail()
        if type(v) is dict:
            if any(type(k) is not str or len(k)>128 for k in v): _fail()
            for item in v.values(): visit(item,depth+1)
        elif type(v) is list:
            for item in v: visit(item,depth+1)
        elif type(v) is str:
            if len(v)>8192: _fail()
        elif type(v) is int:
            if v.bit_length()>32: _fail()
        elif v is not None and type(v) is not bool: _fail()
    visit(value)
    raw=json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False).encode('ascii')
    if not 1<=len(raw)<=MAX_BODY: _fail()
    return raw


def _decode(raw):
    if type(raw) is not bytes or not 1<=len(raw)<=MAX_BODY: _fail()
    def pairs(items):
        out={}
        for k,v in items:
            if k in out: _fail()
            out[k]=v
        return out
    value=json.loads(raw,object_pairs_hook=pairs,parse_float=lambda _: _fail(),parse_constant=lambda _: _fail())
    if _canonical(value)!=raw: _fail()
    return value


def _keys(v, keys):
    if type(v) is not dict or set(v)!=set(keys): _fail()


def _flags(v):
    if type(v) is not dict or any(type(v.get(k)) is not type(x) or v.get(k)!=x for k,x in FLAGS.items()): _fail()


def _flag_tree(v):
    if type(v) is dict:
        present=set(v)&set(FLAGS)
        if any(type(v[k]) is not type(FLAGS[k]) or v[k]!=FLAGS[k] for k in present): _fail()
        if present and 'schema_version' in v:
            if v['schema_version']=='synthetic-date-mask.v2':
                if present!={'synthetic'}: _fail()
            else: _flags(v)
        for item in v.values(): _flag_tree(item)
    elif type(v) is list:
        for item in v: _flag_tree(item)


def _month(label):
    if type(label) is not str or not re.fullmatch(r'[0-9]{4}-(0[1-9]|1[0-2])',label): _fail()
    y,m=map(int,label.split('-'))
    if not 1<=y<=9998: _fail()
    return 12*y+m-1


def _name(n):
    return f'{n//12:04d}-{n%12+1:02d}'


def _months(first,last):
    return [_name(n) for n in range(_month(first),_month(last)+1)]


def _end(label):
    y,m=map(int,label.split('-'))
    return f'{label}-{calendar.monthrange(y,m)[1]:02d}T23:59:59.999999Z'


def _shape(snapshot, request, payload):
    _keys(request,{'component_bindings','join_policy','joined_publication'})
    _keys(request['component_bindings'],{'commodity','fx'})
    _flags(request['join_policy']); _flags(payload)
    _flag_tree(request); _flag_tree(payload)
    _keys(payload,{'schema_version',*FLAGS,'status','as_of','required_months','mask_sha256','request_sha256',
                   'channel_statuses','component_identities','joined_identity','code_identities','series','load_ledger'})
    p=request['join_policy']
    labels=p.get('required_months')
    if type(labels) is not list or not 1<=len(labels)<=240: _fail()
    n=[_month(m) for m in labels]
    if any(b!=a+1 for a,b in zip(n,n[1:])): _fail()
    if (payload['schema_version']!='synthetic-panel-series.v1' or
        payload['status'] not in ('SYNTHETIC_OFFLINE_ONLY','SYNTHETIC_OFFLINE_INCOMPLETE') or
        payload['as_of']!=p.get('as_of') or payload['required_months']!=labels or
        payload['mask_sha256']!=_sha(_canonical(labels)) or payload['request_sha256']!=_sha(snapshot.request)):
        _fail()
    _keys(payload['channel_statuses'],{'commodity','fx','joined'})
    _keys(payload['component_identities'],{'commodity','fx'})
    if type(payload['series']) is not list or len(payload['series'])!=5: _fail()
    for s,(kind,sid,unit,source) in zip(payload['series'],REGISTRY):
        _keys(s,{'series_kind','series_id','unit','source_id','is_synthetic','publication_status','rows'})
        if (s['series_kind'],s['series_id'],s['unit'],s['source_id'])!=(kind,sid,unit,source) or s['is_synthetic'] is not True: _fail()
        if s['publication_status']!=payload['channel_statuses'][kind] or type(s['rows']) is not list or len(s['rows'])!=len(labels): _fail()
        for r,m in zip(s['rows'],labels):
            _keys(r,{'month','status','reasons','basis','available_at_proxy'})
            if r['month']!=m or r['available_at_proxy']!=_end(_name(_month(m)+1)): _fail()


def _controls(protocol_raw, source_manifest_raw):
    for raw,(size,digest) in zip((protocol_raw,source_manifest_raw),CONTROL_BINDINGS):
        if type(raw) is not bytes or len(raw)!=size or _sha(raw)!=digest: _fail('CONSUMER_CONTROL_HOLD')


def _fresh(snapshot, request):
    fresh=load_verified_panel_series(**request)
    if type(fresh) is not VerifiedPanelSeries or fresh.request!=snapshot.request or fresh.payload!=snapshot.payload:
        _fail('CONSUMER_SNAPSHOT_MISMATCH')


def _windows():
    result=[]
    for split,first,last in [('validation','2021-09','2023-08'),('test','2024-03','2026-02')]:
        for origin in _months(first,last):
            n=_month(origin); training=_months('2016-09',_name(n-1))
            result.append({'split':split,'origin':origin,'training_mask':{'first':training[0],
                'last':training[-1],'count':len(training),'sha256_of_ordered_month_list':_sha(_canonical(training))},
                'targets':[{'horizon':h,'model_lead':h+1,'month':_name(n+h),
                    'available_at_proxy_utc':_end(_name(n+h+1))} for h in (1,3,6)],
                'execution_authorized':False,'conversion_readiness':'not_checked',
                'meaning':'planned exact role masks only; no data read or release',
                'evaluated_at_proxy_utc':_end(origin),'training_cutoff_available_at_proxy_utc':_end(origin),
                'read_purpose_labels':{'training':'fitter_training_proposed',
                    'targets':'scorer_actuals_after_required_prediction_and_validation_freeze_proposed'}})
    return result


def _project(readiness, code_identity):
    series=json.loads(_canonical(readiness['series_readiness']))
    for s in series:
        for split in ('validation','test'):
            for origin in s['origins'][split]:
                origin['conversion_readiness']='not_checked'; origin['phase_execution']='held'
    ledger=[{**r,'reasons':list(dict.fromkeys([*r['reasons'],*EXTRA_REASONS]))} for r in readiness['candidate_ledger']]
    return {'schema_version':'synthetic-consumer-boundary-readiness.v1',**FLAGS,'status':'SYNTHETIC_CONSUMER_READINESS_ONLY',
        'control_identity':{k:readiness[k] for k in ('protocol_identity','observed_source_control_identity')},
        'snapshot_identity':readiness['snapshot_identity'],
        'readiness_identity':{'sha256':_sha(_canonical(readiness)),'bytes':len(_canonical(readiness)),
            'all_models_not_evaluated':readiness['all_models_not_evaluated']},
        'consumer_code_identity':code_identity,'exposure':dict(EXPOSURE),
        'planned_windows':{'origins':_windows(),'purged_origin_labels':_months('2023-09','2024-02'),
            'expected_months':_months('2016-09','2026-08'),'structural_common_origins':readiness['structural_common_origins']},
        'series_readiness':series,'candidate_ledger':ledger,'phase_dependencies':list(PHASE_HOLDS),
        'selection':None,'calibration':None,'metrics':None}



def _identity(s, directory):
    base=(s.st_dev,s.st_ino,s.st_mode,s.st_uid)
    return base if directory else (*base,s.st_size,s.st_mtime_ns,s.st_ctime_ns)


class _Lifetime:
    """Own metadata/code anchors. Descriptor ownership precedes every fstat."""
    def __init__(self):
        self.fds=[]; self.records={}; self.absences=[]; self.closed=False

    def _open(self,path,directory,private,*,parent=None,name=None,code=False,maximum=MAX_BODY):
        if path in self.records:
            fd,identity,kind,label=self.records[path]
            if kind!=directory or label!=code: _fail('CONSUMER_INTEGRITY_UNRESOLVED')
            self.check()
            details=os.fstat(fd)
        else:
            flags=os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK
            if directory: flags|=os.O_DIRECTORY
            fd=os.open(path if parent is None else name,flags,dir_fd=parent)
            self.fds.append(fd)  # Own even when this first fstat fails.
            details=os.fstat(fd)
            self.records[path]=(fd,_identity(details,directory),directory,code)
        valid=stat.S_ISDIR(details.st_mode) if directory else stat.S_ISREG(details.st_mode) and 1<=details.st_size<=maximum
        if not valid or (private and (details.st_uid!=os.getuid() or stat.S_IMODE(details.st_mode)&0o077)):
            _fail('CONSUMER_CODE_CHANGED' if code else 'CONSUMER_SOURCE_CHANGED')
        return fd

    def root(self,path,private=True,code=False):
        if type(path) is not str or len(path)>4096 or not path.startswith('/') or any(x in ('','.','..') for x in path.split('/')[1:]): _fail()
        label=self.records['/'][3] if '/' in self.records else code
        fd=self._open('/',True,False,code=label)
        walked=''
        for part in path.split('/')[1:]:
            walked+='/'+part
            label=self.records[walked][3] if walked in self.records else code
            fd=self._open(walked,True,private and walked==path,parent=fd,name=part,code=label)
        return fd

    def path(self,fd):
        return next(p for p,r in self.records.items() if r[0]==fd)

    def directory(self,parent,name):
        return self._open(self.path(parent)+'/'+name,True,True,parent=parent,name=name)

    def file(self,parent,name,maximum=MAX_BODY,code=False):
        return self._open(self.path(parent)+'/'+name,False,not code,parent=parent,name=name,code=code,maximum=maximum)

    def absent(self,parent,name):
        path=self.path(parent)+'/'+name
        try: os.stat(name,dir_fd=parent,follow_symlinks=False)
        except FileNotFoundError:
            self.absences.append(path); return
        _fail('CONSUMER_SOURCE_CHANGED')  # Any entry, no opens or deletion.

    def hardlink(self,a,b):
        first,second=os.fstat(a),os.fstat(b)
        if (first.st_dev,first.st_ino)!=(second.st_dev,second.st_ino): _fail('CONSUMER_SOURCE_CHANGED')

    def check(self,descriptors=True):
        for path,(fd,identity,directory,code) in self.records.items():
            failure='CONSUMER_CODE_CHANGED' if code else 'CONSUMER_SOURCE_CHANGED'
            try:
                if descriptors and _identity(os.fstat(fd),directory)!=identity: _fail(failure)
                if _identity(os.stat(path,follow_symlinks=False),directory)!=identity: _fail(failure)
            except OSError: _fail(failure)
        for path in self.absences:
            try: os.stat(path,follow_symlinks=False)
            except FileNotFoundError: continue
            except OSError: _fail('CONSUMER_SOURCE_CHANGED')
            _fail('CONSUMER_SOURCE_CHANGED')

    def close(self):
        if self.closed: return
        self.closed=True; failure=None
        try: self.check()
        except BaseException as exc: failure=exc
        for fd in reversed(self.fds):
            try: os.close(fd)
            except BaseException: failure=ConsumerBoundaryError('CONSUMER_INTEGRITY_UNRESOLVED')
        # Never fstat a closed/reused descriptor; paths and absence facts remain.
        try: self.check(descriptors=False)
        except BaseException as exc:
            if failure is None: failure=exc
        if failure is not None: raise failure from None


def _pin_code(guard):
    procure=Path(__file__).parent.parent
    identities={**TRUSTED_CODE,'evaluation/consumer_boundary.py':{'sha256':CODE_SHA256,'bytes':CODE_BYTES}}
    for relative,expected in identities.items():
        path=procure/relative
        parent=guard.root(str(path.parent),private=False,code=True)
        fd=guard.file(parent,path.name,code=True)
        chunks=[]; remaining=MAX_BODY+1
        while remaining:
            chunk=os.read(fd,min(remaining,65536))  # CODE ONLY, no original/panel/receipt body.
            if not chunk: break
            chunks.append(chunk); remaining-=len(chunk)
        raw=b''.join(chunks)
        if len(raw)!=expected['bytes'] or len(raw)!=os.fstat(fd).st_size or _sha(raw)!=expected['sha256']:
            _fail('CONSUMER_CODE_CHANGED')
    guard.check()
    return identities


def _pin_panel_metadata(guard,b,complete,joined=False):
    root=guard.root(b['output_root'] if joined else b['panel_root'])
    run=b['run_id'] if joined else b['panel_run_id']
    if joined:
        failure=guard.directory(root,'disqualified'); guard.absent(failure,run+'.json')
    obj=guard.directory(guard.directory(root,'objects'),run)
    manifest=guard.file(obj,'manifest.json',65536)
    panel=guard.file(obj,'panel.json' if joined else 'panel.csv')
    if os.fstat(panel).st_size!=b['panel_bytes']: _fail('CONSUMER_SOURCE_CHANGED')
    if joined and os.fstat(manifest).st_size!=b['manifest_bytes']: _fail('CONSUMER_SOURCE_CHANGED')
    positive=guard.directory(root,'complete'); negative=guard.directory(root,'incomplete')
    guard.absent(negative if complete else positive,run+'.json')
    marker=guard.file(positive if complete else negative,run+'.json',65536)
    guard.hardlink(manifest,marker)


def _pin_admitted_metadata(guard,request,payload):
    # PUBLIC closure/equality must succeed first. Refused/unsupplied originals
    # stay unopened; this wrapper only opens metadata descriptors, never bodies.
    for kind in ('commodity','fx'):
        channel=payload['channel_statuses'][kind]
        if channel not in ('complete_synthetic','natural_incomplete_diagnostic'): continue
        if payload['component_identities'][kind] is None: _fail('CONSUMER_INTEGRITY_UNRESOLVED')
        b=request['component_bindings'][kind]
        root=guard.root(b['original_root'])
        run=b['fixture_run_id'] if kind=='commodity' else b['source_run_id']
        obj=guard.directory(guard.directory(root,'objects'),run)
        committed=guard.directory(root,'committed'); receipt=guard.file(committed,run+'.json',65536)
        if kind=='commodity':
            original=guard.file(obj,'original.csv')
            if os.fstat(original).st_size!=b['original_bytes']: _fail('CONSUMER_SOURCE_CHANGED')
        else:
            guard.absent(guard.directory(root,'incomplete'),run+'.json')
            manifest=guard.file(obj,'manifest.json',65536); guard.hardlink(manifest,receipt)
            for sid,expected in b['source_originals'].items():
                original=guard.file(obj,sid+'.raw')
                if os.fstat(original).st_size!=expected['bytes']: _fail('CONSUMER_SOURCE_CHANGED')
        _pin_panel_metadata(guard,b,channel=='complete_synthetic')
    channel=payload['channel_statuses']['joined']
    if channel in ('complete_synthetic','natural_incomplete_diagnostic'):
        if payload['joined_identity'] is None: _fail('CONSUMER_INTEGRITY_UNRESOLVED')
        _pin_panel_metadata(guard,request['joined_publication'],channel=='complete_synthetic',joined=True)
    guard.check()


def build_consumer_boundary_readiness(snapshot: VerifiedPanelSeries, protocol_raw: bytes,
                                     source_manifest_raw: bytes) -> ConsumerBoundaryReadiness:
    guard=_Lifetime()
    try:
        if type(snapshot) is not VerifiedPanelSeries: _fail()
        _controls(protocol_raw,source_manifest_raw)
        try:
            request,payload=_decode(snapshot.request),_decode(snapshot.payload)
            _shape(snapshot,request,payload)
        except ConsumerBoundaryError: raise
        except (ValueError,TypeError,KeyError,UnicodeError,OverflowError,RecursionError): _fail()
        code_identity=_pin_code(guard)
        _fresh(snapshot,request)
        _pin_admitted_metadata(guard,request,payload)
        readiness=build_panel_readiness(snapshot,protocol_raw,source_manifest_raw)
        guard.check()
        result=ConsumerBoundaryReadiness(snapshot.request,_canonical(_project(readiness,code_identity)))
        guard.check()
        _fresh(snapshot,request)
        guard.check()
    except ConsumerBoundaryError: raise
    except (PanelAdapterError,OSError,ValueError,TypeError,KeyError,OverflowError,RecursionError):
        _fail('CONSUMER_INTEGRITY_UNRESOLVED')
    finally:
        guard.close()  # No result until successful cleanup AND postclose paths.
    return result
