"""Read-only synthetic panel projection and readiness. Never model authority.

Only public component APIs establish artifact closure. Owned read anchors detect
bounded interleavings through close, not hostile same-UID code or later liveness.
Exact fractions are the sole price representation; no lossy model boundary here.
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from fractions import Fraction
import hashlib
import json
import os
from pathlib import Path
import re
import stat

from . import commodity_component as commodity
from . import fx_normalization as fx
from . import commodity_fx_join as join

FLAGS = {'synthetic': True, 'source_authenticity': False, 'observed_eligible': False,
         'execution_authorized': False, 'rights': 'synthetic_only'}
MAX_BODY, MAX_META, MAX_BITS = 4194304, 65536, 4096
COMPLETE, INCOMPLETE = 'SYNTHETIC_OFFLINE_ONLY', 'SYNTHETIC_OFFLINE_INCOMPLETE'
CODE_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
TRUSTED = {'commodity': '8196147a7dfa626efbef04990763c52a85eaa214829c493947f7b436d4ce2763',
           'fx': '872c9ff5b0b614e136f27a87ae62cc24c07aa83a1953a7326aaf3968c79a71c6',
           'transport': 'f41ed298ffc938d38749413168dfbed080b7a9d881ae54f642ec7275e3c8e060',
           'join': '990be562463697e6a095e8aeac771e1daabf7a89a2e7c7e528eae4ca886eb3d2'}
REGISTRY = (('commodity','copper','USD/metric_tonne','synthetic-commodity-component'),
            ('commodity','aluminum','USD/metric_tonne','synthetic-commodity-component'),
            ('fx','USDINR','INR/USD','synthetic-ecb-daily-cross'),
            ('joined','copper_INR_per_kg','INR/kg','synthetic-commodity-fx-join'),
            ('joined','aluminum_INR_per_kg','INR/kg','synthetic-commodity-fx-join'))
CHANNELS = {'complete_synthetic','natural_incomplete_diagnostic','not_supplied',
            'integrity_refused','incomplete_inspection_unavailable','dependency_unavailable'}
HOLDS = ['READINESS_ONLY_NO_MODEL_RUN','SYNTHETIC_NOT_OBSERVED','OBSERVED_EXECUTION_HOLD']
PAYLOAD_KEYS = {'schema_version',*FLAGS,'status','as_of','required_months','mask_sha256',
                'request_sha256','channel_statuses','component_identities','joined_identity',
                'code_identities','series','load_ledger'}


class PanelAdapterError(ValueError):
    """Sanitized fixed codes only; no paths, source tokens or OS messages."""


def _fail(code='ADAPTER_BINDING_INVALID'):
    raise PanelAdapterError(code)


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _canonical(value, maximum=MAX_BODY):
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
    try: raw=json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False).encode('ascii')
    except (ValueError,TypeError,RecursionError): _fail()
    if len(raw)>maximum: _fail()
    return raw


def _decode(raw, maximum=MAX_BODY):
    if type(raw) is not bytes or not 1<=len(raw)<=maximum: _fail()
    def pairs(items):
        out={}
        for k,v in items:
            if k in out: _fail()
            out[k]=v
        return out
    try:
        value=json.loads(raw,object_pairs_hook=pairs,parse_float=lambda _: _fail(),
                         parse_constant=lambda _: _fail())
        if _canonical(value,maximum)!=raw: _fail()
        return value
    except (ValueError,TypeError,UnicodeError,RecursionError): _fail()


def _keys(value, keys):
    if type(value) is not dict or set(value)!=set(keys): _fail()


def _flags(value):
    if type(value) is not dict or any(type(value.get(k)) is not type(v) or value.get(k)!=v for k,v in FLAGS.items()):
        _fail('ADAPTER_OBSERVED_HOLD')


def _hash(value):
    if type(value) is not str or not re.fullmatch('[0-9a-f]{64}',value): _fail()


def _size(value, maximum=MAX_BODY):
    if type(value) is not int or not 1<=value<=maximum: _fail()


def _run(value):
    if type(value) is not str or not re.fullmatch('[A-Za-z0-9][A-Za-z0-9_-]{0,63}',value): _fail()


def _path(value):
    if (type(value) is not str or len(value)>4096 or not value.startswith('/') or
            any(p in ('','.','..') for p in value.split('/')[1:])): _fail()
    return value


def _instant(value):
    if type(value) is not str or not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z',value): _fail()
    try: out=datetime.fromisoformat(value.replace('Z','+00:00'))
    except ValueError: _fail()
    if out.isoformat(timespec='microseconds').replace('+00:00','Z')!=value: _fail()
    return out


def _month(value):
    if type(value) is not str or not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])',value): _fail()
    y,m=map(int,value.split('-'))
    if not 1<=y<=9998: _fail()
    return y*12+m-1


def _add(month, count):
    y,m=divmod(_month(month)+count,12)
    return f'{y:04d}-{m+1:02d}'


def _months(first,last):
    return [_add(first,i) for i in range(_month(last)-_month(first)+1)]


def _end(month):
    y,m=map(int,month.split('-'))
    return f'{y:04d}-{m:02d}-{calendar.monthrange(y,m)[1]:02d}T23:59:59.999999Z'


def _proxy(month):
    return _end(_add(month,1))


def _bounded(value):
    if value<=0 or value.numerator.bit_length()>MAX_BITS or value.denominator.bit_length()>MAX_BITS:
        _fail('ADAPTER_ARITHMETIC_LIMIT')
    return value


def _basis(value):
    value=_bounded(value)
    return {'numerator':str(value.numerator),'denominator':str(value.denominator)}


def _fraction(value):
    _keys(value,{'numerator','denominator'})
    for item in value.values():
        if type(item) is not str or len(item)>1234 or not re.fullmatch('[1-9][0-9]*',item): _fail('ADAPTER_ARITHMETIC_LIMIT')
    result=_bounded(Fraction(int(value['numerator']),int(value['denominator'])))
    if _basis(result)!=value: _fail('ADAPTER_ARITHMETIC_LIMIT')
    return result


def _validate_request(request):
    _keys(request,{'component_bindings','join_policy','joined_publication'})
    b,p,j=request['component_bindings'],request['join_policy'],request['joined_publication']
    _keys(b,{'commodity','fx'}); _keys(p,join.POLICY_KEYS); _flags(p)
    if (p['schema_version']!='synthetic-commodity-fx-join.v1' or p['series_ids']!=['copper','aluminum'] or
        p['arithmetic_version']!='component_fraction_product_v1' or p['rounding_version']!='decimal50_half_even_normalized_plain_v1'):
        _fail()
    if _instant(p['as_of']).date().isoformat()!='2026-10-08': _fail('ADAPTER_AS_OF_MISMATCH')
    months=p['required_months']
    if type(months) is not list or not 1<=len(months)<=240: _fail()
    ordinals=[_month(m) for m in months]
    if any(y!=x+1 for x,y in zip(ordinals,ordinals[1:])): _fail()
    for kind,module in [('commodity',commodity),('fx',fx)]:
        if p[kind+'_schema_sha256']!=_sha(_canonical(module.SCHEMA)): _fail()
    _hash(p['fx_coverage_sha256']); roots=[]
    for kind,keys in [('commodity',join.COMMODITY_BINDING),('fx',join.FX_BINDING)]:
        row=b[kind]
        if row is None: continue
        _keys(row,keys)
        for k,v in row.items():
            if k.endswith('sha256'): _hash(v)
            elif k.endswith('bytes'): _size(v)
            elif k.endswith('run_id'): _run(v)
            elif k.endswith('root'): roots.append(_path(v))
        if row['code_sha256']!=TRUSTED[kind] or row['schema_sha256']!=p[kind+'_schema_sha256']: _fail()
        if kind=='fx':
            _keys(row['provenance'],{*FLAGS,'retrieved_at'}); _flags(row['provenance']); _instant(row['provenance']['retrieved_at'])
            _keys(row['source_originals'],{'ecb_usd_eur','ecb_inr_eur'})
            for identity in row['source_originals'].values():
                _keys(identity,{'bytes','sha256'}); _size(identity['bytes']); _hash(identity['sha256'])
            c=row['coverage']; _keys(c,{'schema_version','synthetic','as_of','required_months','dates_by_month'})
            if (c['schema_version']!='synthetic-date-mask.v2' or c['synthetic'] is not True or
                c['as_of']!=p['as_of'] or c['required_months']!=months or
                row['coverage_sha256']!=p['fx_coverage_sha256'] or row['coverage_sha256']!=_sha(_canonical(c,MAX_META))): _fail()
            _keys(c['dates_by_month'],months)
            for m,days in c['dates_by_month'].items():
                if type(days) is not list or not 1<=len(days)<=31 or days!=sorted(set(days)): _fail()
                for day in days:
                    if type(day) is not str or day[:7]!=m: _fail()
                    try:
                        if datetime.strptime(day,'%Y-%m-%d').strftime('%Y-%m-%d')!=day: _fail()
                    except ValueError: _fail()
    if j is not None:
        _keys(j,{'output_root','run_id','manifest_sha256','manifest_bytes','panel_sha256','panel_bytes'})
        roots.append(_path(j['output_root'])); _run(j['run_id'])
        _hash(j['manifest_sha256']); _hash(j['panel_sha256'])
        _size(j['manifest_bytes'],MAX_META); _size(j['panel_bytes'])
    for i,x in enumerate(roots):
        for y in roots[i+1:]:
            if x==y or x.startswith(y+'/') or y.startswith(x+'/'): _fail()
    _canonical(request)


def _identity(s, directory):
    base=(s.st_dev,s.st_ino,s.st_mode,s.st_uid)
    return base if directory else (*base,s.st_size,s.st_mtime_ns,s.st_ctime_ns)


class _ReadLifetime:
    """Adapter-owned read anchors, including path checks after successful close."""
    def __init__(self):
        self.records=[]; self.absences=[]; self.present=[]; self.closed=False

    def _remember(self, fd, path, directory, private):
        s=os.fstat(fd); self.records.append((fd,path,_identity(s,directory),directory))
        if private and (s.st_uid!=os.getuid() or stat.S_IMODE(s.st_mode)&0o077): _fail()
        if directory and not stat.S_ISDIR(s.st_mode): _fail()
        return fd

    def root(self,path,private=True):
        path=_path(path); fd=self._remember(os.open('/',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW),'/',True,False)
        walked=''
        for part in path.split('/')[1:]:
            walked+='/'+part
            fd=self._remember(os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd),walked,True,False)
        s=os.fstat(fd)
        if private and (s.st_uid!=os.getuid() or stat.S_IMODE(s.st_mode)&0o077): _fail()
        return fd

    def directory(self,parent,name):
        path=self.path(parent)+'/'+name
        return self._remember(os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=parent),path,True,True)

    def path(self,fd):
        return next(r[1] for r in self.records if r[0]==fd)

    def absent(self,parent,name):
        path=self.path(parent)+'/'+name
        try:
            s=os.stat(name,dir_fd=parent,follow_symlinks=False)
        except FileNotFoundError:
            self.absences.append(path); return
        self.present.append((path,_identity(s,False)))
        _fail('ADAPTER_PUBLICATION_REFUSED')

    def read(self,parent,name,maximum=MAX_BODY,private=True):
        fd=self._remember(os.open(name,os.O_RDONLY|os.O_NONBLOCK|os.O_NOFOLLOW,dir_fd=parent),self.path(parent)+'/'+name,False,private)
        s=os.fstat(fd)
        if not stat.S_ISREG(s.st_mode) or not 1<=s.st_size<=maximum: _fail('ADAPTER_PUBLICATION_REFUSED')
        chunks=[]; remaining=maximum+1
        while remaining:
            chunk=os.read(fd,min(remaining,65536))
            if not chunk: break
            chunks.append(chunk); remaining-=len(chunk)
        raw=b''.join(chunks)
        if len(raw)!=s.st_size or len(raw)>maximum: _fail('ADAPTER_SOURCE_CHANGED')
        return raw,s

    def check(self, descriptors=True):
        try:
            for fd,path,identity,directory in self.records:
                if descriptors and _identity(os.fstat(fd),directory)!=identity: _fail('ADAPTER_SOURCE_CHANGED')
                if _identity(os.stat(path,follow_symlinks=False),directory)!=identity: _fail('ADAPTER_SOURCE_CHANGED')
            for path in self.absences:
                try: os.stat(path,follow_symlinks=False)
                except FileNotFoundError: continue
                _fail('ADAPTER_SOURCE_CHANGED')
            for path,identity in self.present:
                if _identity(os.stat(path,follow_symlinks=False),False)!=identity: _fail('ADAPTER_SOURCE_CHANGED')
        except OSError: _fail('ADAPTER_SOURCE_CHANGED')

    def close(self):
        if self.closed: return
        self.closed=True; error=None
        try: self.check()
        except BaseException as exc: error=exc
        for fd,_,_,_ in reversed(self.records):
            try: os.close(fd)
            except BaseException:
                if error is None: error=PanelAdapterError('ADAPTER_INTEGRITY_UNRESOLVED')
        try: self.check(descriptors=False)
        except BaseException as exc:
            if error is None: error=exc
        if error is not None: raise error


def _pin_code(guard):
    root=guard.root(str(Path(__file__).parent),private=False)
    identities={**TRUSTED,'adapter':CODE_SHA256}
    for kind,name in [('commodity','commodity_component.py'),('fx','fx_normalization.py'),
                      ('transport','source_transport.py'),('join','commodity_fx_join.py'),('adapter','panel_adapter.py')]:
        raw,_=guard.read(root,name,private=False)
        if _sha(raw)!=identities[kind]: _fail('ADAPTER_CODE_CHANGED')
    return identities


def _admit_source_receipt(raw,kind,b,run):
    """Admission only: anchored metadata must pass before any body-reading API.

    Caller-bound digests are identity checks, never substitute permission gates.
    Public loaders/verifiers still establish full source/artifact closure later.
    """
    receipt=_decode(raw,MAX_META)
    if kind=='commodity':
        _keys(receipt,{'schema_version',*FLAGS,'run_id','first_month','last_month',
                       'month_count','original_bytes','original_sha256'})
        _flags(receipt)
        if receipt['schema_version']!='synthetic-commodity-fixture-receipt.v1' or receipt['run_id']!=run: _fail()
        count=receipt['month_count']
        if type(count) is not int or not 1<=count<=240: _fail()
        if _month(receipt['last_month'])-_month(receipt['first_month'])+1!=count: _fail()
        _size(receipt['original_bytes']); _hash(receipt['original_sha256'])
        if receipt['original_bytes']!=b['original_bytes'] or receipt['original_sha256']!=b['original_sha256']: _fail()
    else:
        _keys(receipt,{'schema_version','run_id','status','offline_only','execution_authorized',
                       'originals','settled','capture_verified'})
        if (receipt['schema_version']!='offline-source-publication.v1' or receipt['run_id']!=run or
            receipt['status']!='OFFLINE_COMPLETE' or receipt['offline_only'] is not True or
            receipt['execution_authorized'] is not False or receipt['settled'] is not True or
            receipt['capture_verified'] is not True): _fail('ADAPTER_OBSERVED_HOLD')
        rows=receipt['originals']
        if type(rows) is not list or len(rows)!=2: _fail()
        for row,sid in zip(rows,sorted(b['source_originals'])):
            _keys(row,{'source_id','path','bytes','sha256'})
            _size(row['bytes']); _hash(row['sha256'])
            if (row['source_id']!=sid or row['path']!=f'objects/{run}/{sid}.raw' or
                row['bytes']!=b['source_originals'][sid]['bytes'] or row['sha256']!=b['source_originals'][sid]['sha256']): _fail()


def _pin_sources(guard,kind,b):
    root=guard.root(b['original_root']); objects=guard.directory(root,'objects')
    run=b['fixture_run_id'] if kind=='commodity' else b['source_run_id']
    obj=guard.directory(objects,run); committed=guard.directory(root,'committed')
    receipt,s=guard.read(committed,run+'.json',MAX_META)
    if _sha(receipt)!=b['fixture_receipt_sha256' if kind=='commodity' else 'source_receipt_sha256']: _fail()
    _admit_source_receipt(receipt,kind,b,run)
    guard.check()
    if kind=='commodity':
        raw,_=guard.read(obj,'original.csv')
        if len(raw)!=b['original_bytes'] or _sha(raw)!=b['original_sha256']: _fail()
    else:
        negative=guard.directory(root,'incomplete'); guard.absent(negative,run+'.json')
        manifest,ms=guard.read(obj,'manifest.json',MAX_META)
        if manifest!=receipt or (ms.st_dev,ms.st_ino)!=(s.st_dev,s.st_ino): _fail()
        for sid,expected in b['source_originals'].items():
            guard.check()
            raw,_=guard.read(obj,sid+'.raw')
            if len(raw)!=expected['bytes'] or _sha(raw)!=expected['sha256']: _fail()


def _pin_panel(guard,b,joined=False):
    root=guard.root(b['output_root'] if joined else b['panel_root'])
    run=b['run_id'] if joined else b['panel_run_id']
    if joined:
        failure=guard.directory(root,'disqualified'); guard.absent(failure,run+'.json')
    objects=guard.directory(root,'objects'); obj=guard.directory(objects,run)
    raw,ms=guard.read(obj,'manifest.json',MAX_META); manifest=_decode(raw,MAX_META); _flags(manifest)
    if (len(raw)!=(b['manifest_bytes'] if joined else len(raw)) or
            _sha(raw)!=b['manifest_sha256' if joined else 'panel_manifest_sha256']): _fail()
    panel,_=guard.read(obj,'panel.json' if joined else 'panel.csv')
    if len(panel)!=b['panel_bytes'] or _sha(panel)!=b['panel_sha256']: _fail()
    status=manifest.get('status')
    if status not in (COMPLETE,INCOMPLETE): _fail()
    positive=guard.directory(root,'complete'); negative=guard.directory(root,'incomplete')
    guard.absent(negative if status==COMPLETE else positive,run+'.json')
    marker,s=guard.read(positive if status==COMPLETE else negative,run+'.json',MAX_META)
    if marker!=raw or (s.st_dev,s.st_ino)!=(ms.st_dev,ms.st_ino): _fail()
    return raw,panel,manifest


def _fx_basis(row,coverage):
    if row['status']!='complete': return None
    tokens=row['daily_tokens']; expected=coverage['dates_by_month'][row['month']]
    if (row['unit']!='INR/USD' or row['expected_days']!=len(expected) or
        row['matched_days']!=len(expected) or [r[0] for r in tokens]!=expected or
        _sha(_canonical(tokens))!=row['daily_basis_sha256']): _fail()
    total=Fraction(0)
    for _,usd,inr in tokens:
        ratio=_bounded(Fraction(Decimal(inr))/Fraction(Decimal(usd)))
        total=_bounded(total+ratio)
    return _basis(_bounded(total/len(expected)))


def _component(guard,kind,b,p):
    _pin_sources(guard,kind,b)
    raw,_,manifest=_pin_panel(guard,b)
    if manifest['code_sha256']!=b['code_sha256'] or manifest['schema_sha256']!=b['schema_sha256']: _fail()
    guard.check()  # Admitted receipt identity must still hold before public body reads.
    if kind=='commodity':
        verified=commodity.load_synthetic_component(b['original_root'],b['fixture_run_id'],FLAGS)
        if (verified['receipt_sha256']!=b['fixture_receipt_sha256'] or verified['original_sha256']!=b['original_sha256'] or
                verified['original_bytes']!=b['original_bytes']): _fail()
        guard.check()
        candidate=commodity.normalize_commodity_component(verified,commodity.SCHEMA,p['required_months'],p['as_of'])
        if manifest['as_of']!=p['as_of'] or manifest['mask_sha256']!=_sha(_canonical(p['required_months'])): _fail()
        if manifest['status']==COMPLETE:
            guard.check()
            checked=commodity.verify_commodity_component(b['panel_root'],b['panel_run_id'],verified,commodity.SCHEMA,p['required_months'],p['as_of'])
        else: checked=None
    else:
        verified=fx.load_verified_originals(b['original_root'],b['source_run_id'],b['provenance'])
        source_identity={sid:{k:r[k] for k in ('sha256','bytes')} for sid,r in verified['originals'].items()}
        if verified['receipt_sha256']!=b['source_receipt_sha256'] or source_identity!=b['source_originals']: _fail()
        guard.check()
        candidate=fx.normalize_ecb_pair(verified,fx.SCHEMA,b['coverage'])
        if manifest['coverage_sha256']!=b['coverage_sha256']: _fail()
        if manifest['status']==COMPLETE:
            guard.check()
            checked=fx.verify_fx_panel(b['panel_root'],b['panel_run_id'],verified,fx.SCHEMA,b['coverage'])
        else: checked=None
    complete=all(r['status']=='complete' for r in candidate['months'])
    if complete!=(manifest['status']==COMPLETE): _fail()
    if checked is not None and _canonical(checked,MAX_META)!=raw: _fail()
    # Incomplete standalone publications have no public inspector; no certification.
    return {'candidate':candidate,'manifest':manifest,'manifest_bytes':len(raw),
            'status':'complete_synthetic' if complete else 'incomplete_inspection_unavailable'}


def _evaluate(request,guard):
    b,p,j=request['component_bindings'],request['join_policy'],request['joined_publication']
    channels={}; components={}; identities={'commodity':None,'fx':None}; ledger=[]
    for kind in ('commodity','fx'):
        if b[kind] is None:
            channels[kind]='not_supplied'; ledger.append({'channel':kind,'reasons':['COMPONENT_NOT_SUPPLIED']}); continue
        try:
            part=_component(guard,kind,b[kind],p); components[kind]=part; channels[kind]=part['status']
            if part['status']=='complete_synthetic':
                identities[kind]={'manifest':part['manifest'],'panel_manifest_sha256':b[kind]['panel_manifest_sha256'],
                                  'panel_manifest_bytes':part['manifest_bytes']}
            reasons=[] if part['status']=='complete_synthetic' else ['NATURAL_INCOMPLETE_PUBLIC_INSPECTOR_UNAVAILABLE']
        except (PanelAdapterError,OSError,ValueError,TypeError,KeyError,OverflowError):
            channels[kind]='integrity_refused'; reasons=['COMPONENT_INTEGRITY_REFUSED']
        ledger.append({'channel':kind,'reasons':reasons})
    composed=None
    if len(components)==2:
        try:
            guard.check()
            inputs=join.load_join_inputs(b,p); composed=join.compose_joined_panel(inputs,p)
            public=_decode(inputs.payload)
            for kind,key in [('commodity','commodity_USD'),('fx','FX_INR_USD')]:
                expected=COMPLETE if channels[kind]=='complete_synthetic' else INCOMPLETE
                if composed['component_statuses'][key]!=expected: _fail()
                if expected==INCOMPLETE:
                    channels[kind]='natural_incomplete_diagnostic'
                    identities[kind]=public['component_identities'][kind]
                    next(r for r in ledger if r['channel']==kind)['reasons']=['NATURAL_INCOMPLETE_DIAGNOSTIC_ONLY']
            for row in composed['rows']:
                native=next(r for r in components['commodity']['candidate']['months'] if r['month']==row['month'] and r['series_id']==row['series_id'])
                if row['commodity']['native_basis']!=native['native_basis']: _fail()
                rate=next(r for r in components['fx']['candidate']['months'] if r['month']==row['month'])
                if row['fx']['basis']!=_fx_basis(rate,b['fx']['coverage']): _fail()
        except (PanelAdapterError,OSError,ValueError,TypeError,KeyError,OverflowError):
            # A reproduced public integrity refusal is not an inspector gap.
            # Independently complete lanes retain their separate certification.
            composed=None
            for kind,part in components.items():
                if part['status']!='complete_synthetic':
                    channels[kind]='integrity_refused'; identities[kind]=None
                    next(r for r in ledger if r['channel']==kind)['reasons']=['COMPONENT_PUBLIC_CLOSURE_INTEGRITY_REFUSED']
    joined_identity=None; joined_panel=None; joined_refused=False; pinned_join=None
    if j is not None:
        try: pinned_join=_pin_panel(guard,j,joined=True)
        except (PanelAdapterError,OSError,ValueError,TypeError,KeyError,OverflowError): joined_refused=True
    if j is None:
        channels['joined']='not_supplied'; ledger.append({'channel':'joined','reasons':['JOIN_PANEL_NOT_SUPPLIED']})
    elif joined_refused:
        channels['joined']='integrity_refused'; ledger.append({'channel':'joined','reasons':['JOIN_PUBLICATION_INTEGRITY_REFUSED']})
    elif composed is None:
        # Supplied bytes/markers do not prove a publication without PUBLIC closure.
        channels['joined']='integrity_refused'; ledger.append({'channel':'joined','reasons':['JOIN_PUBLIC_CLOSURE_UNVERIFIED']})
    else:
        try:
            raw,panel,manifest=pinned_join
            guard.check()
            checked=join.inspect_joined_panel(j['output_root'],j['run_id'],b,p)
            if manifest['status']==COMPLETE:
                guard.check()
                if join.verify_joined_panel(j['output_root'],j['run_id'],b,p)!=checked: _fail()
            if _canonical(checked,MAX_META)!=raw or panel!=_canonical(composed): _fail()
            channels['joined']='complete_synthetic' if manifest['status']==COMPLETE else 'natural_incomplete_diagnostic'
            joined_identity={'manifest':manifest,'manifest_sha256':j['manifest_sha256'],'manifest_bytes':j['manifest_bytes']}
            joined_panel=composed
            ledger.append({'channel':'joined','reasons':[] if channels['joined']=='complete_synthetic' else ['NATURAL_INCOMPLETE_DIAGNOSTIC_ONLY']})
        except (PanelAdapterError,OSError,ValueError,TypeError,KeyError,OverflowError):
            channels['joined']='integrity_refused'; ledger.append({'channel':'joined','reasons':['JOIN_PUBLICATION_INTEGRITY_REFUSED']})
    series=[]
    for kind,sid,unit,source in REGISTRY:
        rows=[]; channel=channels[kind]
        usable=channel in ('complete_synthetic','natural_incomplete_diagnostic')
        for m in p['required_months']:
            basis=None; reasons=[]; status='unavailable'
            if kind=='commodity' and kind in components:
                r=next(r for r in components[kind]['candidate']['months'] if r['month']==m and r['series_id']==sid)
                if r['reason'] is not None: reasons.append(r['reason'])
                if usable and r['status']=='complete':
                    native=_fraction(r['native_basis'])
                    if native/1000!=_fraction(r['kg_basis']): _fail()
                    basis=_basis(native); status='complete'
            elif kind=='fx' and kind in components:
                r=next(r for r in components[kind]['candidate']['months'] if r['month']==m)
                reasons.extend(r['reasons'])
                if usable and r['status']=='complete': basis=_fx_basis(r,b['fx']['coverage']); status='complete'
            elif kind=='joined' and joined_panel is not None:
                material=sid.removesuffix('_INR_per_kg')
                r=next(r for r in joined_panel['rows'] if r['month']==m and r['series_id']==material)
                reasons.extend(r['joined']['reasons'])
                if r['joined']['status']=='complete':
                    expected=_bounded(_fraction(r['commodity']['native_basis'])/1000*_fraction(r['fx']['basis']))
                    if _basis(expected)!=r['joined']['basis']: _fail()
                    basis=_basis(expected); status='complete'
            if not usable: reasons.extend(next(r for r in ledger if r['channel']==kind)['reasons'])
            rows.append({'month':m,'status':status,'reasons':list(dict.fromkeys(reasons)),
                         'basis':basis,'available_at_proxy':_proxy(m)})
        series.append({'series_kind':kind,'series_id':sid,'unit':unit,'source_id':source,
                       'is_synthetic':True,'publication_status':channel,'rows':rows})
    return {'schema_version':'synthetic-panel-series.v1',**FLAGS,
            'status':COMPLETE if all(c=='complete_synthetic' for c in channels.values()) else INCOMPLETE,
            'as_of':p['as_of'],'required_months':list(p['required_months']),
            'mask_sha256':_sha(_canonical(p['required_months'])),'request_sha256':_sha(_canonical(request)),
            'channel_statuses':channels,'component_identities':identities,'joined_identity':joined_identity,
            'code_identities':{**TRUSTED,'adapter':CODE_SHA256},'series':series,
            'load_ledger':ledger+[{'channel':'iron_ore','reasons':['UNIT_HOLD_DMTU']}]}


@dataclass(frozen=True)
class VerifiedPanelSeries:
    """Owned canonical bytes, historical validation only, never a permission."""
    request: bytes
    payload: bytes


def load_verified_panel_series(component_bindings: dict, join_policy: dict,
                               joined_publication: dict | None = None) -> VerifiedPanelSeries:
    request={'component_bindings':component_bindings,'join_policy':join_policy,'joined_publication':joined_publication}
    raw=_canonical(request); request=_decode(raw); _validate_request(request)
    guard=_ReadLifetime()
    try:
        _pin_code(guard)
        payload=_canonical(_evaluate(request,guard)); guard.check()
        if _canonical(_evaluate(request,guard))!=payload: _fail('ADAPTER_SOURCE_CHANGED')
        guard.check()
        result=VerifiedPanelSeries(raw,payload)
    except PanelAdapterError: raise
    except (OSError,ValueError,TypeError,KeyError,OverflowError): _fail('ADAPTER_INTEGRITY_UNRESOLVED')
    finally: guard.close()
    return result


PROTOCOL_SHA256 = 'ac970b1dace90dec6882913f32c146f9fd592f1502125c0e63da460f5c874f66'
SOURCE_CONTROL_SHA256 = '6230bc824373c4378dc4c4f3dde945c5ebb6bac9df73218e37c0f810ac3feea7'


def _validate_snapshot(snapshot):
    if type(snapshot) is not VerifiedPanelSeries: _fail('ADAPTER_SNAPSHOT_INVALID')
    request=_decode(snapshot.request); _validate_request(request)
    payload=_decode(snapshot.payload); _keys(payload,PAYLOAD_KEYS); _flags(payload)
    policy=request['join_policy']; months=policy['required_months']
    if (payload['schema_version']!='synthetic-panel-series.v1' or payload['status'] not in (COMPLETE,INCOMPLETE) or
        payload['as_of']!=policy['as_of'] or payload['required_months']!=months or
        payload['mask_sha256']!=_sha(_canonical(months)) or payload['request_sha256']!=_sha(snapshot.request)):
        _fail('ADAPTER_SNAPSHOT_INVALID')
    _keys(payload['channel_statuses'],{'commodity','fx','joined'})
    if any(type(s) is not str or s not in CHANNELS for s in payload['channel_statuses'].values()): _fail()
    _keys(payload['component_identities'],{'commodity','fx'})
    if payload['code_identities']!={**TRUSTED,'adapter':CODE_SHA256}: _fail('ADAPTER_CODE_CHANGED')
    series=payload['series']
    if type(series) is not list or len(series)!=5: _fail('ADAPTER_SNAPSHOT_INVALID')
    for s,(kind,sid,unit,source) in zip(series,REGISTRY):
        _keys(s,{'series_kind','series_id','unit','source_id','is_synthetic','publication_status','rows'})
        if (s['series_kind'],s['series_id'],s['unit'],s['source_id'])!=(kind,sid,unit,source) or s['is_synthetic'] is not True:
            _fail('ADAPTER_SNAPSHOT_INVALID')
        if s['publication_status']!=payload['channel_statuses'][kind] or type(s['rows']) is not list or len(s['rows'])!=len(months): _fail()
        for r,m in zip(s['rows'],months):
            _keys(r,{'month','status','reasons','basis','available_at_proxy'})
            if r['month']!=m or r['available_at_proxy']!=_proxy(m): _fail()
            reasons=r['reasons']
            if type(reasons) is not list or any(type(reason) is not str or not reason for reason in reasons) or len(set(reasons))!=len(reasons): _fail()
            if r['status']=='complete':
                if reasons or s['publication_status'] not in ('complete_synthetic','natural_incomplete_diagnostic') or _instant(r['available_at_proxy'])>_instant(payload['as_of']): _fail()
                _fraction(r['basis'])
            elif r['status']=='unavailable':
                if r['basis'] is not None or not reasons: _fail()
            else: _fail()
    fresh=load_verified_panel_series(**request)
    if fresh!=snapshot: _fail('ADAPTER_SNAPSHOT_MISMATCH')
    return request,payload


def _control(raw,expected,size):
    if type(raw) is not bytes or len(raw)!=size or _sha(raw)!=expected: _fail('ADAPTER_CONTROL_HOLD')
    # Preserve the exact frozen control, including decimal configuration lexemes,
    # as text. No numeric floating-point value is constructed or rewritten.
    return {'sha256':expected,'bytes':size,'raw_json':raw.decode('ascii')}


def build_panel_readiness(snapshot: VerifiedPanelSeries, protocol_raw: bytes, source_manifest_raw: bytes) -> dict:
    protocol=_control(protocol_raw,PROTOCOL_SHA256,1198)
    source=_control(source_manifest_raw,SOURCE_CONTROL_SHA256,1264)
    request,payload=_validate_snapshot(snapshot)
    expected=_months('2016-09','2026-08')
    planned={'validation':_months('2021-09','2023-08'),
             'purge':_months('2023-09','2024-02'),
             'test':_months('2024-03','2026-02')}
    series_readiness=[]; ledger=[]; summaries=[]
    for series in payload['series']:
        rows={r['month']:r for r in series['rows']}
        absent=[m for m in expected if m not in rows]
        unavailable=[m for m in expected if m in rows and rows[m]['status']!='complete']
        channel=series['publication_status']
        channel_reasons=next(r['reasons'] for r in payload['load_ledger'] if r['channel']==series['series_kind'])
        base=[]
        if channel!='complete_synthetic': base.extend(['PUBLICATION_NOT_COMPLETE',*channel_reasons])
        if absent: base.append('EXPECTED_MONTHS_ABSENT')
        if unavailable: base.append('EXPECTED_MONTHS_UNAVAILABLE')
        origins={}; split_reasons={}
        for split in ('validation','test'):
            origins[split]=[]; split_reasons[split]=list(base)
            for origin in planned[split]:
                cutoff=_add(origin,-1); training=_months('2016-09',cutoff)
                instant=_instant(_end(origin))
                missing=[m for m in training if m not in rows or rows[m]['status']!='complete']
                proxy_unavailable=[m for m in training if m in rows and _instant(rows[m]['available_at_proxy'])>instant]
                count=sum(1 for m in training if m in rows and rows[m]['status']=='complete' and m not in proxy_unavailable)
                targets={str(h):_add(origin,h) for h in (1,3,6)}
                missing_targets={str(h):[target] if target not in rows or rows[target]['status']!='complete' else []
                                 for h,target in ((h,targets[str(h)]) for h in (1,3,6))}
                target_proxies=[m for m in targets.values() if m in rows and _instant(rows[m]['available_at_proxy'])>_instant(payload['as_of'])]
                reasons=list(base)
                if missing: reasons.append('MISSING_TRAINING_MONTHS')
                if count<60: reasons.append('INSUFFICIENT_CONSECUTIVE_HISTORY')
                if proxy_unavailable: reasons.append('TRAINING_PROXY_UNAVAILABLE')
                if any(missing_targets.values()): reasons.append('MISSING_TARGET_MONTHS')
                if target_proxies: reasons.append('TARGET_PROXY_UNAVAILABLE')
                reasons=list(dict.fromkeys(reasons))
                origins[split].append({'origin':origin,'evaluated_at_proxy':_end(origin),
                    'training_cutoff':cutoff,'training_count':count,'required_training_count':len(training),
                    'missing_training_months':missing,'training_proxy_unavailable':proxy_unavailable,
                    'targets_by_horizon':targets,'model_leads_from_last_training_month':[2,4,7],
                    'missing_targets_by_horizon':missing_targets,'target_proxy_unavailable':target_proxies,
                    'structurally_ready':not reasons,'reasons':reasons})
                split_reasons[split].extend(reasons)
            split_reasons[split]=list(dict.fromkeys(split_reasons[split]))
        series_readiness.append({'series_id':series['series_id'],'series_kind':series['series_kind'],
            'unit':series['unit'],'publication_status':channel,'absent_expected_months':absent,
            'unavailable_expected_months':unavailable,'origins':origins,'split_reasons':split_reasons})
        for model in ('naive','ets','gbm'):
            for split in ('validation','test'):
                for h in (1,3,6):
                    ledger.append({'series_id':series['series_id'],'unit':series['unit'],'model':model,
                        'split':split,'horizon':h,'model_lead':h+1,'status':'not_evaluated','backend':None,
                        'reasons':list(dict.fromkeys([*HOLDS,*split_reasons[split]]))})
        summaries.append({'series_id':series['series_id'],'status':'not_evaluated','reasons':list(HOLDS)})
    for split in ('validation','test'):
        for h in (1,3,6):
            ledger.append({'series_id':'iron_ore','unit':'USD/dmtu','model':'all','split':split,
                'horizon':h,'model_lead':h+1,'status':'not_evaluated','backend':None,
                'reasons':[*HOLDS,'UNIT_HOLD_DMTU']})
    summaries.extend([{'series_id':'iron_ore','status':'not_evaluated','reasons':[*HOLDS,'UNIT_HOLD_DMTU']},
                      {'series_id':'provider_dl','status':'not_evaluated','scope':'out_of_scope','reasons':list(HOLDS)}])
    common={split:list(planned[split]) if all(not s['split_reasons'][split] for s in series_readiness) else []
            for split in ('validation','test')}
    result={'schema_version':'synthetic-panel-readiness.v1',**FLAGS,'status':'SYNTHETIC_READINESS_ONLY',
        'protocol_identity':protocol,'observed_source_control_identity':source,
        'snapshot_identity':{'request_sha256':_sha(snapshot.request),'request_bytes':len(snapshot.request),
            'payload_sha256':_sha(snapshot.payload),'payload_bytes':len(snapshot.payload),
            'as_of':payload['as_of'],'mask_sha256':payload['mask_sha256'],
            'component_identities':payload['component_identities'],'joined_identity':payload['joined_identity'],
            'code_identities':payload['code_identities']},
        'expected_months':expected,'planned_origins':planned,'structural_common_origins':common,
        'series_readiness':series_readiness,'candidate_ledger':ledger,'all_models_not_evaluated':summaries,
        'selection':None,'calibration':None,'metrics':None}
    # Reconstruct again after planning. This is source integrity, not staged fitting.
    if load_verified_panel_series(**request)!=snapshot: _fail('ADAPTER_SNAPSHOT_MISMATCH')
    return result
