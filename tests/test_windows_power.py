"""Windows power and performance for the client companion (1.0.0.7): launcher/windows_power.py and the Windows
branches of session_power, performance_monitor, graphics_limit, power_balance and headset_audio, tested on Linux.
Structure layouts are checked against the Win64 headers; NativeApi's own marshalling runs against fake DLL
functions and fake COM objects (C callbacks behind a real vtable); every module runs against a fake Windows API
with sys.platform patched. The Linux paths are checked to stay as they were."""
import ctypes
from ctypes import POINTER, c_int32, c_uint32, c_void_p
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock

from launcher import graphics_limit, headset_audio, performance_monitor, power_balance, session_power
from launcher import windows_power as wp
from launcher.graphics_limit import GpuClocks
from launcher.headset_audio import HeadsetAudio, steamvr_audio_settings
from launcher.performance_monitor import (PerformanceMonitor, WindowsSampler, windows_gpu_rows, windows_power_supplies,
                                          windows_process_group, windows_processor_rows)
from launcher.power_balance import CpuClocks, SteamOSPower
from launcher.session_power import SessionPower

BALANCED, BEST, EFFICIENT = wp.OVERLAY_BALANCED, wp.OVERLAY_BEST_PERFORMANCE, wp.OVERLAY_BEST_EFFICIENCY
BALANCED_PLAN = '381b4222-f694-41f0-9685-ff5bb260df2e'
FT_PER_S = wp.FILETIME_TICKS


class OnWindows(unittest.TestCase):
    """sys.platform is 'win32' for the whole test."""

    def setUp(self):
        patcher = mock.patch.object(sys, 'platform', 'win32')
        patcher.start()
        self.addCleanup(patcher.stop)


def never(*args, **kwargs):
    raise AssertionError('no Linux tool may run on Windows: %r' % (args,))


# --------------------------------------------------------------------------- structures and NativeApi
class StructureTests(unittest.TestCase):
    def test_layouts_are_the_win64_ones(self):
        self.assertEqual(ctypes.sizeof(wp.GUID), 16)
        self.assertEqual(ctypes.sizeof(wp.FILETIME), 8)
        self.assertEqual(ctypes.sizeof(wp.SYSTEM_POWER_STATUS), 12)
        self.assertEqual(ctypes.sizeof(wp.PROCESSOR_POWER_INFORMATION), 24)
        self.assertEqual(ctypes.sizeof(wp.PROCESSENTRY32W), 568)
        self.assertEqual((wp.PROCESSENTRY32W.th32DefaultHeapID.offset, wp.PROCESSENTRY32W.th32ParentProcessID.offset,
                          wp.PROCESSENTRY32W.szExeFile.offset), (16, 32, 44))
        self.assertEqual(ctypes.sizeof(wp.PDH_FMT_COUNTERVALUE), 16)
        self.assertEqual(wp.PDH_FMT_COUNTERVALUE.value.offset, 8)
        self.assertEqual(ctypes.sizeof(wp.PDH_FMT_COUNTERVALUE_ITEM_W), 24)
        self.assertEqual(wp.PDH_FMT_COUNTERVALUE_ITEM_W.FmtValue.offset, 8)
        self.assertEqual(ctypes.sizeof(wp.PROPERTYKEY), 20)
        self.assertEqual(ctypes.sizeof(wp.PROPVARIANT), 24)
        self.assertEqual(wp.PROPVARIANT.pointer.offset, 8)

    def test_values_read_from_windows_memory(self):
        raw = struct.pack('<6I', 3, 3300, 1650, 3300, 2, 0) + struct.pack('<6I', 4, 3300, 3300, 1320, 2, 1)
        entries = (wp.PROCESSOR_POWER_INFORMATION * 2).from_buffer_copy(raw)
        self.assertEqual([(e.Number, e.MaxMhz, e.CurrentMhz, e.MhzLimit) for e in entries],
                         [(3, 3300, 1650, 3300), (4, 3300, 3300, 1320)])
        status = wp.SYSTEM_POWER_STATUS.from_buffer_copy(bytes([1, 8, 83, 1]) + struct.pack('<2I', 0xFFFFFFFF, 0))
        self.assertEqual((status.ACLineStatus, status.BatteryFlag, status.BatteryLifePercent, status.SystemStatusFlag),
                         (1, 8, 83, 1))
        self.assertEqual(wp.FILETIME(0x89ABCDEF, 0x01234567).value, 0x0123456789ABCDEF)
        guid = wp.GUID.from_text(BEST)
        self.assertEqual((guid.Data1, guid.Data2, guid.Data3), (0xded574b5, 0x45a0, 0x4f42))
        self.assertEqual(guid.text(), BEST)
        entry = wp.PROCESSENTRY32W()
        name = 'VROSTBridgeClient.exe'.encode('utf-16-le')
        ctypes.memmove(ctypes.addressof(entry) + wp.PROCESSENTRY32W.szExeFile.offset, name, len(name))
        self.assertEqual(entry.name(), 'VROSTBridgeClient.exe')

    def test_importing_never_touches_windows(self):
        if hasattr(ctypes, 'WinDLL'):
            self.skipTest('this is Windows')
        native = wp.NativeApi()
        for call in (native.power_status, native.overlay, native.processor_information, native.system_times,
                     native.processes, native.default_audio_endpoints, lambda: native.set_execution_state(1)):
            with self.assertRaises(wp.WindowsPowerError):
                call()
        self.assertEqual(native._dlls, {})
        self.assertEqual(wp.reason(5), 'access denied')
        self.assertEqual(wp.reason(-1073738824), 'counter set not found')  # 0xC0000BB8 as a signed status
        self.assertEqual(wp.reason(0x1234), 'Windows error 0x00001234')


class FakeDll:
    """NativeApi with its DLL functions replaced by Python fakes (called with the same ctypes arguments)."""

    def __init__(self, **functions):
        self.api = wp.NativeApi()
        library = {'SetThreadExecutionState': 'kernel32', 'GetSystemPowerStatus': 'kernel32', 'LocalFree': 'kernel32',
                   'GetSystemTimes': 'kernel32', 'CreateToolhelp32Snapshot': 'kernel32', 'CloseHandle': 'kernel32',
                   'Process32FirstW': 'kernel32', 'Process32NextW': 'kernel32', 'OpenProcess': 'kernel32',
                   'GetProcessTimes': 'kernel32', 'ProcessIdToSessionId': 'kernel32'}
        for name, function in functions.items():
            dll = library.get(name) or ('pdh' if name.startswith('Pdh') else
                                        'ole32' if name.startswith(('Co', 'PropVariant')) else 'powrprof')
            self.api._functions[(dll, name)] = function


