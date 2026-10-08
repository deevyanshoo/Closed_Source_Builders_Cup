"""Offline regressions only: synthetic private files and owned fake processes."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('shipment_demo', ROOT / 'scripts/run_shipment_hero_demo.py')
demo = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(demo)
CATALOG = ROOT / 'scripts/demo/shipment_recovery_catalog.json'


def wait_for(path, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return
        time.sleep(.01)
    raise AssertionError('fake process did not become ready')


class Guards(unittest.TestCase):
    def test_occupied_port_is_rejected_and_listener_is_preserved(self):
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            listener.listen()
            port = listener.getsockname()[1]
            with self.assertRaises(demo.DemoError):
                demo.ensure_ports_free([port])
            self.assertEqual(listener.getsockname()[1], port)

    def test_all_child_env_is_allowlisted_not_inherited(self):
        poisoned = {'GOOGLE_APPLICATION_CREDENTIALS': 'never-read', 'NODE_OPTIONS': '--require poison',
                    'DATABASE_URL_FILE': 'private', 'PATH': 'untrusted', 'UNLISTED_SECRET': 'secret',
                    'GATEWAY_MODE': 'private_machine', 'GATEWAY_DEV_TOKEN_FILE': 'old-private'}
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, poisoned):
            env = demo.base_environment(Path(tmp), [Path('/trusted/bin')])
            for name in poisoned.keys() - {'PATH'}:
                self.assertTrue(name not in env, 'unallowlisted child variable: '+name)
            self.assertEqual(env['PATH'], '/trusted/bin:/usr/bin:/bin')
            self.assertEqual(env['HOME'], tmp)

    def test_private_file_requires_regular_owned_restricted_nonempty_nonsymlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            good = root / 'synthetic-identity'
            good.write_text('synthetic only')
            good.chmod(0o600)
            self.assertEqual(demo.private_file(good), good)
            good.chmod(0o644)
            with self.assertRaises(demo.DemoError):
                demo.private_file(good)
            good.chmod(0o600)
            link = root / 'symlink'
            link.symlink_to(good)
            with self.assertRaises(demo.DemoError):
                demo.private_file(link)
            good.write_bytes(b'')
            with self.assertRaises(demo.DemoError):
                demo.private_file(good)
            with self.assertRaises(demo.DemoError):
                demo.private_file(root)

    def test_dotenv_paths_reject_by_name_even_dangling_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            web = root / 'apps/web'
            web.mkdir(parents=True)
            demo.check_dotenv(root)
            (web / '.env.local').symlink_to(root / 'absent-private')
            with self.assertRaises(demo.DemoError):
                demo.check_dotenv(root)


class Lifecycle(unittest.TestCase):
    def test_cleanup_kills_owned_stubborn_group_without_touching_other_process(self):
        # Both fake generations ignore TERM; an inherited stdout would hang pipe readers.
        with tempfile.TemporaryDirectory() as tmp:
            ready, heartbeat = Path(tmp)/'ready', Path(tmp)/'heartbeat'
            grandchild = 'import signal,time,pathlib; signal.signal(signal.SIGTERM,signal.SIG_IGN); p=pathlib.Path('+repr(str(heartbeat))+');\nwhile True: p.write_text(str(time.monotonic())); time.sleep(.01)'
            parent = 'import subprocess,signal,time,pathlib,sys; signal.signal(signal.SIGTERM,signal.SIG_IGN); subprocess.Popen([sys.executable,"-c",'+repr(grandchild)+']); pathlib.Path('+repr(str(ready))+').write_text("ready"); time.sleep(60)'
            outsider = subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'], start_new_session=True,
                                        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            owner = demo.OwnedProcesses(grace=.1)
            child = owner.spawn([sys.executable,'-c',parent], Path(tmp), demo.base_environment(Path(tmp), []))
            try:
                wait_for(ready)
                wait_for(heartbeat)
                start=time.monotonic()
                owner.close()
                self.assertLess(time.monotonic()-start, 2)
                self.assertIsNotNone(child.poll(), 'owned direct child was not killed/reaped')
                time.sleep(.08)
                stamp=heartbeat.read_text()
                time.sleep(.08)
                self.assertEqual(heartbeat.read_text(), stamp, 'owned grandchild still executes')
                self.assertIsNone(outsider.poll(), 'unrelated process was killed')
                owner.close()  # idempotent; no additional process signals
            finally:
                for process in (child, outsider):
                    try: os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError: pass
                    process.wait(timeout=2)

    def test_startup_timeout_is_bounded_and_cleans_fake_child(self):
        with tempfile.TemporaryDirectory() as tmp:
            owner=demo.OwnedProcesses(grace=.1)
            child=owner.spawn([sys.executable,'-c','import time; time.sleep(60)'], Path(tmp), demo.base_environment(Path(tmp), []))
            with socket.socket() as reserved:
                reserved.bind(('127.0.0.1', 0))  # bound, not listening
                port=reserved.getsockname()[1]
                try:
                    start=time.monotonic()
                    with self.assertRaises(demo.DemoError):
                        owner.wait_started([port], timeout=.15)
                    self.assertLess(time.monotonic()-start, 2)
                finally:
                    owner.close()
                    if child.poll() is None:
                        os.killpg(child.pid, signal.SIGKILL)
                        child.wait(timeout=2)
            self.assertIsNotNone(child.poll())

    def test_nonzero_build_and_partial_spawn_failure_leave_no_owned_child(self):
        with tempfile.TemporaryDirectory() as tmp:
            with demo.OwnedProcesses(grace=.1) as owner:
                with self.assertRaises(demo.DemoError):
                    owner.run([sys.executable,'-c','raise SystemExit(3)'], Path(tmp), demo.base_environment(Path(tmp), []), timeout=1)
                child=owner.spawn([sys.executable,'-c','import time; time.sleep(60)'], Path(tmp), demo.base_environment(Path(tmp), []))
                with self.assertRaises(demo.DemoError):
                    owner.spawn(['/nonexistent/offline-fake'], Path(tmp), {})
            self.assertIsNotNone(child.poll())

    def test_signal_between_popen_and_registration_cannot_orphan_child(self):
        # Inject the signal in the real Popen return window; only an owned fake runs.
        created=[]
        real_popen=subprocess.Popen
        with tempfile.TemporaryDirectory() as tmp:
            def interrupting_popen(*args,**kwargs):
                child=real_popen(*args,**kwargs)
                created.append(child)
                os.kill(os.getpid(),signal.SIGTERM)
                return child
            def fake_launch(args, stop_event=None):
                with demo.OwnedProcesses(grace=.1) as owner:
                    owner.spawn([sys.executable,'-c','import time; time.sleep(60)'],Path(tmp),demo.base_environment(Path(tmp),[]))
                    if stop_event is not None and stop_event.is_set():
                        raise KeyboardInterrupt
            argv=['--repo',tmp,'--python',sys.executable,'--go',sys.executable,'--node',sys.executable,
                  '--git',sys.executable,'--go-cache',tmp,'--go-mod-cache',tmp,
                  '--identities-file',tmp+'/synthetic-id','--proxy-token-file',tmp+'/synthetic-proxy',
                  '--reviewed-head','b2e6b961f79c4698d162b93a60cd2d7294f8df7d']
            try:
                with patch.object(demo,'launch',fake_launch),patch.object(demo.subprocess,'Popen',interrupting_popen):
                    self.assertEqual(demo.main(argv),130)
                self.assertEqual(len(created),1)
                self.assertIsNotNone(created[0].poll(),'signal window left an unregistered owned child alive')
            finally:
                for child in created:
                    if child.poll() is None:
                        os.killpg(child.pid,signal.SIGKILL)
                    child.wait(timeout=2)

    def test_command_deadline_reaps_stubborn_fake_child(self):
        with tempfile.TemporaryDirectory() as tmp:
            with demo.OwnedProcesses(grace=.1) as owner:
                start=time.monotonic()
                with self.assertRaises(demo.DemoError):
                    owner.run([sys.executable,'-c','import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(60)'],
                              Path(tmp),demo.base_environment(Path(tmp),[]),timeout=.15)
                self.assertLess(time.monotonic()-start,2)
                self.assertEqual(owner.children,[])

    def test_exited_parent_does_not_leave_inherited_output_grandchild_running(self):
        with tempfile.TemporaryDirectory() as tmp:
            heartbeat=Path(tmp)/'heartbeat'
            grandchild='import signal,time,pathlib; signal.signal(signal.SIGTERM,signal.SIG_IGN); p=pathlib.Path('+repr(str(heartbeat))+');\nwhile True: p.write_text(str(time.monotonic())); time.sleep(.01)'
            parent='import subprocess,pathlib,time,sys; subprocess.Popen([sys.executable,"-c",'+repr(grandchild)+']); p=pathlib.Path('+repr(str(heartbeat))+');\nwhile not p.exists(): time.sleep(.01)'
            with demo.OwnedProcesses(grace=.1) as owner:
                owner.run([sys.executable,'-c',parent],Path(tmp),demo.base_environment(Path(tmp),[]),timeout=2)
                stamp=heartbeat.read_text()
                time.sleep(.08)
                self.assertEqual(heartbeat.read_text(),stamp)


class FixtureAndRuntime(unittest.TestCase):
    def test_exact_accepted_catalog_and_three_profiles(self):
        self.assertTrue(CATALOG.is_file(), 'accepted catalog has not been written')
        original=CATALOG.read_bytes()
        self.assertEqual(hashlib.sha256(original).hexdigest(), '5b0c076ffdb8ff2dd5d7a0e33a6a3cca03ddb990cb12d97ce848762a7af89d82')
        hero=demo.load_catalog(CATALOG, 'hero')
        self.assertEqual(len(json.loads(hero)['contexts']), 4)
        self.assertEqual(json.loads(demo.load_catalog(CATALOG, 'empty')), {'catalog_version':'1','contexts':[]})
        self.assertIsNone(demo.load_catalog(CATALOG, 'disabled'))
        with self.assertRaises(demo.DemoError): demo.load_catalog(CATALOG, 'invented')
        with tempfile.TemporaryDirectory() as tmp:
            drift=Path(tmp)/'catalog'
            drift.write_bytes(original.replace(b'2.123456789123456789',b'2.123456789123456788',1))
            with self.assertRaises(demo.DemoError): demo.load_catalog(drift, 'hero')
            drift.write_bytes(original+b'\n')
            with self.assertRaises(demo.DemoError): demo.load_catalog(drift, 'empty')

    def test_runtime_permissions_manifest_and_cleanup_all_profiles(self):
        for profile,count in [('hero',4),('empty',0),('disabled',0)]:
            with self.subTest(profile=profile):
                with demo.runtime_files(CATALOG, profile, {'head':'reviewed','files':{'src':'hash'}}) as (run,catalog,manifest):
                    self.assertEqual(run.stat().st_mode & 0o777, 0o700)
                    self.assertEqual(manifest.stat().st_mode & 0o777, 0o600)
                    data=json.loads(manifest.read_bytes())
                    self.assertEqual(data['profile'],profile)
                    self.assertEqual(data['source']['head'],'reviewed')
                    self.assertNotIn('token',data)
                    self.assertEqual(len(data['cases']),count)
                    if profile=='disabled': self.assertIsNone(catalog)
                    else:
                        self.assertEqual(catalog.stat().st_mode & 0o777,0o600)
                        self.assertEqual(hashlib.sha256(catalog.read_bytes()).hexdigest(),data['catalog_sha256'])
                self.assertFalse(run.exists())

    def test_reference_flags_disabled_and_explicit_runtime_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            command=demo.gateway_command(root/'gateway',root/'python',root,root/'catalog')
            self.assertEqual(command,[str(root/'gateway'),'-shipment-local-contexts',str(root/'catalog'),'-shipment-local-python',str(root/'python'),'-shipment-local-source',str(root/'procurement-engine/src')])
            self.assertEqual(demo.gateway_command(root/'gateway',root/'python',root,None),[str(root/'gateway')])
            env=demo.gateway_environment(root,[root/'tools'],root,root/'synthetic-auth')
            self.assertEqual(env['GATEWAY_MODE'],'local')
            self.assertEqual(env['GATEWAY_ADDR'],'127.0.0.1:8844')
            self.assertEqual(env['DEV_ALLOWED_ORIGIN'],'http://127.0.0.1:5344')
            self.assertEqual(env['PYTHON_SERVICE_URL'],'http://127.0.0.1:9')
            self.assertTrue('DATABASE_URL_FILE' not in env)
            node=demo.web_environment(root,[root/'tools'],root/'synthetic-proxy')
            self.assertEqual(node['GATEWAY_URL'],'http://127.0.0.1:8844')
            go=demo.build_environment(root,[root/'tools'],root,root)
            for key,value in {'GOPROXY':'off','GOSUMDB':'off','GOTOOLCHAIN':'local','GOENV':'off','GOFLAGS':'-mod=readonly','CGO_ENABLED':'0'}.items():
                self.assertEqual(go[key],value)

    def test_source_hash_guard_rejects_changed_and_symlinked_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            file=root/'source.txt'
            file.write_bytes(b'abc')
            entries=[('100644','f2ba8f84ab5c1bce84a7b441cb1959cfc7093b7f','source.txt')]
            hashes=demo.verify_source_files(root,entries)
            self.assertEqual(hashes['source.txt'],'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad')
            file.write_bytes(b'changed')
            with self.assertRaises(demo.DemoError): demo.verify_source_files(root,entries)
            file.unlink()
            file.symlink_to(root/'absent')
            with self.assertRaises(demo.DemoError): demo.verify_source_files(root,entries)

    def test_missing_relative_tool_and_invalid_timeout_fail_before_children(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(demo.DemoError): demo.tool_path(Path('relative'))
            with self.assertRaises(demo.DemoError): demo.tool_path(Path(tmp)/'missing')
            with self.assertRaises(demo.DemoError): demo.OwnedProcesses(grace=0)
            with self.assertRaises(demo.DemoError): demo.OwnedProcesses(grace=100)


class OfflineOrchestration(unittest.TestCase):
    def test_source_inventory_is_clean_head_bound_and_detects_drift(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp).resolve()
            repo=root/'repo'; repo.mkdir()
            source=repo/'source.txt'; source.write_bytes(b'abc')
            runtime=root/'runtime'; runtime.mkdir()
            git=root/'fake-git'
            head='b2e6b961f79c4698d162b93a60cd2d7294f8df7d'
            git.write_text('#!'+sys.executable+'\nimport sys\nargs=sys.argv\nif "ls-tree" in args: sys.stdout.buffer.write(b"100644 blob f2ba8f84ab5c1bce84a7b441cb1959cfc7093b7f\\tsource.txt\\x00")\nelif "rev-parse" in args: print("'+head+'")\n')
            git.chmod(0o700)
            self.assertEqual(demo.verify_source(repo,git,head,runtime)['files']['source.txt'],
                             'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad')
            with self.assertRaises(demo.DemoError): demo.verify_source(repo,git,'0'*40,runtime)
            source.write_text('drift')
            with self.assertRaises(demo.DemoError): demo.verify_source(repo,git,head,runtime)

    def test_full_fake_build_spawn_startup_failure_reaps_and_removes_runtime(self):
        # No Go, Vite, Python adapter, HTTP or private identity consumption occurs.
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp).resolve(); repo=root/'repo'; tools=root/'tools'; tools.mkdir()
            (repo/'services/gateway').mkdir(parents=True)
            files={'scripts/run_shipment_hero_demo.py':(ROOT/'scripts/run_shipment_hero_demo.py').read_bytes(),
                   'scripts/demo/shipment_recovery_catalog.json':CATALOG.read_bytes(),
                   'apps/web/src/main.tsx':b'// synthetic pending UI fixture',
                   'apps/web/node_modules/vite/bin/vite.js':b'// not executed'}
            for name,body in files.items():
                file=repo/name; file.parent.mkdir(parents=True,exist_ok=True); file.write_bytes(body)
            head='b2e6b961f79c4698d162b93a60cd2d7294f8df7d'
            rows=b''.join(b'100644 blob '+hashlib.sha1(b'blob '+str(len(body)).encode()+b'\0'+body).hexdigest().encode()+b'\t'+name.encode()+b'\0' for name,body in files.items())
            def executable(name,body):
                file=tools/name; file.write_text('#!'+sys.executable+'\n'+body); file.chmod(0o700); return file
            git=executable('git','import sys\nargs=sys.argv\nif "ls-tree" in args: sys.stdout.buffer.write('+repr(rows)+')\nelif "rev-parse" in args: print("'+head+'")\n')
            go_trace,node_trace=root/'go-trace',root/'node-trace'
            fake_gateway='#!'+sys.executable+'\nimport os,pathlib,json,time,sys\np=pathlib.Path('+repr(str(go_trace))+')\np.write_text(json.dumps({"pid":os.getpid(),"env_keys":list(os.environ),"argv":sys.argv}))\ntime.sleep(60)\n'
            go=executable('go','import pathlib,sys\np=pathlib.Path(sys.argv[sys.argv.index("-o")+1]); p.write_text('+repr(fake_gateway)+'); p.chmod(0o700)\n')
            node=executable('node','import os,pathlib,json,time,sys\npathlib.Path('+repr(str(node_trace))+').write_text(json.dumps({"pid":os.getpid(),"env_keys":list(os.environ),"argv":sys.argv}))\ntime.sleep(60)\n')
            cache=root/'cache'; cache.mkdir()
            identity,proxy=root/'synthetic-identity',root/'synthetic-proxy'
            for file in (identity,proxy): file.write_text('synthetic only; never consumed'); file.chmod(0o600)
            args=demo.parser().parse_args(['--repo',str(repo),'--python',sys.executable,'--go',str(go),'--node',str(node),
                 '--git',str(git),'--go-cache',str(cache),'--go-mod-cache',str(cache),
                 '--identities-file',str(identity),'--proxy-token-file',str(proxy),'--reviewed-head',head])
            observed=[]
            def fail_readiness(owner,ports,timeout):
                wait_for(go_trace); wait_for(node_trace)
                observed.extend(owner.children)
                raise demo.DemoError('synthetic readiness timeout')
            with patch.object(demo,'__file__',str(repo/'scripts/run_shipment_hero_demo.py')), \
                 patch.object(demo,'ensure_ports_free'), \
                 patch.object(demo.OwnedProcesses,'wait_started',fail_readiness), \
                 patch.dict(os.environ,{'UNLISTED_SECRET':'must-not-pass','NODE_OPTIONS':'poison'}):
                with self.assertRaisesRegex(demo.DemoError,'synthetic readiness timeout'): demo.launch(args)
            self.assertEqual(len(observed),2)
            for child in observed: self.assertIsNotNone(child.poll())
            go_data,node_data=json.loads(go_trace.read_text()),json.loads(node_trace.read_text())
            for data in (go_data,node_data):
                self.assertTrue('UNLISTED_SECRET' not in data['env_keys'])
                self.assertTrue('NODE_OPTIONS' not in data['env_keys'])
            runtime_catalog=Path(go_data['argv'][go_data['argv'].index('-shipment-local-contexts')+1])
            self.assertFalse(runtime_catalog.parent.exists())
            self.assertEqual(node_data['argv'][-5:],['--host','127.0.0.1','--port','5344','--strictPort'])
            self.assertEqual(identity.read_text(),'synthetic only; never consumed')
            self.assertEqual(proxy.read_text(),'synthetic only; never consumed')


if __name__ == '__main__':
    unittest.main()
