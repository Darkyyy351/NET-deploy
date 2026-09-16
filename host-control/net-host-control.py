#!/usr/bin/env python3
"""Fixed-action NET host bridge. Never accepts shell commands or repository URLs."""
import datetime
import fcntl
import hmac
import json
import os
from pathlib import Path
import re
import socketserver
import subprocess
import threading
import time
import urllib.request

RELEASE_URL = 'https://raw.githubusercontent.com/Darkyyy351/NET-deploy/main/release.json'
INSTALL_DIR = Path('/usr/local/lib/net/host-control')
STATE_DIR = Path('/var/lib/net-host-control')
SOCKET = '/run/net-host-control/control.sock'
SCHEDULE = Path('/run/systemd/shutdown/scheduled')
HISTORY_LIMIT = 20
BASELINE_HISTORY = [{
    'version': '0.2.0-dev.2',
    'state': 'baseline',
    'at': None,
    'message': 'Baseline release recorded before automatic update history was enabled.'
}]


def net_power_pending():
    try:
        return 'NET administrator request' in SCHEDULE.read_text()
    except OSError:
        return False


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def validate_release(value):
    if not isinstance(value, dict) or value.get('schemaVersion') != 1:
        raise ValueError('Unsupported release manifest')
    if not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+(?:-[A-Za-z0-9.-]+)?', str(value.get('version', ''))):
        raise ValueError('Invalid release version')
    for key in ('backend', 'frontend'):
        if not re.fullmatch(r'[0-9a-f]{40}', str(value.get(key, ''))):
            raise ValueError('Release must pin full commit hashes')
    notes = value.get('notes')
    if not isinstance(notes, list) or not 1 <= len(notes) <= 12 or any(not isinstance(n, str) or not 1 <= len(n) <= 300 for n in notes):
        raise ValueError('Invalid release notes')
    return {key: value[key] for key in ('schemaVersion', 'version', 'backend', 'frontend', 'notes')}