class NativeApiTests(unittest.TestCase):
    def test_power_mode_guids_go_by_reference(self):
        stored = {'guid': EFFICIENT}
        calls = []

        def actual(reference):
            calls.append('actual')
            ctypes.pointer(reference._obj)[0] = wp.GUID.from_text(stored['guid'])
            return 0

        def put(reference):
            stored['guid'] = reference._obj.text()
            return 0
        native = FakeDll(PowerGetActualOverlayScheme=actual, PowerSetActiveOverlayScheme=put).api
        self.assertEqual(native.overlay(), EFFICIENT)
        native.set_overlay(BEST)
        self.assertEqual(native.overlay(), BEST)
        refused = FakeDll(PowerGetActualOverlayScheme=lambda reference: 50,
                          PowerGetEffectiveOverlayScheme=actual, PowerSetActiveOverlayScheme=lambda reference: 5).api
        self.assertEqual(refused.overlay(), BEST, 'the effective power mode when the actual one is not reported')
        with self.assertRaises(wp.WindowsPowerError) as caught:
            refused.set_overlay(BALANCED)
        self.assertEqual((caught.exception.code, str(caught.exception)), (5, 'access denied'))

    def test_active_plan_is_freed(self):
        plan, freed = wp.GUID.from_text('8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c'), []

        def active(root, reference):
            reference._obj.contents = plan
            return 0
        native = FakeDll(PowerGetActiveScheme=active, LocalFree=lambda pointer: freed.append(pointer.value)).api
        self.assertEqual(native.active_scheme(), '8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c')
        self.assertEqual(freed, [ctypes.addressof(plan)])

    def test_processor_clocks_grow_the_buffer_until_it_fits(self):
        sizes = []

        def information(level, inputs, input_size, output, output_size):
            self.assertEqual((level, inputs, input_size), (wp.PROCESSOR_INFORMATION, None, 0))
            sizes.append(output_size)
            if output_size < 4 * 24:
                return wp.STATUS_BUFFER_TOO_SMALL
            for number, current in enumerate((3300, 1650, 540)):
                output[number] = wp.PROCESSOR_POWER_INFORMATION(number, 3300, current, 3300, 2, 0)
            return 0
        native = FakeDll(CallNtPowerInformation=information).api
        with mock.patch.object(wp.os, 'cpu_count', return_value=2):
            rows = native.processor_information()
        self.assertEqual(sizes, [48, 96])
        self.assertEqual([row['current_mhz'] for row in rows], [3300, 1650, 540], 'unfilled entries are left out')
        failing = FakeDll(CallNtPowerInformation=lambda *args: 0xC0000022).api
        with self.assertRaises(wp.WindowsPowerError):
            failing.processor_information()

    def test_programs_are_listed_from_a_toolhelp_snapshot(self):
        programs = iter([(4, 0, 'System'), (1100, 900, 'VROSTBridgeClient.exe')])
        closed = []

        def fill(snapshot, reference):
            entry = reference._obj
            self.assertEqual((snapshot, entry.dwSize), (77, 568))
            try:
                pid, parent, name = next(programs)
            except StopIteration:
                return 0
            entry.th32ProcessID, entry.th32ParentProcessID = pid, parent
            ctypes.memset(ctypes.addressof(entry.szExeFile), 0, 520)
            data = name.encode('utf-16-le')
            ctypes.memmove(ctypes.addressof(entry.szExeFile), data, len(data))
            return 1
        native = FakeDll(CreateToolhelp32Snapshot=lambda flags, pid: 77 if flags == wp.TH32CS_SNAPPROCESS else None,
                         Process32FirstW=fill, Process32NextW=fill, CloseHandle=closed.append).api
        self.assertEqual(native.processes(), [(4, 0, 'System'), (1100, 900, 'VROSTBridgeClient.exe')])
        self.assertEqual(closed, [77])
        invalid = FakeDll(CreateToolhelp32Snapshot=lambda flags, pid: wp.INVALID_HANDLE_VALUE).api
        with self.assertRaises(wp.WindowsPowerError):
            invalid.processes()

    def test_program_times_and_session(self):
        closed = []

        def times(handle, creation, exited, kernel, user):
            creation._obj.dwLowDateTime, kernel._obj.dwLowDateTime, user._obj.dwHighDateTime = 5, 20, 1
            return 1

        def session(pid, reference):
            reference._obj.value = 1
            return pid != 4
        native = FakeDll(OpenProcess=lambda access, inherit, pid: 55 if access == 0x1000 and pid != 4 else None,
                         GetProcessTimes=times, CloseHandle=closed.append, ProcessIdToSessionId=session).api
        self.assertEqual(native.process_times(1100), (5, (1 << 32) + 20))
        self.assertIsNone(native.process_times(4), 'a program that cannot be opened is skipped')
        self.assertEqual(closed, [55])
        self.assertEqual((native.session_id(1100), native.session_id(4)), (1, None))

    def test_pdh_counters_are_sized_read_and_closed(self):
        names = [ctypes.create_unicode_buffer(text) for text in
                 ('pid_1_luid_0x00000000_0x0000d1b5_phys_0_eng_0_engtype_3D', 'pid_2_new', 'pid_3_invalid')]
        calls = []

        def opened(source, user, reference):
            reference._obj.value = 11
            return 0

        def added(query, path, user, reference):
            calls.append(path)
            reference._obj.value = 21 if 'Engine' in path else 22
            return 0

        def array(counter, fmt, size, count, buffer):
            self.assertEqual(fmt, wp.PDH_FMT_DOUBLE | wp.PDH_FMT_NOCAP100)
            needed = 3 * 24 + 512
            if buffer is None or size._obj.value < needed:
                size._obj.value, count._obj.value = needed, 3
                return wp.PDH_MORE_DATA
            self.assertEqual(ctypes.addressof(buffer) % 8, 0)
            items = (wp.PDH_FMT_COUNTERVALUE_ITEM_W * 3).from_address(ctypes.addressof(buffer))
            for item, name, status, value in zip(items, names, (0, 1, 0xC0000BC6), (61.5, 2.0, 99.0)):
                item.szName = ctypes.addressof(name)
                item.FmtValue.CStatus, item.FmtValue.value.doubleValue = status, value
            count._obj.value = 3
            return 0

        def single(counter, fmt, kind, reference):
            reference._obj.CStatus, reference._obj.value.doubleValue = 0, 16.4
            return 0
        native = FakeDll(PdhOpenQueryW=opened, PdhAddEnglishCounterW=added, PdhCollectQueryData=lambda query: 0,
                         PdhGetFormattedCounterArrayW=array, PdhGetFormattedCounterValue=single,
                         PdhCloseQuery=lambda query: calls.append('closed %s' % query.value)).api
        query = native.pdh_query()
        query.add('engine', r'\GPU Engine(*)\Utilization Percentage')
        query.add('performance', r'\Processor Information(_Total)\% Processor Performance')
        self.assertEqual(query.collect(), 0)
        self.assertEqual(query.items('engine'), [('pid_1_luid_0x00000000_0x0000d1b5_phys_0_eng_0_engtype_3D', 61.5),
                                                 ('pid_2_new', 2.0)], 'items without valid data are skipped')
        self.assertEqual(query.value('performance'), 16.4)
        self.assertEqual((query.items('missing'), query.value('missing')), ([], None))
        query.close()
        query.close()
        self.assertEqual(calls, [r'\GPU Engine(*)\Utilization Percentage',
                                 r'\Processor Information(_Total)\% Processor Performance', 'closed 11'])
        refused = FakeDll(PdhOpenQueryW=opened, PdhAddEnglishCounterW=lambda *args: 0xC0000BB8).api.pdh_query()
        with self.assertRaises(wp.WindowsPowerError) as caught:
            refused.add('engine', r'\GPU Engine(*)\Utilization Percentage')
        self.assertIn('counter set not found', str(caught.exception))


