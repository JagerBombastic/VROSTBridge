"""Startup races reproduced from the 0.2.5/0.2.6 lifecycle controller."""
import json,threading,time,unittest,tempfile
from pathlib import Path
from unittest.mock import Mock,patch
from test_host_control import HostControlTests
from test_session_link import SessionTests,eventually
from host.control import HostControl
from launcher.session_link import SessionClient,SessionError

class NegotiationRaceTests(HostControlTests):
    # Reuse the real HTTP driver fixture; inherited tests remain in the original
    # suite only (load_tests below selects these new cases).
    def test_partial_negotiation_write_and_exit_wait_for_restart(self):
        self.start();self.path.write_text('{');self.driver.active=False
        self.wait_for(lambda:self.control.snapshot()['runtime_phase']=='recovering')
        self.assertEqual(self.control.snapshot()['error'],'')
        self.document['restart_settings_hash']+=1;self.path.write_text(json.dumps(self.document))
        self.wait_for(lambda:self.driver.launches==2 and self.control.snapshot()['available'])
        self.assertEqual(self.control.snapshot()['negotiation_restarts'],1)
        self.assertEqual(self.control.snapshot()['error'],'')

    def test_first_negotiation_can_finish_before_first_process_sample(self):
        def quick_exit():
            self.driver.launch();self.driver.active=False
            self.document['restart_settings_hash']+=1;self.path.write_text(json.dumps(self.document))
            self.control.launch=self.driver.launch
        self.control.launch=quick_exit
        self.control.start()
        self.wait_for(lambda:self.driver.launches==2 and self.control.snapshot()['available'])
        self.assertEqual(self.control.snapshot()['negotiation_restarts'],1)

    def test_actual_exit_still_fails_after_bounded_grace(self):
        self.control.RUNTIME_LOSS_GRACE=.3;self.start();self.driver.active=False
        self.wait_for(lambda:self.control.snapshot()['runtime_state']=='error')
        self.assertIn('without a negotiated restart',self.control.snapshot()['error'])
        self.assertEqual(self.driver.launches,1)

    def test_stop_during_negotiation_grace_does_not_relaunch(self):
        self.start();self.path.write_text('{');self.driver.active=False
        self.wait_for(lambda:self.control.snapshot()['runtime_phase']=='recovering')
        self.control.stop()
        self.assertEqual(self.driver.launches,1)
        self.assertEqual(self.control.snapshot()['runtime_state'],'stopped')

class BrokerStartupTests(SessionTests):
    def test_launch_acceptance_is_not_driver_readiness_and_restart_keeps_lease(self):
        runtime={'runtime_state':'starting','available':False,'message':'Driver starting'}
        self.host.status_callback=lambda:dict(runtime)
        self.client.start(host_ip='127.0.0.1');eventually(lambda:self.started.call_count==1)
        self.assertEqual(self.host.snapshot()['state'],'starting')
        lease=self.host.snapshot()['lease_id']
        runtime.update(runtime_state='ready',available=True,message='Driver ready')
        eventually(lambda:self.host.snapshot()['state']=='running')
        runtime.update(runtime_state='starting',available=False,message='Negotiated restart')
        eventually(lambda:self.host.snapshot()['state']=='starting')
        self.assertEqual(self.client.heartbeat()['active'],True)
        self.assertEqual(self.host.snapshot()['lease_id'],lease)
        self.stopped.assert_not_called()
        runtime.update(runtime_state='ready',available=True)
        eventually(lambda:self.host.snapshot()['state']=='running')

    def test_fast_start_failure_is_observed_while_broker_is_starting(self):
        self.host.status_callback=lambda:{'runtime_state':'error','error':'Bootstrap failed'}
        self.client.start(host_ip='127.0.0.1')
        eventually(lambda:self.host.snapshot()['state']=='waiting')
        self.stopped.assert_called_once()
        self.assertIn('Bootstrap failed',self.client.heartbeat()['message'])

class ProcessQuietTests(unittest.TestCase):
    def test_late_monitor_delays_next_launch_until_it_is_quiet(self):
        with tempfile.TemporaryDirectory() as d:
            control=HostControl(Path(d),lambda _:None,lambda:None,lambda:set())
            control.api=Mock();control.api.request.return_value='v';control.runtime_observed=True;control.expected_version='v';control.SHUTDOWN_TIMEOUT=4
            now=[0.0];live={};monitor={'pid':88,'path':str(Path(d)/'vrmonitor.exe'),'created':1}
            def capture(_):
                if .25<=now[0]<1.5:live[88]=monitor;control.identities[88]=monitor
                else:live.clear()
            def sleep(n):now[0]+=n
            control._capture_identities=capture
            with patch('host.control.identify',side_effect=live.get),patch('host.control.time.monotonic',side_effect=lambda:now[0]),patch('host.control.time.sleep',side_effect=sleep):
                control._shutdown_runtime()
            self.assertGreaterEqual(now[0],2.5)
            self.assertEqual(control.identities,{})

class StaleHeartbeatTests(unittest.TestCase):
    def test_previous_lease_error_cannot_cancel_new_session(self):
        client=SessionClient.__new__(SessionClient);client.lock=threading.RLock();client.host_ip='192.168.1.2';client.host_id='a'*32;client.lease_id='b'*32;client.state='starting';client.message='New launch';client.last_seen=time.monotonic()
        client.closed=Mock();client.closed.wait.side_effect=[False,True]
        def old_request():
            client.lease_id='c'*32
            raise SessionError('Old lease no longer owns the host')
        client.heartbeat=old_request;client._heartbeats()
        self.assertEqual(client.state,'starting');self.assertEqual(client.message,'New launch')

def load_tests(loader,tests,pattern):
    suite=unittest.TestSuite()
    for cls in (NegotiationRaceTests,BrokerStartupTests,ProcessQuietTests,StaleHeartbeatTests):
        for name in cls.__dict__:
            if name.startswith('test_'):suite.addTest(cls(name))
    return suite
