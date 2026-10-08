#!/usr/bin/env python3
"""Local synthetic shipment demo launcher; real launch requires root's separate review.

No token creation, database, SDK, installation or Python web service is used.
Root must first integrate/review the UI route and supply trusted absolute tools,
private development files, offline Go caches and the exact reviewed clean HEAD.
The standalone launcher consumes auth only when explicitly invoked by that root.
"""
import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import resource
import selectors
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time

CATALOG_SHA256 = '5b0c076ffdb8ff2dd5d7a0e33a6a3cca03ddb990cb12d97ce848762a7af89d82'
PORTS = (8844, 5344, 9)
LABELS = ('precision_delayed', 'numerical_unsupported', 'no_eligible_empty', 'source_limit_expired')


class DemoError(RuntimeError):
    """Fixed, nonsecret diagnostic; never include child output or environment."""


CAPTURE_LIMIT = 2 << 20
DEMO_ACK = b'local-demo-listener-v1 127.0.0.1:8844\n'
GIT_PREFIX = ('-c', 'core.fsmonitor=false', '-c', 'core.hooksPath=/dev/null', '--no-optional-locks')
GIT_READS = {('rev-parse', 'HEAD'), ('rev-parse', 'HEAD^{tree}'),
             ('status', '--porcelain', '--untracked-files=all'), ('ls-tree', '-rz', 'HEAD')}
NODE_BOOTSTRAP = """const fs=require('node:fs');
const {pathToFileURL}=require('node:url');
const [raw,entry,...args]=process.argv.slice(1);
if(!/^[0-9]+$/.test(raw)||Number(raw)<3||!fs.fstatSync(Number(raw)).isSocket())process.exit(2);
// Keep this inherited descriptor open for the entire proxy lifetime; never accept.
// No stream/FileHandle wrapper owns it and no code closes it before process exit.
process.argv=[process.execPath,entry,...args];
import(pathToFileURL(entry).href).catch(()=>process.exit(2));
"""


def validate_darwin_listener_info(info):
    # ROOT-approved Mac ABI: tcpi_state is the first uint8, LISTEN is 1.
    # Reject future/unknown layouts instead of reading a truncated state byte.
    if len(info) != 112 or info[0] != 1:
        raise DemoError('native listening state invalid')


def verify_native_listening(listener):
    try:
        if listener.family != socket.AF_INET or listener.getsockopt(socket.SOL_SOCKET, socket.SO_TYPE) != socket.SOCK_STREAM:
            raise DemoError('native listener role invalid')
        if sys.platform == 'darwin':
            validate_darwin_listener_info(listener.getsockopt(socket.IPPROTO_TCP, 0x106, 256))
        elif sys.platform == 'linux':
            if listener.getsockopt(socket.SOL_SOCKET, socket.SO_ACCEPTCONN) != 1:
                raise DemoError('native listening state invalid')
        else:
            raise DemoError('native listening platform unsupported')
    except OSError:
        raise DemoError('native listening state unavailable') from None


@contextmanager
def reserve_listener(port=8844):
    if sys.platform not in ('darwin', 'linux') or not hasattr(socket, 'SO_REUSEPORT'):
        raise DemoError('exclusive native listener unsupported')
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 0)
        listener.bind(('127.0.0.1', port))
        listener.listen(128)
        verify_native_listening(listener)
        if (listener.getsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR) != 0 or
                listener.getsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT) != 0):
            raise DemoError('exclusive listener validation failed')
        yield listener
    except OSError:
        raise DemoError('exclusive loopback listener unavailable') from None
    finally:
        listener.close()


def node_command(node, entry, listener_fd):
    return [str(node), '-e', NODE_BOOTSTRAP, '--', str(listener_fd), str(entry),
            '--host', '127.0.0.1', '--port', '5344', '--strictPort']