class FakeCom:
    """A COM object: a real vtable of C callbacks, so NativeApi's vtable calls run as on Windows."""

    def __init__(self, methods):
        self.released = 0

        def release(this):
            self.released += 1
            return 0
        methods = dict(methods)
        methods[2] = (c_uint32, (), release)  # IUnknown::Release
        self.vtable = (c_void_p * (max(methods) + 1))()
        self.callbacks = []
        for index, (restype, argtypes, function) in methods.items():
            callback = ctypes.CFUNCTYPE(restype, c_void_p, *argtypes)(function)
            self.callbacks.append(callback)
            self.vtable[index] = ctypes.cast(callback, c_void_p).value
        self.object = (c_void_p * 1)(ctypes.addressof(self.vtable))
        self.pointer = ctypes.addressof(self.object)


class CoreAudioTests(unittest.TestCase):
    def make(self, recording_missing=False, name='Headphones (Bigscreen Beyond)'):
        self.texts = [ctypes.create_unicode_buffer(name)]
        self.cleared, self.com = [], []

        def get_value(this, key, value):
            self.assertEqual((key.contents.fmtid.text(), key.contents.pid), wp.PKEY_Device_FriendlyName)
            value.contents.vt, value.contents.pointer = wp.VT_LPWSTR, ctypes.addressof(self.texts[0])
            return 0
        self.store = FakeCom({5: (c_int32, (POINTER(wp.PROPERTYKEY), POINTER(wp.PROPVARIANT)), get_value)})

        def open_store(this, access, out):
            self.assertEqual(access, wp.STGM_READ)
            out[0] = self.store.pointer
            return 0
        self.device = FakeCom({4: (c_int32, (c_uint32, POINTER(c_void_p)), open_store)})

        def default(this, flow, role, out):
            self.assertEqual(role, wp.E_CONSOLE)
            if flow == wp.E_CAPTURE and recording_missing:
                return wp.E_NOTFOUND
            out[0] = self.device.pointer
            return 0
        self.enumerator = FakeCom({4: (c_int32, (c_uint32, c_uint32, POINTER(c_void_p)), default)})

        def create(clsid, outer, context, iid, out):
            self.assertEqual((clsid._obj.text(), iid._obj.text()), (wp.CLSID_MMDeviceEnumerator, wp.IID_IMMDeviceEnumerator))
            out._obj.value = self.enumerator.pointer
            return 0

        def clear(reference):
            self.cleared.append(reference._obj.vt)
            reference._obj.vt = 0
            return 0
        return FakeDll(CoInitializeEx=lambda reserved, flags: self.com.append('init') or 0,
                       CoUninitialize=lambda: self.com.append('uninit'), CoCreateInstance=create,
                       PropVariantClear=clear).api

    def test_default_devices_are_named_through_their_vtables(self):
        native = self.make()
        self.assertEqual(native.default_audio_endpoints(), {'playback': 'Headphones (Bigscreen Beyond)',
                                                            'recording': 'Headphones (Bigscreen Beyond)'})
        self.assertEqual(self.cleared, [wp.VT_LPWSTR, wp.VT_LPWSTR], 'each value is cleared')
        self.assertEqual((self.store.released, self.device.released, self.enumerator.released), (2, 2, 1))
        self.assertEqual(self.com, ['init', 'uninit'])

    def test_a_missing_device_is_none_and_com_is_always_closed(self):
        native = self.make(recording_missing=True)
        self.assertEqual(native.default_audio_endpoints()['recording'], None)
        self.assertEqual(self.com, ['init', 'uninit'])
        refused = FakeDll(CoInitializeEx=lambda reserved, flags: 0, CoUninitialize=lambda: self.com.append('uninit'),
                          CoCreateInstance=lambda *args: -2147221164).api  # REGDB_E_CLASSNOTREG
        with self.assertRaises(wp.WindowsPowerError):
            refused.default_audio_endpoints()
        self.assertEqual(self.com[-1], 'uninit')


# --------------------------------------------------------------------------- staying awake
class FakeKernel:
    def __init__(self, accept=True):
        self.accept, self.calls = accept, []

    def set_execution_state(self, flags):
        self.calls.append((flags, threading.current_thread().name))
        return 0x80000000 if self.accept else 0


class ExecutionStateHoldTests(unittest.TestCase):
    def test_held_and_cleared_by_its_own_thread(self):
        self.assertEqual(wp.SESSION_AWAKE, 0x80000003, 'continuous, system and display required')
        kernel = FakeKernel()
        hold = wp.ExecutionStateHold(api=kernel)
        self.assertTrue(hold.acquire())
        self.assertEqual(kernel.calls, [(wp.SESSION_AWAKE, 'VROSTBridge-session-awake')])
        self.assertNotEqual(threading.current_thread().name, 'VROSTBridge-session-awake')
        hold.release()
        self.assertEqual(kernel.calls[1], (wp.ES_CONTINUOUS, 'VROSTBridge-session-awake'), 'cleared on the same thread')
        hold.release()
        self.assertEqual(len(kernel.calls), 2)

    def test_refused_request(self):
        kernel = FakeKernel(accept=False)
        hold = wp.ExecutionStateHold(api=kernel)
        self.assertFalse(hold.acquire())
        hold.release()
        self.assertEqual(kernel.calls, [(wp.SESSION_AWAKE, 'VROSTBridge-session-awake')])


# --------------------------------------------------------------------------- session power
class FakeWindows:
    """The Windows surface session_power uses: power status, power plan and power mode per power source."""

    def __init__(self, overlay=BALANCED, ac_line=1, battery_flag=1, scheme=BALANCED_PLAN, accept_awake=True,
                 refuse_set=False, ignore_set=False):
        self.overlays = {'ac': overlay, 'dc': overlay}
        self.ac_line, self.battery_flag, self.scheme = ac_line, battery_flag, scheme
        self.accept_awake, self.refuse_set, self.ignore_set = accept_awake, refuse_set, ignore_set
        self.unreadable = False
        self.sets, self.awake = [], []

    def source(self):
        return {1: 'ac', 0: 'dc'}.get(self.ac_line, 'ac')

    def set_execution_state(self, flags):
        self.awake.append(flags)
        return 0x80000000 if self.accept_awake else 0

    def power_status(self):
        return {'ac_line': self.ac_line, 'battery_flag': self.battery_flag, 'battery_percent': 80, 'saver': 0}

    def active_scheme(self):
        return self.scheme

    def overlay(self):
        if self.unreadable:
            raise wp.WindowsPowerError('not supported', 50)
        return self.overlays[self.source()]

    def set_overlay(self, guid):
        if self.refuse_set:
            raise wp.WindowsPowerError('access denied', 5)
        self.sets.append((self.source(), guid))
        if not self.ignore_set:
            self.overlays[self.source()] = guid


