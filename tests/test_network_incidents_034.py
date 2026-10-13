"""Incident evidence stays reviewable after long streams, hitches and restart."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from launcher.diagnostics import Journal
from launcher.network_path import transport_sockets


class IncidentTests(unittest.TestCase):
    def test_hitches_cannot_replace_transport_failure_and_restart_keeps_it(self):
        with tempfile.TemporaryDirectory() as folder:
            journal=Journal(folder,'client')
            with patch('launcher.diagnostics.time.monotonic',return_value=100):
                journal.record('log','TIMING pre_timeout=known')
                journal.record('log','PRESENTATION_INCIDENT '+json.dumps({'max_ms':90}))
            with patch('launcher.diagnostics.time.monotonic',return_value=101):
                journal.record('log','TRANSPORT_INCIDENT control timeout silence_ms=2001')
            for tick in range(120,330,15):
                with patch('launcher.diagnostics.time.monotonic',return_value=tick):
                    journal.record('log','PRESENTATION_INCIDENT '+json.dumps({'max_ms':tick}))
            for _ in range(1600):journal.record('log','TIMING normal=1')
            report=journal.report()
            self.assertNotIn('pre_timeout',json.dumps(report['events']))
            transport=[item for item in report['incidents'] if item['kind']=='transport']
            self.assertEqual(len(transport),1)
            self.assertIn('pre_timeout',json.dumps(transport))
            self.assertIn('silence_ms=2001',json.dumps(transport))
            self.assertEqual(len([item for item in report['incidents'] if item['kind']=='presentation']),2)
            reopened=Journal(folder,'client').report()
            self.assertIn('silence_ms=2001',json.dumps(reopened['incidents']))
            self.assertNotEqual(reopened['session'],transport[0]['session'])

    def test_host_severity_and_repeated_log_trigger_capture(self):
        with tempfile.TemporaryDirectory() as folder:
            journal=Journal(folder,'host')
            journal.record('alvr-log','Warning: TRANSPORT_INCIDENT control stalled')
            self.assertEqual(journal.report()['incidents'][0]['kind'],'transport')
            self.assertEqual(Journal._incident_reason('alvr-log-repeated',{'message':'Info: Client disconnected. Timeout'}),'transport-or-runtime-failure')

    def test_read_only_socket_evidence_requires_process_ownership(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'77/fd').mkdir(parents=True);(root/'77/net').mkdir()
            (root/'77/fd/3').symlink_to('socket:[123]')
            def line(inode):return f' 0: 080032C0:26D7 010032C0:CB20 01 0000000A:00000014 0 0 0 0 {inode}\n'
            (root/'77/net/tcp').write_text('header\n'+line(123)+line(999))
            rows=transport_sockets(77,root)
            self.assertEqual(len(rows),1)
            self.assertEqual(rows[0]['local'],{'ip':'192.50.0.8','port':9943})
            self.assertEqual(rows[0]['send_queue_bytes'],10)
            self.assertEqual(rows[0]['receive_queue_bytes'],20)

    def test_controller_recovery_survives_metrics_and_does_not_evict_transport(self):
        with tempfile.TemporaryDirectory() as folder:
            journal=Journal(folder,'client')
            with patch('launcher.diagnostics.time.monotonic',return_value=100):
                journal.record('log','TRANSPORT_INCIDENT control timeout')
                journal.record('log','CONTROLLER_STATE hand=right connected=1 tracked=1')
                self.assertEqual(len(journal.report()['incidents']),1)
                journal.record('log','CONTROLLER_STATE hand=right connected=1 tracked=0')
            with patch('launcher.diagnostics.time.monotonic',return_value=118.3):
                journal.record('log','CONTROLLER_STATE hand=right connected=1 tracked=1')
            with patch('launcher.diagnostics.time.monotonic',return_value=120):
                for _ in range(800):journal.record('log','INPUT right_tracked=1 '+('x'*120))
            for tick in (132,148,164):
                with patch('launcher.diagnostics.time.monotonic',return_value=tick):
                    journal.record('log','PRESENTATION_INCIDENT hitch')
            reopened=Journal(folder,'client').report()['incidents']
            controller=next(item for item in reopened if item['kind']=='controller')
            self.assertIn('tracked=0',controller['trigger']['data'])
            self.assertIn('tracked=1',json.dumps(controller['controller_events']))
            self.assertEqual(len([item for item in reopened if item['kind']=='transport']),1)

if __name__=='__main__':unittest.main()
