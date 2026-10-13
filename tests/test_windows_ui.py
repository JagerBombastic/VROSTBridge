"""1.0.0.7: the Windows client's tray icon and sign-in startup (launcher/windows_tray.py, launcher/windows_startup.py).

Runs anywhere: the tray's Win32 calls go through a fake user32/shell32/kernel32 (as in test_connection_028) and
startup through a fake registry. The host's tray and startup must stay exactly as they were.
"""
import copy
import ctypes
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from launcher.windows_startup import ARGUMENT, EXECUTABLES, RUN_KEY, VALUE, VALUES, WindowsStartup  # noqa: E402
from launcher.windows_tray import OPEN, QUIT, START, STOP, WM_TRAY, WindowsTray, tray_view  # noqa: E402

IDLE = {'role': 'client', 'platform': 'windows', 'available': True, 'running': False, 'busy': False, 'state': 'stopped',
        'startup_error': '', 'video': {},
        'session_link': {'state': 'waiting', 'message': 'Ready to start a session.', 'host_ip': '', 'active': False}}


def client(link=None, **changes):
    value = copy.deepcopy(IDLE)
    value.update(changes)
    if link is not None:
        value['session_link'] = dict(value['session_link'], **link)
    return value


STREAMING = client(running=True, state='streaming', video={'width': 2016, 'height': 2208, 'codec': 'Hevc'},
                   link={'state': 'running', 'message': 'Streaming from the host.', 'host_ip': '192.168.1.8',
                         'active': True})


