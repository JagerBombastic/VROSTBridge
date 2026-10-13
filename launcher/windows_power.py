"""Windows power, performance and sound-device calls for the headset-PC companion (1.0.0.7), through ctypes.

Stdlib only: the Windows companion runs on the embedded CPython in runtime\\python. Importing this module never
touches a Windows DLL, so it imports (and its structures are tested) on Linux; each DLL function is resolved on
first use, with its argument and result types declared. Structures use fixed-size ctypes types (c_uint32 for
DWORD/ULONG, c_uint16 for WCHAR, c_void_p for pointers and handles), so their layouts are the Win64 ones
whatever platform builds them.

Every call into Windows goes through NativeApi. The module-level `_api` is the one the companion uses; tests
replace it, or hand their own fake to the classes that take an `api`. Calls raise WindowsPowerError (an
OSError) with a short reason; callers turn that into their own 'unavailable' status.

Windows APIs used (Microsoft documentation names):
- SetThreadExecutionState (winbase.h). Windows ties the request to the calling thread and drops it when the
  thread ends, so ExecutionStateHold keeps a thread of its own for it: the request also ends if the
  companion exits or crashes.
- PowerGetActualOverlayScheme, PowerGetEffectiveOverlayScheme, PowerSetActiveOverlayScheme (powrprof.dll,
  Windows 10 1709 and later): the power mode of Settings > System > Power & battery. Windows keeps one power
  mode for battery and one for the charger and these read and set the one of the current power source.
  PowerSetActiveOverlayScheme takes its GUID by value, which the x64 calling convention passes by address.
  PowerGetActiveScheme and LocalFree (powersetting.h) for the power plan.
- GetSystemPowerStatus, SYSTEM_POWER_STATUS (winbase.h).
- CallNtPowerInformation(ProcessorInformation), PROCESSOR_POWER_INFORMATION (powerbase.h).
- GetSystemTimes, CreateToolhelp32Snapshot, Process32FirstW, Process32NextW, PROCESSENTRY32W (tlhelp32.h),
  OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION), GetProcessTimes, ProcessIdToSessionId, CloseHandle.
- PDH (pdh.dll): PdhOpenQueryW, PdhAddEnglishCounterW (English counter paths work on every display language),
  PdhCollectQueryData, PdhGetFormattedCounterArrayW, PdhGetFormattedCounterValue, PdhCloseQuery.
- Core Audio through COM (ole32.dll): CoInitializeEx, CoCreateInstance(MMDeviceEnumerator),
  IMMDeviceEnumerator::GetDefaultAudioEndpoint, IMMDevice::OpenPropertyStore,
  IPropertyStore::GetValue(PKEY_Device_FriendlyName), PropVariantClear, called through their vtables.
"""
import ctypes
from ctypes import POINTER, Structure, Union, c_double, c_int32, c_int64, c_size_t, c_uint8, c_uint16, c_uint32, \
    c_void_p, c_wchar_p, sizeof
import os
import threading
import uuid


class WindowsPowerError(OSError):
    """A Windows API is unavailable or refused; str() is a short reason, .code the Windows error or status."""

    def __init__(self, message, code=None):
        super().__init__(message)
        self.code = code


# --------------------------------------------------------------------------- constants
ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
ES_DISPLAY_REQUIRED = 0x00000002
SESSION_AWAKE = ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED

# The power mode ("power slider") overlays; Windows 10 names the efficient one Better battery.
OVERLAY_BEST_PERFORMANCE = 'ded574b5-45a0-4f42-8737-46345c09c238'
OVERLAY_BETTER_PERFORMANCE = '3af9b8d9-7c97-431d-ad78-34a8bfea439f'
OVERLAY_BALANCED = '00000000-0000-0000-0000-000000000000'
OVERLAY_BEST_EFFICIENCY = '961cc777-2547-4f9d-8174-7d86181b8a7a'
OVERLAYS = {OVERLAY_BEST_PERFORMANCE: ('best-performance', 'Best performance'),
            OVERLAY_BETTER_PERFORMANCE: ('better-performance', 'Better performance'),
            OVERLAY_BALANCED: ('balanced', 'Balanced'),
            OVERLAY_BEST_EFFICIENCY: ('best-power-efficiency', 'Best power efficiency')}