def wait_gateway_ack(owner, child, ready_fd, timeout):
    if not 0 < timeout <= 60:
        raise DemoError('invalid acknowledgement timeout')
    os.set_blocking(ready_fd, False)
    deadline = time.monotonic() + timeout
    body = bytearray()
    with selectors.DefaultSelector() as selector:
        selector.register(ready_fd, selectors.EVENT_READ)
        while time.monotonic() < deadline:
            owner._check_stop()
            if child.poll() is not None:
                raise DemoError('owned gateway exited before adoption')
            if not selector.select(min(.02, max(0, deadline-time.monotonic()))):
                continue
            try:
                part = os.read(ready_fd, 129-len(body))
            except BlockingIOError:
                continue
            if not part:
                raise DemoError('incomplete listener adoption acknowledgement')
            body.extend(part)
            if len(body) > 128 or not DEMO_ACK.startswith(body):
                raise DemoError('invalid listener adoption acknowledgement')
            if bytes(body) == DEMO_ACK:
                # Do not block for EOF. The child writes exactly one frame and
                # closes its sole write end; ownership comes from retained FD.
                if child.poll() is not None:
                    raise DemoError('owned gateway exited during adoption')
                return
    raise DemoError('listener adoption acknowledgement timeout')


def capture_argv_valid(argv):
    if (len(argv) < len(GIT_PREFIX)+2 or tuple(argv[1:1+len(GIT_PREFIX)]) != GIT_PREFIX or
            tuple(argv[1+len(GIT_PREFIX):]) not in GIT_READS):
        return False
    try:
        path = tool_path(Path(argv[0]))
        return not (path.stat().st_mode & (stat.S_ISUID | stat.S_ISGID))
    except (DemoError, OSError):
        return False


def capture_exec(argv):
    # Internal exec mode only. No normal launcher/auth/config logic is entered.
    if (not capture_argv_valid(argv) or os.geteuid() == 0 or os.getuid() != os.geteuid() or
            not stat.S_ISREG(os.fstat(1).st_mode)):
        return 70
    try:
        resource.setrlimit(resource.RLIMIT_FSIZE, (CAPTURE_LIMIT, CAPTURE_LIMIT))
        signal.signal(signal.SIGXFSZ, signal.SIG_DFL)
        os.execve(argv[0], argv, dict(os.environ))
    except (OSError, ValueError):
        return 70


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode('utf8')