class ClientTrayViewTests(unittest.TestCase):
    def view(self, status):
        return tray_view(status, 'client')

    def test_idle_offers_start(self):
        self.assertEqual(self.view(client()), ('Ready to start', ('Ready to start a session.',), 'start'))

    def test_a_start_under_way_offers_stop(self):
        # The first moments of a Start: the companion is busy and nothing shows yet, then SteamVR starts on this PC.
        self.assertEqual(self.view(client(busy=True))[0::2], ('Please wait', ''))
        self.assertEqual(self.view(client(busy=True, state='starting'))[0::2], ('Starting session', 'stop'))
        finding = self.view(client(busy=True, link={'state': 'discovering', 'message': 'Finding the rendering host.'}))
        self.assertEqual(finding, ('Finding your host', ('Finding the rendering host.',), 'stop'))
        self.assertEqual(self.view(client(running=True, state='connecting'))[0::2], ('Connecting to your host', 'stop'))
        self.assertEqual(self.view(client(running=True, state='restarting'))[0::2], ('Host is restarting', 'stop'))

    def test_streaming_names_the_host_and_the_stream(self):
        label, details, offer = self.view(STREAMING)
        self.assertEqual((label, offer), ('Streaming', 'stop'))
        self.assertEqual(details, ('Streaming from the host.', 'Host: 192.168.1.8', '2016 × 2208 per eye · HEVC'))
        self.assertEqual(self.view(dict(STREAMING, state='recovery'))[0], 'Recovering video')
        self.assertEqual(self.view(dict(STREAMING, state='unresponsive'))[0], 'Not responding')
        self.assertIn('2016 × 2208 per eye · H.264', self.view(dict(STREAMING, video={'width': 2016, 'height': 2208,
                                                                                      'codec': 'H264'}))[1])

    def test_a_held_session_offers_stop_without_video(self):
        # The host's lease is held while the client is not running yet (the page's Stop does the same).
        self.assertEqual(self.view(client(link={'state': 'starting', 'active': True}))[0::2], ('Starting session', 'stop'))
        self.assertEqual(self.view(client(link={'lease': {'active': True}}))[2], 'stop')

    def test_stopping_offers_neither(self):
        view = self.view(dict(STREAMING, session_link=dict(STREAMING['session_link'], state='stopping')))
        self.assertEqual(view[0::2], ('Stopping session', ''))

    def test_failures_need_attention_and_can_be_retried(self):
        label, details, offer = self.view(client(state='error', startup_error='SteamVR did not start on this PC.'))
        self.assertEqual((label, details, offer), ('Needs attention', ('SteamVR did not start on this PC.',), 'start'))
        refused = self.view(client(link={'state': 'error', 'message': 'The host refused the session.'}))
        self.assertEqual(refused, ('Needs attention', ('The host refused the session.',), 'start'))
        held = self.view(client(state='error', startup_error='Lost the host.', link={'active': True}))
        self.assertEqual(held[0::2], ('Needs attention', 'stop'), 'a held session can still be stopped')
        restarted = self.view(dict(STREAMING, runtime_restart={'message': "SteamVR's compositor closed; restarting it."},
                                   session_link=dict(STREAMING['session_link'], message='')))
        self.assertEqual(restarted[1][0], "SteamVR's compositor closed; restarting it.")

    def test_nothing_to_start_without_the_installed_client(self):
        label, details, offer = self.view(client(available=False))
        self.assertEqual((label, offer), ('Needs attention', ''))
        self.assertEqual(details, ('Required application files are missing. Repair or reinstall VROSTBridge, then reopen it.',))
        self.assertEqual(self.view({}), ('Checking this PC', (), ''), 'nothing is offered before the first status')
        self.assertEqual(self.view('stopped')[0], 'Ready to start')

    def test_menu_text_is_escaped_and_short(self):
        _, details, _ = self.view(client(state='error', startup_error='Wi-Fi & Ethernet\n  failed ' * 20))
        self.assertTrue(details[0].startswith('Wi-Fi && Ethernet failed Wi-Fi && Ethernet'))
        self.assertLessEqual(len(details[0].replace('&&', '&')), 110)
        self.assertNotIn('\n', details[0])

    def test_odd_values_do_not_break_the_tray(self):
        # The companion polls this twice a second on its main thread: no status may raise.
        for status in ({'state': ['x']}, {'session_link': ['x']}, {'session_link': {'state': {}, 'lease': 'x'}},
                       {'running': True, 'video': 'x'}, {'running': True, 'video': {'width': 1, 'height': 1, 'codec': []}},
                       {'runtime_restart': 'x'}):
            with self.subTest(status=status):
                label, details, offer = self.view(status)
                self.assertIsInstance(label, str)
                self.assertIn(offer, ('', 'start', 'stop'))

    def test_the_host_view_is_unchanged(self):
        for status in ({'state': 'host-ready'}, {'state': 'stopped', 'session_link': {'last_error': 'Handshake failed'}},
                       {'running': True, 'host_control': {'clients': [{'connection_state': 'Streaming'}]}}, {}):
            self.assertEqual(tray_view(status), tray_view(status, 'host'))
            self.assertEqual(len(tray_view(status)), 2, 'the host keeps (label, details)')
        self.assertEqual(tray_view({'state': 'host-ready'})[0], 'Ready for headset')
        self.assertEqual(tray_view({}), ('Waiting for headset', ('Start a session from the headset client.',)))


class Fn:
    def __init__(self, fn=lambda *args: 1):
        self.fn = fn

    def __call__(self, *args):
        return self.fn(*args)


class API:
    def __getattr__(self, name):
        value = Fn()
        setattr(self, name, value)
        return value


