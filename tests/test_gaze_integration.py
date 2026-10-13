import math
import socket
import struct
import tempfile
import time
import unittest
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from launcher.gaze_provider import parse_osc, GazeRelay
from launcher.validation import SessionEvidence

def packet(address, *values):
    def string(value):
        data=value.encode()+b'\0';return data+b'\0'*((-len(data))%4)
    return string(address)+string(','+'f'*len(values))+struct.pack('>'+'f'*len(values),*values)

class GazeIntegration(unittest.TestCase):
    def test_binocular_matches_center_when_eyes_agree(self):
        q=parse_osc(packet('/tracking/eye/LeftRightPitchYaw',15,-20,15,-20))
        center=parse_osc(packet('/tracking/eye/CenterPitchYaw',15,-20))
        for a,b in zip(q,center):self.assertAlmostEqual(a,b)
    def test_bundle_and_malformed(self):
        message=packet('/tracking/eye/CenterPitchYaw',0,0)
        bundle=b'#bundle\0'+struct.pack('>Q',1)+struct.pack('>I',len(message))+message
        self.assertEqual(parse_osc(bundle),(0,0,0,1))
        for invalid in (bundle[:-1],bundle[:8]+struct.pack('>Q',99)+bundle[16:],packet('/tracking/eye/LeftRightPitchYaw',0,float('nan'),0,0)):
            with self.assertRaises(ValueError):parse_osc(invalid)
    @unittest.skipUnless(hasattr(socket,'AF_UNIX'),'Unix gaze endpoint')
    def test_live_datagram_forwarding_and_stop(self):
        try:probe=socket.socket(socket.AF_UNIX,socket.SOCK_DGRAM)
        except PermissionError:self.skipTest('Execution environment denies Unix datagram sockets')
        probe.close()
        with tempfile.TemporaryDirectory() as directory:
            path=str(Path(directory)/'gaze.sock')
            with socket.socket(socket.AF_UNIX,socket.SOCK_DGRAM) as sink, socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as source:
                sink.bind(path);sink.settimeout(1)
                relay=GazeRelay(path,0).start()
                try:
                    before=time.monotonic_ns()
                    source.sendto(packet('/tracking/eye/LeftRightPitchYaw',0,0,0,0),relay.incoming.getsockname())
                    fields=sink.recv(1024).decode().split()
                    self.assertEqual(fields[0],'VRG1');self.assertGreaterEqual(int(fields[1]),before)
                    self.assertEqual(fields[2:],["1","0","0","0","1"])
                    self.assertEqual(relay.status()['state'],'receiving')
                    # RC4.5: Baballonia sends about 30 a second; half a second without one is a pause.
                    time.sleep(.55);self.assertEqual(relay.status()['state'],'waiting')
                    relay.set_target(None)  # between sessions samples are only counted
                    source.sendto(packet('/tracking/eye/CenterPitchYaw',0,0),relay.incoming.getsockname())
                    deadline=time.monotonic()+2
                    while relay.status()['received']<2 and time.monotonic()<deadline:time.sleep(.01)
                    self.assertEqual((relay.status()['received'],relay.status()['forwarded'],relay.status()['unsent']),(2,1,1))
                finally:relay.close()
                self.assertFalse(relay.thread.is_alive())
    def test_session_report_does_not_claim_physical_validation(self):
        evidence=SessionEvidence();evidence.record({'state':'streaming','frame_age_ms':'20'})
        evidence.record({'state':'recovery'})
        report=evidence.report();self.assertEqual(report['streaming_samples'],1)
        self.assertEqual(report['source_frame_age_p95_ms'],20)
        self.assertIn('Not automatically verified',report['physical_checks'])
