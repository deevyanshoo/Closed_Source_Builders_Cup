"""Stdlib OFFLINE source-readiness mechanics; no operational transport is installed.

`acquire_sources` always refuses. Schema flags are not ROOT authorization. The
stream/child primitives accept trusted host-injected fakes, never create a socket
or interpret a URL/command from a grant. They cannot contain an older unknown job.
Publication is explicitly synthetic/offline and is not an observed source receipt.
"""
from __future__ import annotations

from contextlib import contextmanager
from collections.abc import Mapping
from types import MappingProxyType
import fcntl
import ctypes
import hashlib
import json
import math
import os
from pathlib import Path
import re
import resource
import signal
import stat
import subprocess
import sys
import threading
import time

SOURCES = {
    'ecb_usd_eur': 'https://data-api.ecb.europa.eu/service/data/EXR/D.USD.EUR.SP00.A?startPeriod=2009-01-02&endPeriod=2026-09-30&format=csvdata',
    'ecb_inr_eur': 'https://data-api.ecb.europa.eu/service/data/EXR/D.INR.EUR.SP00.A?startPeriod=2009-01-02&endPeriod=2026-09-30&format=csvdata',
}
MAX_BODY = 4194304
MAX_METADATA = 4096
POLICY = {'scope': 'offline_only', 'root_offline_acceptance_sha256': '5401146e11cebf9060763c11c6cec86d5e1fbb5c64b6584b63023cc823414cab',
          'historical_ownership':'UNRESOLVED_NO_OWNED_SESSION', 'new_source_requests_authorized':0,
          'sources': SOURCES, 'attempts': 2,
          'body_bytes': MAX_BODY, 'request_seconds': 30, 'bundle_seconds': 65,
          'connect_read_seconds': 5, 'retry': 0, 'redirect': 0}
ERRORS = frozenset(('NO_OPERATIONAL_GRANT', 'BODY_LIMIT', 'HEADERS', 'HTTP_STATUS',
                   'ENCODING', 'MIME', 'INCOMPLETE_ENTITY', 'DEADLINE', 'CANCELLED',
                   'IO_FAILURE', 'METADATA_LIMIT', 'LAYOUT', 'BUDGET_EXHAUSTED',
                   'UNSETTLED_ATTEMPT', 'BUDGET_INVALID', 'SOURCE_ORDER',
                   'CLEANUP_UNRESOLVED', 'SPAWN_UNCERTAIN', 'CHILD_FAILED',
                   'HASH_MISMATCH', 'PUBLICATION_EXISTS', 'PUBLICATION_INVALID'))

class TransportError(ValueError):
    """Only fixed error codes may escape; source bodies, logs and OS text never do."""
    def __init__(self, code):
        self.code = code if code in ERRORS else 'IO_FAILURE'
        super().__init__(self.code)


_CAPTURE_SEAL=object()


class _CapturedEntity(Mapping):
    """Internal immutable evidence from a completed bounded fake-stream capture.

    JSON/dict flags cannot substitute for this host-owned value. This is not a
    network/ROOT grant or a security boundary against malicious Python code.
    """
    __slots__=('_record',)
    def __init__(self,record,seal):
        if seal is not _CAPTURE_SEAL: raise TransportError('PUBLICATION_INVALID')
        object.__setattr__(self,'_record',MappingProxyType(dict(record)))
    def __getitem__(self,key): return self._record[key]
    def __iter__(self): return iter(self._record)
    def __len__(self): return len(self._record)
    def __setattr__(self,key,value): raise TypeError('immutable capture evidence')


def _json(value):
    if isinstance(value,_CapturedEntity): value=dict(value)
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def _strict_json(data):
    def pairs(rows):
        out = {}
        for key, value in rows:
            if key in out: raise TransportError('METADATA_LIMIT')
            out[key] = value
        return out
    try:
        return json.loads(data, object_pairs_hook=pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, TypeError, UnicodeError) as exc:
        raise TransportError('METADATA_LIMIT') from None


def acquire_sources(grant, destination):
    """Refuse before filesystem/spawn/DNS. Future operational grants need new code review."""
    return {'status': 'HOLD', 'error': 'NO_OPERATIONAL_GRANT',
            'historical_ownership': 'UNRESOLVED_NO_OWNED_SESSION',
            'new_requests_authorized': 0, 'execution_authorized': False}


