import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from launcher.client_network import ClientNetwork, private_targets, rich_rules


class FakeFirewall:
    def __init__(self):
        self.rules = {'runtime': set(), 'permanent': set()}
        self.services = {'runtime': {'mdns'}, 'permanent': {'mdns'}}
        self.calls = []
        self.fail_runtime = False

    def __call__(self, arguments, timeout):
        self.calls.append(arguments)
        scope = 'permanent' if '--permanent' in arguments else 'runtime'
        code, output = 0, ''
        if '--state' in arguments:
            output = 'running\n'
        elif any(arg.startswith('--get-zone-of-interface=') for arg in arguments):
            output = 'public\n'
        elif '--list-rich-rules' in arguments:
            output = '\n'.join(sorted(self.rules[scope]))
        elif '--list-services' in arguments:
            output = ' '.join(self.services[scope])
        elif any(arg.startswith('--add-rich-rule=') for arg in arguments):
            if self.fail_runtime and scope == 'runtime':
                code = 126
            else:
                self.rules[scope].update(arg.partition('=')[2] for arg in arguments if arg.startswith('--add-rich-rule='))
        else:
            raise AssertionError(arguments)
        return subprocess.CompletedProcess(arguments, code, output, '')


class ClientNetworkTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        self.firewall = root / 'firewall-cmd'
        self.pkexec = root / 'pkexec'
        self.firewall.touch()
        self.pkexec.touch()
        self.saved = root / 'state/client-firewall.json'
        self.fake = FakeFirewall()
        self.addresses = [{'address': '192.168.1.25', 'interface': 'wlp1s0', 'prefix': 24, 'physical': True, 'connected': True}]
        self.network = ClientNetwork(lambda: self.addresses, self.fake, str(self.firewall), str(self.pkexec),
                                     auto_refresh=False, state_path=self.saved)

    def test_status_only_reads_cached_data_and_refresh_never_mutates(self):
        self.network.status()
        self.assertEqual(self.fake.calls, [])
        self.assertEqual(self.network.refresh()['state'], 'needs-permission')
        self.assertFalse(any('--add-rich-rule=' in ' '.join(call) for call in self.fake.calls))
        self.assertFalse(self.saved.exists())

    def test_only_actual_physical_private_subnet_is_eligible(self):
        good = self.addresses[0]
        invalid = [dict(good, address='8.8.8.8'), dict(good, prefix=0), dict(good, physical=False), dict(good, connected=False),
                   dict(good, prefix=None), dict(good, interface='wlan0;sudo'), dict(good, address='169.254.1.2')]
        self.assertEqual(private_targets(invalid), [])
        self.assertEqual(private_targets(self.addresses), [{'interface': 'wlp1s0', 'subnet': '192.168.1.0/24'}])
        self.addresses[:] = invalid
        self.assertEqual(self.network.refresh()['state'], 'unavailable')
        with self.assertRaises(ValueError):
            self.network.allow()
        self.assertFalse(any(call[0] == str(self.pkexec) for call in self.fake.calls))

    def test_explicit_action_saves_scoped_rules_without_claiming_preexisting(self):
        existing = rich_rules('192.168.1.0/24')[0]
        self.fake.rules['permanent'].add(existing)
        result = self.network.allow()
        self.assertEqual(result['state'], 'ready')
        self.assertTrue(result['runtime_ready'])
        self.assertTrue(result['permanent_ready'])
        owned = json.loads(self.saved.read_text())['rules']
        self.assertNotIn({'zone': 'public', 'scope': 'permanent', 'rule': existing}, owned)
        self.assertEqual(len(owned), 7)
        mutations = [call for call in self.fake.calls if call[0] == str(self.pkexec)]
        self.assertEqual(len(mutations), 2)
        for call in mutations:
            self.assertNotIn('--reload', call)
            self.assertFalse(any(arg.startswith(('--add-port=', '--change-interface=', '--set-default-zone=')) for arg in call))
            for arg in call:
                if arg.startswith('--add-rich-rule='):
                    self.assertIn('source address="192.168.1.0/24"', arg)
                    self.assertNotIn('5353', arg)  # Existing mdns service is preserved.

    def test_discovery_rule_added_only_when_mdns_service_absent(self):
        self.fake.services = {'runtime': set(), 'permanent': set()}
        self.network.allow()
        for scope in ('runtime', 'permanent'):
            self.assertEqual(len(self.fake.rules[scope]), 5)
            self.assertTrue(any('port="5353"' in rule for rule in self.fake.rules[scope]))
            self.assertTrue(any('port="9945" protocol="tcp"' in rule for rule in self.fake.rules[scope]))
            self.assertTrue(any('port="9946" protocol="udp"' in rule for rule in self.fake.rules[scope]))
            self.assertFalse(any('port="9946" protocol="tcp"' in rule for rule in self.fake.rules[scope]))

    def test_upgrade_adds_only_missing_scoped_companion_discovery_rule(self):
        previous = [rule for rule in rich_rules('192.168.1.0/24') if 'port="9946"' not in rule]
        for scope in ('runtime', 'permanent'):
            self.fake.rules[scope].update(previous)
        before = self.network.refresh()
        self.assertEqual(before['state'], 'needs-permission')
        self.assertEqual(before['missing_rules'], 2)
        self.network.allow()
        commands = [call for call in self.fake.calls if call[0] == str(self.pkexec)]
        self.assertEqual(len(commands), 2)
        for command in commands:
            added = [argument for argument in command if argument.startswith('--add-rich-rule=')]
            self.assertEqual(added, ['--add-rich-rule=rule family="ipv4" source address="192.168.1.0/24" port port="9946" protocol="udp" accept'])
        self.assertEqual(len(json.loads(self.saved.read_text())['rules']), 2)

    def test_partial_failure_is_visible_and_successful_rules_remain_owned(self):
        self.fake.fail_runtime = True
        with self.assertRaisesRegex(ValueError, 'not authorised'):
            self.network.allow()
        status = self.network.status()
        self.assertFalse(status['runtime_ready'])
        self.assertTrue(status['permanent_ready'])
        self.assertTrue(status['can_allow'])
        owned = json.loads(self.saved.read_text())['rules']
        self.assertEqual({item['scope'] for item in owned}, {'permanent'})
        self.fake.fail_runtime = False
        self.assertEqual(self.network.allow()['state'], 'ready')

    def test_pending_applied_rules_are_reconciled_even_when_firewall_is_ready(self):
        rules = [rule for rule in rich_rules('192.168.1.0/24') if 'port="5353"' not in rule]
        for scope in ('runtime', 'permanent'):
            self.fake.rules[scope].update(rules)
        pending = [{'zone': 'public', 'scope': 'runtime', 'rule': rules[0]}]
        self.saved.parent.mkdir(parents=True)
        self.saved.write_text(json.dumps({'schema': 1, 'rules': [], 'pending': pending}))
        self.assertTrue(self.network.refresh()['can_allow'])
        self.assertEqual(self.network.allow()['state'], 'ready')
        owned = json.loads(self.saved.read_text())
        self.assertEqual(owned['rules'], pending)
        self.assertNotIn('pending', owned)
        self.assertFalse(self.network.refresh()['can_allow'])
        self.assertFalse(any(call[0] == str(self.pkexec) for call in self.fake.calls))

    def test_pending_rules_outside_policy_are_not_queried_or_claimed(self):
        self.saved.parent.mkdir(parents=True)
        invalid = {'zone': 'public', 'scope': 'runtime',
                   'rule': 'rule family="ipv4" source address="0.0.0.0/0" port port="22" protocol="tcp" accept'}
        self.saved.write_text(json.dumps({'schema': 1, 'rules': [], 'pending': [invalid]}))
        with self.assertRaises(ValueError):
            self.network.allow()
        owned = json.loads(self.saved.read_text())
        self.assertEqual(owned['rules'], [])
        self.assertEqual(owned['pending'], [invalid])
        self.assertFalse(any(call[0] == str(self.pkexec) for call in self.fake.calls))


if __name__ == '__main__':
    unittest.main()