# Power plans that run the processor flat out and hide the power mode.
PERFORMANCE_PLANS = {'8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c': 'High performance',
                     'e9a42b02-d5df-448d-aa00-03f14749eb61': 'Ultimate Performance'}

PROCESSOR_INFORMATION = 11  # POWER_INFORMATION_LEVEL ProcessorInformation
STATUS_BUFFER_TOO_SMALL = 0xC0000023
TH32CS_SNAPPROCESS = 0x00000002
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
INVALID_HANDLE_VALUE = (1 << (8 * sizeof(c_void_p))) - 1
FILETIME_TICKS = 10_000_000          # 100-ns units per second
FILETIME_UNIX_OFFSET_S = 11644473600  # 1601-01-01 to 1970-01-01

PDH_FMT_DOUBLE = 0x00000200
PDH_FMT_NOCAP100 = 0x00008000
PDH_MORE_DATA = 0x800007D2
PDH_CSTATUS_VALID_DATA = 0x0
PDH_CSTATUS_NEW_DATA = 0x1

COINIT_MULTITHREADED = 0x0
CLSCTX_ALL = 0x17
STGM_READ = 0x0
VT_LPWSTR = 31
E_RENDER, E_CAPTURE, E_CONSOLE = 0, 1, 0
E_NOTFOUND = -2147023728          # HRESULT_FROM_WIN32(ERROR_NOT_FOUND), 0x80070490
RPC_E_CHANGED_MODE = -2147417850  # 0x80010106: COM already runs in another apartment on this thread

_REASONS = {
    2: 'not found', 5: 'access denied', 50: 'not supported', 87: 'invalid parameter', 120: 'not supported',
    127: 'not available on this Windows version', 1168: 'not found', 0xC0000022: 'access denied',
    0xC0000023: 'buffer too small', 0xC0000BB8: 'counter set not found', 0xC0000BB9: 'counter not found',
    0xC0000BC6: 'no valid data yet', 0x800007D5: 'no data', 0xC0000BB1: 'counter not found',
}

# Plain-language Windows steps for the graphics and power-balance advice (there is no Game Mode on Windows).
POWER_MODE_STEP = ("choose Best performance as Windows' power mode (Settings > System > Power & battery, or the "
                   'battery icon on the taskbar)')
VENDOR_TOOL = ("the handheld maker's or the GPU maker's own tool (for example the maker's control app or "
               'AMD Software)')


def reason(code):
    """A short, human reason for a Windows error or status code."""
    if code is None:
        return 'unknown error'
    unsigned = code & 0xFFFFFFFF if isinstance(code, int) else code
    return _REASONS.get(unsigned, 'Windows error 0x%08X' % unsigned if isinstance(unsigned, int) else str(code))


def overlay_slug(guid):
    """'best-performance', 'balanced', ...; 'custom' for a power mode this module does not know; None if unknown."""
    return OVERLAYS.get(guid, ('custom', ''))[0] if guid else None


def overlay_label(guid):
    return OVERLAYS.get(guid, ('custom', 'a custom power mode'))[1] if guid else 'unknown'


# --------------------------------------------------------------------------- structures
class GUID(Structure):
    _fields_ = [('Data1', c_uint32), ('Data2', c_uint16), ('Data3', c_uint16), ('Data4', c_uint8 * 8)]

    @classmethod
    def from_text(cls, text):
        return cls.from_buffer_copy(uuid.UUID(str(text)).bytes_le)

    def text(self):
        """Lower case, without braces: the form of the OVERLAY_* constants."""
        return str(uuid.UUID(bytes_le=bytes(self)))


class FILETIME(Structure):
    _fields_ = [('dwLowDateTime', c_uint32), ('dwHighDateTime', c_uint32)]

    @property
    def value(self):
        return (self.dwHighDateTime << 32) | self.dwLowDateTime


class SYSTEM_POWER_STATUS(Structure):
    _fields_ = [('ACLineStatus', c_uint8), ('BatteryFlag', c_uint8), ('BatteryLifePercent', c_uint8),
                ('SystemStatusFlag', c_uint8), ('BatteryLifeTime', c_uint32), ('BatteryFullLifeTime', c_uint32)]


class PROCESSOR_POWER_INFORMATION(Structure):
    _fields_ = [('Number', c_uint32), ('MaxMhz', c_uint32), ('CurrentMhz', c_uint32), ('MhzLimit', c_uint32),
                ('MaxIdleState', c_uint32), ('CurrentIdleState', c_uint32)]