class SessionPowerWindowsTests(OnWindows):
    def setUp(self):
        super().setUp()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.lines = []

    def make(self, windows):
        self.windows = windows
        power = SessionPower(runner=never, log=self.lines.append, systemd_inhibit='', powerprofilesctl='', busctl='',
                             cat='', popen=never, state_directory=self.directory, windows=windows)
        self.addCleanup(self.crash, power)
        return power

    @staticmethod
    def crash(power):
        """The companion dies: Windows drops its keep-awake request with the thread; the power mode stays."""
        hold, power._hold, power._overlay = power._hold, None, None
        if hold is not None:
            hold.release()

    def receipt(self):
        path = self.directory / session_power.RECEIPT_NAME
        return json.loads(path.read_text(encoding='utf-8'))['power_modes'] if path.exists() else None

    def test_session_keeps_the_pc_and_screen_awake_in_best_performance(self):
        power = self.make(FakeWindows())
        status = power.begin()
        self.assertEqual((status['state'], status['sleep_blocked'], status['display_awake']), ('active', True, True))
        self.assertEqual((status['power_profile'], status['original_profile'], status['power_mode']),
                         ('best-performance', 'balanced', 'Best performance'))
        self.assertEqual(status['lid'], 'unchanged')
        for words in ('This PC and its screen stay awake during the session.',
                      "Windows' power mode is Best performance until the session stops.",
                      'Closing the lid still does what Windows is set to do'):
            self.assertIn(words, status['message'])
        self.assertEqual(self.windows.awake, [wp.SESSION_AWAKE])
        self.assertEqual(self.windows.overlays['ac'], BEST)
        self.assertEqual(self.receipt(), {'ac': {'previous': BALANCED, 'set': BEST}}, 'a crash can be undone')
        ended = power.end()
        self.assertEqual(self.windows.awake, [wp.SESSION_AWAKE, wp.ES_CONTINUOUS])
        self.assertEqual(self.windows.overlays['ac'], BALANCED)
        self.assertIsNone(self.receipt())
        self.assertEqual((ended['state'], ended['sleep_blocked'], ended['power_profile']), ('idle', False, 'balanced'))
        self.assertIn("Windows' power mode is back to Balanced.", ended['message'])
        power.end()  # idempotent
        self.assertEqual(self.windows.sets, [('ac', BEST), ('ac', BALANCED)])
        self.assertEqual(len(self.windows.awake), 2)

    def test_a_power_mode_the_user_changed_is_left_alone(self):
        power = self.make(FakeWindows())
        power.begin()
        self.windows.overlays['ac'] = EFFICIENT
        status = power.end()
        self.assertEqual(self.windows.sets, [('ac', BEST)])
        self.assertEqual(status['power_profile'], 'best-power-efficiency')
        self.assertIn('was changed during the session, so it was left as it is', status['message'])
        self.assertIsNone(self.receipt())

    def test_best_performance_already_or_a_high_performance_plan_is_never_changed(self):
        power = self.make(FakeWindows(overlay=BEST))
        status = power.begin()
        self.assertEqual((status['state'], status['original_profile']), ('active', None))
        self.assertIn('was already Best performance', status['message'])
        power.end()
        self.assertEqual(self.windows.sets, [])
        power = self.make(FakeWindows(scheme='8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c'))
        status = power.begin()
        self.assertEqual((status['state'], status['power_plan']), ('active', 'High performance'))
        self.assertIn("The High performance power plan is in use, so Windows' power mode was left as it is.",
                      status['message'])
        power.end()
        self.assertEqual(self.windows.sets, [])
        self.assertIsNone(self.receipt())

    def test_a_refused_or_ignored_change_is_reported_and_never_undone(self):
        power = self.make(FakeWindows(refuse_set=True))
        status = power.begin()
        self.assertEqual((status['state'], status['power_profile']), ('partial', 'balanced'))
        self.assertIn("could not be set to Best performance (access denied)", status['message'])
        self.assertIsNone(self.receipt())
        power.end()
        power = self.make(FakeWindows(ignore_set=True))  # a power plan without power modes
        status = power.begin()
        self.assertIn("does not offer the Best performance power mode", status['message'])
        self.assertIsNone(self.receipt())
        power.end()
        self.assertEqual(self.windows.sets, [('ac', BEST)], 'nothing to set back')
        power = self.make(FakeWindows())
        self.windows.unreadable = True
        status = power.begin()
        self.assertEqual(status['state'], 'active')
        self.assertIn("Windows' power mode is not available on this PC (not supported).", status['message'])

    def test_a_refused_keep_awake_request(self):
        power = self.make(FakeWindows(accept_awake=False))
        status = power.begin()
        self.assertEqual((status['state'], status['sleep_blocked'], status['display_awake']), ('partial', False, False))
        self.assertIn('This PC may go to sleep during the session', status['message'])
        power.end()
        self.assertEqual(self.windows.awake, [wp.SESSION_AWAKE], 'nothing was held, so nothing to clear')
        self.assertEqual(self.windows.overlays['ac'], BALANCED)

    def test_a_crash_is_undone_by_recover_or_the_next_start(self):
        crashed = self.make(FakeWindows())
        crashed.begin()
        self.crash(crashed)  # ... without end()
        windows = self.windows
        after = self.make(windows)
        status = after.recover()
        self.assertEqual(windows.overlays['ac'], BALANCED)
        self.assertIsNone(self.receipt())
        self.assertIn('back to Balanced (a session that did not stop normally had changed it)', status['message'])
        self.assertEqual(after.recover()['message'], status['message'], 'nothing more to do')
        self.assertEqual(windows.sets, [('ac', BEST), ('ac', BALANCED)])
        # The next start does the same before it records the power mode to set back.
        crashed = self.make(FakeWindows())
        crashed.begin()
        self.crash(crashed)
        restarted = self.make(self.windows)
        status = restarted.begin()
        self.assertEqual(status['original_profile'], 'balanced')
        restarted.end()
        self.assertEqual(self.windows.overlays['ac'], BALANCED)
        # A power mode the user chose after the crash stays.
        crashed = self.make(FakeWindows())
        crashed.begin()
        self.crash(crashed)
        self.windows.overlays['ac'] = EFFICIENT
        self.make(self.windows).recover()
        self.assertEqual(self.windows.overlays['ac'], EFFICIENT)
        self.assertIsNone(self.receipt())

    def test_unplugged_during_the_session(self):
        power = self.make(FakeWindows())
        power.begin()
        self.windows.ac_line = 0
        status = power.end()
        self.assertEqual(self.windows.sets, [('ac', BEST)], 'the battery power mode was never changed')
        self.assertIn("Windows' power mode plugged in goes back to Balanced when VROSTBridge next starts while this PC "
                      'is plugged in.', status['message'])
        self.assertEqual(self.receipt(), {'ac': {'previous': BALANCED, 'set': BEST}})
        self.make(self.windows).recover()
        self.assertEqual(self.windows.overlays['ac'], BEST, 'still on battery')
        self.windows.ac_line = 1
        self.make(self.windows).recover()
        self.assertEqual(self.windows.overlays['ac'], BALANCED)
        self.assertIsNone(self.receipt())

    def test_without_a_battery_the_lid_is_not_mentioned_and_a_bad_receipt_is_ignored(self):
        (self.directory / session_power.RECEIPT_NAME).write_text('{"power_modes": {"ac": {"previous": "x"}}}')
        power = self.make(FakeWindows(battery_flag=128))
        status = power.begin()
        self.assertNotIn('lid', status['message'])
        self.assertEqual(status['lid'], 'unchanged')
        self.assertEqual(self.receipt(), {'ac': {'previous': BALANCED, 'set': BEST}})
        power.end()

    def test_linux_never_touches_windows(self):
        class Untouchable:
            def __getattr__(self, name):
                raise AssertionError('Windows API used on Linux: ' + name)
        with mock.patch.object(sys, 'platform', 'linux'):
            power = SessionPower(runner=lambda arguments, timeout: subprocess.CompletedProcess(arguments, 1, '', ''),
                                 log=self.lines.append, systemd_inhibit='', powerprofilesctl='', busctl='', cat='',
                                 state_directory=self.directory, windows=Untouchable())
            self.assertEqual(power.recover()['state'], 'idle')
            status = power.begin()
            self.assertEqual(status['state'], 'unavailable')
            self.assertNotIn('display_awake', status)
            self.assertEqual(set(power.end()), {'state', 'sleep_blocked', 'power_profile', 'original_profile',
                                                'message'})
        self.assertIsNone(self.receipt())