class Controller:
    def __init__(self, config, runner=subprocess.run):
        self.config = config
        self.runner = runner
        self.lock = threading.RLock()
        self.checking = False
        self.last_attempt = -60
        self.release = None
        self.checked_at = None
        self.error = None
        self.operation = {'state': 'idle'}
        self.guard = None
        self.auth_failures = []
        state = STATE_DIR / 'operation.json'
        if state.exists():
            self.operation = json.loads(state.read_text())
            if self.operation.get('state') == 'scheduled' and net_power_pending():
                self.guard = self.acquire_guard()
            elif self.operation.get('state') in ('installing', 'scheduled'):
                self.operation = {'state': 'interrupted', 'message': 'Previous action ended or was interrupted. Verify host status.', 'at': now()}
                self.save()

    def save(self):
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        temp = STATE_DIR / 'operation.tmp'
        temp.write_text(json.dumps(self.operation))
        temp.replace(STATE_DIR / 'operation.json')

    def history(self):
        path = STATE_DIR / 'history.json'
        try:
            value = json.loads(path.read_text())
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)][:HISTORY_LIMIT]
        except (OSError, ValueError):
            pass
        return list(BASELINE_HISTORY)

    def record_history(self, state, release, message):
        try:
            entry = {'version': release['version'], 'state': state, 'at': now(), 'message': message,
                     'backend': release['backend'], 'frontend': release['frontend']}
            history = [item for item in self.history() if item.get('version') != release['version']]
            history.insert(0, entry)
            STATE_DIR.mkdir(parents=True, exist_ok=True)
            temp = STATE_DIR / 'history.tmp'
            temp.write_text(json.dumps(history[:HISTORY_LIMIT]))
            temp.replace(STATE_DIR / 'history.json')
        except (OSError, ValueError, TypeError, KeyError):
            # Installation result remains authoritative even if optional history cannot be written.
            pass

    def status(self):
        with self.lock:
            deployed = {}
            try:
                deployed = json.loads((Path(self.config['apps']) / 'NET-serverBCEND/data/deployment.json').read_text())
            except (OSError, ValueError):
                pass
            current = self.release and deployed.get('status') == 'healthy' and all(
                self.release[k].startswith(deployed.get(k, {}).get('commit') or '?') for k in ('backend', 'frontend'))
            state = 'unavailable' if self.error else 'unchecked' if not self.release else 'current' if current else 'available'
            return {'available': True, 'updateState': state, 'checking': self.checking,
                    'checkedAt': self.checked_at, 'release': self.release, 'error': self.error,
                    'operation': dict(self.operation), 'history': self.history(), 'powerAvailable': True}

    def check(self):
        with self.lock:
            if self.checking or time.monotonic() - self.last_attempt < 60:
                return
            self.checking = True
            self.last_attempt = time.monotonic()
        try:
            request = urllib.request.Request(RELEASE_URL, headers={'User-Agent': 'NET-host-control/1'})
            with urllib.request.urlopen(request, timeout=10) as response:
                raw = response.read(32769)
            if len(raw) > 32768:
                raise ValueError('Release manifest too large')
            release = validate_release(json.loads(raw))
            with self.lock:
                self.release, self.checked_at, self.error = release, now(), None
        except Exception:
            with self.lock:
                self.error = 'Release check failed. Last successful check is retained.'
        finally:
            with self.lock:
                self.checking = False

    def authenticate(self, credential):
        stamp = time.monotonic()
        self.auth_failures = [t for t in self.auth_failures if stamp - t < 60]
        if len(self.auth_failures) >= 5:
            raise ValueError('Too many attempts. Retry in one minute.')
        if not isinstance(credential, str) or not hmac.compare_digest(credential, self.config['adminKey']):
            self.auth_failures.append(stamp)
            raise ValueError('Invalid administrator key')

    def acquire_guard(self):
        handle = open(Path(self.config['apps']) / '.net-update.lock', 'a')
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            raise ValueError('Another update or power action is active')
        return handle

    def install(self, release):
        # The update runs on the host and survives replacement of the backend container.
        try:
            env = dict(os.environ, HOME=self.config['home'], NET_APPS_DIR=self.config['apps'],
                       NET_BACKEND_REF=release['backend'], NET_FRONTEND_REF=release['frontend'])
            with open(STATE_DIR / 'update.log', 'w') as log:
                result = self.runner(['/usr/sbin/runuser', '-u', self.config['user'], '--',
                                      '/bin/bash', str(INSTALL_DIR / 'update.sh')],
                                     env=env, stdout=log, stderr=subprocess.STDOUT, check=False)
            with self.lock:
                state = 'succeeded' if result.returncode == 0 else 'failed'
                message = 'Update completed' if result.returncode == 0 else 'Update failed; inspect host update.log and container status.'
                self.operation = {'state': state,
                                  'version': release['version'], 'at': now(),
                                  'message': message}
                self.save()
                self.record_history(state, release, message)
        except Exception:
            with self.lock:
                self.operation = {'state': 'failed', 'version': release['version'], 'at': now(), 'message': 'Host updater could not finish.'}
                self.save()
                self.record_history('failed', release, self.operation['message'])

    def handle(self, request):
        if not isinstance(request, dict):
            raise ValueError('Invalid request')
        action = request.get('action')
        if action == 'status':
            return self.status()
        if action == 'check':
            threading.Thread(target=self.check, daemon=True).start()
            return self.status()
        with self.lock:
            self.authenticate(request.get('credential'))
            if action == 'cancel-power':
                if self.operation.get('state') != 'scheduled' or not net_power_pending():
                    raise ValueError('No scheduled power action')
                self.runner(['/usr/sbin/shutdown', '-c'], check=True, timeout=10, capture_output=True)
                if self.guard:
                    self.guard.close()
                    self.guard = None
                self.operation = {'state': 'cancelled', 'at': now()}
                self.save()
                return self.status()
            if self.operation.get('state') in ('installing', 'scheduled'):
                raise ValueError('Another host action is active')
            if SCHEDULE.exists():
                raise ValueError('A system power action is already scheduled outside this session')
            if action == 'install':
                if self.status()['updateState'] != 'available' or self.checking:
                    raise ValueError('No verified update available')
                release = dict(self.release)
                if request.get('version') != release['version'] or request.get('backend') != release['backend'] or request.get('frontend') != release['frontend']:
                    raise ValueError('Release changed. Review it again.')
                if request.get('confirmation') != 'UPDATE NET':
                    raise ValueError('Confirmation required')
                guard = self.acquire_guard()
                guard.close()  # update.sh obtains the same lock for its complete lifetime.
                self.operation = {'state': 'installing', 'version': release['version'], 'at': now()}
                self.save()
                threading.Thread(target=self.install, args=(release,), daemon=True).start()
            elif action in ('reboot', 'poweroff'):
                expected = 'RESTART CM5' if action == 'reboot' else 'VYPNOUT CM5'
                if request.get('confirmation') != expected:
                    raise ValueError('Confirmation required')
                self.guard = self.acquire_guard()
                try:
                    self.runner(['/usr/sbin/shutdown', '-r' if action == 'reboot' else '-P', '+1', 'NET administrator request'], check=True, timeout=10, capture_output=True)
                except Exception:
                    self.guard.close()
                    self.guard = None
                    raise
                self.operation = {'state': 'scheduled', 'action': action, 'at': now(), 'message': 'Power action scheduled in one minute'}
                self.save()
            else:
                raise ValueError('Unsupported host action')
            return self.status()


def main():
    config = json.loads(Path('/etc/net-host-control.json').read_text())
    controller = Controller(config)

    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            self.connection.settimeout(3)
            try:
                raw = self.rfile.readline(4097)
                if len(raw) > 4096:
                    raise ValueError('Request too large')
                data = controller.handle(json.loads(raw))
                result = {'ok': True, 'data': data}
            except ValueError as error:
                result = {'ok': False, 'error': str(error)}
            except Exception:
                result = {'ok': False, 'error': 'Host operation failed'}
            self.wfile.write(json.dumps(result).encode() + b'\n')

    def checks():
        while True:
            controller.check()
            time.sleep(900)

    threading.Thread(target=checks, daemon=True).start()
    if os.path.exists(SOCKET):
        os.unlink(SOCKET)
    with socketserver.UnixStreamServer(SOCKET, Handler) as server:
        os.chmod(SOCKET, 0o600)
        server.serve_forever()


if __name__ == '__main__':
    main()
