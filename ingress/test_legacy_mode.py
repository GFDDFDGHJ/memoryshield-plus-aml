"""Invented lease/concurrency controls; no configuration files or live systems."""
from concurrent.futures import ThreadPoolExecutor
import json
import threading
import unittest

try:
    from .legacy_mode import LegacyModeController, LegacyModeBusy, LegacyModeUnavailable
except ImportError:
    from legacy_mode import LegacyModeController, LegacyModeBusy, LegacyModeUnavailable


class Reader:
    def __init__(self, value='textual'):
        self.value = value
        self.error = None
        self.calls = 0
        self.guard = threading.Lock()

    def __call__(self):
        with self.guard:
            self.calls += 1
            if self.error is not None:
                raise self.error
            return self.value


class LegacyModeTests(unittest.TestCase):
    def setUp(self):
        self.reader = Reader()
        self.controller = LegacyModeController(self.reader)

    def test_defaults_text_and_constructor_does_not_read_or_apply_configuration(self):
        self.assertEqual(self.reader.calls, 0)
        self.assertEqual(self.controller.status(), {
            'legacy_key_target':'textual','requested_legacy_key_target':'textual',
            'legacy_active_requests':0,'legacy_config_valid':True,'legacy_switch_pending':False})
        self.assertEqual(self.reader.calls, 1)

    def test_idle_request_applies_valid_configuration_and_preserves_canonical_mode(self):
        self.reader.value = ' coding \r\n'
        with self.controller.request() as mode:
            self.assertEqual(mode, 'coding')
            self.assertEqual(self.controller.status()['legacy_active_requests'], 1)
        status = self.controller.status()
        self.assertEqual(status['legacy_key_target'], 'coding')
        self.assertEqual(status['legacy_active_requests'], 0)

    def test_idle_status_can_apply_a_requested_change(self):
        self.reader.value = 'coding'
        status = self.controller.status()
        self.assertEqual(status['legacy_key_target'], 'coding')
        self.assertEqual(status['requested_legacy_key_target'], 'coding')
        self.assertFalse(status['legacy_switch_pending'])

    def test_active_request_is_pinned_and_changed_mode_refuses_new_request(self):
        with self.controller.request() as original:
            self.reader.value = 'coding'
            status = self.controller.status()
            self.assertEqual(status['legacy_key_target'], 'textual')
            self.assertEqual(status['requested_legacy_key_target'], 'coding')
            self.assertEqual(status['legacy_active_requests'], 1)
            self.assertTrue(status['legacy_switch_pending'])
            with self.assertRaises(LegacyModeBusy):
                with self.controller.request():
                    self.fail('a pending backend change cannot acquire a new lease')
            self.assertEqual(original, 'textual')
            self.assertEqual(self.controller.status()['legacy_active_requests'], 1)
        self.assertEqual(self.controller.status()['legacy_key_target'], 'coding')

    def test_pending_target_can_be_cancelled_without_changing_existing_lease(self):
        with self.controller.request() as mode:
            self.reader.value = 'coding'
            self.assertTrue(self.controller.status()['legacy_switch_pending'])
            self.reader.value = 'textual'
            with self.controller.request() as nested:
                self.assertEqual((mode, nested), ('textual', 'textual'))
                self.assertEqual(self.controller.status()['legacy_active_requests'], 2)
            self.assertFalse(self.controller.status()['legacy_switch_pending'])
        self.assertEqual(self.controller.status()['legacy_active_requests'], 0)

    def test_exception_inside_request_always_releases_lease(self):
        class SyntheticFailure(Exception):
            pass
        with self.assertRaises(SyntheticFailure):
            with self.controller.request():
                self.reader.value = 'coding'
                raise SyntheticFailure('invented request failure')
        self.assertEqual(self.controller.status()['legacy_active_requests'], 0)
        self.assertEqual(self.controller.status()['legacy_key_target'], 'coding')

    def test_base_exception_also_releases_lease(self):
        class SyntheticCancellation(BaseException):
            pass
        with self.assertRaises(SyntheticCancellation):
            with self.controller.request():
                raise SyntheticCancellation()
        self.assertEqual(self.controller.status()['legacy_active_requests'], 0)

    def test_invalid_reader_values_are_unavailable_and_status_never_echoes_them(self):
        for value in ('', 'CODING', 'coding\nprivate-detail', None, 1, True, {}, []):
            with self.subTest(type=type(value).__name__):
                self.reader.value = value
                status = self.controller.status()
                self.assertEqual(status, {
                    'legacy_key_target':'textual','requested_legacy_key_target':None,
                    'legacy_active_requests':0,'legacy_config_valid':False,'legacy_switch_pending':False})
                with self.assertRaises(LegacyModeUnavailable) as captured:
                    with self.controller.request():
                        self.fail('invalid configuration cannot enter a request')
                self.assertNotIn('private-detail', str(captured.exception))
                self.assertNotIn('private-detail', json.dumps(status))
        self.reader.value = 'textual'
        with self.controller.request() as mode:
            self.assertEqual(mode, 'textual')

    def test_reader_exception_is_sanitized_and_does_not_modify_current_mode(self):
        self.reader.error = OSError('SYNTHETIC-PRIVATE-PATH-AND-CONTENTS')
        status = self.controller.status()
        self.assertFalse(status['legacy_config_valid'])
        self.assertEqual(status['legacy_key_target'], 'textual')
        with self.assertRaises(LegacyModeUnavailable) as captured:
            with self.controller.request():
                self.fail('a failed reader cannot enter')
        self.assertNotIn('SYNTHETIC-PRIVATE', str(captured.exception))
        self.assertNotIn('SYNTHETIC-PRIVATE', json.dumps(status))
        self.reader.error = None
        self.assertEqual(self.controller.status()['legacy_active_requests'], 0)

    def test_invalid_configuration_during_active_request_never_redirects_it(self):
        with self.controller.request() as mode:
            self.reader.value = 'INVALID-SYNTHETIC'
            self.assertEqual(mode, 'textual')
            self.assertEqual(self.controller.status()['legacy_active_requests'], 1)
            self.assertFalse(self.controller.status()['legacy_config_valid'])
            with self.assertRaises(LegacyModeUnavailable):
                with self.controller.request():
                    self.fail('invalid setting cannot admit a second request')
            self.reader.value = 'coding'
            self.assertTrue(self.controller.status()['legacy_switch_pending'])
        self.assertEqual(self.controller.status()['legacy_key_target'], 'coding')

    def test_sixteen_active_requests_finish_on_original_mode_before_switch(self):
        entered = threading.Barrier(17)
        release = threading.Event()
        def original_request(_):
            with self.controller.request() as mode:
                entered.wait(timeout=10)
                self.assertTrue(release.wait(timeout=10))
                return mode
        with ThreadPoolExecutor(max_workers=16) as pool:
            futures = [pool.submit(original_request, index) for index in range(16)]
            try:
                entered.wait(timeout=10)
                self.assertEqual(self.controller.status()['legacy_active_requests'], 16)
                self.reader.value = 'coding'
                status = self.controller.status()
                self.assertEqual(status['legacy_key_target'], 'textual')
                self.assertTrue(status['legacy_switch_pending'])
                for _ in range(16):
                    with self.assertRaises(LegacyModeBusy):
                        with self.controller.request():
                            self.fail('pending switch must not create a Coding lease')
                self.assertEqual(self.controller.status()['legacy_active_requests'], 16)
            finally:
                release.set()
            self.assertEqual([future.result(timeout=10) for future in futures], ['textual']*16)
        self.assertEqual(self.controller.status()['legacy_active_requests'], 0)
        self.assertEqual(self.controller.status()['legacy_key_target'], 'coding')

    def test_sixteen_simultaneous_idle_entries_apply_new_mode_once_and_remain_consistent(self):
        self.reader.value = 'coding'
        entered = threading.Barrier(17)
        release = threading.Event()
        def new_request(_):
            with self.controller.request() as mode:
                entered.wait(timeout=10)
                self.assertTrue(release.wait(timeout=10))
                return mode
        with ThreadPoolExecutor(max_workers=16) as pool:
            futures = [pool.submit(new_request, index) for index in range(16)]
            try:
                entered.wait(timeout=10)
                status = self.controller.status()
                self.assertEqual(status['legacy_key_target'], 'coding')
                self.assertEqual(status['legacy_active_requests'], 16)
                self.assertFalse(status['legacy_switch_pending'])
            finally:
                release.set()
            self.assertEqual([future.result(timeout=10) for future in futures], ['coding']*16)
        self.assertEqual(self.controller.status()['legacy_active_requests'], 0)

    def test_invalid_constructor_arguments_fail_without_calling_reader(self):
        with self.assertRaises(TypeError):LegacyModeController(None)
        with self.assertRaises(ValueError):LegacyModeController(self.reader, initial='other')
        self.assertEqual(self.reader.calls, 0)


if __name__ == '__main__':
    unittest.main()
