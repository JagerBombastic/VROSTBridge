import json
import math
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from host.control import HostControl


class HostChaperoneTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.directory = self.root / 'runtime' / 'alvr'
        self.directory.mkdir(parents=True)
        helper = self.root / 'host' / 'VROSTBridgeVRHelper.exe'
        helper.parent.mkdir(); helper.write_bytes(b'test helper')
        self.logs = []
        self.control = HostControl(self.directory, self.logs.append)
        self.control.active = True
        self.control.launch_generation = 1
        self.control._set(available=True, runtime_state='ready')
        self.environ = patch.dict(os.environ, {'LOCALAPPDATA': str(self.root/'state')})
        self.environ.start()
        self.calls = []
        self.changed = False
        def run(args, **kwargs):
            self.calls.append(args)
            if self.changed: Path(args[-1]).write_text('original')
            return Mock(returncode=0, stdout=json.dumps({'applied':True, 'changed':self.changed,
                        'width':float(args[2]), 'depth':float(args[3]), 'boundary':'rectangle'}))
        self.runner = patch('host.control.subprocess.run', side_effect=run)
        self.mock_run = self.runner.start()

    def tearDown(self):
        self.runner.stop(); self.environ.stop(); self.temp.cleanup()

    def test_same_bounds_reverified_without_false_backup_or_repeat_apply_log(self):
        self.control.apply_playspace({'width':3, 'depth':2.25})
        self.control.apply_playspace({'width':3, 'depth':2.25})
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(len(self.logs), 1)
        self.assertIn('verified unchanged', self.logs[0])
        self.assertIsNone(self.control.snapshot()['playspace']['backup'])

    def test_corrected_native_bounds_keep_real_backup_across_reconnect(self):
        self.changed = True
        self.control.apply_playspace({'width':3, 'depth':2.25})
        backup = self.control.snapshot()['playspace']['backup']
        self.assertTrue(Path(backup).is_file())
        self.changed = False
        self.control.apply_playspace({'width':3, 'depth':2.25})
        self.assertEqual(self.control.snapshot()['playspace']['backup'], backup)
        self.assertEqual(len(self.logs), 1)

    def test_new_runtime_does_not_reuse_previous_acceptance_or_backup(self):
        self.changed = True
        self.control.apply_playspace({'width':3, 'depth':2.25})
        self.control.launch_generation = 2
        self.changed = False
        self.control.apply_playspace({'width':3, 'depth':2.25})
        self.assertIsNone(self.control.snapshot()['playspace']['backup'])
        self.assertEqual(self.control.snapshot()['playspace']['runtime_generation'], 2)
        self.assertEqual(len(self.logs), 2)

    def test_changed_dimensions_rechecked_and_invalid_values_do_not_reach_helper(self):
        self.control.apply_playspace({'width':3, 'depth':2.25})
        self.changed = True
        self.control.apply_playspace({'width':4, 'depth':2.25})
        self.assertEqual(self.control.snapshot()['playspace']['width'], 4)
        for value in (None, [], {'width': True, 'depth':2}, {'width':math.nan, 'depth':2},
                      {'width':0, 'depth':2}, {'width':101, 'depth':2}):
            with self.assertRaises(ValueError):self.control.apply_playspace(value)
        self.assertEqual(len(self.calls), 2)

    def test_failure_or_unverified_dimensions_never_record_success(self):
        self.mock_run.side_effect = None
        self.mock_run.return_value = Mock(returncode=0, stdout=json.dumps(
            {'applied':True, 'changed':False, 'width':6, 'depth':4.5, 'boundary':'rectangle'}))
        with self.assertRaisesRegex(ValueError, 'not confirmed'):
            self.control.apply_playspace({'width':3, 'depth':2.25})
        self.assertIsNone(self.control.snapshot()['playspace'])

    def test_runtime_restarted_during_helper_does_not_record_old_success(self):
        original = self.mock_run.side_effect
        def restarted(*args, **kwargs):
            value = original(*args, **kwargs)
            self.control.launch_generation += 1
            return value
        self.mock_run.side_effect = restarted
        with self.assertRaisesRegex(ValueError, 'runtime changed'):
            self.control.apply_playspace({'width':3, 'depth':2.25})
        self.assertIsNone(self.control.snapshot()['playspace'])


if __name__ == '__main__': unittest.main()
