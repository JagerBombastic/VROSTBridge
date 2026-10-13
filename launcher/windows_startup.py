"""Owned, per-user Windows sign-in startup; does not override Task Manager.

1.0.0.7: one entry for each program, started with --background (the tray icon only: no browser, no SteamVR).
The host's Run value is VROSTBridgeHost (VROSTBridge Host.exe), the Windows client's VROSTBridgeClient
(VROSTBridge Client.exe)."""
import os
from pathlib import Path
import threading
RUN_KEY=r'Software\Microsoft\Windows\CurrentVersion\Run'
APPROVED_KEY=r'Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run'
VALUE='VROSTBridgeHost'  # the Run value's name
VALUES={'host':VALUE,'client':'VROSTBridgeClient'}
EXECUTABLES={'host':'VROSTBridge Host.exe','client':'VROSTBridge Client.exe'}  # the launcher in the installation
PROGRAMS={'host':'VROSTBridge Host','client':'VROSTBridge Client'}
ARGUMENT='--background'
class WindowsStartup:
    def __init__(self, executable, registry=None, role='host'):
        if role not in VALUES:raise ValueError('Unknown program for sign-in startup: '+str(role))
        self.role,self.value,self.program=role,VALUES[role],PROGRAMS[role]
        self.executable=Path(executable);self.registry=registry;self.lock=threading.RLock()
        if registry is None and os.name=='nt':
            import winreg
            self.registry=winreg
        self.command='"'+str(executable)+'" '+ARGUMENT
    def read(self):
        try:
            with self.registry.OpenKey(self.registry.HKEY_CURRENT_USER,RUN_KEY) as key:return self.registry.QueryValueEx(key,self.value)
        except FileNotFoundError:return None,None
    def status(self):
        # 'available' is 'supported' under the name the client's page reads (1.0.0.7).
        result={'supported':False,'available':False,'enabled':False,'conflict':False,'message':'Available in the installed Windows '+self.role+'.'}
        if self.registry is None or not self.executable.is_file():return result
        if len(self.command.encode('utf-16-le'))>520:
            result['message']='Installation path is too long for Windows sign-in startup.';return result
        try:
            value,kind=self.read();enabled=value==self.command and kind==self.registry.REG_SZ
            result.update(supported=True,available=True,enabled=enabled,conflict=value is not None and not enabled,
                          message='Start quietly in the system tray. No browser or SteamVR opens.')
            if result['conflict']:result['message']='Another program owns VROSTBridge\'s sign-in startup entry ('+self.value+'); it was left unchanged.'
            if enabled:
                try:
                    with self.registry.OpenKey(self.registry.HKEY_CURRENT_USER,APPROVED_KEY) as key:
                        approved,_=self.registry.QueryValueEx(key,self.value)
                    if isinstance(approved,bytes) and len(approved)>=4 and int.from_bytes(approved[:4],'little')==3:
                        result['message']='Windows disabled this item. Enable '+self.program+' in Windows Settings → Apps → Startup.'
                except FileNotFoundError:pass
        except OSError as e:result.update(supported=False,available=False,message=str(e))
        return result
    def set_enabled(self,enabled):
        if type(enabled) is not bool:raise ValueError('Startup must be true or false.')
        with self.lock:
            status=self.status()
            if not status['supported'] or status['conflict']:raise ValueError(status['message'])
            r=self.registry
            with r.CreateKeyEx(r.HKEY_CURRENT_USER,RUN_KEY,0,r.KEY_SET_VALUE) as key:
                if enabled:r.SetValueEx(key,self.value,0,r.REG_SZ,self.command)
                elif status['enabled']:r.DeleteValue(key,self.value)
        return self.status()
