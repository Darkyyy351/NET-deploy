import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

# Windows tests mock flock; real Linux locking is covered by the CM5 preflight.
if sys.platform == 'win32':
    sys.modules['fcntl'] = types.SimpleNamespace(flock=Mock(), LOCK_EX=2, LOCK_NB=4)
spec = importlib.util.spec_from_file_location('host', Path(__file__).with_name('net-host-control.py'))
host = importlib.util.module_from_spec(spec)
spec.loader.exec_module(host)


class HostTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        host.STATE_DIR = self.root / 'state'
        self.config = dict(user='darkman', home=str(self.root), apps=str(self.root), adminKey='a' * 64)
        self.runner = Mock(return_value=types.SimpleNamespace(returncode=0))
        self.controller = host.Controller(self.config, self.runner)
        self.release = dict(schemaVersion=1, version='0.2.1', backend='a'*40, frontend='b'*40, notes=['Telemetry'])

    def test_manifest(self):
        self.assertEqual(host.validate_release(self.release), self.release)
        for change in [dict(backend='main; reboot'), dict(notes=[]), dict(version='../bad'), dict(frontend='short')]:
            with self.assertRaises(ValueError):
                host.validate_release(dict(self.release, **change))

    def test_history_starts_with_baseline_release(self):
        history = self.controller.status()['history']
        self.assertEqual(history[0]['version'], '0.2.0-dev.2')
        self.assertEqual(history[0]['state'], 'baseline')

    def test_unknown_not_current(self):
        self.assertEqual(self.controller.status()['updateState'], 'unchecked')
        self.controller.release = self.release
        self.assertEqual(self.controller.status()['updateState'], 'available')
        data = self.root / 'NET-serverBCEND/data'
        data.mkdir(parents=True)
        (data/'deployment.json').write_text(json.dumps(dict(status='healthy', backend=dict(commit='a'*12), frontend=dict(commit='b'*12))))
        self.assertEqual(self.controller.status()['updateState'], 'current')
        self.controller.error = 'Offline'
        self.assertEqual(self.controller.status()['updateState'], 'unavailable')

    def test_separate_key_and_confirmation(self):
        for body in [dict(action='reboot'), dict(action='reboot', credential='device-key'), dict(action='reboot', credential='a'*64, confirmation='yes')]:
            with self.assertRaises(ValueError):
                self.controller.handle(body)
        self.runner.assert_not_called()

    def test_power_and_cancel(self):
        result = self.controller.handle(dict(action='reboot', credential='a'*64, confirmation='RESTART CM5', delayMinutes=5))
        self.assertEqual(result['operation']['state'], 'scheduled')
        self.assertEqual(result['operation']['delayMinutes'], 5)
        self.assertTrue(result['operation']['cancellable'])
        self.assertEqual(self.runner.call_args.args[0], ['/usr/sbin/shutdown', '-r', '+5', 'NET administrator request'])
        with self.assertRaises(ValueError):
            self.controller.handle(dict(action='poweroff', credential='a'*64, confirmation='VYPNOUT CM5'))
        self.controller.handle(dict(action='cancel-power', credential='a'*64))
        self.assertEqual(self.runner.call_args.args[0], ['/usr/sbin/shutdown', '-c'])
        self.assertIsNone(self.controller.guard)

    def test_immediate_power_action_cannot_be_cancelled(self):
        result = self.controller.handle(dict(action='poweroff', credential='a'*64, confirmation='VYPNOUT CM5', delayMinutes=0))
        self.assertEqual(result['operation']['delayMinutes'], 0)
        self.assertFalse(result['operation']['cancellable'])
        self.assertEqual(self.runner.call_args.args[0], ['/usr/sbin/shutdown', '-P', 'now', 'NET administrator request'])
        with self.assertRaisesRegex(ValueError, 'No scheduled'):
            self.controller.handle(dict(action='cancel-power', credential='a'*64))
        self.controller.guard.close()
        self.controller.guard = None

    def test_power_delay_is_allowlisted(self):
        for delay in (-1, 2, 15, '5', True):
            with self.assertRaisesRegex(ValueError, 'Unsupported power action delay'):
                self.controller.handle(dict(action='reboot', credential='a'*64, confirmation='RESTART CM5', delayMinutes=delay))
        self.runner.assert_not_called()

    def test_stale_power_state_is_persisted_as_interrupted(self):
        host.STATE_DIR.mkdir()
        state = host.STATE_DIR / 'operation.json'
        state.write_text(json.dumps(dict(state='scheduled', action='reboot')))
        with patch.object(host, 'net_power_pending', return_value=False):
            controller = host.Controller(self.config, self.runner)
        self.assertEqual(controller.operation['state'], 'interrupted')
        self.assertEqual(json.loads(state.read_text())['state'], 'interrupted')

    def test_install_rejects_changed_release(self):
        self.controller.release = self.release
        with self.assertRaises(ValueError):
            self.controller.handle(dict(action='install', credential='a'*64, confirmation='UPDATE NET', version='0.2.0', backend='a'*40, frontend='b'*40))
        self.runner.assert_not_called()

    def test_install_pins_and_runs_as_user(self):
        host.STATE_DIR.mkdir()
        self.controller.install(self.release)
        args = self.runner.call_args
        self.assertEqual(args.args[0][:4], ['/usr/sbin/runuser', '-u', 'darkman', '--'])
        self.assertEqual(args.kwargs['env']['NET_BACKEND_REF'], 'a'*40)
        self.assertEqual(self.controller.operation['state'], 'succeeded')
        history = json.loads((host.STATE_DIR/'history.json').read_text())
        self.assertEqual(history[0]['version'], '0.2.1')
        self.assertEqual(history[0]['state'], 'succeeded')
        self.assertEqual(history[0]['notes'], ['Telemetry'])

    def test_rate_limit(self):
        for _ in range(5):
            with self.assertRaises(ValueError):
                self.controller.handle(dict(action='reboot', credential='bad'))
        with self.assertRaisesRegex(ValueError, 'Too many'):
            self.controller.handle(dict(action='reboot', credential='a'*64, confirmation='RESTART CM5'))

    def test_power_failure_releases_lock(self):
        self.runner.side_effect = RuntimeError('Power unavailable')
        with self.assertRaises(RuntimeError):
            self.controller.handle(dict(action='poweroff', credential='a'*64, confirmation='VYPNOUT CM5'))
        self.assertIsNone(self.controller.guard)
        self.assertEqual(self.controller.operation['state'], 'idle')

    def test_lock_conflict_blocks_power(self):
        with patch.object(self.controller, 'acquire_guard', side_effect=ValueError('Another update')):
            with self.assertRaises(ValueError):
                self.controller.handle(dict(action='poweroff', credential='a'*64, confirmation='VYPNOUT CM5'))
        self.runner.assert_not_called()

    def test_failed_update(self):
        host.STATE_DIR.mkdir()
        self.runner.return_value.returncode = 1
        self.controller.install(self.release)
        self.assertEqual(self.controller.operation['state'], 'failed')
        self.assertEqual(self.controller.status()['history'][0]['state'], 'failed')

    def test_network_error_retains_check_time(self):
        self.controller.release = self.release
        self.controller.checked_at = 'previous'
        with patch.object(host.urllib.request, 'urlopen', side_effect=OSError('Offline')):
            self.controller.check()
        self.assertEqual(self.controller.checked_at, 'previous')
        self.assertEqual(self.controller.status()['updateState'], 'unavailable')
        self.assertFalse(self.controller.checking)

    def test_manual_check_bypasses_automatic_cooldown(self):
        self.controller.last_attempt = host.time.monotonic()
        self.assertFalse(self.controller.check())
        self.assertTrue(self.controller.reserve_check(force=True))
        self.assertTrue(self.controller.status()['checking'])


if __name__ == '__main__':
    unittest.main()