def base_environment(runtime, tool_dirs):
    """Construct from scratch; nothing from the caller's environment is copied."""
    directories = list(dict.fromkeys(str(p) for p in tool_dirs))
    return {'PATH': ':'.join(directories + ['/usr/bin', '/bin']), 'HOME': str(runtime),
            'TMPDIR': str(runtime), 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8', 'TZ': 'UTC'}


def build_environment(runtime, tool_dirs, cache, modules):
    env = base_environment(runtime, tool_dirs)
    env.update(GOPROXY='off', GOSUMDB='off', GOTOOLCHAIN='local', GOENV='off',
               GOFLAGS='-mod=readonly', CGO_ENABLED='0', GOCACHE=str(cache),
               GOMODCACHE=str(modules), GOPATH=str(runtime / 'gopath'))
    return env


def gateway_environment(runtime, tool_dirs, repo, identities):
    env = base_environment(runtime, tool_dirs)
    env.update(GATEWAY_MODE='local', GATEWAY_ADDR='127.0.0.1:8844',
               DEV_IDENTITIES_FILE=str(identities), DEV_ALLOWED_ORIGIN='http://127.0.0.1:5344',
               PYTHON_SERVICE_URL='http://127.0.0.1:9', CONTRACT_EXAMPLES_DIR=str(repo / 'contracts/examples'))
    return env


def web_environment(runtime, tool_dirs, token_file):
    env = base_environment(runtime, tool_dirs)
    env.update(GATEWAY_URL='http://127.0.0.1:8844', GATEWAY_DEV_TOKEN_FILE=str(token_file), BROWSER='none')
    return env


def tool_path(path):
    if not path.is_absolute() or not path.is_file() or not os.access(path, os.X_OK):
        raise DemoError('trusted absolute executable unavailable')
    # Keep an explicitly supplied venv interpreter's name, even if it is a symlink.
    return path


def private_file(path):
    try:
        info = path.lstat()
    except OSError:
        raise DemoError('private local auth file unavailable') from None
    if (not path.is_absolute() or not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
            or info.st_mode & 0o077 or not 0 < info.st_size <= 1 << 20):
        raise DemoError('private local auth file unavailable')
    # Only metadata is checked here. The future Go/Vite children own consumption.
    return path


def check_dotenv(repo):
    if any((repo / 'apps/web').glob('.env*')):
        raise DemoError('web dotenv path present; explicit clean setup required')


def ensure_ports_free(ports):
    """No listener is killed, contacted with application data or reassigned."""
    for port in ports:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            try:
                probe.bind(('127.0.0.1', port))
            except PermissionError:
                # Some hosts forbid binding port9. A refused loopback connection is
                # the only accepted alternative; timeout or listener is fail-closed.
                if port != 9:
                    raise DemoError('fixed loopback port unavailable') from None
                probe.settimeout(.2)
                try:
                    probe.connect(('127.0.0.1', port))
                except ConnectionRefusedError:
                    continue
                except OSError:
                    raise DemoError('legacy loopback vacancy not established') from None
                raise DemoError('legacy loopback port occupied')
            except OSError:
                raise DemoError('fixed loopback port occupied or unavailable') from None


class OwnedProcesses:
    """New sessions only; no pipes, no external PIDs, bounded TERM/KILL/wait."""
    def __init__(self, grace=.5, stop_event=None):
        if not 0 < grace <= 5:
            raise DemoError('invalid cleanup timeout')
        self.grace = grace
        self.children = []
        self.closed = False
        self.stop_event = stop_event

    def _check_stop(self):
        if self.stop_event is not None and self.stop_event.is_set():
            raise KeyboardInterrupt

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def spawn(self, argv, cwd, env, *, stdout=subprocess.DEVNULL, pass_fds=()):
        if self.closed:
            raise DemoError('owned process supervisor closed')
        self._check_stop()
        try:
            child = subprocess.Popen(argv, cwd=cwd, env=env, start_new_session=True,
                                     stdin=subprocess.DEVNULL, stdout=stdout, stderr=subprocess.DEVNULL, pass_fds=pass_fds, close_fds=True)
        except OSError:
            raise DemoError('owned executable failed to start') from None
        self.children.append(child)
        self._check_stop()
        return child

    @staticmethod
    def _signal(child, sig):
        try:
            os.killpg(child.pid, sig)  # child PID is the new session/group ID
        except ProcessLookupError:
            pass

    def _stop(self, children):
        if not children:
            return
        for child in children:
            # Darwin can deny signalling a zombie-only group. Reap our own
            # exited leader first; still signal its group for live descendants.
            child.poll()
            self._signal(child, signal.SIGTERM)
        # Allow the whole group, including grandchildren, a fixed grace. Do not
        # shortcut just because its leader exited and inherited pipes remain.
        deadline = time.monotonic() + self.grace
        while time.monotonic() < deadline:
            time.sleep(min(.02, max(0, deadline - time.monotonic())))
        for child in children:
            child.poll()  # Reap a TERM-exited leader before signaling remaining group.
            self._signal(child, signal.SIGKILL)
        failed = False
        for child in children:
            try:
                child.wait(timeout=1)
            except subprocess.TimeoutExpired:
                failed = True
        if failed:
            raise DemoError('owned child reaping failed')

    def close(self):
        if self.closed:
            return
        self.closed = True
        children, self.children = self.children, []
        failure = None
        for child in reversed(children):  # proxy registered after Go: stop/reap it first
            try:
                self._stop([child])
            except OSError:
                failure = DemoError('owned child signalling failed')
            except DemoError as error:
                failure = error
        if failure is not None:
            raise failure

    def run(self, argv, cwd, env, timeout, *, stdout=subprocess.DEVNULL, output_guard=None):
        if not 0 < timeout <= 60:
            raise DemoError('invalid command timeout')
        child = self.spawn(argv, cwd, env, stdout=stdout)
        try:
            deadline = time.monotonic() + timeout
            while child.poll() is None:
                self._check_stop()
                if output_guard is not None:
                    output_guard()
                if time.monotonic() >= deadline:
                    raise DemoError('owned command timeout')
                time.sleep(min(.02, max(0, deadline - time.monotonic())))
            self._check_stop()
            if output_guard is not None:
                output_guard()
            if child.returncode:
                raise DemoError('owned command failed')
        finally:
            self._stop([child])
            self.children.remove(child)

    def wait_started(self, ports, timeout):
        if not 0 < timeout <= 60:
            raise DemoError('invalid startup timeout')
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self._check_stop()
            if any(child.poll() is not None for child in self.children):
                raise DemoError('owned service exited before startup')
            ready = True
            for port in ports:
                try:
                    with socket.create_connection(('127.0.0.1', port), timeout=.05):
                        pass
                except OSError:
                    ready = False
            if ready:
                return  # Transport only; not session/page/provider verification.
            time.sleep(.02)
        raise DemoError('owned service startup timeout')


def capture_command(argv, repo, runtime, tools, stop_event=None):
    if not capture_argv_valid(argv):
        raise DemoError('capture command outside fixed source allowlist')
    env = base_environment(runtime, tools)
    env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull, GIT_OPTIONAL_LOCKS='0')
    helper = tool_path(Path(sys.executable))
    with tempfile.TemporaryFile(dir=runtime) as output, OwnedProcesses(grace=.05, stop_event=stop_event) as owner:
        def guard():
            if os.fstat(output.fileno()).st_size >= CAPTURE_LIMIT:
                raise DemoError('source inventory too large')
        owner.run([str(helper), '-I', '-S', str(Path(__file__).resolve()), '--_capture-exec', *argv],
                  repo, env, timeout=10, stdout=output, output_guard=guard)
        guard()
        output.seek(0)
        return output.read(CAPTURE_LIMIT)