# --------------------------------------------------------------------------- performance samples
ENGINE = 'pid_%d_luid_0x00000000_0x0000D1B5_phys_0_eng_%d_engtype_%s'


class WindowsSampleParserTests(unittest.TestCase):
    def test_gpu_use_per_adapter(self):
        engines = [(ENGINE % (100, 0, '3D'), 40.0), (ENGINE % (200, 0, '3D'), 35.5), (ENGINE % (200, 1, '3D'), 10.0),
                   (ENGINE % (100, 4, 'VideoDecode'), 22.0), (ENGINE % (100, 2, 'Compute_0'), 5.0),
                   (ENGINE % (100, 3, 'Copy'), 50.0),
                   ('pid_300_luid_0x00000000_0x0000AAAA_phys_0_eng_0_engtype_3D', 70.0),
                   ('pid_301_luid_0x00000000_0x0000AAAA_phys_0_eng_0_engtype_3D', 50.0),
                   ('pid_302_luid_0x00000000_0x0000BBBB_phys_0_eng_7_engtype_Copy', 3.0),
                   ('garbage', 5.0), (ENGINE % (1, 0, '3D'), float('nan')), (ENGINE % (2, 0, '3D'), -1.0)]
        memory = [('luid_0x00000000_0x0000D1B5_phys_0', 536870912.0), ('luid_0x00000000_0x0000FFFF_phys_0', 1.0)]
        self.assertEqual(windows_gpu_rows(engines, memory), [
            {'card': 'luid_0x00000000_0x0000aaaa_phys_0', 'gpu_busy_percent': 100.0},
            {'card': 'luid_0x00000000_0x0000d1b5_phys_0', 'gpu_busy_percent': 75.5, 'video_decode_percent': 22.0,
             'compute_busy_percent': 5.0, 'vram_used_bytes': 536870912}])
        self.assertEqual(windows_gpu_rows([]), [])

    def test_processor_clocks_and_power_supplies(self):
        rows = windows_processor_rows([{'number': 0, 'max_mhz': 3300, 'current_mhz': 3300, 'limit_mhz': 3300},
                                       {'number': 1, 'max_mhz': 3300, 'current_mhz': 0, 'limit_mhz': 1320}, 'junk'])
        self.assertEqual(rows, [{'policy': 'cpu0', 'reported_frequency_mhz': 3300, 'maximum_mhz': 3300, 'limit_mhz': 3300},
                                {'policy': 'cpu1', 'maximum_mhz': 3300, 'limit_mhz': 1320}])
        self.assertEqual(windows_power_supplies({'ac_line': 1, 'battery_flag': 9, 'battery_percent': 83, 'saver': 0}),
                         [{'type': 'Mains', 'online': 1}, {'type': 'Battery', 'capacity_percent': 83, 'status': 'Charging'}])
        self.assertEqual(windows_power_supplies({'ac_line': 0, 'battery_flag': 0, 'battery_percent': 40, 'saver': 1}),
                         [{'type': 'Mains', 'online': 0},
                          {'type': 'Battery', 'capacity_percent': 40, 'status': 'Discharging', 'battery_saver': True}])
        self.assertEqual(windows_power_supplies({'ac_line': 1, 'battery_flag': 1, 'battery_percent': 100, 'saver': 0})[1]
                         ['status'], 'Full')
        self.assertEqual(windows_power_supplies({'ac_line': 1, 'battery_flag': 128, 'battery_percent': 255}),
                         [{'type': 'Mains', 'online': 1}], 'a desktop PC has no battery')
        self.assertEqual(windows_power_supplies({'ac_line': 255, 'battery_flag': 255, 'battery_percent': 255}), [])

    def test_windows_programs_join_the_session_groups(self):
        for name, group in (('VROSTBridgeClient.exe', 'VROSTBridge headset client'),
                            ('VROSTBridge Client.exe', 'VROSTBridge'), ('vrcompositor.exe', 'vrcompositor'),
                            ('vrserver.exe', 'vrserver'), ('VRMONITOR.EXE', 'vrmonitor'),
                            ('Baballonia.Desktop.exe', 'eye tracking (Baballonia)'), ('dwm.exe', 'dwm'),
                            ('steamwebhelper.exe', 'steamwebhelper'), ('steam.exe', 'steam'),
                            ('python.exe', 'other'), ('firefox.exe', 'other')):
            self.assertEqual(windows_process_group(name, 50, own_pid=1000), group, name)
        self.assertEqual(windows_process_group('python.exe', 1000, own_pid=1000), 'VROSTBridge', 'the companion')


class FakePdh:
    def __init__(self, engines=(), memory=(), performance=None):
        self.engines, self.memory, self.performance = list(engines), list(memory), performance
        self.counters, self.collections, self.closed = {}, 0, False

    def add(self, name, path):
        self.counters[name] = path

    def collect(self):
        self.collections += 1
        return 0

    def items(self, name):
        if name == 'engine':
            return self.engines if self.collections > 1 else []  # a rate counter reads from the second collection
        return self.memory if name == 'memory' else []

    def value(self, name):
        return self.performance if self.collections > 1 else None

    def close(self):
        self.closed = True


class FakePerformance:
    """The Windows surface the performance sampler uses."""
    WALL = 1_700_000_000.0
    WALL_FT = int((WALL + wp.FILETIME_UNIX_OFFSET_S) * FT_PER_S)
    OLD, NEW = WALL_FT - 1000 * FT_PER_S, WALL_FT + FT_PER_S // 10

    def __init__(self):
        self.list = [(0, 0, '[System Process]'), (4, 0, 'System'), (500, 4, 'svchost.exe'), (1000, 900, 'python.exe'),
                     (1100, 1000, 'VROSTBridgeClient.exe'), (1200, 900, 'vrcompositor.exe'), (1300, 900, 'firefox.exe'),
                     (1400, 600, 'dwm.exe'), (1600, 900, 'vrserver.exe'), (1700, 900, 'notepad.exe')]
        self.sessions = {0: 0, 4: 0, 500: 0}
        self.times = {500: (self.OLD, 0), 1000: (self.OLD, 10_000_000), 1100: (self.OLD, 20_000_000),
                      1200: (self.OLD, 0), 1300: (self.OLD, 50_000_000), 1400: (self.OLD, 100_000_000),
                      1700: (self.OLD, 0)}
        self.system = (10_000_000_000, 12_000_000_000, 3_000_000_000)
        self.pdh = FakePdh(memory=[('luid_0x00000000_0x0000D1B5_phys_0', 268435456.0)])
        self.pdh_error = None
        self.opened = 0

    def processes(self):
        return list(self.list)

    def session_id(self, pid):
        return self.sessions.get(pid, 1)

    def process_times(self, pid):
        return self.times.get(pid)

    def system_times(self):
        return self.system

    def processor_information(self):
        return [{'number': n, 'max_mhz': 3300, 'current_mhz': 3300, 'limit_mhz': 3300} for n in range(16)]

    def power_status(self):
        return {'ac_line': 0, 'battery_flag': 0, 'battery_percent': 62, 'saver': 0}

    def pdh_query(self):
        self.opened += 1
        if self.pdh_error:
            raise wp.WindowsPowerError(self.pdh_error)
        self.pdh = FakePdh(self.pdh.engines, self.pdh.memory, self.pdh.performance)
        return self.pdh

    def five_seconds_later(self):
        """A VR session's next five seconds on a 16-thread handheld, 25 % busy."""
        self.system = (10_600_000_000, 12_650_000_000, 3_150_000_000)
        self.list = [entry for entry in self.list if entry[0] != 1300] + [(1300, 900, 'steamwebhelper.exe'),
                                                                          (1500, 1000, 'Baballonia.Desktop.exe')]
        self.times.update({500: (self.OLD, 10 ** 12), 1000: (self.OLD, 15_000_000), 1100: (self.OLD, 45_000_000),
                           1200: (self.OLD, 10_000_000), 1300: (self.NEW, 2_000_000), 1400: (self.OLD, 103_000_000),
                           1500: (self.NEW, 7_500_000), 1600: (self.OLD, 999_000_000), 1700: (self.OLD, 100_000)})
        self.pdh.engines = [(ENGINE % (1100, 0, '3D'), 61.0), (ENGINE % (1200, 0, '3D'), 20.5),
                            (ENGINE % (1100, 4, 'VideoDecode'), 18.0)]
        self.pdh.performance = 16.4