class PROCESSENTRY32W(Structure):
    _fields_ = [('dwSize', c_uint32), ('cntUsage', c_uint32), ('th32ProcessID', c_uint32),
                ('th32DefaultHeapID', c_size_t), ('th32ModuleID', c_uint32), ('cntThreads', c_uint32),
                ('th32ParentProcessID', c_uint32), ('pcPriClassBase', c_int32), ('dwFlags', c_uint32),
                ('szExeFile', c_uint16 * 260)]

    def name(self):
        return bytes(self.szExeFile).decode('utf-16-le', 'replace').split('\x00', 1)[0]


class _PDH_VALUE(Union):
    _fields_ = [('longValue', c_int32), ('doubleValue', c_double), ('largeValue', c_int64), ('pointer', c_void_p)]


class PDH_FMT_COUNTERVALUE(Structure):
    _fields_ = [('CStatus', c_uint32), ('value', _PDH_VALUE)]


class PDH_FMT_COUNTERVALUE_ITEM_W(Structure):
    _fields_ = [('szName', c_void_p), ('FmtValue', PDH_FMT_COUNTERVALUE)]


class PROPERTYKEY(Structure):
    _fields_ = [('fmtid', GUID), ('pid', c_uint32)]


class PROPVARIANT(Structure):
    """The Win64 PROPVARIANT (24 bytes): the type, then a 16-byte union whose first member is the pointer."""
    _fields_ = [('vt', c_uint16), ('wReserved1', c_uint16), ('wReserved2', c_uint16), ('wReserved3', c_uint16),
                ('pointer', c_void_p), ('extra', c_void_p)]


CLSID_MMDeviceEnumerator = 'bcde0395-e52f-467c-8e3d-c4579291692e'
IID_IMMDeviceEnumerator = 'a95664d2-9614-4f35-a746-de8db63617e6'
PKEY_Device_FriendlyName = ('a45c254e-df1c-4efd-8020-67d146a850e0', 14)

# (restype, argtypes) per exported function; set when the function is first used.
_PROTOTYPES = {
    ('kernel32', 'SetThreadExecutionState'): (c_uint32, [c_uint32]),
    ('kernel32', 'GetSystemPowerStatus'): (c_int32, [POINTER(SYSTEM_POWER_STATUS)]),
    ('kernel32', 'GetSystemTimes'): (c_int32, [POINTER(FILETIME), POINTER(FILETIME), POINTER(FILETIME)]),
    ('kernel32', 'CreateToolhelp32Snapshot'): (c_void_p, [c_uint32, c_uint32]),
    ('kernel32', 'Process32FirstW'): (c_int32, [c_void_p, POINTER(PROCESSENTRY32W)]),
    ('kernel32', 'Process32NextW'): (c_int32, [c_void_p, POINTER(PROCESSENTRY32W)]),
    ('kernel32', 'OpenProcess'): (c_void_p, [c_uint32, c_int32, c_uint32]),
    ('kernel32', 'GetProcessTimes'): (c_int32, [c_void_p, POINTER(FILETIME), POINTER(FILETIME), POINTER(FILETIME),
                                                POINTER(FILETIME)]),
    ('kernel32', 'ProcessIdToSessionId'): (c_int32, [c_uint32, POINTER(c_uint32)]),
    ('kernel32', 'CloseHandle'): (c_int32, [c_void_p]),
    ('kernel32', 'LocalFree'): (c_void_p, [c_void_p]),
    ('powrprof', 'CallNtPowerInformation'): (c_uint32, [c_int32, c_void_p, c_uint32, c_void_p, c_uint32]),
    ('powrprof', 'PowerGetActiveScheme'): (c_uint32, [c_void_p, POINTER(POINTER(GUID))]),
    ('powrprof', 'PowerGetActualOverlayScheme'): (c_uint32, [POINTER(GUID)]),
    ('powrprof', 'PowerGetEffectiveOverlayScheme'): (c_uint32, [POINTER(GUID)]),
    ('powrprof', 'PowerSetActiveOverlayScheme'): (c_uint32, [POINTER(GUID)]),
    ('pdh', 'PdhOpenQueryW'): (c_uint32, [c_wchar_p, c_size_t, POINTER(c_void_p)]),
    ('pdh', 'PdhAddEnglishCounterW'): (c_uint32, [c_void_p, c_wchar_p, c_size_t, POINTER(c_void_p)]),
    ('pdh', 'PdhCollectQueryData'): (c_uint32, [c_void_p]),
    ('pdh', 'PdhGetFormattedCounterArrayW'): (c_uint32, [c_void_p, c_uint32, POINTER(c_uint32), POINTER(c_uint32),
                                                          c_void_p]),
    ('pdh', 'PdhGetFormattedCounterValue'): (c_uint32, [c_void_p, c_uint32, POINTER(c_uint32),
                                                         POINTER(PDH_FMT_COUNTERVALUE)]),
    ('pdh', 'PdhCloseQuery'): (c_uint32, [c_void_p]),
    ('ole32', 'CoInitializeEx'): (c_int32, [c_void_p, c_uint32]),
    ('ole32', 'CoUninitialize'): (None, []),
    ('ole32', 'CoCreateInstance'): (c_int32, [POINTER(GUID), c_void_p, c_uint32, POINTER(GUID), POINTER(c_void_p)]),
    ('ole32', 'PropVariantClear'): (c_int32, [POINTER(PROPVARIANT)]),
}

