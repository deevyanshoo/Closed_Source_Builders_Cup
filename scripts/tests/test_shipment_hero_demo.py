"""Offline regressions only: synthetic private files and owned fake processes."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import resource
import threading
from contextlib import contextmanager
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
        self._full_fake_build('startup')

    def test_gateway_exit_before_ack_never_starts_proxy(self):
        self._full_fake_build('before-ack')

    def test_gateway_exit_after_ack_never_starts_proxy(self):
        self._full_fake_build('after-ack')

    def test_gateway_exit_just_before_proxy_registration_keeps_endpoint_owned(self):
        self._full_fake_build('before-proxy')

    def test_gateway_exit_after_proxy_registration_reaps_proxy_before_release(self):
        self._full_fake_build('after-proxy')

    def test_cancellation_after_ack_does_not_spawn_proxy(self):
        self._full_fake_build('cancel-after-ack')

    def test_cancellation_during_proxy_creation_registers_then_reaps_child(self):
        self._full_fake_build('cancel-proxy-creation')

    def _full_fake_build(self,phase):
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
            fake_gateway='#!'+sys.executable+'\nimport os,pathlib,json,time,sys\np=pathlib.Path('+repr(str(go_trace))+')\np.write_text(json.dumps({"pid":os.getpid(),"env_keys":list(os.environ),"argv":sys.argv}))\nfd=int(sys.argv[sys.argv.index("-local-demo-ready-fd")+1]); os.write(fd,b"local-demo-listener-v1 127.0.0.1:8844\\n"); os.close(fd)\ntime.sleep(60)\n'
            if phase=='before-ack': fake_gateway=fake_gateway[:fake_gateway.index('fd=int(')]+'os._exit(0)\n'
            go=executable('go','import pathlib,sys\np=pathlib.Path(sys.argv[sys.argv.index("-o")+1]); p.write_text('+repr(fake_gateway)+'); p.chmod(0o700)\n')
            node=executable('node','import os,pathlib,json,time,sys\npathlib.Path('+repr(str(node_trace))+').write_text(json.dumps({"pid":os.getpid(),"env_keys":list(os.environ),"argv":sys.argv}))\ntime.sleep(60)\n')
            cache=root/'cache'; cache.mkdir()
            identity,proxy=root/'synthetic-identity',root/'synthetic-proxy'
            for file in (identity,proxy): file.write_text('synthetic only; never consumed'); file.chmod(0o600)
            args=demo.parser().parse_args(['--repo',str(repo),'--python',sys.executable,'--go',str(go),'--node',str(node),
                 '--git',str(git),'--go-cache',str(cache),'--go-mod-cache',str(cache),
                 '--identities-file',str(identity),'--proxy-token-file',str(proxy),'--reviewed-head',head])
            observed=[]; endpoint=[];stop=threading.Event()
            actual_spawn=demo.OwnedProcesses.spawn;actual_ack=demo.wait_gateway_ack;actual_wait=demo.OwnedProcesses.wait_started;actual_popen=demo.subprocess.Popen
            def popen(argv,**kwargs):
                child=actual_popen(argv,**kwargs)
                if phase=='cancel-proxy-creation' and argv[0]==str(node):
                    observed.append(child);stop.set()
                return child
            def assert_held(fd):
                with socket.fromfd(fd,socket.AF_INET,socket.SOCK_STREAM) as retained:
                    address=retained.getsockname();endpoint[:]=[address]
                with socket.socket() as outsider:
                    with self.assertRaises(OSError): outsider.bind(address)
            def spawn(owner,argv,cwd,env,**kwargs):
                is_proxy=argv[0]==str(node)
                is_gateway='-local-demo-listener-fd' in argv
                if is_proxy and phase=='before-proxy':
                    demo.OwnedProcesses._signal(owner.children[0],signal.SIGTERM);owner.children[0].wait(timeout=1)
                    assert_held(kwargs['pass_fds'][0])
                child=actual_spawn(owner,argv,cwd,env,**kwargs)
                if is_gateway or is_proxy: observed.append(child)
                return child
            def ack(owner,child,fd,timeout):
                actual_ack(owner,child,fd,timeout)
                if phase=='cancel-after-ack': stop.set()
                if phase=='after-ack':
                    demo.OwnedProcesses._signal(child,signal.SIGTERM);child.wait(timeout=1)
            def fail_readiness(owner,ports,timeout):
                wait_for(go_trace); wait_for(node_trace)
                if phase=='after-proxy':
                    data=json.loads(go_trace.read_text());assert_held(int(data['argv'][data['argv'].index('-local-demo-listener-fd')+1]))
                    demo.OwnedProcesses._signal(owner.children[0],signal.SIGTERM);owner.children[0].wait(timeout=1)
                if phase in ('before-proxy','after-proxy'): actual_wait(owner,[],.1)
                raise demo.DemoError('synthetic readiness timeout')
            real_reserve=demo.reserve_listener
            with patch.object(demo,'__file__',str(repo/'scripts/run_shipment_hero_demo.py')), \
                 patch.object(demo,'ensure_ports_free'), \
                 patch.object(demo,'reserve_listener',lambda: real_reserve(0)), \
                 patch.object(demo,'wait_gateway_ack',ack), \
                 patch.object(demo.subprocess,'Popen',popen), \
                 patch.object(demo.OwnedProcesses,'spawn',spawn), \
                 patch.object(demo.OwnedProcesses,'wait_started',fail_readiness), \
                 patch.dict(os.environ,{'UNLISTED_SECRET':'must-not-pass','NODE_OPTIONS':'poison'}):
                with self.assertRaises(KeyboardInterrupt if phase.startswith('cancel-') else demo.DemoError): demo.launch(args,stop)
            self.assertEqual(len(observed),1 if phase in ('before-ack','after-ack','cancel-after-ack') else 2)
            for child in observed: self.assertIsNotNone(child.poll())
            go_data=json.loads(go_trace.read_text());all_data=[go_data]
            if phase in ('before-ack','after-ack','cancel-after-ack'): self.assertFalse(node_trace.exists())
            elif phase!='cancel-proxy-creation' or node_trace.exists():
                node_data=json.loads(node_trace.read_text());all_data.append(node_data)
                self.assertEqual(node_data['argv'][-5:],['--host','127.0.0.1','--port','5344','--strictPort'])
            for data in all_data:
                self.assertTrue('UNLISTED_SECRET' not in data['env_keys'])
                self.assertTrue('NODE_OPTIONS' not in data['env_keys'])
            runtime_catalog=Path(go_data['argv'][go_data['argv'].index('-shipment-local-contexts')+1])
            self.assertFalse(runtime_catalog.parent.exists())
            self.assertEqual(identity.read_text(),'synthetic only; never consumed')
            self.assertEqual(proxy.read_text(),'synthetic only; never consumed')
            if endpoint:
                with socket.socket() as released: released.bind(endpoint[0])


NODE = Path('/opt/homebrew/bin/node')
GIT_ARGS = ['-c','core.fsmonitor=false','-c','core.hooksPath=/dev/null','--no-optional-locks','ls-tree','-rz','HEAD']

class NativeRepairProbe(unittest.TestCase):
    def test_installed_node_retains_exclusive_descriptor_with_fake_entrypoint(self):
        # Mandatory native support preflight. No Vite, gateway or auth is used.
        bootstrap = "const fs=require('node:fs'); const {pathToFileURL}=require('node:url'); const [fd,entry,...args]=process.argv.slice(1); if(!fs.fstatSync(Number(fd)).isSocket())process.exit(2); process.argv=[process.execPath,entry,...args]; import(pathToFileURL(entry).href);"
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp).resolve(); ready=root/'ready'; entry=root/'fake.mjs'
            entry.write_text('import fs from "node:fs"; fs.writeFileSync('+json.dumps(str(ready))+',JSON.stringify(process.argv.slice(2))); setInterval(()=>{},1000);')
            reserve=socket.socket(socket.AF_INET,socket.SOCK_STREAM)
            reserve.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,0)
            reserve.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEPORT,0)
            reserve.bind(('127.0.0.1',0)); reserve.listen()
            port=reserve.getsockname()[1]
            child=subprocess.Popen([str(NODE),'-e',bootstrap,'--',str(reserve.fileno()),str(entry),'fake-argument'],
                pass_fds=(reserve.fileno(),),start_new_session=True,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                env=demo.base_environment(root,[NODE.parent]))
            try:
                wait_for(ready)
                self.assertEqual(json.loads(ready.read_text()),['fake-argument'])
                reserve.close()
                with socket.socket() as outsider:
                    with self.assertRaises(OSError): outsider.bind(('127.0.0.1',port))
            finally:
                reserve.close()
                os.killpg(child.pid,signal.SIGTERM); child.wait(timeout=2)
            with socket.socket() as after: after.bind(('127.0.0.1',port))


class RuntimeRepair(unittest.TestCase):
    def test_production_bootstrap_retains_socket_and_unchanged_entry_arguments(self):
        with tempfile.TemporaryDirectory() as tmp, socket.socket() as reservation:
            root=Path(tmp).resolve(); ready=root/'ready'; entry=root/'fake.mjs'
            entry.write_text('import fs from "node:fs"; fs.writeFileSync('+json.dumps(str(ready))+',JSON.stringify(process.argv.slice(2))); setInterval(()=>{},1000);')
            reservation.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,0)
            reservation.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEPORT,0)
            reservation.bind(('127.0.0.1',0)); reservation.listen(); port=reservation.getsockname()[1]
            with demo.OwnedProcesses(grace=.05) as owner:
                child=owner.spawn(demo.node_command(NODE,entry,reservation.fileno()),root,
                                  demo.base_environment(root,[NODE.parent]),pass_fds=(reservation.fileno(),))
                wait_for(ready); reservation.close()
                self.assertEqual(json.loads(ready.read_text()),['--host','127.0.0.1','--port','5344','--strictPort'])
                with socket.socket() as outsider:
                    with self.assertRaises(OSError): outsider.bind(('127.0.0.1',port))
            self.assertIsNotNone(child.poll())
            with socket.socket() as released: released.bind(('127.0.0.1',port))

    def test_foreign_listener_is_not_gateway_readiness_authority(self):
        with tempfile.TemporaryDirectory() as tmp, socket.socket() as foreign:
            foreign.bind(('127.0.0.1',0)); foreign.listen(); port=foreign.getsockname()[1]
            with demo.OwnedProcesses(grace=.1) as owner:
                owner.spawn([sys.executable,'-c','import time; time.sleep(60)'],Path(tmp),demo.base_environment(Path(tmp),[]))
                if hasattr(demo,'reserve_listener'):
                    with self.assertRaises(demo.DemoError):
                        with demo.reserve_listener(port): pass
                else:
                    # V1 considers connectivity to the foreign listener proof.
                    with self.assertRaises(demo.DemoError): owner.wait_started([port],.2)
            self.assertEqual(foreign.getsockname()[1],port)

    def test_capture_overrun_is_kernel_bounded_before_child_completion(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp).resolve(); fake=root/'git'
            fake.write_text('#!'+sys.executable+'\nimport os\nos.write(1,b"x"*(3<<20))\n'); fake.chmod(0o700)
            sizes=[]; original=demo.tempfile.TemporaryFile
            @contextmanager
            def observe(**kwargs):
                with original(**kwargs) as f:
                    try: yield f
                    finally: sizes.append(os.fstat(f.fileno()).st_size)
            before=resource.getrlimit(resource.RLIMIT_FSIZE)
            with patch.object(demo.tempfile,'TemporaryFile',observe):
                with self.assertRaises(demo.DemoError): demo.capture_command([str(fake),*GIT_ARGS],root,root,[root])
            self.assertTrue(sizes and max(sizes)<=2<<20,'capture exceeded2MiB before rejection')
            self.assertEqual(resource.getrlimit(resource.RLIMIT_FSIZE),before)


class CaptureRepairEdges(unittest.TestCase):
    def capture_fixture(self, body):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp).resolve(); fake=root/'git'
            fake.write_text('#!'+sys.executable+'\n'+body); fake.chmod(0o700)
            sizes=[]; original=demo.tempfile.TemporaryFile
            @contextmanager
            def observe(**kwargs):
                with original(**kwargs) as output:
                    try: yield output
                    finally: sizes.append(os.fstat(output.fileno()).st_size)
            before=resource.getrlimit(resource.RLIMIT_FSIZE)
            with patch.object(demo.tempfile,'TemporaryFile',observe):
                try:
                    result=demo.capture_command([str(fake),*GIT_ARGS],root,root,[root])
                except demo.DemoError:
                    result=None
            self.assertEqual(resource.getrlimit(resource.RLIMIT_FSIZE),before)
            self.assertTrue(sizes and max(sizes)<=demo.CAPTURE_LIMIT)
            return result,sizes[-1]

    def test_small_capture_survives_exactly(self):
        result,size=self.capture_fixture('import os\nos.write(1,b"small\\x00inventory")\n')
        self.assertEqual(result,b'small\x00inventory'); self.assertEqual(size,len(result))

    def test_exact_cap_success_exit_is_rejected(self):
        result,size=self.capture_fixture('import os\nos.write(1,b"x"*(2<<20))\n')
        self.assertIsNone(result); self.assertEqual(size,demo.CAPTURE_LIMIT)

    def test_repeated_writes_and_caught_efbig_remain_bounded(self):
        result,size=self.capture_fixture('import os,signal\nsignal.signal(signal.SIGXFSZ,signal.SIG_IGN)\ntry:\n for _ in range(768): os.write(1,b"x"*4096)\nexcept OSError: pass\n')
        self.assertIsNone(result); self.assertEqual(size,demo.CAPTURE_LIMIT)

    def test_child_cannot_raise_hard_limit(self):
        result,size=self.capture_fixture('import os,resource\ntry: resource.setrlimit(resource.RLIMIT_FSIZE,(3<<20,3<<20))\nexcept ValueError: os.write(1,b"hard-limit-held")\n')
        self.assertEqual(result,b'hard-limit-held'); self.assertEqual(size,len(result))

    def test_internal_allowlist_rejects_arbitrary_commands(self):
        self.assertFalse(demo.capture_argv_valid([sys.executable,'-c','print(1)']))
        self.assertFalse(demo.capture_argv_valid([sys.executable,*GIT_ARGS,'extra']))
        self.assertEqual(demo.capture_exec([sys.executable,'-c','print(1)']),70)

    def test_failed_limit_setup_does_not_exec_or_change_parent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp).resolve(); fake=root/'git'; marker=root/'executed'
            fake.write_text('#!'+sys.executable+'\nfrom pathlib import Path\nPath('+repr(str(marker))+').touch()\n'); fake.chmod(0o700)
            loader='import importlib.util; s=importlib.util.spec_from_file_location("demo",'+repr(str(ROOT/'scripts/run_shipment_hero_demo.py'))+'); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); m.resource.setrlimit=lambda *_: (_ for _ in ()).throw(ValueError()); raise SystemExit(m.capture_exec('+repr([str(fake),*GIT_ARGS])+'))'
            before=resource.getrlimit(resource.RLIMIT_FSIZE)
            with tempfile.TemporaryFile(dir=root) as output, demo.OwnedProcesses(grace=.05) as owner:
                with self.assertRaises(demo.DemoError): owner.run([sys.executable,'-B','-I','-S','-c',loader],root,demo.base_environment(root,[]),1,stdout=output)
            self.assertFalse(marker.exists()); self.assertEqual(resource.getrlimit(resource.RLIMIT_FSIZE),before)

    def test_capture_timeout_reaps_own_child(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp).resolve(); fake=root/'git'
            fake.write_text('#!'+sys.executable+'\nimport time\ntime.sleep(60)\n'); fake.chmod(0o700)
            argv=[sys.executable,'-I','-S',str(ROOT/'scripts/run_shipment_hero_demo.py'),'--_capture-exec',str(fake),*GIT_ARGS]
            before=resource.getrlimit(resource.RLIMIT_FSIZE)
            with tempfile.TemporaryFile(dir=root) as output, demo.OwnedProcesses(grace=.05) as owner:
                with self.assertRaisesRegex(demo.DemoError,'timeout'): owner.run(argv,root,demo.base_environment(root,[]),.1,stdout=output)
                self.assertEqual(owner.children,[])
            self.assertEqual(resource.getrlimit(resource.RLIMIT_FSIZE),before)

    def test_capture_cancellation_reaps_registered_child(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp).resolve(); fake=root/'git'; ready=root/'ready'
            fake.write_text('#!'+sys.executable+'\nimport time,pathlib\npathlib.Path('+repr(str(ready))+').touch()\ntime.sleep(60)\n'); fake.chmod(0o700)
            stop=threading.Event(); observer_errors=[]
            def cancel():
                try: wait_for(ready); stop.set()
                except Exception as err: observer_errors.append(str(type(err)))
            thread=threading.Thread(target=cancel); thread.start()
            before=resource.getrlimit(resource.RLIMIT_FSIZE)
            try:
                with self.assertRaises(KeyboardInterrupt): demo.capture_command([str(fake),*GIT_ARGS],root,root,[root],stop)
            finally: thread.join(timeout=4)
            self.assertFalse(thread.is_alive()); self.assertEqual(observer_errors,[])
            self.assertEqual(resource.getrlimit(resource.RLIMIT_FSIZE),before)

    def test_exited_writer_with_descendant_holding_file_has_no_eof_wait(self):
        result,size=self.capture_fixture('import os,time\nif os.fork()==0: time.sleep(60); os._exit(0)\nos.write(1,b"parent-done")\n')
        self.assertEqual(result,b'parent-done'); self.assertEqual(size,len(result))


class DarwinListenerAmendment(unittest.TestCase):
    def test_native_unbound_bound_listen_and_connected_roles(self):
        with socket.socket(socket.AF_INET,socket.SOCK_STREAM) as listener:
            for stage in ('unbound','bound'):
                if stage=='bound': listener.bind(('127.0.0.1',0))
                info=listener.getsockopt(socket.IPPROTO_TCP,0x106,256)
                self.assertEqual((len(info),info[0]),(112,0))
                with self.assertRaises(demo.DemoError): demo.verify_native_listening(listener)
            listener.listen(); listener.settimeout(1)
            info=listener.getsockopt(socket.IPPROTO_TCP,0x106,256)
            self.assertEqual((len(info),info[0]),(112,1)); demo.verify_native_listening(listener)
            with socket.create_connection(listener.getsockname(),timeout=1) as client:
                accepted,_=listener.accept()
                with accepted:
                    for connected in (accepted,client):
                        info=connected.getsockopt(socket.IPPROTO_TCP,0x106,256)
                        self.assertEqual((len(info),info[0]),(112,4))
                        with self.assertRaises(demo.DemoError): demo.verify_native_listening(connected)

    def test_wrong_family_type_closed_socket_and_option_error_fail_closed(self):
        for family,kind in ((socket.AF_UNIX,socket.SOCK_STREAM),(socket.AF_INET,socket.SOCK_DGRAM)):
            with socket.socket(family,kind) as wrong:
                with self.assertRaises(demo.DemoError): demo.verify_native_listening(wrong)
        closed=socket.socket(); closed.close()
        with self.assertRaises(demo.DemoError): demo.verify_native_listening(closed)
        class BrokenOption:
            family=socket.AF_INET
            def getsockopt(self,level,option,*args):
                if level==socket.SOL_SOCKET and option==socket.SO_TYPE: return socket.SOCK_STREAM
                raise OSError(42,'synthetic unsupported option')
        with self.assertRaises(demo.DemoError): demo.verify_native_listening(BrokenOption())

    def test_short_long_empty_unknown_and_nonlisten_payloads_rejected(self):
        for length,state in ((0,0),(1,1),(111,1),(113,1),(256,1),(112,0),(112,4),(112,255)):
            info=(bytes([state])+bytes(length-1)) if length else b''
            with self.subTest(length=length,state=state):
                with self.assertRaises(demo.DemoError): demo.validate_darwin_listener_info(info)
        demo.validate_darwin_listener_info(bytes([1])+bytes(111))

    def test_native_reservation_accepts_exact_listening_state(self):
        try:
            with demo.reserve_listener(0) as listener:
                port=listener.getsockname()[1]
                with socket.socket() as outsider:
                    with self.assertRaises(OSError): outsider.bind(('127.0.0.1',port))
        except demo.DemoError:
            self.fail('native listening reservation rejected')


class ListenerLifecycleRepair(unittest.TestCase):
    def test_pending_synthetic_connection_cannot_transfer_endpoint_after_gateway_exit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp).resolve();ready=root/'ready';entry=root/'fake.mjs'
            entry.write_text('import fs from "node:fs";fs.writeFileSync('+json.dumps(str(ready))+',"ready");setInterval(()=>{},1000);')
            with demo.reserve_listener(0) as listener:
                port=listener.getsockname()[1]
                with demo.OwnedProcesses(grace=.05) as owner:
                    gateway=owner.spawn([sys.executable,'-B','-c','import time;time.sleep(60)'],root,demo.base_environment(root,[]),pass_fds=(listener.fileno(),))
                    proxy=owner.spawn(demo.node_command(NODE,entry,listener.fileno()),root,demo.base_environment(root,[]),pass_fds=(listener.fileno(),))
                    wait_for(ready)
                    with socket.create_connection(('127.0.0.1',port),timeout=1) as pending:
                        pending.sendall(b'synthetic-pending-no-auth')
                        demo.OwnedProcesses._signal(gateway,signal.SIGTERM);gateway.wait(timeout=1)
                        with socket.socket() as outsider:
                            with self.assertRaises(OSError): outsider.bind(('127.0.0.1',port))
                        self.assertIsNone(proxy.poll())
                self.assertIsNotNone(proxy.poll())
                with socket.socket() as outsider:
                    with self.assertRaises(OSError): outsider.bind(('127.0.0.1',port))
            with socket.socket() as released: released.bind(('127.0.0.1',port))

    def test_unrelated_fixture_process_and_listener_survive_demo_cleanup(self):
        with tempfile.TemporaryDirectory() as tmp, socket.socket() as unrelated:
            root=Path(tmp).resolve();unrelated.bind(('127.0.0.1',0));unrelated.listen()
            with demo.OwnedProcesses(grace=.05) as fixture_owner:
                foreign=fixture_owner.spawn([sys.executable,'-B','-c','import time;time.sleep(60)'],root,demo.base_environment(root,[]))
                with demo.OwnedProcesses(grace=.05) as demo_owner:
                    local=demo_owner.spawn([sys.executable,'-B','-c','import time;time.sleep(60)'],root,demo.base_environment(root,[]))
                self.assertIsNotNone(local.poll());self.assertIsNone(foreign.poll())
                with socket.create_connection(unrelated.getsockname(),timeout=1): pass
    def test_abrupt_launcher_death_keeps_node_reservation_until_proxy_exit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp).resolve(); metadata=root/'launcher.json'; ready=root/'node.json'; done=root/'done'; entry=root/'fake.mjs'
            entry.write_text('import fs from "node:fs"; fs.writeFileSync('+json.dumps(str(ready))+',JSON.stringify({pid:process.pid})); setInterval(()=>{if(fs.existsSync('+json.dumps(str(done))+'))process.exit(0)},10);')
            code='import importlib.util,subprocess,time,json,pathlib\ns=importlib.util.spec_from_file_location("demo",'+repr(str(ROOT/'scripts/run_shipment_hero_demo.py'))+');m=importlib.util.module_from_spec(s);s.loader.exec_module(m)\nwith m.reserve_listener(0) as listener:\n child=subprocess.Popen(m.node_command(pathlib.Path('+repr(str(NODE))+'),pathlib.Path('+repr(str(entry))+'),listener.fileno()),pass_fds=(listener.fileno(),),close_fds=True,start_new_session=True,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,env=m.base_environment(pathlib.Path('+repr(str(root))+'),[]))\n pathlib.Path('+repr(str(metadata))+').write_text(json.dumps({"pid":child.pid,"port":listener.getsockname()[1]}))\n time.sleep(60)'
            node_pid=None; port=None
            with demo.OwnedProcesses(grace=.05) as owner:
                launcher=owner.spawn([sys.executable,'-B','-I','-S','-c',code],root,demo.base_environment(root,[]))
                try:
                    wait_for(metadata); wait_for(ready)
                    receipt=json.loads(metadata.read_text()); node_pid=receipt['pid'];port=receipt['port']
                    self.assertEqual(json.loads(ready.read_text())['pid'],node_pid)
                    demo.OwnedProcesses._signal(launcher,signal.SIGKILL);launcher.wait(timeout=1)
                    with socket.socket() as outsider:
                        with self.assertRaises(OSError): outsider.bind(('127.0.0.1',port))
                    done.touch() # synthetic Node self-termination, not orphan reap proof
                    deadline=time.monotonic()+2
                    while time.monotonic()<deadline:
                        with socket.socket() as released:
                            try: released.bind(('127.0.0.1',port));break
                            except OSError: time.sleep(.01)
                    else: self.fail('proxy reservation remained after fake entry exit')
                finally:
                    done.touch()
                    if node_pid is not None:
                        try: os.killpg(node_pid,signal.SIGTERM)
                        except ProcessLookupError: pass

    def test_exited_owned_leader_is_reaped_during_cleanup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp).resolve(); owner=demo.OwnedProcesses(grace=.05)
            child=owner.spawn([sys.executable,'-B','-c','import os; os._exit(0)'],root,demo.base_environment(root,[]))
            # Observe only this fake child's kernel zombie state; don't reap it
            # before exercising the supervisor's real cleanup boundary.
            deadline=time.monotonic()+2
            while time.monotonic()<deadline:
                status=subprocess.run(['/bin/ps','-p',str(child.pid),'-o','stat='],capture_output=True,
                    env=demo.base_environment(root,[]),timeout=1).stdout
                if b'Z' in status: break
                time.sleep(.01)
            self.assertIn(b'Z',status)
            try:
                try: owner.close()
                except demo.DemoError: self.fail('owned exited leader cleanup failed')
            finally: child.wait(timeout=1)
            self.assertIsNotNone(child.poll())

    def ack_fixture(self, body, expected, stop=None, timeout=.3):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp).resolve(); read_fd,write_fd=os.pipe()
            try:
                with demo.OwnedProcesses(grace=.05,stop_event=stop) as owner:
                    child=owner.spawn([sys.executable,'-B','-c',body.replace('WRITE_FD',str(write_fd))],root,
                        demo.base_environment(root,[]),pass_fds=(write_fd,))
                    os.close(write_fd); write_fd=None
                    if 'os._exit(0)' in body: child.wait(timeout=1)
                    if expected is None: demo.wait_gateway_ack(owner,child,read_fd,timeout)
                    else:
                        with self.assertRaises(expected): demo.wait_gateway_ack(owner,child,read_fd,timeout)
                self.assertIsNotNone(child.poll())
            finally:
                os.close(read_fd)
                if write_fd is not None: os.close(write_fd)

    def test_partial_ack_completes_without_waiting_for_eof(self):
        self.ack_fixture('import os,time; os.write(WRITE_FD,b"local-demo-"); time.sleep(.02); os.write(WRITE_FD,b"listener-v1 127.0.0.1:8844\\n"); time.sleep(60)',None)

    def test_wrong_oversized_partial_eof_and_exited_ack_rejected(self):
        bodies=[
            'import os,time; os.write(WRITE_FD,b"other\\n"); time.sleep(60)',
            'import os,time; os.write(WRITE_FD,b"local-demo-listener-v1 127.0.0.1:8844\\nextra"); time.sleep(60)',
            'import os,time; os.write(WRITE_FD,b"x"*129); time.sleep(60)',
            'import os,time; os.write(WRITE_FD,b"local-demo-"); os.close(WRITE_FD); time.sleep(60)',
            'import os,time; os.close(WRITE_FD); time.sleep(60)',
            'import os; os.write(WRITE_FD,b"local-demo-listener-v1 127.0.0.1:8844\\n"); os._exit(0)',
        ]
        for i,body in enumerate(bodies):
            with self.subTest(case=i): self.ack_fixture(body,demo.DemoError)

    def test_ack_timeout_and_cancellation_reap_owned_child(self):
        self.ack_fixture('import time; time.sleep(60)',demo.DemoError,timeout=.05)
        stop=threading.Event(); timer=threading.Timer(.05,stop.set);timer.start()
        try: self.ack_fixture('import time; time.sleep(60)',KeyboardInterrupt,stop)
        finally: timer.cancel();timer.join()

    def test_proxy_is_reaped_before_gateway_and_parent_reservation_closes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp).resolve(); trace=root/'trace'
            with demo.reserve_listener(0) as listener:
                port=listener.getsockname()[1]
                with demo.OwnedProcesses(grace=.05) as owner:
                    children=[]
                    for role in ('gateway','proxy'):
                        marker=root/role
                        code='import os,signal,time,pathlib,socket\ndef stop(*_):\n s=socket.socket()\n try: s.bind(("127.0.0.1",'+str(port)+')); status="released-early"\n except OSError: status="held"\n s.close()\n with open('+repr(str(trace))+',"a") as f: f.write('+repr(role)+'+":"+status+"\\n")\n raise SystemExit(0)\nsignal.signal(signal.SIGTERM,stop)\npathlib.Path('+repr(str(marker))+').touch()\ntime.sleep(60)'
                        children.append(owner.spawn([sys.executable,'-B','-c',code],root,demo.base_environment(root,[]),pass_fds=(listener.fileno(),)))
                        wait_for(marker)
                self.assertEqual(trace.read_text().splitlines(),['proxy:held','gateway:held'])
                self.assertTrue(all(child.poll() is not None for child in children))
                with socket.socket() as outsider:
                    with self.assertRaises(OSError): outsider.bind(('127.0.0.1',port))
            with socket.socket() as released: released.bind(('127.0.0.1',port))


if __name__ == '__main__':
    unittest.main()