class WindowsSamplerTests(unittest.TestCase):
    def make(self, native=None):
        self.native = native or FakePerformance()
        self.now = [10.0]
        return WindowsSampler(api=self.native, clock=lambda: self.now[0], wall=lambda: FakePerformance.WALL,
                              own_pid=1000)

    def test_a_session_sample_has_the_linux_keys(self):
        sampler = self.make()
        first = sampler.sample()
        self.assertEqual((first['gpus'], first['available']), ([], True))
        self.assertNotIn('processes', first, 'nothing to compare with yet')
        self.assertNotIn('cpu_busy_percent', first)
        self.assertEqual(len(first['cpu_policies']), 16)
        self.assertEqual(first['cpu_policies'][0], {'policy': 'cpu0', 'reported_frequency_mhz': 3300,
                                                    'maximum_mhz': 3300, 'limit_mhz': 3300})
        self.assertEqual(first['power_supplies'], [{'type': 'Mains', 'online': 0},
                                                   {'type': 'Battery', 'capacity_percent': 62, 'status': 'Discharging'}])
        self.native.five_seconds_later()
        self.now[0] = 15.0
        second = sampler.sample()
        self.assertEqual(second['processes'], {'VROSTBridge headset client': 50.0, 'vrcompositor': 20.0,
                                               'eye tracking (Baballonia)': 15.0, 'VROSTBridge': 10.0, 'dwm': 6.0,
                                               'steamwebhelper': 4.0})
        self.assertEqual(list(second['processes'])[0], 'VROSTBridge headset client', 'busiest first')
        self.assertEqual(second['cpu_busy_percent'], 25.0)
        self.assertEqual(second['gpus'], [{'card': 'luid_0x00000000_0x0000d1b5_phys_0', 'gpu_busy_percent': 81.5,
                                           'video_decode_percent': 18.0, 'vram_used_bytes': 268435456}])
        self.assertEqual((second['cpu_performance_percent'], second['cpu_effective_mhz']), (16.4, 541))
        for key in ('gpus', 'cpu_policies', 'power_supplies', 'available', 'processes', 'cpu_busy_percent'):
            self.assertIn(key, second)
        for absent in ('temperature_c', 'clock_mhz', 'sensors', 'selected_sclk_mhz', 'max_sclk_mhz'):
            self.assertNotIn(absent, json.dumps(second), 'no generic Windows source: left out')
        self.assertEqual(self.native.opened, 1, 'the counters stay open during the session')
        sampler.reset()
        self.assertTrue(self.native.pdh.closed)
        self.assertNotIn('processes', sampler.sample(), 'a new session starts afresh')
        self.assertEqual(self.native.opened, 2)

    def test_missing_counters_are_said_once_and_the_rest_still_samples(self):
        native = FakePerformance()
        native.pdh_error = 'PDH could not open (access denied).'
        sampler = self.make(native)
        first = sampler.sample()
        self.assertEqual(first['counter_error'], 'PDH could not open (access denied).')
        self.assertTrue(first['cpu_policies'])
        native.five_seconds_later()
        self.now[0] = 15.0
        second = sampler.sample()
        self.assertNotIn('counter_error', second)
        self.assertEqual(second['gpus'], [])
        self.assertEqual(second['cpu_busy_percent'], 25.0)
        self.assertEqual(native.opened, 1, 'not retried every sample')
        sampler.reset()
        sampler.sample()
        self.assertEqual(native.opened, 2, 'retried in the next session')

    def test_failures_are_left_out(self):
        class Broken(FakePerformance):
            def processor_information(self):
                raise wp.WindowsPowerError('no')

            def power_status(self):
                raise wp.WindowsPowerError('no')

            def processes(self):
                raise wp.WindowsPowerError('Windows did not list the programs (access denied).')
        sample = self.make(Broken()).sample()
        self.assertEqual((sample['cpu_policies'], sample['power_supplies'], sample['available']), ([], [], False))
        self.assertEqual(sample['process_error'], 'Windows did not list the programs (access denied).')


class PerformanceMonitorWindowsTests(OnWindows):
    def test_the_monitor_samples_windows_during_sessions_only(self):
        native = FakePerformance()
        sampler = WindowsSampler(api=native, own_pid=1000)
        context, records = {'active': True, 'phase': 'streaming'}, []
        monitor = PerformanceMonitor(lambda: context, lambda *args: records.append(args), auto_start=False,
                                     windows=sampler)
        with mock.patch.object(performance_monitor, 'read_performance', side_effect=AssertionError('Linux')):
            value = monitor.sample()
            self.assertEqual((value['active'], value['phase']), (True, 'streaming'))
            self.assertTrue(value['cpu_policies'])
            self.assertEqual(records[0][0], 'performance-sample')
            context['active'] = False
            monitor.sample()
        self.assertTrue(native.pdh.closed, 'the counters close between sessions')
        self.assertIsNone(sampler.previous)
        self.assertEqual(len(records), 1)
        monitor.close()

    def test_the_default_sampler_uses_windows_power(self):
        native = FakePerformance()
        with mock.patch.object(wp, '_api', native):
            monitor = PerformanceMonitor(lambda: {'active': True}, lambda *args: None, auto_start=False)
            self.assertEqual(len(monitor.sample()['cpu_policies']), 16)
            self.assertIsInstance(monitor.windows, WindowsSampler)
            monitor.close()
        self.assertTrue(native.pdh.closed)


# --------------------------------------------------------------------------- the graphics limit
def gpu_sample(busy, at, decode=30.0, compute=None):
    row = {'card': 'luid_0x00000000_0x0000d1b5_phys_0', 'gpu_busy_percent': busy, 'video_decode_percent': decode}
    if compute is not None:
        row['compute_busy_percent'] = compute
    return {'active': True, 'observed_monotonic': at, 'available': True, 'gpus': [row]}


LIMITED = {'limited': True, 'recent_throttled_s': 12, 'window_s': 30, 'episodes': 1, 'share': .4}
STANDARD = {'label': 'Standard', 'width': 2016, 'height': 2016}