def verify_source_files(repo, entries):
    repo = repo.resolve()
    hashes = {}
    for mode, blob, name in entries:
        relative = Path(name)
        path = repo / relative
        if relative.is_absolute() or '..' in relative.parts or mode not in ('100644', '100755'):
            raise DemoError('unsupported reviewed source entry')
        if any((repo / Path(*relative.parts[:i])).is_symlink() for i in range(1, len(relative.parts) + 1)):
            # The repo argument is resolved first; tracked file/parent symlinks
            # must never substitute source outside that reviewed tree.
            raise DemoError('reviewed source symlink rejected')
        if not path.is_file():
            raise DemoError('reviewed source missing')
        body = path.read_bytes()
        actual = hashlib.sha1(b'blob ' + str(len(body)).encode() + b'\0' + body).hexdigest()
        if actual != blob:
            raise DemoError('reviewed source drift')
        hashes[name] = hashlib.sha256(body).hexdigest()
    return hashes


def verify_source(repo, git, reviewed_head, runtime, stop_event=None):
    if not re.fullmatch('[0-9a-f]{40}', reviewed_head):
        raise DemoError('exact reviewed HEAD required')
    command = [str(git), '-c', 'core.fsmonitor=false', '-c', 'core.hooksPath=/dev/null', '--no-optional-locks']
    def read(*args):
        return capture_command(command + list(args), repo, runtime, [git.parent], stop_event)
    head = read('rev-parse', 'HEAD').decode().strip()
    if head != reviewed_head or read('status', '--porcelain', '--untracked-files=all').strip():
        raise DemoError('reviewed clean HEAD mismatch')
    tree = read('rev-parse', 'HEAD^{tree}').decode().strip()
    entries = []
    for row in read('ls-tree', '-rz', 'HEAD').split(b'\0'):
        if row:
            metadata, name = row.split(b'\t', 1)
            mode, kind, blob = metadata.decode().split()
            if kind != 'blob':
                raise DemoError('reviewed submodule unsupported')
            entries.append((mode, blob, name.decode('utf8')))
    hashes = verify_source_files(repo, entries)
    if read('rev-parse', 'HEAD').decode().strip() != head:
        raise DemoError('reviewed HEAD changed during preflight')
    return {'head': head, 'tree': tree, 'files': hashes}


