"""Windows process identity checks for bounded, owned-runtime shutdown recovery."""
import ctypes
from ctypes import wintypes
import os
from pathlib import Path

def identify(pid):
    if os.name!='nt':return None
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD];kernel.OpenProcess.restype=wintypes.HANDLE
    kernel.CloseHandle.argtypes=[wintypes.HANDLE]
    kernel.QueryFullProcessImageNameW.argtypes=[wintypes.HANDLE,wintypes.DWORD,wintypes.LPWSTR,ctypes.POINTER(wintypes.DWORD)]
    kernel.GetProcessTimes.argtypes=[wintypes.HANDLE]+[ctypes.POINTER(wintypes.FILETIME)]*4
    handle=kernel.OpenProcess(0x1000,False,pid)
    if not handle:return None
    try:
        name=ctypes.create_unicode_buffer(32768);length=wintypes.DWORD(len(name))
        created,ended,kernel_time,user=[wintypes.FILETIME() for _ in range(4)]
        if not kernel.QueryFullProcessImageNameW(handle,0,name,ctypes.byref(length)) or not kernel.GetProcessTimes(handle,*[ctypes.byref(t) for t in (created,ended,kernel_time,user)]):return None
        return {'pid':pid,'path':str(Path(name.value).resolve()),'created':(created.dwHighDateTime<<32)|created.dwLowDateTime}
    finally:kernel.CloseHandle(handle)

def terminate(identity):
    if not identity or identify(identity['pid'])!=identity:raise ValueError('Runtime process identity changed; it was not terminated.')
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD];kernel.OpenProcess.restype=wintypes.HANDLE
    kernel.GetProcessTimes.argtypes=[wintypes.HANDLE]+[ctypes.POINTER(wintypes.FILETIME)]*4
    kernel.TerminateProcess.argtypes=[wintypes.HANDLE,wintypes.UINT];kernel.WaitForSingleObject.argtypes=[wintypes.HANDLE,wintypes.DWORD];kernel.CloseHandle.argtypes=[wintypes.HANDLE]
    handle=kernel.OpenProcess(0x1000|0x100000|1,False,identity['pid'])
    if not handle:raise OSError('Cannot open the owned SteamVR process for shutdown.')
    try:
        times=[wintypes.FILETIME() for _ in range(4)]
        if not kernel.GetProcessTimes(handle,*[ctypes.byref(t) for t in times]) or ((times[0].dwHighDateTime<<32)|times[0].dwLowDateTime)!=identity['created']:raise ValueError('SteamVR PID was reused; no process was stopped.')
        if not kernel.TerminateProcess(handle,1):raise OSError('Owned SteamVR process could not be stopped.')
        if kernel.WaitForSingleObject(handle,5000)!=0:raise OSError('Owned SteamVR process did not exit.')
    finally:kernel.CloseHandle(handle)