class GraphicsLimitWindowsTests(OnWindows):
    def test_a_busy_gpu_without_clocks(self):
        clocks = GpuClocks()
        for at in (1.0, 2.0):
            clocks.observe(gpu_sample(92.0, at))
        self.assertIsNone(clocks.starved(), 'three samples are needed')
        clocks.observe(gpu_sample(91.0, 3.0))
        self.assertEqual(clocks.starved(), {'clock_mhz': None, 'max_mhz': None, 'power_w': None, 'busy_percent': 92,
                                            'basis': 'gpu-busy'})
        self.assertIsNone(clocks.latest(), 'no clock: no clock readout, as on Linux without the sensor')
        self.assertEqual(clocks.load(), {'busy_percent': 91, 'video_decode_percent': 30})
        idle = GpuClocks()
        for at in (1.0, 2.0, 3.0):
            idle.observe(gpu_sample(45.0, at))
        self.assertIsNone(idle.starved())
        compute = GpuClocks()
        for at in (1.0, 2.0, 3.0):
            sample = gpu_sample(10.0, at, compute=85.0)
            sample['gpus'].insert(0, {'card': 'luid_0x00000000_0x0000aaaa_phys_0', 'gpu_busy_percent': 20.0})
            compute.observe(sample)
        self.assertEqual(compute.starved()['busy_percent'], 85, 'the busier engine of the busiest GPU')
        compute.reset()
        self.assertIsNone(compute.load())

    def test_the_same_samples_on_linux_need_clocks(self):
        clocks = GpuClocks()
        for at in (1.0, 2.0, 3.0):
            clocks.observe(gpu_sample(92.0, at))
        with mock.patch.object(sys, 'platform', 'linux'):
            self.assertIsNone(clocks.starved())

    def test_windows_advice_names_windows_own_steps(self):
        starved = {'clock_mhz': None, 'max_mhz': None, 'power_w': None, 'busy_percent': 92, 'basis': 'gpu-busy'}
        result = graphics_limit.advice(LIMITED, 2560, 75, False, False, STANDARD, False, starved, gpu_auto=True)
        self.assertIn('Its GPU was 92% busy: it is at its limit', result['message'])
        for words in ('Connect the charger', 'Best performance', 'Power & battery', 'AMD Software',
                      "handheld maker's", 'Or choose Standard (2016 x 2016 per eye)'):
            self.assertIn(words, result['message'])
        for words in ('Game Mode', 'Quick Access', 'steamos'):
            self.assertNotIn(words, result['message'])
        self.assertEqual(result['actions'], ['use-standard'], 'no gpu-auto on Windows')
        self.assertEqual(result['short'], 'GPU 92% busy · raise the TDP limit')
        clocked = graphics_limit.advice(LIMITED, 2016, 75, starved={'clock_mhz': 800, 'max_mhz': 2700, 'power_w': 15})
        self.assertIn('Its GPU ran at 800 of its 2700 MHz while busy', clocked['message'])
        self.assertNotIn('Game Mode', clocked['message'])
        self.assertEqual(clocked['short'], 'GPU held at 800 MHz · raise the TDP limit')
        light = graphics_limit.advice(LIMITED, 2016, 75, load={'busy_percent': 35, 'video_decode_percent': 20})
        self.assertIn('Its GPU was only 35% busy, so the processor or another program', light['message'])
        self.assertIn('Close other programs on this PC', light['message'])
        busy = graphics_limit.advice(LIMITED, 2016, 75, load={'busy_percent': 65, 'video_decode_percent': 20})
        self.assertNotIn('only', busy['message'])

    def test_linux_advice_is_unchanged(self):
        with mock.patch.object(sys, 'platform', 'linux'):
            result = graphics_limit.advice(LIMITED, 2016, 75, starved={'clock_mhz': 800, 'max_mhz': 2700, 'power_w': 15},
                                           gpu_auto=True, load={'busy_percent': 10})
        self.assertIn("Steam's Game Mode: Quick Access menu > Performance", result['message'])
        self.assertEqual(result['actions'], ['gpu-auto'])
        self.assertNotIn('only 10% busy', result['message'])


# --------------------------------------------------------------------------- the power balance
def cpu_sample(effective, busy, when, base=3300, limit=3300, current=3300):
    sample = {'active': True, 'observed_monotonic': when, 'cpu_busy_percent': busy,
              'cpu_policies': [{'policy': 'cpu%d' % i, 'reported_frequency_mhz': float(current),
                                'maximum_mhz': float(base), 'limit_mhz': float(limit)} for i in range(16)],
              'gpus': [{'card': 'luid_0x00000000_0x0000d1b5_phys_0', 'gpu_busy_percent': 60.0}]}
    if effective is not None:
        sample['cpu_effective_mhz'] = effective
    return sample


class PowerBalanceWindowsTests(OnWindows):
    def held(self, *samples):
        clocks = CpuClocks()
        for sample in samples:
            clocks.observe(sample)
        return clocks

    def test_a_busy_processor_far_below_its_base_clock_is_held(self):
        clocks = self.held(cpu_sample(540, 45.0, 1), cpu_sample(545, 47.0, 2))
        self.assertEqual(clocks.starved(), {'cpu_mhz': 545, 'cpu_min_mhz': None, 'cause': 'power-limit',
                                            'cpu_max_mhz': 3300, 'cpu_limit_mhz': 3300, 'cpu_busy': 47, 'gpu_busy': 60})
        self.assertEqual(clocks.latest(), {'cpu_mhz': 545, 'cpu_min_mhz': None, 'cpu_max_mhz': 3300})
        self.assertTrue(CpuClocks.held(clocks.samples[-1]))
        for effective, busy, held in ((540, 8.0, False), (2000, 60.0, False), (1320, 25.0, True), (1330, 25.0, False),
                                      (900, 19.0, False), (900, 20.0, True)):
            clocks = self.held(cpu_sample(effective, busy, 1), cpu_sample(effective, busy, 2))
            self.assertEqual(bool(clocks.starved()), held, (effective, busy))
        self.assertIsNone(self.held(cpu_sample(540, 45.0, 1)).starved(), 'two samples in a row')

    def test_windows_own_limit_and_a_static_current_clock(self):
        clocks = self.held(cpu_sample(2500, 50.0, 1, limit=1000), cpu_sample(2500, 50.0, 2, limit=1000))
        self.assertEqual((clocks.starved()['cause'], clocks.starved()['cpu_limit_mhz']), ('processor-limit', 1000))
        static = self.held(cpu_sample(None, 90.0, 1), cpu_sample(None, 90.0, 2))
        self.assertIsNone(static.starved(), "Windows' CurrentMhz at the base clock never reads as held")
        self.assertEqual(static.latest()['cpu_mhz'], 3300)
        slow = self.held(cpu_sample(None, 90.0, 1, current=600), cpu_sample(None, 90.0, 2, current=600))
        self.assertEqual(slow.starved()['cpu_mhz'], 600, 'CurrentMhz where the processor performance is unknown')

    def test_the_same_samples_on_linux_have_no_minimum_and_are_never_held(self):
        with mock.patch.object(sys, 'platform', 'linux'):
            clocks = self.held(cpu_sample(540, 45.0, 1, current=540), cpu_sample(540, 45.0, 2, current=540))
            self.assertIsNone(clocks.starved())
            self.assertEqual(clocks.latest(), {'cpu_mhz': 540, 'cpu_min_mhz': None})

    def test_windows_advice(self):
        held = {'cpu_mhz': 545, 'cpu_min_mhz': None, 'cause': 'power-limit', 'cpu_max_mhz': 3300, 'cpu_busy': 47}
        result = power_balance.advice(held, {'available': True, 'gpu_level': 'manual', 'tdp_w': 28})
        self.assertEqual((result['state'], result['actions']), ('starved', []))
        self.assertIn('about 545 MHz (its base clock is 3300 MHz) while busy streaming', result['message'])
        for words in ('Connect the charger', 'Best performance', 'raise the TDP limit in', 'AMD Software'):
            self.assertIn(words, result['message'])
        for words in ('Game Mode', 'Quick Access', 'normal minimum', 'None'):
            self.assertNotIn(words, result['message'])
        self.assertEqual(result['short'], 'CPU held at 545 MHz · raise the TDP limit')
        limited = power_balance.advice(dict(held, cause='processor-limit', cpu_limit_mhz=1000))
        self.assertIn("Windows limits this PC's processor to about 1000 MHz", limited['message'])
        self.assertIn('Maximum processor state is 100%', limited['message'])
        self.assertEqual(limited['short'], 'CPU limited to 1000 MHz · check cooling and power mode')
        fixed = power_balance.advice(dict(held, cause='fixed-gpu-clock', gpu_mhz=2700),
                                     {'available': True, 'gpu_level': 'manual'})
        self.assertIn('The GPU clock is fixed at 2700 MHz', fixed['message'])
        self.assertEqual(fixed['actions'], [])
        self.assertEqual(power_balance.advice(None)['state'], 'ok')

    def test_steamos_manager_is_unavailable_and_runs_nothing(self):
        steamos = SteamOSPower(runner=never, busctl='/usr/bin/busctl')
        self.assertEqual(steamos.read(force=True), {'available': False,
                                                    'reason': 'steamos-manager is not available on Windows'})
        self.assertIn('not available on Windows', steamos.set_gpu_automatic())
        self.assertIn('AMD Software', power_balance.gpu_clock_hint())
        with mock.patch.object(sys, 'platform', 'linux'):
            self.assertIn("Steam's Game Mode, Quick Access menu > Performance", power_balance.gpu_clock_hint())
            self.assertEqual(SteamOSPower(runner=never, busctl='').read(), {'available': False})