class TrayMenuTests(unittest.TestCase):
    """Right-click the icon, read the menu, choose an item (WNDPROC and popup dispatch through fake Win32 calls)."""

    def pump(self, tray, choice, messages=((WM_TRAY, 0x7b),)):
        user, shell, kernel = API(), API(), API()
        state = {'messages': list(messages)}
        seen = {'items': [], 'tips': []}
        user.RegisterClassW = Fn(lambda wc: state.update(proc=wc._obj.proc) or 1)
        user.CreateWindowExW = Fn(lambda *args: 123)
        user.RegisterWindowMessageW = Fn(lambda _: 0xc123)
        user.AppendMenuW = Fn(lambda h, flags, command, text: seen['items'].append((flags, command, text)) or 1)
        user.TrackPopupMenu = Fn(lambda *args: choice)
        shell.Shell_NotifyIconW = Fn(lambda action, nid: seen['tips'].append((action, nid._obj.tip)) or 1)

        def get_message(message, *_):
            if not state['messages']:
                return 0
            message._obj.message, message._obj.lParam = state['messages'].pop(0)
            return 1
        user.GetMessageW = Fn(get_message)
        user.DispatchMessageW = Fn(lambda message: state['proc'](123, message._obj.message, 0, message._obj.lParam))
        libraries = {'user32': user, 'shell32': shell, 'kernel32': kernel}
        with patch.object(ctypes, 'WinDLL', side_effect=lambda name, **kw: libraries[name], create=True), \
                patch.object(ctypes, 'WINFUNCTYPE', ctypes.CFUNCTYPE, create=True):
            tray._pump()
        return seen

    def client_tray(self, status, choice=0, **callbacks):
        events = {name: threading.Event() for name in ('open', 'quit', 'start', 'stop')}
        logs = []
        tray = WindowsTray('icon.ico', events['open'].set, events['quit'].set, logs.append, role='client',
                           **{name + '_callback': events[name].set for name in callbacks.get('with', ('start', 'stop'))})
        tray.update(status)
        seen = self.pump(tray, choice)
        self.assertFalse(logs)
        return seen, events

    def test_the_client_menu(self):
        seen, _ = self.client_tray(client())
        self.assertEqual(seen['items'][0], (2, 0, 'VROSTBridge Client — Ready to start'))
        self.assertEqual(seen['items'][1], (2, 0, 'Ready to start a session.'))
        self.assertEqual(seen['items'][2][0], 0x800)
        self.assertEqual(seen['items'][3:], [(0, OPEN, 'Open VROSTBridge'), (0, START, 'Start session'),
                                             (1, STOP, 'Stop session'), (0x800, 0, None), (0, QUIT, 'Quit')])
        streaming, _ = self.client_tray(STREAMING)
        commands = {command: flags for flags, command, text in streaming['items'] if command}
        self.assertEqual((commands[START], commands[STOP]), (1, 0), 'Stop while streaming; Start greyed')
        stopping, _ = self.client_tray(dict(STREAMING, session_link=dict(STREAMING['session_link'], state='stopping')))
        commands = {command: flags for flags, command, text in stopping['items'] if command}
        self.assertEqual((commands[START], commands[STOP], commands[OPEN], commands[QUIT]), (1, 1, 0, 0))

    def test_the_client_tooltip(self):
        seen, _ = self.client_tray(STREAMING)
        added = [tip for action, tip in seen['tips'] if action == 0]
        self.assertEqual(added, ['VROSTBridge Client — Streaming'])
        tray = WindowsTray('icon.ico', lambda: None, lambda: None, role='client')
        tray.update(client(state='error', startup_error='x'))
        seen = self.pump(tray, 0, messages=((0x8048, 0),))  # the update's repaint
        self.assertIn((1, 'VROSTBridge Client — Needs attention'), seen['tips'])

    def test_each_item_does_its_own_thing(self):
        for status, choice, expected in ((client(), START, 'start'), (STREAMING, STOP, 'stop'),
                                         (client(), OPEN, 'open'), (STREAMING, QUIT, 'quit')):
            with self.subTest(choice=choice):
                _, events = self.client_tray(status, choice)
                self.assertTrue(events[expected].wait(1))
                self.assertEqual([name for name, event in events.items() if event.is_set()], [expected])

    def test_start_and_stop_need_their_callbacks(self):
        seen, _ = self.client_tray(client(), **{'with': ()})
        commands = {command: flags for flags, command, text in seen['items'] if command}
        self.assertEqual((commands[START], commands[STOP]), (1, 1))

    def test_a_failing_action_is_logged(self):
        logs = []

        def refuse():
            raise ValueError('Already running')
        tray = WindowsTray('icon.ico', lambda: None, lambda: None, logs.append, role='client', start_callback=refuse)
        tray.update(client())
        done = threading.Event()
        tray.log = lambda line: (logs.append(line), done.set())
        self.pump(tray, START)
        self.assertTrue(done.wait(1))
        self.assertEqual(logs, ['Tray action: Already running'])

    def test_the_host_menu_and_tooltip_are_unchanged(self):
        opened, exited = threading.Event(), threading.Event()
        tray = WindowsTray('icon.ico', opened.set, exited.set, lambda line: None)  # positional, as app.py calls it
        self.assertEqual((tray.role, tray.title), ('host', 'VROSTBridge Host'))
        seen = self.pump(tray, 1)
        self.assertTrue(opened.wait(1))
        self.assertEqual(seen['items'][0], (2, 0, 'VROSTBridge Host — Waiting for headset'))
        self.assertEqual(seen['items'][-2:], [(0, 1, 'Settings — open web UI'), (0, 2, 'Exit')])
        self.assertNotIn('Start session', [text for _, _, text in seen['items']])
        self.assertEqual([tip for action, tip in seen['tips'] if action == 0], ['VROSTBridge Host — Waiting for headset'])

    def test_an_unknown_role_is_refused(self):
        with self.assertRaises(ValueError):
            WindowsTray('icon.ico', lambda: None, lambda: None, role='headset')

    def test_update_repaints_only_on_change(self):
        tray = WindowsTray('icon.ico', lambda: None, lambda: None, role='client', start_callback=lambda: None)
        posted = []
        tray.user, tray.hwnd = API(), 123
        tray.user.PostMessageW = Fn(lambda *args: posted.append(args) or 1)
        tray.update(client())
        tray.update(client())
        self.assertEqual(len(posted), 1)
        tray.update(client(busy=True))  # 'Please wait': the label and the offer change
        self.assertEqual(len(posted), 2)
        self.assertEqual((tray.state, tray.offer), ('Please wait', ''))


