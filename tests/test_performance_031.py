from unittest.mock import Mock
import unittest
from test_session_lifecycle import SessionLifecycleTests

class PerformanceSettingsTests(SessionLifecycleTests):
    def test_late_frame_comparison_setting_persists_and_reaches_native(self):
        app=self.app();app.launch=Mock()
        self.assertTrue(app.settings['late_frame_catchup'])
        app.action({'action':'start','late_frame_catchup':False})
        self.assertIn('--no-late-frame-catchup',app.launch.call_args.args[0])
        self.assertFalse(self.app().settings['late_frame_catchup'])
    def test_late_frame_setting_rejects_non_boolean(self):
        app=self.app()
        with self.assertRaisesRegex(ValueError,'late_frame_catchup'):
            app.action({'action':'start','late_frame_catchup':'false'})

def load_tests(loader,tests,pattern):
    return unittest.TestSuite(PerformanceSettingsTests(name) for name in
                             PerformanceSettingsTests.__dict__ if name.startswith('test_'))