# --------------------------------------------------------------------------- headset audio
NAMES = {'playback': 'Headphones (Bigscreen Beyond)', 'recording': 'Microphone (Bigscreen Beyond)'}


class HeadsetAudioWindowsTests(OnWindows):
    def setUp(self):
        super().setUp()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        config = self.root / 'Steam' / 'config'
        config.mkdir(parents=True)
        (self.root / 'openvr').mkdir()
        (self.root / 'openvr' / 'openvrpaths.vrpath').write_text(
            json.dumps({'config': [str(config)], 'runtime': ['C:/x']}), encoding='utf-8-sig')
        (config / 'steamvr.vrsettings').write_text(json.dumps({'audio': {
            'setOsDefaultPlaybackDevice': False, 'playbackDeviceOverride': '{0.0.0.00000000}.{1234}',
            'playbackDeviceOverrideName': 'Headphones (Bigscreen Beyond)', 'enablePlaybackMirror': True,
            'enablePlaybackDeviceOverride': 'not a bool?', 'viveHDMIGain': False}}), encoding='utf-8')
        environment = mock.patch.dict(os.environ, {'LOCALAPPDATA': str(self.root)})
        environment.start()
        self.addCleanup(environment.stop)
        self.lines = []

    def make(self, endpoints=lambda: dict(NAMES)):
        return HeadsetAudio(runner=never, log=self.lines.append, pactl='/usr/bin/pactl', endpoints=endpoints)

    def test_steamvr_manages_the_devices_and_nothing_is_changed(self):
        audio = self.make()
        status = audio.begin('Bigscreen Beyond 2e', None, True)
        self.assertEqual((status['state'], status['sink'], status['source'], status['managed_by']),
                         ('steamvr', None, None, 'steamvr'))
        self.assertEqual((status['playback'], status['recording']), (NAMES['playback'], NAMES['recording']))
        self.assertIn('SteamVR switches sound to the headset itself (SteamVR Settings > Audio)', status['message'])
        self.assertIn('Windows plays sound through Headphones (Bigscreen Beyond) and records from Microphone '
                      '(Bigscreen Beyond).', status['message'])
        self.assertIn('SteamVR is set not to switch the sound output to the headset', status['message'])
        self.assertNotIn('microphone to the headset', status['message'])
        self.assertEqual(status['steamvr_audio'], {'setOsDefaultPlaybackDevice': False,
                                                   'playbackDeviceOverrideName': 'Headphones (Bigscreen Beyond)',
                                                   'enablePlaybackMirror': True}, 'device ids and odd values are left out')
        self.assertEqual(audio.status()['managed_by'], 'steamvr')
        ended = audio.end()
        self.assertEqual(ended['state'], 'steamvr')
        audio.begin('Valve Index', None, False)  # the switch changes nothing on Windows either

    def test_names_are_refreshed_in_the_background_during_a_session_only(self):
        answers = iter([dict(NAMES), {'playback': 'Speakers (Realtek(R) Audio)', 'recording': None}])
        audio = self.make(endpoints=lambda: next(answers))
        audio.begin('Bigscreen Beyond 2e')
        with mock.patch.object(headset_audio, 'ENDPOINT_REFRESH_S', 0):
            audio.status()
            audio._endpoint_thread.join(2)
            status = audio.status()
            audio._endpoint_thread.join(2)
        self.assertEqual((status['playback'], status['recording']), ('Speakers (Realtek(R) Audio)', None))
        self.assertIn('records from no device', status['message'])
        audio.end()
        thread = audio._endpoint_thread
        with mock.patch.object(headset_audio, 'ENDPOINT_REFRESH_S', 0):
            audio.status()
        self.assertIs(audio._endpoint_thread, thread, 'no reading after the session')

    def test_unreadable_names_or_settings_still_report_steamvr(self):
        def refused():
            raise wp.WindowsPowerError('The sound device list could not be opened (access denied).')
        status = self.make(endpoints=refused).begin('Bigscreen Beyond 2e')
        self.assertEqual((status['state'], status['playback']), ('steamvr', None))
        self.assertIn("Windows' current sound devices could not be read (The sound device list could not be opened "
                      '(access denied).)', status['message'])
        with mock.patch.object(headset_audio, 'READ_ENDPOINT_NAMES', False):
            status = self.make(endpoints=never).begin('Bigscreen Beyond 2e')
        self.assertTrue(status['message'].startswith('On Windows, SteamVR switches sound'))
        self.assertNotIn('could not be read', status['message'])
        devices = self.make().devices()
        self.assertEqual((devices['available'], devices['default_playback']), (False, NAMES['playback']))
        self.assertIn('SteamVR chooses', devices['message'])

    def test_steamvr_settings_reader(self):
        self.assertEqual(steamvr_audio_settings()['playbackDeviceOverrideName'], 'Headphones (Bigscreen Beyond)')
        with mock.patch.dict(os.environ, {'LOCALAPPDATA': ''}):
            self.assertEqual(steamvr_audio_settings(), {})
        registry = self.root / 'relative.vrpath'
        registry.write_text(json.dumps({'config': ['Steam/config']}))
        self.assertEqual(steamvr_audio_settings(registry), {}, 'never a path relative to the working folder')

    def test_linux_status_is_unchanged(self):
        with mock.patch.object(sys, 'platform', 'linux'):
            audio = HeadsetAudio(runner=never, log=self.lines.append, pactl='', endpoints=never)
            self.assertEqual(set(audio.status()), {'state', 'sink', 'source', 'message', 'explanation'})
            self.assertEqual(audio.begin('Bigscreen Beyond 2e')['state'], 'unavailable')


if __name__ == '__main__':
    unittest.main()