def validate_proposal(proposal):
    """Metadata-only inspection; even a valid-looking candidate is never a grant."""
    ownership = proposal.get('historical_ownership') if isinstance(proposal, dict) else None
    return {'status': 'HOLD_UNRESOLVED_OWNERSHIP' if ownership != 'SETTLED' else 'HOLD_NO_OPERATIONAL_GRANT',
            'new_requests_authorized': 0, 'execution_authorized': False,
            'policy_sha256': hashlib.sha256(_json(POLICY)).hexdigest()}


def _write_all(fd, data):
    view = memoryview(data)
    while view:
        count = os.write(fd, view)
        if count <= 0: raise TransportError('IO_FAILURE')
        view = view[count:]


def _positive_limit(value, ceiling):
    if type(value) is not int or not 0 < value <= ceiling:
        raise TransportError('BODY_LIMIT')
    return value


def capture_response(response, raw_fd, *, max_bytes=MAX_BODY, deadline=None,
                     clock=time.monotonic, cancelled=lambda: False):
    """Consume a trusted in-memory fake response under real stream/write bounds.

    The fake supplies status, a header-pair list, read(n), complete and close().
    Blocking reads must run in an owned child; per-read checks cannot interrupt DNS
    or blocking I/O. No operational HTTP response factory is supplied here.
    """
    try:
        limit = _positive_limit(max_bytes, MAX_BODY)
        info=os.fstat(raw_fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size!=0 or os.lseek(raw_fd,0,os.SEEK_CUR)!=0:
            raise TransportError('LAYOUT')
        end = clock() + 30 if deadline is None else deadline
        if not isinstance(end, (int, float)) or not math.isfinite(end):
            raise TransportError('DEADLINE')
        def check():
            if cancelled(): raise TransportError('CANCELLED')
            if clock() >= end: raise TransportError('DEADLINE')
        check()
        if type(response.status) is not int or response.status != 200:
            raise TransportError('HTTP_STATUS')
        rows = response.headers
        if type(rows) is not list or len(rows) > 100: raise TransportError('HEADERS')
        headers, total = {}, 0
        for row in rows:
            if not isinstance(row, (list, tuple)) or len(row) != 2:
                raise TransportError('HEADERS')
            key, value = row
            if type(key) is not str or type(value) is not str or not key or any(c in key+value for c in '\r\n'):
                raise TransportError('HEADERS')
            total += len(key.encode()) + len(value.encode()) + 4
            if total > 32768 or key.lower() in headers: raise TransportError('HEADERS')
            headers[key.lower()] = value.strip()
        if headers.get('content-encoding', 'identity').lower() != 'identity':
            raise TransportError('ENCODING')
        if headers.get('content-type', '').split(';', 1)[0].strip().lower() not in {'text/csv', 'application/csv', 'application/vnd.sdmx.data+csv'}:
            raise TransportError('MIME')
        length = headers.get('content-length')
        declared = None
        if length is not None:
            if not re.fullmatch(r'[0-9]{1,10}', length): raise TransportError('HEADERS')
            declared = int(length)
            if declared > limit: raise TransportError('BODY_LIMIT')
        if 'transfer-encoding' in headers and (declared is not None or headers['transfer-encoding'].lower() != 'chunked'):
            raise TransportError('HEADERS')
        digest, count = hashlib.sha256(), 0
        while True:
            check()
            request = min(65536, limit-count+1)
            chunk = response.read(request)
            check()
            if type(chunk) is not bytes or len(chunk) > request:
                raise TransportError('INCOMPLETE_ENTITY')
            if not chunk: break
            if count+len(chunk) > limit: raise TransportError('BODY_LIMIT')
            _write_all(raw_fd, chunk)
            digest.update(chunk); count += len(chunk)
        if count == 0 or response.complete is not True or (declared is not None and declared != count):
            raise TransportError('INCOMPLETE_ENTITY')
        os.fsync(raw_fd)
        return _CapturedEntity({'bytes': count, 'sha256': digest.hexdigest(), 'entity_complete': True,
                                'offline_only': True, 'status': 'OFFLINE_CAPTURE_COMPLETE'},_CAPTURE_SEAL)
    except TransportError:
        raise
    except Exception:
        raise TransportError('IO_FAILURE') from None
    finally:
        try: response.close()
        except Exception: raise TransportError('IO_FAILURE') from None


def capture_in_child(response, raw_fd, metadata_fd, *, max_bytes=MAX_BODY):
    """Trusted child entry primitive. Never call in a controller/parent process.

    The child alone changes its file-size limit. Both descriptors are closed on
    every path. Metadata has an independent 4096-byte write bound, despite the
    kernel per-file cap being the body ceiling. No preexec_fn or stdout is used.
    """
    try:
        limit = _positive_limit(max_bytes, MAX_BODY)
        resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_BODY, MAX_BODY))
        result = capture_response(response, raw_fd, max_bytes=limit)
    except TransportError as exc:
        result = {'status': 'OFFLINE_INCOMPLETE', 'error': exc.code, 'offline_only': True}
    except Exception:
        result = {'status': 'OFFLINE_INCOMPLETE', 'error': 'IO_FAILURE', 'offline_only': True}
    try:
        encoded = _json(result)
        if len(encoded) > MAX_METADATA: raise TransportError('METADATA_LIMIT')
        _write_all(metadata_fd, encoded); os.fsync(metadata_fd)
        return result
    finally:
        os.close(raw_fd)
        os.close(metadata_fd)


