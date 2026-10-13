"""Regressions grounded in the 27 September client/host failure reports."""
import os, unittest
from unittest.mock import Mock, patch
from test_session_lifecycle import SessionLifecycleTests
from launcher.windows_tray import tray_view

class ClientStartupTests(SessionLifecycleTests):
    def test_native_and_steamvr_children_do_not_inherit_frozen_private_libraries(self):
        app=self.app();app.launch=Mock()
        app.lease_runtime.prepare=Mock(return_value={'WAYLAND_DISPLAY':'bridge-lease'})
        with patch.dict(os.environ,{'LD_LIBRARY_PATH':'/frozen/_internal','LD_LIBRARY_PATH_ORIG':'/system/optional'}):
            app.action({'action':'start'})
        env=app.launch.call_args.args[1]
        self.assertEqual(env['LD_LIBRARY_PATH'],'/system/optional')
        self.assertEqual(env['WAYLAND_DISPLAY'],'bridge-lease')
        self.assertNotIn('_internal',env['LD_LIBRARY_PATH'])
    def test_clean_native_exit_without_stop_is_reported_before_cleanup(self):
        app=self.app();process=Mock();process.stdout=[];process.wait.return_value=0
        process.poll.return_value=0;app.process=process;seen=[]
        app.session_link.stop.side_effect=lambda:seen.append(app.startup_error)
        app.watch(process)
        self.assertIn('ended unexpectedly (exit 0)',seen[0])
    def test_explicit_stop_is_not_reported_as_unexpected_exit(self):
        app=self.app();process=Mock();process.stdout=[];process.wait.return_value=-15
        process.poll.return_value=-15;app.process=process;app.stopping=process
        app.watch(process);self.assertEqual(app.startup_error,'')

class TrayStatusTests(unittest.TestCase):
    def test_idle_menu_does_not_claim_a_stream_is_continuing(self):
        label,details=tray_view({'running':False,'audio':{'warning':'No route'}})
        self.assertFalse(any('continues' in x for x in details))

def load_tests(loader,tests,pattern):
    suite=unittest.TestSuite()
    for cls in (ClientStartupTests,TrayStatusTests):
        for name in cls.__dict__:
            if name.startswith('test_'):suite.addTest(cls(name))
    return suite