def load_catalog(path, profile):
    if profile not in ('hero', 'empty', 'disabled'):
        raise DemoError('unsupported demo profile')
    try:
        body = path.read_bytes()
    except OSError:
        raise DemoError('accepted demo catalog unavailable') from None
    if hashlib.sha256(body).hexdigest() != CATALOG_SHA256 or len(body) != 18153:
        raise DemoError('accepted demo catalog identity mismatch')
    if profile == 'disabled':
        return None
    if profile == 'empty':
        return canonical({'catalog_version': '1', 'contexts': []})
    return body


@contextmanager
def runtime_files(path, profile, source):
    body = load_catalog(path, profile)
    with tempfile.TemporaryDirectory(prefix='shipment-hero-demo-') as directory:
        run = Path(directory).resolve()
        run.chmod(0o700)
        def write(name, value):
            file = run / name
            with file.open('xb') as output:
                os.chmod(file, 0o600)
                output.write(value)
            return file
        catalog = write('catalog.json', body) if body is not None else None
        cases = []
        if body is not None:
            for index, context in enumerate(json.loads(body)['contexts']):
                request = canonical(context['request'])
                cases.append({'label': LABELS[index], 'request_sha256': hashlib.sha256(request).hexdigest()})
        manifest = write('manifest.json', canonical({'format': 'shipment-hero-demo-run-v1',
                         'profile': profile, 'source': source, 'ports': list(PORTS), 'cases': cases,
                         'catalog_sha256': hashlib.sha256(body).hexdigest() if body is not None else None,
                         'synthetic': True, 'browser_verified': False}))
        yield run, catalog, manifest


def gateway_command(binary, python, repo, catalog, listener_fd=None, ready_fd=None):
    command = [str(binary)]
    if (listener_fd is None) != (ready_fd is None):
        raise DemoError('both listener descriptors required')
    if listener_fd is not None:
        command += ['-local-demo-listener-fd', str(listener_fd), '-local-demo-ready-fd', str(ready_fd)]
    if catalog is not None:
        command += ['-shipment-local-contexts', str(catalog), '-shipment-local-python', str(python),
                    '-shipment-local-source', str(repo / 'procurement-engine/src')]
    return command


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    for name in ('repo', 'python', 'go', 'node', 'git', 'go-cache', 'go-mod-cache', 'identities-file', 'proxy-token-file'):
        result.add_argument('--' + name, required=True, type=Path)
    result.add_argument('--reviewed-head', required=True)
    result.add_argument('--profile', choices=('hero', 'empty', 'disabled'), default='hero')
    result.add_argument('--startup-timeout', type=float, default=20)
    result.add_argument('--build-timeout', type=float, default=60)
    return result


