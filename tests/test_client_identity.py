import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from launcher.client_identity import ClientIdentity, config_path, usable_ipv4, valid_hostname


class ClientIdentityTests(unittest.TestCase):
    def test_persisted_protocol_identity_survives_launches_without_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'session.json'
            original = json.dumps({'hostname': '0317.client.local.', 'protocol_id': 'pinned-protocol'})
            path.write_text(original)
            identity = ClientIdentity(path, lambda: [])
            self.assertEqual(identity.status()['hostname'], '0317.client.local.')
            identity.reset()
            self.assertEqual(identity.status()['hostname'], '0317.client.local.')
            self.assertEqual(path.read_text(), original)

    def test_missing_corrupt_or_invalid_config_does_not_invent_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'session.json'
            identity = ClientIdentity(path, lambda: [])
            for contents in (None, 'not json', '[]', '{"hostname":"bazzite"}', '{"hostname":"../../foo.local."}'):
                if contents is not None:
                    path.write_text(contents)
                self.assertEqual(identity.status()['hostname'], '')

    def test_live_hud_identity_wins_after_protocol_regeneration(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'session.json'
            path.write_text('{"hostname":"0317.client.local.","protocol_id":"old"}')
            identity = ClientIdentity(path, lambda: [])
            identity.observe('STATUS ALVR v21.0.0-dev14\n')
            identity.observe('hostname: 9128.client.local.\n')
            identity.observe('IP: 127.0.0.1\n')
            identity.observe('Searching for streamer...\n')
            identity.observe('Open ALVR on your PC then click "Trust"\n')
            self.assertEqual(identity.status()['hostname'], '9128.client.local.')
            self.assertEqual(identity.status()['addresses'], [])
            self.assertEqual(identity.status()['connection_state'], 'searching')

    def test_multiline_connection_error_remains_visible_despite_recovery_metrics(self):
        identity = ClientIdentity('/nonexistent/config', lambda: [])
        for line in ('Connection error:\n', 'Address already in use\n', 'Check the PC for more details\n',
                     'METRICS state=recovery decoded=0 dropped=0 frame_age_ms=0\n'):
            identity.observe(line)
        self.assertEqual(identity.status()['connection_state'], 'error')
        self.assertEqual(identity.status()['message'], 'Connection error: Address already in use')
        identity.observe('Successful connection!')
        self.assertEqual(identity.status()['connection_state'], 'connecting')
        identity.observe('STATUS Stream negotiated 1440 x 1600 per eye')
        self.assertEqual(identity.status()['connection_state'], 'connected')

    def test_xdg_config_matches_native_path(self):
        with patch.dict(os.environ, {'XDG_CONFIG_HOME': '/tmp/custom-config'}):
            self.assertEqual(config_path(), Path('/tmp/custom-config/ALVR Client/session.json'))
        with patch.dict(os.environ, {'XDG_CONFIG_HOME': 'relative-is-invalid'}):
            self.assertEqual(config_path(), Path.home() / '.config/ALVR Client/session.json')

    def test_usable_addresses_and_exact_hostnames(self):
        for address in ('0.0.0.0', '127.0.0.1', '169.254.1.2', '224.0.0.251', '255.255.255.255', '::1', 'not an IP'):
            self.assertFalse(usable_ipv4(address), address)
        for address in ('192.168.1.42', '10.10.0.7', '172.20.0.4', '100.65.0.1'):
            self.assertTrue(usable_ipv4(address), address)
        self.assertTrue(valid_hostname('1234.client.local.'))
        self.assertFalse(valid_hostname('1234.client.local'))
        self.assertFalse(valid_hostname('wrong hostname.local.'))


if __name__ == '__main__':
    unittest.main()