def _directory(path):
    path = Path(path)
    if path.is_symlink() or not path.is_dir(): raise TransportError('LAYOUT')
    return path


def _sync_dir(path):
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0) | os.O_NOFOLLOW)
    try: os.fsync(fd)
    finally: os.close(fd)


def _private_dir(path):
    try: Path(path).mkdir(mode=0o700)
    except FileExistsError: pass
    _directory(path)
    if stat.S_IMODE(Path(path).stat().st_mode) & 0o077: raise TransportError('LAYOUT')


def _read_bounded(path, maximum):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode) or os.fstat(fd).st_size > maximum:
            raise TransportError('METADATA_LIMIT')
        chunks, count = [], 0
        while True:
            chunk = os.read(fd, min(65536, maximum-count+1))
            if not chunk: break
            count += len(chunk)
            if count > maximum: raise TransportError('METADATA_LIMIT')
            chunks.append(chunk)
        return b''.join(chunks)
    finally: os.close(fd)


class OfflineBudget:
    """Crash-durable OFFLINE attempt accounting. It cannot authorize a GET.

    One stable flock inode protects read/reserve/settle across controllers. The
    policy/run identity and all consumed reservations survive replacement of the
    JSON state. A crash or incomplete reservation stops the remaining bundle.
    """
    def __init__(self, directory, run_id):
        if type(run_id) is not str or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', run_id):
            raise TransportError('BUDGET_INVALID')
        self.directory, self.run_id = Path(directory), run_id
        self.policy_hash = hashlib.sha256(_json(POLICY)).hexdigest()
        try: _private_dir(self.directory)
        except OSError: raise TransportError('IO_FAILURE') from None
    @contextmanager
    def _locked(self):
        fd = os.open(self.directory/'budget.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        except OSError: raise TransportError('IO_FAILURE') from None
        finally:
            try: fcntl.flock(fd, fcntl.LOCK_UN)
            finally: os.close(fd)
    def _load(self):
        path = self.directory/'budget.json'
        if not path.exists():
            if (self.directory/'budget.history').exists(): raise TransportError('BUDGET_INVALID')
            return {'scope': 'offline_only', 'run_id': self.run_id,
                    'policy_sha256': self.policy_hash, 'attempts': []}
        try: value = _strict_json(_read_bounded(path, 16384))
        except Exception: raise TransportError('BUDGET_INVALID') from None
        if type(value) is not dict or set(value) != {'scope','run_id','policy_sha256','attempts'} or value['scope'] != 'offline_only' or value['run_id'] != self.run_id or value['policy_sha256'] != self.policy_hash or type(value['attempts']) is not list or len(value['attempts']) > 2:
            raise TransportError('BUDGET_INVALID')
        for index, entry in enumerate(value['attempts']):
            if type(entry) is not dict or set(entry) != {'source_id','state','token'} or entry['source_id'] != list(SOURCES)[index] or entry['token'] != index or entry['state'] not in {'RESERVED','COMPLETE','INCOMPLETE'}:
                raise TransportError('BUDGET_INVALID')
        try:
            history=_read_bounded(self.directory/'budget.history',16384)
            if not history.endswith(b'\n') or _strict_json(history.splitlines()[-1])!=value:
                raise TransportError('BUDGET_INVALID')
        except Exception: raise TransportError('BUDGET_INVALID') from None
        return value
    def _store(self, value):
        history_fd=os.open(self.directory/'budget.history',os.O_CREAT | os.O_APPEND | os.O_WRONLY | os.O_NOFOLLOW,0o600)
        try: _write_all(history_fd,_json(value)+b'\n'); os.fsync(history_fd)
        finally: os.close(history_fd)
        _sync_dir(self.directory)
        path = self.directory/('pending-'+os.urandom(12).hex())
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try: _write_all(fd, _json(value)); os.fsync(fd)
        finally: os.close(fd)
        os.replace(path, self.directory/'budget.json'); _sync_dir(self.directory)
    def read(self):
        with self._locked(): return self._load()
    def reserve(self, source_id):
        with self._locked():
            state = self._load()
            if any(x['state'] != 'COMPLETE' for x in state['attempts']):
                raise TransportError('UNSETTLED_ATTEMPT')
            token = len(state['attempts'])
            if token >= 2: raise TransportError('BUDGET_EXHAUSTED')
            if source_id != list(SOURCES)[token]: raise TransportError('SOURCE_ORDER')
            state['attempts'].append({'source_id':source_id, 'state':'RESERVED', 'token':token})
            self._store(state)  # fsync before caller can attempt an uncertain spawn.
            return token
    def settle(self, token, status):
        with self._locked():
            state = self._load()
            if type(token) is not int or not 0 <= token < len(state['attempts']) or status not in {'COMPLETE','INCOMPLETE'} or state['attempts'][token]['state'] != 'RESERVED':
                raise TransportError('BUDGET_INVALID')
            state['attempts'][token]['state'] = status; self._store(state)


def _child_environment(directory):
    return {'PATH':'/usr/bin:/bin', 'HOME':str(directory), 'TMPDIR':str(directory),
            'LANG':'C', 'LC_ALL':'C', 'TZ':'UTC'}


def _owned_group_only_exited_leader(child):
    """Non-signaling Darwin membership check while leader PID remains anchored.

    Installed SDK: PROC_PGRP_ONLY=2; proc_listpids returns PID bytes. Never inspect
    arguments/environment or query an unowned/reaped group. Unknown/truncated or
    unsupported platform membership fails closed, not a claimed absent group.
    """
    if child.returncode is not None or sys.platform != 'darwin': return False
    try:
        info=os.waitid(os.P_PID,child.pid,os.WEXITED | os.WNOHANG | os.WNOWAIT)
        if info is None or info.si_pid != child.pid: return False
        lib=ctypes.CDLL('/usr/lib/libproc.dylib',use_errno=True)
        fn=lib.proc_listpids
        fn.argtypes=[ctypes.c_uint32,ctypes.c_uint32,ctypes.c_void_p,ctypes.c_int]
        fn.restype=ctypes.c_int
        slots=(ctypes.c_int*1024)()
        count=fn(2,child.pid,slots,ctypes.sizeof(slots))
        if count <= 0 or count >= ctypes.sizeof(slots) or count % ctypes.sizeof(ctypes.c_int): return False
        members={p for p in slots[:count//ctypes.sizeof(ctypes.c_int)] if p>0}
        return members == {child.pid}
    except (OSError,AttributeError,ValueError): return False


def run_owned_child(command, directory, *, timeout=30, cancelled=lambda: False,
                    spawn=subprocess.Popen, term_grace=0.5):
    """Supervise a trusted host-owned synthetic command; not a public command API.

    Waitid WNOWAIT keeps the session leader unreaped until group cleanup, preventing
    reused/reaped PID authority. No poll(), raw argv/env inspection, preexec_fn,
    shell or inherited stdout pipes. A cleanup denial retains identity/incomplete.
    """
    directory = _directory(directory)
    if not isinstance(timeout,(int,float)) or isinstance(timeout,bool) or not math.isfinite(timeout) or not 0 < timeout <= 30 or not isinstance(term_grace,(int,float)) or not math.isfinite(term_grace) or not 0 <= term_grace <= 0.5:
        raise TransportError('DEADLINE')
    if type(command) not in (list,tuple) or not command or any(type(x) is not str for x in command) or not Path(command[0]).is_absolute():
        raise TransportError('LAYOUT')
    result={'status':'OFFLINE_INCOMPLETE','offline_only':True,'settled':False,
            'direct_reaped':False,'pid':None,'error':None,'returncode':None}
    child, authority, cleanup_failed = None, False, False
    end = time.monotonic()+timeout
    # Only the owning main thread installs handlers; explicit callback covers tests/threads.
    latch = threading.Event(); old_handlers={}
    if threading.current_thread() is threading.main_thread():
        for sig in (signal.SIGINT, signal.SIGTERM):
            old_handlers[sig]=signal.signal(sig, lambda _s,_f: latch.set())
    try:
        if latch.is_set() or cancelled():
            result['error']='CANCELLED'; result['settled']=True; return result
        try:
            # Return/registration is protected by latching handlers, not exceptions.
            child=spawn(command,cwd=directory,env=_child_environment(directory),
                        stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                        start_new_session=True,close_fds=True)
            result['pid']=child.pid
            authority = child.returncode is None and os.getpgid(child.pid) == child.pid
            if not authority: raise TransportError('CLEANUP_UNRESOLVED')
            while True:
                if latch.is_set() or cancelled(): result['error']='CANCELLED'; break
                if time.monotonic() >= end: result['error']='DEADLINE'; break
                info=os.waitid(os.P_PID,child.pid,os.WEXITED | os.WNOHANG | os.WNOWAIT)
                if info is not None and info.si_pid == child.pid: break
                time.sleep(min(0.005,max(0,end-time.monotonic())))
        except TransportError as exc: result['error']=exc.code
        except Exception: result['error']='SPAWN_UNCERTAIN' if child is None else 'CLEANUP_UNRESOLVED'
        finally:
            if child is not None:
                if authority and child.returncode is None:
                    # The leader has never been reaped, including after an early exit.
                    group_settled=_owned_group_only_exited_leader(child)
                    for sig,delay in ((signal.SIGTERM,term_grace),(signal.SIGKILL,0)):
                        if group_settled: break
                        try: os.killpg(child.pid,sig)
                        except ProcessLookupError: group_settled=True
                        except OSError:
                            # EPERM alone is never success. Native membership can
                            # positively prove only the anchored exited leader.
                            if not _owned_group_only_exited_leader(child): cleanup_failed=True
                        if delay: time.sleep(delay)
                        group_settled=group_settled or _owned_group_only_exited_leader(child)
                    settle_end=time.monotonic()+0.2
                    while not group_settled and time.monotonic()<settle_end:
                        group_settled=_owned_group_only_exited_leader(child)
                        if not group_settled: time.sleep(0.005)
                    if not group_settled: cleanup_failed=True
                else: cleanup_failed=True
                try:
                    result['returncode']=child.wait(timeout=2)
                    result['direct_reaped']=True
                except Exception: cleanup_failed=True
                result['settled']=result['direct_reaped'] and authority and not cleanup_failed
                if cleanup_failed: result['error']='CLEANUP_UNRESOLVED'
                if result['error'] is None and result['returncode'] != 0: result['error']='CHILD_FAILED'
                if result['error'] is None and result['settled']: result['status']='OFFLINE_CHILD_COMPLETE'
        return result
    finally:
        for sig, handler in old_handlers.items(): signal.signal(sig,handler)


def _valid_run(run_id):
    if type(run_id) is not str or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',run_id):
        raise TransportError('LAYOUT')


def publish_offline(directory, run_id, originals, settlement):
    """No-clobber synthetic archive; completed capture evidence, not flags, is required.

    This low-level offline archive does not prove any child/process/rights/grant.
    The injected host owns settlement; JSON success booleans alone are incomplete.
    """
    _valid_run(run_id)
    directory=Path(directory)
    if type(originals) is not dict or not originals or not set(originals) <= set(SOURCES):
        raise TransportError('LAYOUT')
    if type(settlement) is not dict or settlement.get('offline_only') is not True:
        raise TransportError('PUBLICATION_INVALID')
    captures=settlement.get('captures')
    complete=settlement.get('settled') is True and type(captures) is dict and set(captures)==set(originals) and all(isinstance(x,_CapturedEntity) for x in captures.values())
    linked=None
    object_created=False
    rows=[]
    try:
        _private_dir(directory)
        for sub in ('objects','committed','incomplete'): _private_dir(directory/sub)
        obj=directory/'objects'/run_id
        try:
            obj.mkdir(mode=0o700); object_created=True
        except FileExistsError: raise TransportError('PUBLICATION_EXISTS') from None
        for source_id, source_path in sorted(originals.items()):
            body=_read_bounded(source_path,MAX_BODY)
            if complete and (captures[source_id]['bytes']!=len(body) or captures[source_id]['sha256']!=hashlib.sha256(body).hexdigest() or captures[source_id]['entity_complete'] is not True):
                complete=False
            target=obj/(source_id+'.raw')
            fd=os.open(target,os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW,0o600)
            try: _write_all(fd,body); os.fsync(fd); os.fchmod(fd,0o400); os.fsync(fd)
            finally: os.close(fd)
            rows.append({'source_id':source_id,'path':str(target.relative_to(directory)),
                         'bytes':len(body),'sha256':hashlib.sha256(body).hexdigest()})
        receipt={'schema_version':'offline-source-publication.v1','run_id':run_id,
                 'status':'OFFLINE_COMPLETE' if complete else 'OFFLINE_INCOMPLETE',
                 'offline_only':True,'execution_authorized':False,'originals':rows,
                 'settled':complete,'capture_verified':complete}
        manifest=obj/'manifest.json'; data=_json(receipt)
        if len(data)>MAX_METADATA: raise TransportError('METADATA_LIMIT')
        fd=os.open(manifest,os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW,0o600)
        try: _write_all(fd,data); os.fsync(fd); os.fchmod(fd,0o400); os.fsync(fd)
        finally: os.close(fd)
        _sync_dir(obj); obj.chmod(0o500); _sync_dir(obj); _sync_dir(directory/'objects')
        destination=directory/('committed' if complete else 'incomplete')/(run_id+'.json')
        os.link(manifest,destination,follow_symlinks=False)
        linked=destination  # atomic, same filesystem, EEXIST refuses.
        _sync_dir(destination.parent); _sync_dir(directory)
        if complete: verify_publication(directory,run_id)
        return receipt
    except (TransportError,OSError) as error:
        code=error.code if isinstance(error,TransportError) else ('PUBLICATION_EXISTS' if isinstance(error,FileExistsError) else 'IO_FAILURE')
        # Only remove a marker linked by THIS call. Never invalidate a preexisting run.
        if linked is not None:
            try: linked.unlink(); _sync_dir(linked.parent)
            except OSError: pass  # still failure; no acknowledged durable success.
        if object_created: _record_publication_failure(directory,run_id,rows,code)
        raise TransportError(code) from None


def _record_publication_failure(directory,run_id,rows,code):
    marker=directory/'incomplete'/(run_id+'.json')
    receipt={'schema_version':'offline-source-publication.v1','run_id':run_id,
             'status':'OFFLINE_INCOMPLETE','error':code,'offline_only':True,
             'execution_authorized':False,'settled':False,'originals':rows}
    pending=directory/'incomplete'/('pending-'+os.urandom(12).hex()+'.json')
    try:
        fd=os.open(pending,os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW,0o600)
        try: _write_all(fd,_json(receipt)); os.fsync(fd); os.fchmod(fd,0o400)
        finally: os.close(fd)
        os.link(pending,marker,follow_symlinks=False); _sync_dir(marker.parent)
        pending.unlink()
    except OSError: pass  # original fixed failure is retained, never a success receipt.


def verify_publication(directory,run_id):
    _valid_run(run_id); directory=_directory(directory)
    try:
        if (directory/'incomplete'/(run_id+'.json')).exists(): raise TransportError('PUBLICATION_INVALID')
        receipt=_strict_json(_read_bounded(directory/'committed'/(run_id+'.json'),MAX_METADATA))
        if type(receipt) is not dict or receipt.get('run_id')!=run_id or receipt.get('offline_only') is not True or receipt.get('settled') is not True or receipt.get('status')!='OFFLINE_COMPLETE' or receipt.get('execution_authorized') is not False or receipt.get('capture_verified') is not True or type(receipt.get('originals')) is not list or not receipt['originals']:
            raise TransportError('PUBLICATION_INVALID')
        obj=_directory(directory/'objects'/run_id)
        if stat.S_IMODE(obj.stat().st_mode)&0o222: raise TransportError('PUBLICATION_INVALID')
        seen=set()
        for row in receipt['originals']:
            sid=row.get('source_id'); expected='objects/'+run_id+'/'+str(sid)+'.raw'
            if sid not in SOURCES or sid in seen or row.get('path')!=expected:
                raise TransportError('PUBLICATION_INVALID')
            seen.add(sid); path=directory/expected
            body=_read_bounded(path,MAX_BODY)
            if type(row.get('bytes')) is not int or row['bytes']!=len(body) or row.get('sha256')!=hashlib.sha256(body).hexdigest():
                raise TransportError('HASH_MISMATCH')
            if stat.S_IMODE(path.stat().st_mode)&0o222: raise TransportError('PUBLICATION_INVALID')
        return receipt
    except TransportError: raise
    except Exception: raise TransportError('PUBLICATION_INVALID') from None
