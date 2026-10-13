"""Current-session Steam quit classification, including rotated/stale logs."""
import datetime
from pathlib import Path
import tempfile
import unittest
from host.control import SteamShutdownObserver
from tests import test_host_control as host_fixture


class SteamShutdownObserverTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'vrserver.txt'
        self.now = datetime.datetime(2026, 9, 28, 0, 36, 0).timestamp()
        self.observer = SteamShutdownObserver(lambda: [self.path], lambda: self.now)

    def message(self, delta=0, message='Process 23440: steam has initiated a quit all'):
        stamp = datetime.datetime.fromtimestamp(self.now + delta)
        return stamp.strftime('%a %b %d %Y %H:%M:%S') + '.251 [Info] - ' + message + '\n'

    def test_previous_session_quit_is_ignored_but_new_split_write_is_detected(self):
        self.path.write_text(self.message(-30))
        self.observer.reset()
        self.assertIsNone(self.observer.poll())
        self.now += 2
        message = self.message()
        with self.path.open('a') as stream: stream.write(message[:25])
        self.assertIsNone(self.observer.poll())
        with self.path.open('a') as stream: stream.write(message[25:])
        self.assertEqual(self.observer.poll()['reason'], 'steam-quit')

    def test_old_marker_in_recreated_log_cannot_turn_crash_into_clean_exit(self):
        self.path.write_text('before launch\n' * 20)
        self.observer.reset()
        self.path.write_text(self.message(-120) + 'new runtime chatter\n' * 40)
        self.assertIsNone(self.observer.poll())
        with self.path.open('a') as stream: stream.write(self.message())
        self.assertEqual(self.observer.poll()['reason'], 'steam-quit')

    def test_file_appearing_after_launch_is_supported(self):
        self.observer.reset()
        self.path.write_text(self.message())
        self.assertEqual(self.observer.poll()['reason'], 'steam-quit')

    def test_generic_lost_process_restart_or_quit_word_is_not_evidence(self):
        self.observer.reset()
        self.path.write_text(self.message(message='Lost master process 11180 for an unknown reason. Quitting all immediately.') +
                             self.message(message='Process 22: vrmonitor has initiated a quit all') +
                             self.message(message='Process 22: steam has initiated a quit alligator'))
        self.assertIsNone(self.observer.poll())

    def test_new_launch_does_not_reuse_previous_request(self):
        self.observer.reset(); self.path.write_text(self.message())
        self.assertIsNotNone(self.observer.poll())
        self.now += 2; self.observer.reset()
        self.assertIsNone(self.observer.poll())

    def test_incremental_read_is_bounded(self):
        self.observer.reset()
        self.path.write_text(('ordinary log line\n' * 6000) + self.message())
        self.assertIsNone(self.observer.poll())
        self.assertLessEqual(self.observer.cursors[self.path][1], self.observer.MAX_READ)
        self.assertIsNotNone(self.observer.poll())


class ExternalSteamExitTests(unittest.TestCase):
    setUp = host_fixture.HostControlTests.setUp
    tearDown = host_fixture.HostControlTests.tearDown
    wait_for = host_fixture.HostControlTests.wait_for
    start = host_fixture.HostControlTests.start
    # Reuse the real loopback ALVR fixture and lifecycle worker, without copying
    # the rest of the inherited host-control test methods into discovery.
    def test_current_steam_quit_stops_owned_runtime_without_failure(self):
        path = self.path.parent / 'vrserver.txt'; path.write_text('old log\n')
        self.control.shutdown_observer = SteamShutdownObserver(lambda: [path])
        self.start()
        stamp = datetime.datetime.now().strftime('%a %b %d %Y %H:%M:%S.%f')[:-3]
        with path.open('a') as stream:
            stream.write(stamp + ' [Info] - Process 23440: steam has initiated a quit all\n')
        self.wait_for(lambda: not self.control.active)
        status = self.control.snapshot()
        self.assertEqual(status['runtime_state'], 'stopped')
        self.assertEqual(status['shutdown_reason'], 'steam-quit')
        self.assertEqual(status['error'], '')
        self.assertFalse(self.driver.active)
        self.assertIn(('/api/steamvr/shutdown', None), self.driver.calls)

    def test_crash_after_stale_steam_quit_remains_failure(self):
        path = self.path.parent / 'vrserver.txt'
        path.write_text('Mon Sep 28 2020 00:00:00.000 [Info] - Process 23440: steam has initiated a quit all\n')
        self.control.shutdown_observer = SteamShutdownObserver(lambda: [path])
        self.control.RUNTIME_LOSS_GRACE = .05
        self.start(); self.driver.active = False
        self.wait_for(lambda: not self.control.active)
        self.assertEqual(self.control.snapshot()['runtime_state'], 'error')
        self.assertEqual(self.control.snapshot()['shutdown_reason'], 'failure')

if __name__ == '__main__': unittest.main()