class Registry:
    """HKEY_CURRENT_USER's Run and StartupApproved\\Run keys."""
    HKEY_CURRENT_USER, REG_SZ, REG_BINARY, KEY_SET_VALUE = 'HKCU', 1, 3, 2

    class Key:
        def __init__(self, values):
            self.values = values

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def __init__(self):
        self.run, self.approved = {}, {}

    def OpenKey(self, root, path):
        if path == RUN_KEY:
            return self.Key(self.run)
        if path.endswith('Explorer\\StartupApproved\\Run') and self.approved:
            return self.Key(self.approved)
        raise FileNotFoundError(path)

    def CreateKeyEx(self, root, path, reserved, access):
        assert (root, path, access) == (self.HKEY_CURRENT_USER, RUN_KEY, self.KEY_SET_VALUE)
        return self.Key(self.run)

    def QueryValueEx(self, key, name):
        if name not in key.values:
            raise FileNotFoundError(name)
        return key.values[name]

    def SetValueEx(self, key, name, reserved, kind, value):
        key.values[name] = (value, kind)

    def DeleteValue(self, key, name):
        del key.values[name]


class StartupTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.registry = Registry()

    def startup(self, role=None):
        executable = self.folder / EXECUTABLES[role or 'host']
        executable.touch()
        return executable, (WindowsStartup(executable, self.registry) if role is None else
                            WindowsStartup(executable, self.registry, role=role))

    def test_one_entry_for_each_program(self):
        self.assertEqual((VALUE, VALUES), ('VROSTBridgeHost', {'host': 'VROSTBridgeHost', 'client': 'VROSTBridgeClient'}))
        self.assertEqual(EXECUTABLES, {'host': 'VROSTBridge Host.exe', 'client': 'VROSTBridge Client.exe'})
        self.assertEqual(ARGUMENT, '--background')
        host_exe, host = self.startup()
        client_exe, client_startup = self.startup('client')
        self.assertEqual(host.status()['enabled'], False)
        client_startup.set_enabled(True)
        self.assertEqual(self.registry.run, {'VROSTBridgeClient': ('"' + str(client_exe) + '" --background', 1)})
        self.assertEqual(client_startup.status(), {'supported': True, 'available': True, 'enabled': True, 'conflict': False,
                                                   'message': 'Start quietly in the system tray. No browser or SteamVR opens.'})
        self.assertFalse(host.status()['enabled'], 'the host keeps its own entry')
        host.set_enabled(True)
        self.assertEqual(self.registry.run['VROSTBridgeHost'], ('"' + str(host_exe) + '" --background', 1))
        client_startup.set_enabled(False)
        self.assertEqual(list(self.registry.run), ['VROSTBridgeHost'], 'turning one off leaves the other')
        self.assertTrue(host.status()['enabled'])

    def test_the_host_is_as_before(self):
        executable, host = self.startup()
        self.assertEqual((host.role, host.value, host.command), ('host', 'VROSTBridgeHost', '"' + str(executable) + '" --background'))
        self.registry.run['VROSTBridgeHost'] = ('foreign.exe', 1)
        status = host.status()
        self.assertTrue(status['conflict'])
        self.assertEqual(status['message'], "Another program owns VROSTBridge's sign-in startup entry (VROSTBridgeHost); it was left unchanged.")
        with self.assertRaises(ValueError):
            host.set_enabled(True)
        self.assertEqual(self.registry.run['VROSTBridgeHost'], ('foreign.exe', 1))
        missing = WindowsStartup(self.folder / 'elsewhere' / 'VROSTBridge Host.exe', self.registry).status()
        self.assertEqual(missing['message'], 'Available in the installed Windows host.')

    def test_client_messages_name_the_client(self):
        executable, startup = self.startup('client')
        self.registry.run['VROSTBridgeClient'] = ('"C:\\Other\\Client.exe"', 1)
        status = startup.status()
        self.assertEqual((status['available'], status['enabled'], status['conflict']), (True, False, True))
        self.assertEqual(status['message'], "Another program owns VROSTBridge's sign-in startup entry (VROSTBridgeClient); it was left unchanged.")
        with self.assertRaises(ValueError):
            startup.set_enabled(False)
        self.assertEqual(self.registry.run['VROSTBridgeClient'], ('"C:\\Other\\Client.exe"', 1), 'never removed')
        del self.registry.run['VROSTBridgeClient']
        startup.set_enabled(True)
        self.registry.approved['VROSTBridgeClient'] = (bytes([3, 0, 0, 0]) + bytes(8), 3)  # Task Manager: Disabled
        self.assertEqual(startup.status()['message'], 'Windows disabled this item. Enable VROSTBridge Client in Windows '
                                                      'Settings → Apps → Startup.')
        self.registry.approved['VROSTBridgeClient'] = (bytes([2, 0, 0, 0]) + bytes(8), 3)
        self.assertEqual(startup.status()['message'], 'Start quietly in the system tray. No browser or SteamVR opens.')
        missing = WindowsStartup(self.folder / 'elsewhere' / 'VROSTBridge Client.exe', self.registry, role='client').status()
        self.assertEqual(missing, {'supported': False, 'available': False, 'enabled': False, 'conflict': False,
                                   'message': 'Available in the installed Windows client.'})

    def test_bad_requests_are_refused(self):
        _, startup = self.startup('client')
        for value in ('true', 1, None):
            with self.assertRaises(ValueError):
                startup.set_enabled(value)
        self.assertEqual(self.registry.run, {})
        with self.assertRaises(ValueError):
            WindowsStartup(self.folder / 'x.exe', self.registry, role='headset')

    def test_without_a_registry_nothing_is_offered(self):
        if sys.platform == 'win32':
            self.skipTest('Windows has a registry')
        executable = self.folder / 'VROSTBridge Client.exe'
        executable.touch()
        self.assertFalse(WindowsStartup(executable, role='client').status()['available'])


if __name__ == '__main__':
    unittest.main()
