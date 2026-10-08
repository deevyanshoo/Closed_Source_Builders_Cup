"""Offline controller tests: fake entities and task-owned children, never sockets."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

MODULE = Path(__file__).parents[1] / 'src/procure/evaluation/source_transport.py'
CLI = Path(__file__).parents[1] / 'scripts/acquire_forecast_sources.py'
NETWORK_EVENTS = []

def no_network(event, args):
    if event in {'socket.connect', 'socket.getaddrinfo', 'socket.gethostbyname', 'socket.bind'}:
        NETWORK_EVENTS.append(event)
        raise AssertionError('network forbidden in offline tests')
sys.addaudithook(no_network)

def production():
    assert MODULE.is_file(), 'missing offline transport implementation'
    spec = importlib.util.spec_from_file_location('offline_transport_under_test', MODULE)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module

class Response:
    # Fake only the external byte stream, not limits, hashing, publication or lifecycle.
    def __init__(self, body=b'fake,only\n', status=200, headers=None, complete=True):
        self.body, self.offset = body, 0
        self.status, self.complete = status, complete
        self.headers = headers if headers is not None else [('Content-Type', 'text/csv'), ('Content-Length', str(len(body)))]
        self.closed = False
    def read(self, n):
        out = self.body[self.offset:self.offset+n]; self.offset += len(out); return out
    def close(self): self.closed = True

class TransportTests(unittest.TestCase):
    def setUp(self):
        self.m = production()
        self.tmp = tempfile.TemporaryDirectory(prefix='offline-source-test-')
        self.root = Path(self.tmp.name)
    def tearDown(self):
        self.tmp.cleanup()
        self.assertEqual(NETWORK_EVENTS, [])
    def capture(self, response, **kwargs):
        path = self.root/'raw'
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
        try: result = self.m.capture_response(response, fd, **kwargs)
        finally: os.close(fd)
        return result, path
    def settlement(self,raw):
        # Create evidence through actual bounded capture, using only this fake test file.
        with tempfile.TemporaryFile(dir=self.root) as witness:
            evidence=self.m.capture_response(Response(raw.read_bytes()),witness.fileno())
        return {'offline_only':True,'settled':True,'captures':{'ecb_usd_eur':evidence}}
    def two_originals_and_settlement(self):
        stage=self.root/'stage'; stage.mkdir(exist_ok=True)
        originals={}
        captures={}
        for source_id, body in [('ecb_usd_eur',b'fake,usd\n'),('ecb_inr_eur',b'fake,inr\n')]:
            raw=stage/(source_id+'.raw'); raw.write_bytes(body)
            originals[source_id]=raw
            with tempfile.TemporaryFile(dir=self.root) as witness:
                captures[source_id]=self.m.capture_response(Response(body),witness.fileno())
        return originals, {'offline_only':True,'settled':True,'captures':captures}
    def test_schema_boolean_never_authorizes_acquisition(self):
        result = self.m.acquire_sources({'root_authorized': True, 'historical_ownership': 'SETTLED'}, self.root/'forbidden')
        self.assertEqual(result['error'], 'NO_OPERATIONAL_GRANT')
        self.assertFalse(result['execution_authorized'])
        self.assertFalse((self.root/'forbidden').exists())
    def test_unresolved_proposal_is_only_metadata_and_denies_start(self):
        result = self.m.validate_proposal({'historical_ownership': 'UNRESOLVED_NO_OWNED_SESSION'})
        self.assertEqual(result['status'], 'HOLD_UNRESOLVED_OWNERSHIP')
        self.assertEqual(result['new_requests_authorized'], 0)
    def test_body_is_hashed_and_response_closed(self):
        response = Response()
        result, path = self.capture(response)
        self.assertEqual(path.read_bytes(), b'fake,only\n')
        self.assertEqual(result['sha256'], hashlib.sha256(b'fake,only\n').hexdigest())
        self.assertEqual(result['bytes'], 10)
        self.assertTrue(response.closed)
    def test_oversize_never_writes_beyond_cap(self):
        response = Response(b'x'*40, headers=[('Content-Type', 'text/csv')])
        with self.assertRaisesRegex(self.m.TransportError, 'BODY_LIMIT'):
            self.capture(response, max_bytes=16)
        self.assertLessEqual((self.root/'raw').stat().st_size, 16)
        self.assertTrue(response.closed)
    def test_declared_oversize_is_rejected_before_body(self):
        response = Response(b'private body', headers=[('Content-Type', 'text/csv'),('Content-Length', '999')])
        with self.assertRaisesRegex(self.m.TransportError, 'BODY_LIMIT'):
            self.capture(response, max_bytes=16)
        self.assertEqual((self.root/'raw').stat().st_size, 0)
        self.assertEqual(response.offset, 0)
    def test_exact_cap_requires_complete_framing(self):
        response = Response(b'x'*16, complete=False)
        with self.assertRaisesRegex(self.m.TransportError, 'INCOMPLETE_ENTITY'):
            self.capture(response, max_bytes=16)
        os.unlink(self.root/'raw')
        result, path = self.capture(Response(b'x'*16), max_bytes=16)
        self.assertEqual(result['bytes'], 16)
    def test_status_encoding_mime_and_length_fail_closed(self):
        cases=[(Response(status=302), 'HTTP_STATUS'),
               (Response(headers=[('Content-Type','text/csv'),('Content-Encoding','gzip')]),'ENCODING'),
               (Response(headers=[('Content-Type','text/html')]),'MIME'),
               (Response(headers=[('Content-Type','text/csv'),('Content-Length','1')]),'INCOMPLETE_ENTITY'),
               (Response(headers=[('Content-Type','text/csv'),('Content-Length','10'),('content-length','10')]),'HEADERS')]
        for response, error in cases:
            with self.subTest(error=error):
                if (self.root/'raw').exists(): os.unlink(self.root/'raw')
                with self.assertRaisesRegex(self.m.TransportError, error): self.capture(response)
                self.assertTrue(response.closed)
    def test_fake_trickle_deadline_and_cancellation_close_response(self):
        times=iter([0, 0, 0, 2])
        with self.assertRaisesRegex(self.m.TransportError, 'DEADLINE'):
            self.capture(Response(), deadline=1, clock=lambda: next(times, 2))
        os.unlink(self.root/'raw')
        response=Response()
        with self.assertRaisesRegex(self.m.TransportError, 'CANCELLED'):
            self.capture(response, cancelled=lambda: True)
        self.assertTrue(response.closed)
    def test_crashed_reservation_remains_consumed_across_controllers(self):
        first=self.m.OfflineBudget(self.root/'budget', 'run1')
        first.reserve('ecb_usd_eur')
        second=self.m.OfflineBudget(self.root/'budget', 'run1')
        with self.assertRaisesRegex(self.m.TransportError, 'UNSETTLED_ATTEMPT'):
            second.reserve('ecb_usd_eur')
        self.assertEqual(len(second.read()['attempts']), 1)
    def test_two_serial_slots_and_no_third_retry(self):
        budget=self.m.OfflineBudget(self.root/'budget', 'run2')
        a=budget.reserve('ecb_usd_eur'); budget.settle(a, 'COMPLETE')
        b=budget.reserve('ecb_inr_eur'); budget.settle(b, 'COMPLETE')
        with self.assertRaisesRegex(self.m.TransportError, 'BUDGET_EXHAUSTED'):
            budget.reserve('ecb_usd_eur')
        self.assertEqual([r['source_id'] for r in budget.read()['attempts']], ['ecb_usd_eur','ecb_inr_eur'])
    def test_failed_attempt_stops_remaining_slot(self):
        budget=self.m.OfflineBudget(self.root/'budget', 'run3')
        token=budget.reserve('ecb_usd_eur'); budget.settle(token, 'INCOMPLETE')
        with self.assertRaisesRegex(self.m.TransportError, 'UNSETTLED_ATTEMPT'):
            budget.reserve('ecb_inr_eur')
    def test_two_owned_process_controllers_reserve_only_one_durable_slot(self):
        children=[]; budget_dir=self.root/'budget'; gate=self.root/'gate'
        base="import pathlib,time,runpy; m=runpy.run_path(%r); " % str(MODULE)
        try:
            for i in range(2):
                ready=self.root/('ready'+str(i)); output=self.root/('result'+str(i))
                code=base+"pathlib.Path(%r).touch(); end=time.monotonic()+2\nwhile not pathlib.Path(%r).exists():\n if time.monotonic()>end: raise SystemExit(5)\n time.sleep(.005)\ntry:\n m['OfflineBudget'](%r,'parallel').reserve('ecb_usd_eur'); result='RESERVED'\nexcept m['TransportError'] as e: result=e.code\npathlib.Path(%r).write_text(result)\n" % (str(ready),str(gate),str(budget_dir),str(output))
                child=subprocess.Popen([sys.executable,'-I','-S','-B','-c',code],cwd=self.root,env=self.m._child_environment(self.root),start_new_session=True,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                children.append(child)
            end=time.monotonic()+2
            while not all((self.root/('ready'+str(i))).exists() for i in range(2)):
                self.assertLess(time.monotonic(),end); time.sleep(.005)
            gate.touch()
            for child in children: self.assertEqual(child.wait(timeout=2),0)
            self.assertEqual(sorted((self.root/('result'+str(i))).read_text() for i in range(2)),['RESERVED','UNSETTLED_ATTEMPT'])
            self.assertEqual(len(self.m.OfflineBudget(budget_dir,'parallel').read()['attempts']),1)
        finally:
            for child in children:
                if child.returncode is None: child.kill(); child.wait(timeout=2)
    def test_cross_controller_lock_allows_one_reservation(self):
        budgets=[self.m.OfflineBudget(self.root/'budget', 'run4') for _ in range(2)]
        outcomes=[]
        def reserve(b):
            try: outcomes.append(b.reserve('ecb_usd_eur'))
            except self.m.TransportError: outcomes.append(None)
        ts=[threading.Thread(target=reserve,args=(b,)) for b in budgets]
        for t in ts: t.start()
        for t in ts: t.join(2); self.assertFalse(t.is_alive())
        self.assertEqual(sum(x is not None for x in outcomes), 1)
        self.assertEqual(len(budgets[0].read()['attempts']), 1)
    def test_immutable_no_clobber_publication_and_tamper_detection(self):
        originals, settlement=self.two_originals_and_settlement()
        output=self.root/'archive'
        receipt=self.m.publish_offline(output, 'r1', originals, settlement)
        manifest=output/'committed/r1.json'
        self.assertTrue(manifest.is_file())
        self.assertEqual(self.m.verify_publication(output,'r1')['status'], 'OFFLINE_COMPLETE')
        self.assertEqual({row['source_id'] for row in receipt['originals']},set(self.m.SOURCES))
        before=manifest.read_bytes()
        with self.assertRaises(self.m.TransportError):
            self.m.publish_offline(output,'r1',originals,settlement)
        self.assertEqual(manifest.read_bytes(), before)
        original=output/receipt['originals'][0]['path']; original.chmod(0o600); original.write_bytes(b'tampered')
        with self.assertRaisesRegex(self.m.TransportError,'HASH_MISMATCH'):
            self.m.verify_publication(output,'r1')
    def test_one_genuine_capture_is_incomplete_and_retains_only_real_original(self):
        raw=self.root/'usd'; raw.write_bytes(b'fake,usd\n')
        receipt=self.m.publish_offline(self.root/'archive','partial',{'ecb_usd_eur':raw},self.settlement(raw))
        self.assertEqual(receipt['status'],'OFFLINE_INCOMPLETE')
        self.assertEqual([row['source_id'] for row in receipt['originals']],['ecb_usd_eur'])
        self.assertEqual(receipt['originals'][0]['sha256'],hashlib.sha256(raw.read_bytes()).hexdigest())
        self.assertFalse((self.root/'archive/committed/partial.json').exists())
        self.assertTrue((self.root/'archive/incomplete/partial.json').exists())
        with self.assertRaises(self.m.TransportError): self.m.verify_publication(self.root/'archive','partial')
    def test_verifier_rejects_forged_single_source_committed_receipt(self):
        originals, settlement=self.two_originals_and_settlement()
        self.m.publish_offline(self.root/'archive','forged',originals,settlement)
        manifest=self.root/'archive/objects/forged/manifest.json'
        receipt=json.loads(manifest.read_bytes())
        receipt['originals']=receipt['originals'][:1]
        manifest.chmod(0o600); manifest.write_bytes(self.m._json(receipt)); manifest.chmod(0o400)
        with self.assertRaisesRegex(self.m.TransportError,'PUBLICATION_INVALID'):
            self.m.verify_publication(self.root/'archive','forged')
    def test_verifier_rejects_duplicate_or_unknown_original_identity(self):
        originals, settlement=self.two_originals_and_settlement()
        for kind in ('duplicate','unknown'):
            with self.subTest(kind=kind):
                run_id='forged-'+kind
                self.m.publish_offline(self.root/'archive',run_id,originals,settlement)
                manifest=self.root/'archive/objects'/run_id/'manifest.json'
                receipt=json.loads(manifest.read_bytes())
                if kind=='duplicate':
                    receipt['originals'][1]=receipt['originals'][0].copy()
                else:
                    receipt['originals'][1]['source_id']='unexpected_source'
                manifest.chmod(0o600); manifest.write_bytes(self.m._json(receipt)); manifest.chmod(0o400)
                with self.assertRaisesRegex(self.m.TransportError,'PUBLICATION_INVALID'):
                    self.m.verify_publication(self.root/'archive',run_id)
    def test_two_originals_with_one_missing_capture_stay_incomplete(self):
        originals, settlement=self.two_originals_and_settlement()
        del settlement['captures']['ecb_inr_eur']
        receipt=self.m.publish_offline(self.root/'archive','missing-capture',originals,settlement)
        self.assertEqual(receipt['status'],'OFFLINE_INCOMPLETE')
        self.assertEqual({row['source_id'] for row in receipt['originals']},set(self.m.SOURCES))
        self.assertFalse((self.root/'archive/committed/missing-capture.json').exists())
        self.assertTrue((self.root/'archive/incomplete/missing-capture.json').exists())
    def test_settlement_boolean_alone_cannot_publish_unverified_entity(self):
        raw=self.root/'raw'; raw.write_bytes(b'truncated')
        result=self.m.publish_offline(self.root/'archive','unverified',{'ecb_usd_eur':raw},{'offline_only':True,'settled':True})
        self.assertEqual(result['status'],'OFFLINE_INCOMPLETE')
        self.assertFalse((self.root/'archive/committed/unverified.json').exists())
        self.assertFalse(result['execution_authorized'])
    def test_incomplete_or_unsettled_cannot_create_committed_marker(self):
        raw=self.root/'raw'; raw.write_bytes(b'partial')
        result=self.m.publish_offline(self.root/'archive','r2',{'ecb_usd_eur':raw},{'offline_only':True,'settled':False})
        self.assertEqual(result['status'],'OFFLINE_INCOMPLETE')
        self.assertFalse((self.root/'archive/committed/r2.json').exists())
        self.assertTrue((self.root/'archive/incomplete/r2.json').exists())
    def test_publication_sync_failure_cannot_leave_a_success_marker(self):
        originals, settlement=self.two_originals_and_settlement()
        actual=self.m._sync_dir
        def fail_commit(directory):
            if Path(directory).name=='committed': raise OSError('synthetic injected fsync denial')
            return actual(directory)
        with mock.patch.object(self.m,'_sync_dir',side_effect=fail_commit):
            with self.assertRaisesRegex(self.m.TransportError,'IO_FAILURE'):
                self.m.publish_offline(self.root/'archive','syncfail',originals,settlement)
        self.assertFalse((self.root/'archive/committed/syncfail.json').exists())
        self.assertTrue((self.root/'archive/incomplete/syncfail.json').exists())
    def test_mutated_or_missing_ledger_never_resets_consumed_budget(self):
        budget=self.m.OfflineBudget(self.root/'budget','crash')
        budget.reserve('ecb_usd_eur')
        (self.root/'budget/budget.json').unlink()
        with self.assertRaisesRegex(self.m.TransportError,'BUDGET_INVALID'):
            self.m.OfflineBudget(self.root/'budget','crash').reserve('ecb_usd_eur')
    def test_body_length_zero_or_private_error_never_publishes_success(self):
        response=Response(body=b'',headers=[('Content-Type','text/csv')])
        with self.assertRaisesRegex(self.m.TransportError,'INCOMPLETE_ENTITY'): self.capture(response)
    def test_post_link_verification_failure_is_incomplete_not_committed(self):
        originals, settlement=self.two_originals_and_settlement()
        with mock.patch.object(self.m,'verify_publication',side_effect=self.m.TransportError('HASH_MISMATCH')):
            with self.assertRaisesRegex(self.m.TransportError,'HASH_MISMATCH'):
                self.m.publish_offline(self.root/'archive','postlink',originals,settlement)
        self.assertFalse((self.root/'archive/committed/postlink.json').exists())
        self.assertTrue((self.root/'archive/incomplete/postlink.json').exists())
    def test_reservation_sync_error_is_sanitized_and_no_reset(self):
        budget=self.m.OfflineBudget(self.root/'budget','syncerror')
        with mock.patch.object(self.m,'_sync_dir',side_effect=OSError('synthetic private details')):
            try: budget.reserve('ecb_usd_eur')
            except Exception as error:
                self.assertIsInstance(error,self.m.TransportError)
                self.assertEqual(str(error),'IO_FAILURE')
            else: self.fail('sync failure accepted as reservation')
        with self.assertRaisesRegex(self.m.TransportError,'BUDGET_INVALID'):
            self.m.OfflineBudget(self.root/'budget','syncerror').reserve('ecb_usd_eur')
    def test_body_fd_must_be_empty_regular_staging_file(self):
        raw=self.root/'raw'; raw.write_bytes(b'already-present')
        fd=os.open(raw,os.O_RDWR)
        try:
            with self.assertRaisesRegex(self.m.TransportError,'LAYOUT'):
                self.m.capture_response(Response(),fd)
        finally: os.close(fd)
        self.assertEqual(raw.read_bytes(),b'already-present')
    def test_cli_bad_arguments_are_fixed_and_do_not_echo_input(self):
        result=subprocess.run([sys.executable,'-I','-B',str(CLI),'--unapproved','synthetic-private-marker'],cwd=self.root,capture_output=True,text=True,timeout=5)
        self.assertEqual(result.returncode,2)
        self.assertNotIn('synthetic-private-marker',result.stdout+result.stderr)
        self.assertEqual(json.loads(result.stdout)['error'],'INVALID_ARGUMENTS')
    def test_cli_candidate_settled_metadata_is_still_no_network_grant(self):
        proposal=self.root/'proposal.json'; proposal.write_text('{"historical_ownership":"SETTLED","root_authorized":true}')
        result=subprocess.run([sys.executable,'-I','-B',str(CLI),'--validate-proposal',str(proposal)],cwd=self.root,capture_output=True,text=True,timeout=5)
        self.assertEqual(result.returncode,2)
        receipt=json.loads(result.stdout); self.assertEqual(receipt['new_requests_authorized'],0); self.assertFalse(receipt['execution_authorized'])
        self.assertEqual(list(self.root.iterdir()),[proposal])
    def test_cli_default_refuses_without_creating_state(self):
        result=subprocess.run([sys.executable,'-I','-B',str(CLI)],cwd=self.root,capture_output=True,text=True,timeout=5)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stdout)['error'],'NO_OPERATIONAL_GRANT')
        self.assertEqual(list(self.root.iterdir()), [])

class OwnedChildrenTests(unittest.TestCase):
    def setUp(self):
        self.m=production(); self.tmp=tempfile.TemporaryDirectory(prefix='offline-owned-child-'); self.root=Path(self.tmp.name)
    def tearDown(self): self.tmp.cleanup(); self.assertEqual(NETWORK_EVENTS, [])
    def command(self, code): return [sys.executable,'-I','-S','-B','-c',code]
    def child_capture_code(self, response_code):
        return "import os,runpy; m=runpy.run_path(%r); raw=os.open(%r,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600); meta=os.open(%r,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600); " % (str(MODULE),str(self.root/'raw'),str(self.root/'meta')) + response_code
    def test_child_capture_metadata_and_parent_file_limits_are_distinct(self):
        import resource
        before=resource.getrlimit(resource.RLIMIT_FSIZE)
        code=self.child_capture_code("R=type('R',(),{'status':200,'headers':[('Content-Type','text/csv'),('Content-Length','10')],'complete':True,'body':b'fake,only\\n','read':lambda self,n: self.body if not getattr(self,'done',False) and not setattr(self,'done',True) else b'', 'close':lambda self:None}); m['capture_in_child'](R(),raw,meta)")
        result=self.m.run_owned_child(self.command(code),self.root,timeout=2,term_grace=0.02)
        self.assertEqual(result['returncode'],0); self.assertTrue(result['settled'])
        receipt=json.loads((self.root/'meta').read_bytes())
        self.assertEqual(receipt['status'],'OFFLINE_CAPTURE_COMPLETE'); self.assertEqual(receipt['bytes'],10)
        self.assertEqual(receipt['sha256'],hashlib.sha256(b'fake,only\n').hexdigest())
        self.assertLessEqual((self.root/'meta').stat().st_size,4096)
        self.assertEqual(resource.getrlimit(resource.RLIMIT_FSIZE),before)
    def test_child_metadata_cap_fails_before_write_and_closes_both_descriptors(self):
        code=self.child_capture_code("\nimport sys,errno\nR=type('R',(),{'status':200,'headers':[('Content-Type','text/csv'),('Content-Length','10')],'complete':True,'body':b'fake,only\\n','read':lambda self,n: self.body if not getattr(self,'done',False) and not setattr(self,'done',True) else b'', 'close':lambda self:None})\nm['capture_in_child'].__globals__['_json']=lambda value:b'x'*4097\ntry: m['capture_in_child'](R(),raw,meta)\nexcept m['TransportError'] as e:\n if e.code!='METADATA_LIMIT': sys.exit(7)\n for fd in (raw,meta):\n  try: os.write(fd,b'x'); sys.exit(8)\n  except OSError as err:\n   if err.errno!=errno.EBADF: sys.exit(9)\nelse: sys.exit(10)\n")
        result=self.m.run_owned_child(self.command(code),self.root,timeout=2,term_grace=.02)
        self.assertEqual(result['returncode'],0); self.assertTrue(result['settled'])
        self.assertEqual((self.root/'meta').stat().st_size,0)
        self.assertEqual((self.root/'raw').stat().st_size,10)
    def test_child_kernel_limit_stops_a_writer_bypassing_stream_accounting(self):
        code=self.child_capture_code("\nclass Rogue:\n status=200; headers=[('Content-Type','text/csv')]; complete=True\n def read(self,n):\n  for i in range(5): os.write(raw,b'x'*1048576)\n  return b''\n def close(self): pass\nm['capture_in_child'](Rogue(),raw,meta)\n")
        result=self.m.run_owned_child(self.command(code),self.root,timeout=2,term_grace=0.02)
        self.assertEqual(result['returncode'],0)
        self.assertLessEqual((self.root/'raw').stat().st_size,4194304)
        self.assertEqual(json.loads((self.root/'meta').read_bytes())['status'],'OFFLINE_INCOMPLETE')
    def test_sigterm_latches_through_actual_spawn_registration(self):
        handles=[]
        def spawn(*args,**kwargs):
            child=subprocess.Popen(*args,**kwargs); handles.append(child)
            os.kill(os.getpid(),signal.SIGTERM)  # only this task-owned unittest interpreter
            return child
        try:
            result=self.m.run_owned_child(self.command('import time; time.sleep(10)'),self.root,timeout=1,spawn=spawn,term_grace=0.02)
            self.assertEqual(result['error'],'CANCELLED'); self.assertTrue(result['direct_reaped']); self.assertTrue(result['settled'])
        finally:
            for child in handles:
                if child.returncode is None: child.kill(); child.wait(timeout=2)
    def test_no_group_signal_after_direct_reap(self):
        handles=[]; events=[]; original_killpg=os.killpg
        def spawn(*args,**kwargs):
            child=subprocess.Popen(*args,**kwargs); handles.append(child); return child
        def checked_signal(pid,sig):
            self.assertIsNone(handles[0].returncode)
            events.append(int(sig)); return original_killpg(pid,sig)
        with mock.patch.object(self.m.os,'killpg',side_effect=checked_signal):
            result=self.m.run_owned_child(self.command('import time; time.sleep(10)'),self.root,timeout=.08,spawn=spawn,term_grace=.02)
        self.assertTrue(result['settled']); self.assertTrue(result['direct_reaped']); self.assertIn(int(signal.SIGTERM),events)
    def test_owned_deadline_stops_and_reaps_sleeping_child(self):
        result=self.m.run_owned_child(self.command('import time; time.sleep(10)'),self.root,timeout=0.08,term_grace=0.02)
        self.assertEqual(result['error'],'DEADLINE')
        self.assertTrue(result['direct_reaped']); self.assertTrue(result['settled'])
        with self.assertRaises(ProcessLookupError): os.kill(result['pid'],0)
    def test_cancellation_latched_during_spawn_registration(self):
        cancelled=threading.Event()
        handles=[]
        def spawn(*args,**kwargs):
            child=subprocess.Popen(*args,**kwargs); handles.append(child); cancelled.set(); return child
        try:
            result=self.m.run_owned_child(self.command('import time; time.sleep(10)'),self.root,timeout=1,cancelled=cancelled.is_set,spawn=spawn,term_grace=0.02)
            self.assertEqual(result['error'],'CANCELLED'); self.assertTrue(result['direct_reaped'])
            self.assertIsNotNone(handles[0].returncode)
        finally:
            for child in handles:
                if child.returncode is None: child.kill(); child.wait(timeout=2)
    def test_sigterm_during_cleanup_cannot_complete_child(self):
        signalled=[]
        def exited_leader(_child):
            if not signalled:
                os.kill(os.getpid(),signal.SIGTERM)  # this test process while owned handler is installed
                signalled.append(True)
            return True
        with mock.patch.object(self.m,'_owned_group_only_exited_leader',side_effect=exited_leader):
            result=self.m.run_owned_child(self.command('pass'),self.root,timeout=1,term_grace=0.02)
        self.assertEqual(signalled,[True])
        self.assertEqual(result['status'],'OFFLINE_INCOMPLETE')
        self.assertEqual(result['error'],'CANCELLED')
        self.assertEqual(result['returncode'],0)
        self.assertTrue(result['settled']); self.assertTrue(result['direct_reaped'])
    def test_callback_cancellation_at_direct_wait_cannot_complete_child(self):
        cancelled=threading.Event()
        def spawn(*args,**kwargs):
            child=subprocess.Popen(*args,**kwargs)
            actual_wait=child.wait
            def wait(*a,**kw):
                code=actual_wait(*a,**kw); cancelled.set(); return code
            child.wait=wait
            return child
        with mock.patch.object(self.m,'_owned_group_only_exited_leader',return_value=True):
            result=self.m.run_owned_child(self.command('pass'),self.root,timeout=1,cancelled=cancelled.is_set,spawn=spawn,term_grace=0.02)
        self.assertTrue(cancelled.is_set())
        self.assertEqual((result['status'],result['error']),('OFFLINE_INCOMPLETE','CANCELLED'))
        self.assertEqual(result['returncode'],0)
        self.assertTrue(result['settled']); self.assertTrue(result['direct_reaped'])
    def test_callback_cancellation_during_handler_restoration_cannot_complete_child(self):
        cancelled=threading.Event()
        actual_signal=signal.signal
        old_term=signal.getsignal(signal.SIGTERM)
        def intercept(sig,handler):
            if sig==signal.SIGTERM and handler is old_term:
                cancelled.set()
            return actual_signal(sig,handler)
        with mock.patch.object(self.m,'_owned_group_only_exited_leader',return_value=True), mock.patch.object(self.m.signal,'signal',side_effect=intercept):
            result=self.m.run_owned_child(self.command('pass'),self.root,timeout=1,cancelled=cancelled.is_set,term_grace=0.02)
        self.assertTrue(cancelled.is_set())
        self.assertEqual((result['status'],result['error']),('OFFLINE_INCOMPLETE','CANCELLED'))
        self.assertEqual(result['returncode'],0)
        self.assertTrue(result['settled']); self.assertTrue(result['direct_reaped'])
    def test_cleanup_failure_outweighs_concurrent_callback_cancellation(self):
        cancelled=threading.Event()
        def denied_signal(_pid,_sig):
            cancelled.set(); raise PermissionError('synthetic cleanup denial')
        with mock.patch.object(self.m,'_owned_group_only_exited_leader',return_value=False), mock.patch.object(self.m.os,'killpg',side_effect=denied_signal):
            result=self.m.run_owned_child(self.command('pass'),self.root,timeout=1,cancelled=cancelled.is_set,term_grace=0.02)
        self.assertTrue(cancelled.is_set())
        self.assertEqual(result['status'],'OFFLINE_INCOMPLETE')
        self.assertEqual(result['error'],'CLEANUP_UNRESOLVED')
        self.assertFalse(result['settled']); self.assertTrue(result['direct_reaped'])
    def test_child_environment_drops_credentials_proxy_and_home(self):
        code="import os,sys; sys.exit(0 if set(os.environ)<={'PATH','HOME','TMPDIR','LANG','LC_ALL','TZ','__CF_USER_TEXT_ENCODING'} and 'TOKEN' not in os.environ else 4)"
        with mock.patch.dict(os.environ,{'TOKEN':'synthetic-only','HTTPS_PROXY':'synthetic.invalid'}):
            result=self.m.run_owned_child(self.command(code),self.root,timeout=1,term_grace=0.02)
        self.assertEqual(result['returncode'],0); self.assertTrue(result['settled'])
    def test_cleanup_permission_error_is_incomplete_and_direct_reaped(self):
        with mock.patch.object(self.m.os,'killpg',side_effect=PermissionError), mock.patch.object(self.m,'_owned_group_only_exited_leader',return_value=False):
            result=self.m.run_owned_child(self.command('pass'),self.root,timeout=1,term_grace=0.02)
        self.assertEqual(result['error'],'CLEANUP_UNRESOLVED')
        self.assertFalse(result['settled']); self.assertTrue(result['direct_reaped'])
    def test_descendant_held_stdout_cannot_delay_direct_reap(self):
        marker=self.root/'descendant'
        code="import os,time,pathlib; p=os.fork(); pathlib.Path(%r).write_text(str(p)) if p else time.sleep(10)" % str(marker)
        start=time.monotonic()
        result=self.m.run_owned_child(self.command(code),self.root,timeout=1,term_grace=0.02)
        self.assertTrue(result['direct_reaped']); self.assertTrue(result['settled']); self.assertLess(time.monotonic()-start, 2)
        # Descendant owns no pipes to the coordinator; owned session was signalled before leader reap.

if __name__=='__main__': unittest.main()
