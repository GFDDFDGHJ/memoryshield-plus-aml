"""Offline fake-health and temporary-file controls; never contact a live gateway."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

try:
    from . import set_legacy_target as switch
except ImportError:
    import set_legacy_target as switch


def health(target='textual', **changes):
    result = {'status': 'ok', 'system': 'MemoryShield+ track gateway',
              'version': 'v1.1.0', 'legacy_key_target': target,
              'requested_legacy_key_target': target, 'legacy_config_valid': True,
              'legacy_active_requests': 0, 'active_memory_requests': 0,
              'legacy_switch_pending': False}
    result.update(changes)
    return result


class LegacyTargetCliTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'legacy_target.json'
        self.original = b'{"legacy_key_target":"textual"}\n'
        self.path.write_bytes(self.original)

    def switch(self, *, fetch=None, target='coding', **options):
        if fetch is None:
            fetch = Mock(side_effect=[health(), health(target)])
        return switch.set_legacy_target(target, evaluation_idle=True,
                                        config_path=self.path, fetch_health=fetch, **options)

    def assert_clean(self):
        self.assertFalse(self.path.with_name('legacy_target.switch.lock').exists())
        self.assertEqual(list(self.path.parent.glob('.legacy-target-*.tmp')), [])

    def test_atomic_success_minimal_json_and_whitelisted_return(self):
        fetch = Mock(side_effect=[health(), health('coding', invented_private='must-not-return')])
        with patch.object(switch.os, 'replace', wraps=switch.os.replace) as replace:
            result = self.switch(fetch=fetch)
        self.assertEqual(json.loads(self.path.read_text()), {'legacy_key_target': 'coding'})
        self.assertEqual(result['legacy_key_target'], 'coding')
        self.assertNotIn('invented_private', result)
        self.assertEqual(fetch.call_count, 2)
        source, destination = replace.call_args.args
        self.assertEqual(Path(source).parent, self.path.parent)
        self.assertEqual(destination, self.path)
        self.assert_clean()

    def test_missing_or_invalid_idle_configuration_can_be_repaired(self):
        for original in (None, b'not valid json'):
            with self.subTest(original=original):
                self.path.unlink(missing_ok=True)
                if original is not None:
                    self.path.write_bytes(original)
                self.switch(fetch=Mock(side_effect=[health(legacy_config_valid=False,
                    requested_legacy_key_target=None), health('coding')]))
                self.assertEqual(json.loads(self.path.read_text()), {'legacy_key_target': 'coding'})
                self.assert_clean()

    def test_busy_pending_incompatible_and_malformed_health_never_write(self):
        variants = [health(legacy_active_requests=1), health(active_memory_requests=1),
            health(legacy_switch_pending=True), health(version='v1.0.0'),
            health(system='invented other system'), health(status='starting'),
            health(legacy_config_valid=None), health(legacy_active_requests=False),
            health(active_memory_requests=False), health(active_memory_requests=-1),
            health(requested_legacy_key_target='coding'), health(legacy_key_target='unknown'),
            health(legacy_config_valid=False, requested_legacy_key_target='coding'),
            [], None, {}]
        for payload in variants:
            with self.subTest(payload=payload):
                fetch = Mock(return_value=payload)
                with self.assertRaises(switch.LegacyTargetSwitchError) as raised:
                    self.switch(fetch=fetch)
                self.assertFalse(raised.exception.configuration_written)
                self.assertEqual(self.path.read_bytes(), self.original)
                self.assertEqual(fetch.call_count, 1)
                self.assert_clean()

    def test_health_errors_are_sanitized_and_preserve_existing_file(self):
        fetch = Mock(side_effect=RuntimeError('INVENTED_PRIVATE_KEY_AND_RESPONSE'))
        with self.assertRaises(switch.LegacyTargetSwitchError) as raised:
            self.switch(fetch=fetch)
        self.assertNotIn('PRIVATE', str(raised.exception))
        self.assertEqual(self.path.read_bytes(), self.original)
        self.assert_clean()

    def test_existing_lock_is_refused_and_never_removed(self):
        lock = self.path.with_name('legacy_target.switch.lock')
        lock.write_text('invented other operator', encoding='utf-8')
        fetch = Mock()
        with self.assertRaises(switch.LegacyTargetSwitchError):
            self.switch(fetch=fetch)
        self.assertEqual(lock.read_text(), 'invented other operator')
        fetch.assert_not_called()
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_concurrent_switch_is_rejected_while_first_holds_lock(self):
        entered = threading.Event()
        release = threading.Event()
        calls = 0

        def fetch():
            nonlocal calls
            calls += 1
            if calls == 1:
                entered.set()
                if not release.wait(5):
                    raise RuntimeError('invented timeout')
                return health()
            return health('coding')

        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(self.switch, fetch=fetch)
            self.assertTrue(entered.wait(5))
            try:
                second_fetch = Mock()
                with self.assertRaises(switch.LegacyTargetSwitchError):
                    self.switch(fetch=second_fetch, target='textual')
                second_fetch.assert_not_called()
            finally:
                release.set()
            self.assertEqual(first.result(timeout=5)['legacy_key_target'], 'coding')
        self.assert_clean()

    def test_fsync_and_replace_failures_leave_original_and_cleanup(self):
        for operation in ('fsync', 'replace'):
            with self.subTest(operation=operation):
                fetch = Mock(return_value=health())
                with patch.object(switch.os, operation, side_effect=OSError('INVENTED_PRIVATE')):
                    with self.assertRaises(switch.LegacyTargetSwitchError) as raised:
                        self.switch(fetch=fetch)
                self.assertNotIn('PRIVATE', str(raised.exception))
                self.assertFalse(raised.exception.configuration_written)
                self.assertEqual(fetch.call_count, 1)
                self.assertEqual(self.path.read_bytes(), self.original)
                self.assert_clean()

    def test_replace_observes_original_until_complete_fsynced_temp_is_ready(self):
        real_replace = switch.os.replace
        events = []
        real_fsync = switch.os.fsync

        def fsync(descriptor):
            events.append('fsync')
            return real_fsync(descriptor)

        def replace(source, destination):
            self.assertEqual(events, ['fsync'])
            self.assertEqual(self.path.read_bytes(), self.original)
            self.assertEqual(json.loads(Path(source).read_text()), {'legacy_key_target': 'coding'})
            return real_replace(source, destination)

        with patch.object(switch.os, 'fsync', side_effect=fsync), \
                patch.object(switch.os, 'replace', side_effect=replace):
            self.switch()
        self.assert_clean()

    def test_post_write_mismatch_busy_invalid_or_transport_error_never_roll_back(self):
        variants = [health(), health('coding', active_memory_requests=1),
            health('coding', legacy_active_requests=1),
            health('coding', legacy_switch_pending=True),
            health('coding', legacy_config_valid=False, requested_legacy_key_target=None),
            health('coding', requested_legacy_key_target='textual'),
            RuntimeError('INVENTED_PRIVATE_RESPONSE')]
        for payload in variants:
            with self.subTest(payload=type(payload).__name__):
                self.path.write_bytes(self.original)
                with self.assertRaises(switch.LegacyTargetSwitchError) as raised:
                    self.switch(fetch=Mock(side_effect=[health(), payload]))
                self.assertTrue(raised.exception.configuration_written)
                self.assertIn('written', str(raised.exception))
                self.assertNotIn('PRIVATE', str(raised.exception))
                self.assertEqual(json.loads(self.path.read_text()), {'legacy_key_target': 'coding'})
                self.assert_clean()

    def test_target_and_required_idle_assertion_validate_before_any_io(self):
        with patch.object(switch.os, 'open') as opening:
            for target in ('Coding', 'unknown', None, ''):
                with self.assertRaises(switch.LegacyTargetSwitchError):
                    switch.set_legacy_target(target, evaluation_idle=True, config_path=self.path)
            for idle in (False, None, 'yes', 1):
                with self.assertRaises(switch.LegacyTargetSwitchError):
                    switch.set_legacy_target('coding', evaluation_idle=idle, config_path=self.path)
            opening.assert_not_called()

    def test_fixed_http_health_transport_has_no_proxy_or_redirects(self):
        client = Mock()
        response = Mock(status_code=200)
        response.json.return_value = health()
        client.get.return_value = response
        with patch.object(switch.httpx, 'Client') as factory:
            factory.return_value.__enter__.return_value = client
            self.assertEqual(switch.fetch_gateway_health(), health())
        self.assertIs(factory.call_args.kwargs['trust_env'], False)
        self.assertIs(factory.call_args.kwargs['follow_redirects'], False)
        self.assertLessEqual(factory.call_args.kwargs['timeout'].read, 5)
        client.get.assert_called_once_with('http://127.0.0.1:8282/__gateway_health')

    def test_http_bad_status_invalid_json_and_exception_are_sanitized(self):
        for failure in ('status', 'json', 'transport', 'shape'):
            with self.subTest(failure=failure), patch.object(switch.httpx, 'Client') as factory:
                client = factory.return_value.__enter__.return_value
                response = client.get.return_value
                response.status_code = 503 if failure == 'status' else 200
                response.json.return_value = [] if failure == 'shape' else health()
                if failure == 'json':
                    response.json.side_effect = ValueError('INVENTED_PRIVATE_BODY')
                if failure == 'transport':
                    client.get.side_effect = OSError('INVENTED_PRIVATE_URL')
                with self.assertRaises(switch.LegacyTargetSwitchError) as raised:
                    switch.fetch_gateway_health()
                self.assertNotIn('PRIVATE', str(raised.exception))

    def test_cli_requires_idle_and_rejects_custom_path_or_upstream(self):
        for arguments in (['coding'], ['coding', '--evaluation-idle', '--config-path', 'elsewhere'],
                          ['coding', '--evaluation-idle', '--url', 'http://example.invalid'],
                          ['unknown', '--evaluation-idle']):
            with self.subTest(arguments=arguments), \
                    patch.object(switch, 'set_legacy_target') as setter, redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as raised:
                    switch.main(arguments)
                self.assertEqual(raised.exception.code, 2)
                setter.assert_not_called()

    def test_cli_success_and_post_write_failure_do_not_false_report(self):
        with patch.object(switch, 'set_legacy_target', return_value={'legacy_key_target': 'coding'}) as setter:
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(switch.main(['coding', '--evaluation-idle']), 0)
            setter.assert_called_once_with('coding', evaluation_idle=True)
            self.assertIn('confirmed: coding', output.getvalue())
        with patch.object(switch, 'set_legacy_target', side_effect=switch.LegacyTargetSwitchError(
                'Configuration was written, but not confirmed.', configuration_written=True)):
            output, errors = io.StringIO(), io.StringIO()
            with redirect_stdout(output), redirect_stderr(errors):
                self.assertEqual(switch.main(['textual', '--evaluation-idle']), 1)
            self.assertEqual(output.getvalue(), '')
            self.assertIn('not confirmed', errors.getvalue())


if __name__ == '__main__':
    unittest.main()