# COM methods are plain C functions taking the interface pointer first (one calling convention on x64).
_COM_FUNCTYPE = getattr(ctypes, 'WINFUNCTYPE', ctypes.CFUNCTYPE)


def _com_call(interface, index, restype, argtypes, *args):
    """Call the index-th method of a COM interface pointer through its vtable."""
    vtable = ctypes.cast(interface, POINTER(c_void_p))[0]
    address = ctypes.cast(c_void_p(vtable), POINTER(c_void_p))[index]
    if not address:
        raise WindowsPowerError('A COM interface had no method %d.' % index)
    return _COM_FUNCTYPE(restype, c_void_p, *argtypes)(address)(interface, *args)


def _release(interface):
    if interface:
        try:
            _com_call(interface, 2, c_uint32, ())  # IUnknown::Release
        except OSError:
            pass


def _clean_name(text, limit=120):
    text = ''.join(character for character in str(text) if character.isprintable()).strip()
    return text[:limit] or None


# --------------------------------------------------------------------------- the Windows calls
class PdhQuery:
    """One PDH query holding English-named counters. Rate counters (utilisation, processor performance) read
    only from the second collection on, as the average between the last two collections."""

    def __init__(self, api):
        self._api = api
        self.handle = c_void_p()
        self.counters = {}
        code = api._function('pdh', 'PdhOpenQueryW')(None, 0, ctypes.byref(self.handle))
        if code != 0 or not self.handle:
            raise WindowsPowerError('Windows performance counters could not be opened (' + reason(code) + ').', code)

    def add(self, name, path):
        counter = c_void_p()
        code = self._api._function('pdh', 'PdhAddEnglishCounterW')(self.handle, path, 0, ctypes.byref(counter))
        if code != 0 or not counter:
            raise WindowsPowerError(path + ' is not available (' + reason(code) + ').', code)
        self.counters[name] = counter

    def collect(self):
        """Take a reading of every counter; returns the PDH status (non-zero: some counters had no data)."""
        return self._api._function('pdh', 'PdhCollectQueryData')(self.handle)

    def items(self, name):
        """[(instance, value)] of a wildcard counter's valid instances; [] when it has no valid data yet."""
        counter = self.counters.get(name)
        if counter is None:
            return []
        function = self._api._function('pdh', 'PdhGetFormattedCounterArrayW')
        fmt = PDH_FMT_DOUBLE | PDH_FMT_NOCAP100
        size, count = c_uint32(0), c_uint32(0)
        code = function(counter, fmt, ctypes.byref(size), ctypes.byref(count), None)
        buffer = None
        for _ in range(3):  # instances can appear between the size query and the read
            if code != PDH_MORE_DATA or not size.value:
                break
            buffer = (ctypes.c_uint64 * (size.value // 8 + 2))()  # 8-byte aligned for the items' doubles
            size.value = sizeof(buffer)
            code = function(counter, fmt, ctypes.byref(size), ctypes.byref(count), buffer)
        if code != 0 or buffer is None or not count.value:
            return []
        number = min(count.value, sizeof(buffer) // sizeof(PDH_FMT_COUNTERVALUE_ITEM_W))
        rows = (PDH_FMT_COUNTERVALUE_ITEM_W * number).from_buffer(buffer)
        result = []
        for row in rows:
            if row.FmtValue.CStatus in (PDH_CSTATUS_VALID_DATA, PDH_CSTATUS_NEW_DATA) and row.szName:
                result.append((ctypes.wstring_at(row.szName)[:256], row.FmtValue.value.doubleValue))
        return result

    def value(self, name):
        """A single-instance counter's value, or None while it has no valid data."""
        counter = self.counters.get(name)
        if counter is None:
            return None
        value = PDH_FMT_COUNTERVALUE()
        code = self._api._function('pdh', 'PdhGetFormattedCounterValue')(
            counter, PDH_FMT_DOUBLE | PDH_FMT_NOCAP100, None, ctypes.byref(value))
        if code != 0 or value.CStatus not in (PDH_CSTATUS_VALID_DATA, PDH_CSTATUS_NEW_DATA):
            return None
        return value.value.doubleValue

    def close(self):
        handle, self.handle, self.counters = self.handle, c_void_p(), {}
        if handle:
            try:
                self._api._function('pdh', 'PdhCloseQuery')(handle)
            except OSError:
                pass


class NativeApi:
    """Windows DLL calls. Methods raise WindowsPowerError when Windows cannot answer."""

    def __init__(self):
        self._lock = threading.Lock()
        self._dlls = {}
        self._functions = {}

    def _function(self, library, name):
        with self._lock:
            function = self._functions.get((library, name))
            if function is not None:
                return function
            dll = self._dlls.get(library)
            if dll is None:
                loader = getattr(ctypes, 'WinDLL', None)
                if loader is None:
                    raise WindowsPowerError('This is only available on Windows.')
                try:
                    dll = loader(library, use_last_error=True)
                except OSError as error:
                    raise WindowsPowerError(library + '.dll could not be loaded (' + str(error)[:120] + ').') from None
                self._dlls[library] = dll
            try:
                function = getattr(dll, name)
            except AttributeError:
                raise WindowsPowerError(name + ' is not available on this Windows version.') from None
            function.restype, function.argtypes = _PROTOTYPES[(library, name)]
            self._functions[(library, name)] = function
            return function

    @staticmethod
    def _last_error():
        return ctypes.get_last_error() if hasattr(ctypes, 'get_last_error') else None

    # ---- staying awake and the power mode
    def set_execution_state(self, flags):
        """SetThreadExecutionState for the calling thread: the previous state, 0 when Windows refused."""
        return int(self._function('kernel32', 'SetThreadExecutionState')(flags) or 0)

    def overlay(self):
        """The power mode of the current power source (the user's choice where Windows reports it). Raises with
        a short reason, which the caller puts in context."""
        code, found = None, False
        for name in ('PowerGetActualOverlayScheme', 'PowerGetEffectiveOverlayScheme'):
            try:
                function = self._function('powrprof', name)
            except WindowsPowerError:
                continue
            found = True
            guid = GUID()
            code = function(ctypes.byref(guid))
            if code == 0:
                return guid.text()
        raise WindowsPowerError(reason(code) if found else 'not available on this Windows version', code)

    def set_overlay(self, guid_text):
        guid = GUID.from_text(guid_text)
        code = self._function('powrprof', 'PowerSetActiveOverlayScheme')(ctypes.byref(guid))
        if code != 0:
            raise WindowsPowerError(reason(code), code)

    def active_scheme(self):
        """The active power plan's GUID (lower case)."""
        scheme = POINTER(GUID)()
        code = self._function('powrprof', 'PowerGetActiveScheme')(None, ctypes.byref(scheme))
        if code != 0 or not scheme:
            raise WindowsPowerError('The active power plan could not be read (' + reason(code) + ').', code)
        try:
            return scheme.contents.text()
        finally:
            self._function('kernel32', 'LocalFree')(ctypes.cast(scheme, c_void_p))

    def power_status(self):
        """GetSystemPowerStatus as a dict of its raw fields."""
        status = SYSTEM_POWER_STATUS()
        if not self._function('kernel32', 'GetSystemPowerStatus')(ctypes.byref(status)):
            code = self._last_error()
            raise WindowsPowerError('Windows did not report the power status (' + reason(code) + ').', code)
        return {'ac_line': status.ACLineStatus, 'battery_flag': status.BatteryFlag,
                'battery_percent': status.BatteryLifePercent, 'saver': status.SystemStatusFlag}

    # ---- processor and processes
    def processor_information(self):
        """[{'number', 'max_mhz', 'current_mhz', 'limit_mhz'}] per logical processor."""
        function = self._function('powrprof', 'CallNtPowerInformation')
        count = max(1, os.cpu_count() or 1)
        status, buffer = None, None
        while count <= 2048:
            buffer = (PROCESSOR_POWER_INFORMATION * count)()
            status = function(PROCESSOR_INFORMATION, None, 0, buffer, sizeof(buffer))
            if status != STATUS_BUFFER_TOO_SMALL:
                break
            count *= 2
        if status != 0:
            raise WindowsPowerError('Windows did not report the processor clocks (' + reason(status) + ').', status)
        return [{'number': entry.Number, 'max_mhz': entry.MaxMhz, 'current_mhz': entry.CurrentMhz,
                 'limit_mhz': entry.MhzLimit} for entry in buffer if entry.MaxMhz]

    def system_times(self):
        """(idle, kernel, user) processor time of all processors in 100-ns units; kernel includes idle."""
        idle, kernel, user = FILETIME(), FILETIME(), FILETIME()
        if not self._function('kernel32', 'GetSystemTimes')(ctypes.byref(idle), ctypes.byref(kernel),
                                                            ctypes.byref(user)):
            code = self._last_error()
            raise WindowsPowerError('Windows did not report the processor time (' + reason(code) + ').', code)
        return idle.value, kernel.value, user.value

    def processes(self):
        """[(pid, parent pid, image name)] from a Toolhelp32 snapshot."""
        snapshot = self._function('kernel32', 'CreateToolhelp32Snapshot')(TH32CS_SNAPPROCESS, 0)
        if not snapshot or snapshot == INVALID_HANDLE_VALUE:
            code = self._last_error()
            raise WindowsPowerError('Windows did not list the programs (' + reason(code) + ').', code)
        try:
            entry = PROCESSENTRY32W()
            entry.dwSize = sizeof(PROCESSENTRY32W)
            following = self._function('kernel32', 'Process32NextW')
            result = []
            more = self._function('kernel32', 'Process32FirstW')(snapshot, ctypes.byref(entry))
            while more and len(result) < 16384:
                result.append((entry.th32ProcessID, entry.th32ParentProcessID, entry.name()))
                more = following(snapshot, ctypes.byref(entry))
            return result
        finally:
            self._function('kernel32', 'CloseHandle')(snapshot)

    def process_times(self, pid):
        """(creation time, kernel + user time) in 100-ns units, or None where the program cannot be opened."""
        handle = self._function('kernel32', 'OpenProcess')(PROCESS_QUERY_LIMITED_INFORMATION, 0, pid)
        if not handle:
            return None
        try:
            creation, exited, kernel, user = FILETIME(), FILETIME(), FILETIME(), FILETIME()
            if not self._function('kernel32', 'GetProcessTimes')(handle, ctypes.byref(creation), ctypes.byref(exited),
                                                                 ctypes.byref(kernel), ctypes.byref(user)):
                return None
            return creation.value, kernel.value + user.value
        finally:
            self._function('kernel32', 'CloseHandle')(handle)

    def session_id(self, pid):
        """The Windows (Remote Desktop) session a program runs in, or None."""
        session = c_uint32()
        if not self._function('kernel32', 'ProcessIdToSessionId')(pid, ctypes.byref(session)):
            return None
        return session.value

    def pdh_query(self):
        return PdhQuery(self)

    # ---- sound devices
    def default_audio_endpoints(self):
        """{'playback': name or None, 'recording': name or None}: Windows' default sound devices (Default Device,
        not Default Communication Device). Initialises COM on the calling thread for the call: use a thread of
        its own."""
        started = self._function('ole32', 'CoInitializeEx')(None, COINIT_MULTITHREADED)
        if started < 0 and started != RPC_E_CHANGED_MODE:
            raise WindowsPowerError('COM could not start (' + reason(started) + ').', started)
        try:
            enumerator = c_void_p()
            hr = self._function('ole32', 'CoCreateInstance')(
                ctypes.byref(GUID.from_text(CLSID_MMDeviceEnumerator)), None, CLSCTX_ALL,
                ctypes.byref(GUID.from_text(IID_IMMDeviceEnumerator)), ctypes.byref(enumerator))
            if hr < 0 or not enumerator:
                raise WindowsPowerError('The sound device list could not be opened (' + reason(hr) + ').', hr)
            try:
                return {'playback': self._endpoint_name(enumerator, E_RENDER),
                        'recording': self._endpoint_name(enumerator, E_CAPTURE)}
            finally:
                _release(enumerator)
        finally:
            if started >= 0:  # S_OK or S_FALSE: each successful CoInitializeEx is balanced
                self._function('ole32', 'CoUninitialize')()

    def _endpoint_name(self, enumerator, flow):
        device = c_void_p()
        # IMMDeviceEnumerator::GetDefaultAudioEndpoint(EDataFlow, ERole, IMMDevice**) is vtable entry 4.
        hr = _com_call(enumerator, 4, c_int32, (c_uint32, c_uint32, POINTER(c_void_p)), flow, E_CONSOLE,
                       ctypes.byref(device))
        if hr == E_NOTFOUND or (hr >= 0 and not device):
            return None
        if hr < 0:
            raise WindowsPowerError('The default sound device could not be read (' + reason(hr) + ').', hr)
        try:
            store = c_void_p()
            # IMMDevice::OpenPropertyStore(DWORD, IPropertyStore**) is vtable entry 4.
            hr = _com_call(device, 4, c_int32, (c_uint32, POINTER(c_void_p)), STGM_READ, ctypes.byref(store))
            if hr < 0 or not store:
                return None
            try:
                key = PROPERTYKEY(GUID.from_text(PKEY_Device_FriendlyName[0]), PKEY_Device_FriendlyName[1])
                value = PROPVARIANT()
                # IPropertyStore::GetValue(REFPROPERTYKEY, PROPVARIANT*) is vtable entry 5.
                hr = _com_call(store, 5, c_int32, (POINTER(PROPERTYKEY), POINTER(PROPVARIANT)), ctypes.byref(key),
                               ctypes.byref(value))
                if hr < 0:
                    return None
                try:
                    if value.vt == VT_LPWSTR and value.pointer:
                        return _clean_name(ctypes.wstring_at(value.pointer))  # up to its terminator
                    return None
                finally:
                    self._function('ole32', 'PropVariantClear')(ctypes.byref(value))
            finally:
                _release(store)
        finally:
            _release(device)


_api = NativeApi()


def api():
    """The NativeApi the companion uses (tests replace the module-level `_api`)."""
    return _api


# --------------------------------------------------------------------------- staying awake
class ExecutionStateHold:
    """SetThreadExecutionState(flags) held by a thread of its own until release().

    Windows ties the request to the calling thread and drops it when that thread ends, so setting it from a
    short-lived worker would have no lasting effect, and a companion that exits or crashes cannot leave the
    PC unable to sleep. release() clears the request on the same thread that set it.
    """

    def __init__(self, flags=SESSION_AWAKE, api=None, name='VROSTBridge-session-awake'):
        self.flags, self.api, self.name = flags, api, name
        self.accepted = False
        self.detail = ''
        self._thread = self._release = None

    def _hold(self, ready, release):
        native = self.api if self.api is not None else _api
        try:
            previous = native.set_execution_state(self.flags)
        except Exception as error:  # a missing or broken API must never stop a session
            previous, self.detail = 0, str(error)[:120]
        self.accepted = bool(previous)
        ready.set()
        if not previous:
            return
        release.wait()
        try:
            native.set_execution_state(ES_CONTINUOUS)
        except Exception:
            pass  # the request lapses when this thread ends anyway

    def acquire(self, timeout=2.0):
        """Start holding the request; True once Windows accepted it."""
        if self._thread is not None and self._thread.is_alive():
            return self.accepted
        ready, release = threading.Event(), threading.Event()
        self.accepted, self.detail = False, ''
        thread = threading.Thread(target=self._hold, args=(ready, release), daemon=True, name=self.name)
        self._thread, self._release = thread, release
        thread.start()
        if not ready.wait(timeout):
            self.detail = 'Windows did not answer in time'
            return False
        return self.accepted

    def release(self, timeout=2.0):
        thread, release = self._thread, self._release
        self._thread = self._release = None
        if thread is not None:
            release.set()
            thread.join(timeout)
        self.accepted = False
