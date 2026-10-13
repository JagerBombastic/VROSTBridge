"""Quiet sign-in preparation waits for a Private LAN without repeating UAC."""
import threading
import unittest
from unittest.mock import Mock, patch

from launcher.app import Application


class FastWake:
    def __init__(self, after_wait=lambda _: None):
        self.delays = []
        self.closed = False
        self.after_wait = after_wait

    def is_set(self):
        return self.closed

    def set(self):
        self.closed = True

    def wait(self, seconds):
        self.delays.append(seconds)
        self.after_wait(self)
        return self.closed


class HostNetworkStartupTests(unittest.TestCase):
    def app(self, closing=None):
        app = Application.__new__(Application)
        app.role = 'host'
        app.autostart_attempted = False
        app.autostart_cancelled = threading.Event()
        app.closing = closing if closing is not None else FastWake()
        app.action_lock = threading.Lock()
        app.host_prepared = False
        app.host_preparation_attempted = False
        app.network_guard_time = 0
        app.startup_error = ''
        app.settings = {'auto_connect': False}
        app.action = Mock()
        app.setup_host = Mock()
        app.log = Mock()
        return app

    def test_network_arriving_after_sign_in_prepares_once_without_starting_runtime(self):
        app = self.app()
        with patch('launcher.app.require_private_home_network',
                   side_effect=[ValueError('No network'), ValueError('No network'), []]) as query, \
             patch('launcher.app.open_desktop') as browser:
            app.autostart(); app.autostart()
        self.assertEqual(query.call_count, 3)
        self.assertEqual(app.closing.delays, [1, 2])
        app.setup_host.assert_called_once_with('Firewall')
        app.action.assert_called_once_with({'action': 'check'})
        browser.assert_not_called()
        self.assertTrue(app.host_prepared)
        self.assertTrue(app.host_preparation_attempted)
        self.assertEqual(app.startup_error, '')
        self.assertFalse(app.settings['auto_connect'])

    def test_public_network_never_bypasses_guard_or_floods_logs(self):
        closing = FastWake(lambda wake: wake.set() if len(wake.delays) == 8 else None)
        app = self.app(closing)
        with patch('launcher.app.require_private_home_network', side_effect=ValueError('Public network')):
            app.autostart()
        self.assertEqual(closing.delays, [1, 2, 4, 8, 16, 30, 30, 30])
        app.setup_host.assert_not_called()
        self.assertFalse(app.host_prepared)
        self.assertFalse(app.host_preparation_attempted)
        self.assertIn('Public network', app.startup_error)
        self.assertEqual(app.log.call_count, 1)

    def test_failed_firewall_permission_is_attempted_only_once(self):
        app = self.app()
        app.setup_host.side_effect = ValueError('Firewall permission declined')
        with patch('launcher.app.require_private_home_network', side_effect=[ValueError('No LAN'), []]) as query:
            app.autostart()
            app.prepare_host_after_network()
        self.assertEqual(query.call_count, 2)
        app.setup_host.assert_called_once_with('Firewall')
        self.assertFalse(app.host_prepared)
        self.assertEqual(app.startup_error, 'Firewall permission declined')

    def test_readiness_recovery_does_not_clear_an_unrelated_error(self):
        app = self.app()
        app.closing.after_wait = lambda _: setattr(app, 'startup_error', 'Tray failed')
        with patch('launcher.app.require_private_home_network', side_effect=[ValueError('No LAN'), []]):
            app.autostart()
        self.assertTrue(app.host_prepared)
        self.assertEqual(app.startup_error, 'Tray failed')

    def test_setup_failure_does_not_overwrite_unrelated_error(self):
        app = self.app()
        app.startup_error = 'Native runtime files damaged'
        app.setup_host.side_effect = ValueError('Firewall permission declined')
        with patch('launcher.app.require_private_home_network', return_value=[]):
            app.autostart()
        self.assertEqual(app.startup_error, 'Native runtime files damaged')
        app.setup_host.assert_called_once_with('Firewall')

    def test_manual_preparation_while_waiting_prevents_an_extra_setup_attempt(self):
        app = self.app()
        def manual(_):
            app.prepare_host()
        app.closing.after_wait = manual
        with patch('launcher.app.require_private_home_network', side_effect=[ValueError('No LAN'), []]) as query:
            app.autostart()
        self.assertEqual(query.call_count, 2)
        app.setup_host.assert_called_once_with('Firewall')
        self.assertTrue(app.host_prepared)
        self.assertEqual(app.startup_error, '')

    def test_closing_interrupts_the_wait_without_another_os_query(self):
        closing = threading.Event()
        app = self.app(closing)
        entered = threading.Event()
        def no_network():
            entered.set()
            raise ValueError('No network')
        with patch('launcher.app.require_private_home_network', side_effect=no_network) as query:
            worker = threading.Thread(target=app.autostart)
            worker.start()
            self.assertTrue(entered.wait(1))
            closing.set()
            worker.join(1)
            self.assertFalse(worker.is_alive())
        self.assertEqual(query.call_count, 1)
        app.setup_host.assert_not_called()

    def test_closing_during_network_check_prevents_firewall_attempt(self):
        app = self.app()
        def ready_but_closing():
            app.closing.set()
        with patch('launcher.app.require_private_home_network', side_effect=ready_but_closing):
            app.autostart()
        app.setup_host.assert_not_called()
        self.assertFalse(app.host_prepared)
        self.assertEqual(app.startup_error, '')


if __name__ == '__main__':
    unittest.main()
