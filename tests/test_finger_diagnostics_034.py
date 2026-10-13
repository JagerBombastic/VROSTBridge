import json
import tempfile
import unittest
from unittest.mock import patch
from host.events import HostEvents
from host.finger_diagnostics import native_hand_sample
from launcher.diagnostics import Journal

SAMPLE=dict(hand='left',updates=20,sample_ns='23456789',packet_ns='33456789',sample_age_ms=10,source_curls_valid=True,source_curls=[0,.2,.4,.6,.8],applied_curls=[.2,.4,.6,.8],applied=True,ranges='both')

class FingerDiagnosticsTests(unittest.TestCase):
    def test_accepts_only_explicit_driver_evidence_and_bounds_data(self):
        self.assertEqual(native_hand_sample('VROST_NATIVE_HAND '+json.dumps(SAMPLE))[1],SAMPLE)
        for changed in [dict(SAMPLE,source_curls=[0,.2,.4,.6,float('nan')]),dict(SAMPLE,applied='true'),dict(SAMPLE,packet_ns=12),dict(SAMPLE,ranges='one')]:
            self.assertIsNone(native_hand_sample('VROST_NATIVE_HAND '+json.dumps(changed)))
        self.assertIsNone(native_hand_sample('generic finger success'))
    def test_host_status_ages_and_clears_native_applied_evidence(self):
        with tempfile.TemporaryDirectory() as temp,patch.object(HostEvents,'run'):
            events=HostEvents(lambda:None,Journal(temp,'host'));self.addCleanup(events.close)
            with patch('host.events.time.monotonic',return_value=100):
                events.accept(json.dumps({'event_type':{'id':'Log','data':{'severity':'Info','content':'VROST_NATIVE_HAND '+json.dumps(SAMPLE)}}}))
            with patch('host.events.time.monotonic',return_value=103):
                self.assertEqual(events.status()['native_hands']['left']['report_age_s'],3)
                self.assertTrue(events.status()['native_hands']['left']['applied'])
            events.reset();self.assertEqual(events.status()['native_hands'],{})
