"""Only temporary operator configuration; no runtime keys or network."""
import json
from pathlib import Path
import tempfile
import unittest

from gateway import load_legacy_target
from legacy_mode import LegacyModeController, LegacyModeUnavailable


class LocalTargetTests(unittest.TestCase):
    def test_only_canonical_textual_or_coding_modes_are_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'mode.json'
            for target in ('textual','coding'):
                path.write_bytes(json.dumps({'legacy_key_target':target}).encode('utf-8'))
                self.assertEqual(load_legacy_target(path),target)

    def test_missing_oversized_malformed_and_noncanonical_file_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'mode.json'
            controller=LegacyModeController(lambda:load_legacy_target(path),initial='coding')
            def rejected():
                self.assertFalse(controller.status()['legacy_config_valid'])
                self.assertEqual(controller.status()['legacy_key_target'],'coding')
                with self.assertRaises(LegacyModeUnavailable):
                    with controller.request():self.fail('Unavailable mode admitted a request')
            rejected()
            for content in (b'x'*4097,b'{',b'null',b'[]',b'{}',b'\xff',
                    b'{"legacy_key_target": []}',b'{"legacy_key_target": "Coding"}',
                    b'{"legacy_key_target": " textual "}'):
                path.write_bytes(content)
                rejected()


if __name__=='__main__':unittest.main()
