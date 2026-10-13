"""The real vendored WebSocket path, sampling, and raw-event exclusion."""
import json
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from host.events import HostEvents
from host.vendor.websockets.sync.server import serve
from launcher.diagnostics import Journal

class HostEventTests(unittest.TestCase):
    def test_keyframe_stall_cannot_evict_startup_and_following_error(self):
        with tempfile.TemporaryDirectory() as temp,patch.object(HostEvents,'run'):
            journal=Journal(temp,'host');events=HostEvents(lambda:None,journal);self.addCleanup(events.close)
            def log(content,severity='Warning'):
                events.accept(json.dumps({'event_type':{'id':'Log','data':{'content':content,'severity':severity}}}))
            with patch('host.events.time.monotonic',return_value=100):
                log('Encoder starting','Info')
                for _ in range(10000):log('Dropping video packet. Reason: Waiting for IDR frame')
                log('Handshake error: test actionable error','Error')
            report=journal.report()
            self.assertLess(len(report['events']),10)
            self.assertIn('Encoder starting',json.dumps(report))
            self.assertIn('actionable error',events.status()['connection_error'])
            row=events.status()['repeated_logs'][0]
            self.assertEqual(row['count'],10000)
            self.assertEqual(events.status()['coalesced_logs'],9999)
            with patch('host.events.time.monotonic',return_value=131):
                log('Dropping video packet. Reason: Waiting for IDR frame')
            self.assertEqual(journal.report()['events'][-1]['event'],'alvr-log-repeated')
            self.assertEqual(journal.report()['events'][-1]['data']['count'],10001)
            events.reset()
            self.assertEqual(events.status()['repeated_logs'],[])
            self.assertEqual(events.status()['connection_error'],'')

    def test_native_stage_metrics_are_sampled_without_filling_warning_table(self):
        with tempfile.TemporaryDirectory() as temp,patch.object(HostEvents,'run'):
            journal=Journal(temp,'host');events=HostEvents(lambda:None,journal);self.addCleanup(events.close)
            with patch('host.events.time.monotonic',return_value=100):
                for count in range(120):
                    events.accept(json.dumps({'event_type':{'id':'Log','data':{
                        'content':'VROST_HOST_CAPTURE frames='+str(count)+' acquire_failures=2 encoder_wait_max_us=8400',
                        'severity':'Info'}}}))
            self.assertEqual(events.status()['statistics']['HostCapture']['values']['frames'],119)
            self.assertEqual(events.status()['statistics']['HostCapture']['values']['acquire_failures'],2)
            self.assertEqual(sum(row['event']=='native-HostCapture' for row in journal.report()['events']),1)
            self.assertEqual(events.repeats,{})

    def test_transport_timeout_survives_warning_rate_limit(self):
        with tempfile.TemporaryDirectory() as temp,patch.object(HostEvents,'run'):
            events=HostEvents(lambda:None,Journal(temp,'host'));self.addCleanup(events.close)
            with patch('host.events.time.monotonic',return_value=100):
                for n in range(40):
                    events.accept(json.dumps({'event_type':{'id':'Log','data':{'content':'warning '+str(n),'severity':'Warning'}}}))
                events.accept(json.dumps({'event_type':{'id':'Log','data':{'content':'Client disconnected. Timeout','severity':'Warning'}}}))
            self.assertEqual(events.journal.report()['events'][-1]['event'],'transport-incident')
            self.assertTrue(events.journal.report()['incidents'])

    def test_sampling_and_raw_data_exclusion(self):
        with tempfile.TemporaryDirectory() as temp,patch.object(HostEvents,'run'):
            journal=Journal(temp,'host');events=HostEvents(lambda:None,journal);self.addCleanup(events.close)
            for _ in range(5):events.accept(json.dumps({'event_type':{'id':'GraphStatistics','data':{'encoder_s':.003}}}))
            events.accept(json.dumps({'event_type':{'id':'Tracking','data':{'secret_pose':'not-retained'}}}))
            events.accept(json.dumps({'event_type':{'id':'Session','data':{'secret_config':'not-retained'}}}))
            entries=journal.report()['events']
            self.assertEqual(sum(x['event']=='alvr-GraphStatistics' for x in entries),1)
            self.assertNotIn('not-retained',json.dumps(journal.report()))
            self.assertEqual(events.status()['statistics']['GraphStatistics']['values']['encoder_s'],.003)
    def test_real_loopback_wire_reads_stats_without_external_service(self):
        with tempfile.TemporaryDirectory() as temp:
            finished=threading.Event();seen=[]
            def handler(connection):
                seen.append((connection.request.path,connection.request.headers.get('X-ALVR')))
                connection.send(json.dumps({'timestamp':'fixture','event_type':{'id':'StatisticsSummary','data':{'encode_latency_ms':3,'network_latency_ms':2,'client_fps':90}}}))
                finished.wait(3)
            with serve(handler,'127.0.0.1',0) as server:
                thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
                api=SimpleNamespace(base='http://127.0.0.1:'+str(server.socket.getsockname()[1]))
                events=HostEvents(lambda:api,Journal(temp,'host'))
                try:
                    deadline=time.monotonic()+3
                    while time.monotonic()<deadline and not events.status()['statistics']:time.sleep(.02)
                    self.assertEqual(events.status()['statistics']['StatisticsSummary']['values']['client_fps'],90)
                    self.assertEqual(seen,[('/api/events','true')])
                finally:
                    finished.set();events.close();server.shutdown();thread.join()

if __name__=='__main__':unittest.main()