def launch(args, stop_event=None):
    if not args.repo.is_absolute() or not args.repo.is_dir():
        raise DemoError('reviewed absolute repository unavailable')
    repo = args.repo.resolve()
    tools = [tool_path(path) for path in (args.python, args.go, args.node, args.git)]
    for cache in (args.go_cache, args.go_mod_cache):
        if not cache.is_absolute() or not cache.is_dir():
            raise DemoError('existing absolute offline cache unavailable')
    for timeout in (args.startup_timeout, args.build_timeout):
        if not 0 < timeout <= 60:
            raise DemoError('invalid startup/build timeout')
    check_dotenv(repo)
    identities, proxy = private_file(args.identities_file), private_file(args.proxy_token_file)
    vite = repo / 'apps/web/node_modules/vite/bin/vite.js'
    if not vite.is_file():
        raise DemoError('existing Vite dependency unavailable')
    catalog_path = repo / 'scripts/demo/shipment_recovery_catalog.json'
    load_catalog(catalog_path, args.profile)
    ensure_ports_free(PORTS[1:])
    with tempfile.TemporaryDirectory(prefix='shipment-demo-preflight-') as directory:
        source = verify_source(repo, args.git, args.reviewed_head, Path(directory).resolve(), stop_event)
    # These are source-bound files, not an arbitrary external script/binary.
    required = ('scripts/run_shipment_hero_demo.py', 'scripts/demo/shipment_recovery_catalog.json', 'apps/web/src/main.tsx')
    if any(name not in source['files'] for name in required):
        raise DemoError('demo source not part of reviewed HEAD')
    if Path(__file__).resolve() != repo / 'scripts/run_shipment_hero_demo.py':
        raise DemoError('launcher must come from reviewed repository')
    directories = [p.parent for p in tools]
    with runtime_files(catalog_path, args.profile, source) as (run, catalog, manifest):
        with OwnedProcesses(stop_event=stop_event) as build_owner:
            binary = run / 'gateway'
            build_owner.run([str(args.go), 'build', '-o', str(binary), '.'], repo / 'services/gateway',
                            build_environment(run, directories, args.go_cache, args.go_mod_cache), args.build_timeout)
        if verify_source(repo, args.git, args.reviewed_head, run, stop_event) != source:
            raise DemoError('reviewed source changed during build')
        check_dotenv(repo)
        private_file(identities)
        private_file(proxy)
        ensure_ports_free(PORTS[1:])
        # Reservation context outlives supervisor cleanup; Node keeps its own
        # inherited copy if the launcher is abruptly killed.
        with reserve_listener() as listener, OwnedProcesses(stop_event=stop_event) as owner:
            read_fd, write_fd = os.pipe()
            try:
                try:
                    gateway = owner.spawn(gateway_command(binary, args.python, repo, catalog, listener.fileno(), write_fd), repo,
                            gateway_environment(run, directories, repo, identities), pass_fds=(listener.fileno(), write_fd))
                finally:
                    os.close(write_fd)
                wait_gateway_ack(owner, gateway, read_fd, args.startup_timeout)
            finally:
                os.close(read_fd)
            if gateway.poll() is not None:
                raise DemoError('owned gateway exited before proxy start')
            owner.spawn(node_command(args.node, vite, listener.fileno()), repo / 'apps/web',
                        web_environment(run, directories, proxy), pass_fds=(listener.fileno(),))
            # Connectivity is UI transport only, never gateway ownership proof.
            owner.wait_started([5344], args.startup_timeout)
            print('Owned loopback transport started; session, route and browser verification remain required.', flush=True)
            print('http://127.0.0.1:5344/shipment-recovery', flush=True)
            print('Nonsecret runtime manifest: ' + str(manifest), flush=True)
            while True:
                owner._check_stop()
                if any(child.poll() is not None for child in owner.children):
                    raise DemoError('owned service exited')
                time.sleep(.2)


def main(argv=None):
    args = parser().parse_args(argv)
    stop_event = threading.Event()
    def stop(_signum, _frame):
        # Do not throw inside Popen's creation/registration window. The supervisor
        # observes this at safe points; signals cannot interrupt finally cleanup.
        stop_event.set()
    previous = {sig: signal.signal(sig, stop) for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        launch(args, stop_event)
    except KeyboardInterrupt:
        return 130
    except (DemoError, OSError, ValueError):
        # Never echo auth paths, argv, environment, exception or child logs.
        print('Demo unavailable: preflight, build, startup or owned lifecycle failed. No fallback.', flush=True)
        return 1
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


if __name__ == '__main__':
    if sys.argv[1:2] == ['--_capture-exec']:
        raise SystemExit(capture_exec(sys.argv[2:]))
    raise SystemExit(main())
