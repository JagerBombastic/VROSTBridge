import unittest
from unittest.mock import patch
from launcher.reporting import attach_remote_session
from launcher.validation import SessionEvidence


class ReportMeasurementTests(unittest.TestCase):
    def test_host_uses_client_measurements_and_labels_historical_capture(self):
        host={'role':'host','session':{'streaming_samples':0,'elapsed_seconds':216.4}}
        remote={'client_report':{'role':'client','session':{'streaming_samples':183}},
                'client_report_current':False,'client_report_received_utc':123}
        report=attach_remote_session(host,remote)
        self.assertEqual(report['session']['streaming_samples'],183)
        self.assertEqual(report['session']['report_scope'],'last_captured_client_connection')
        self.assertEqual(report['host_session']['elapsed_seconds'],216.4)
        self.assertEqual(host['session']['streaming_samples'],0)
        self.assertNotIn('source',remote['client_report']['session'])
        remote['client_report_current']=True
        self.assertEqual(attach_remote_session(host,remote)['session']['report_scope'],'current_connection')

    def test_missing_client_data_is_unavailable_instead_of_zero_streaming(self):
        for remote in ({},{'client_report':None},{'client_report':{'role':'host','session':{}}}):
            report=attach_remote_session({'session':{'streaming_samples':0}},remote)
            self.assertFalse(report['session']['available'])
            self.assertNotIn('streaming_samples',report['session'])

    def test_stop_does_not_erase_last_streaming_sample_and_new_session_resets_it(self):
        evidence=SessionEvidence()
        metrics={'state':'streaming','decoded':'1000','frame_age_ms':'29'}
        evidence.record(metrics);metrics['decoded']='0'
        evidence.record({'state':'recovery','decoded':'0','frame_age_ms':'0'})
        report=evidence.report()
        self.assertEqual(report['streaming_samples'],1)
        self.assertEqual(report['last_streaming_sample']['metrics']['decoded'],'1000')
        self.assertEqual(report['source_frame_age_p95_ms'],29)
        self.assertIsNone(SessionEvidence().report()['last_streaming_sample'])

    def test_session_duration_stops_when_process_finishes(self):
        with patch('launcher.validation.time.monotonic',side_effect=[10,20]):
            evidence=SessionEvidence();evidence.finish()
        with patch('launcher.validation.time.monotonic',return_value=900):
            self.assertEqual(evidence.report()['elapsed_seconds'],10)
            evidence.finish()
            self.assertEqual(evidence.report()['elapsed_seconds'],10)
            self.assertIsNotNone(evidence.report()['ended_utc'])
