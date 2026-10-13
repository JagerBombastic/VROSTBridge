"""Packaged entry point: persist startup errors before importing the application."""
import os
from pathlib import Path
import sys
import traceback


def gamescope_wrapper(arguments):
    """Game Mode's compositor start: VROSTBridge's additions, or plain gamescope if anything fails (RC4.3)."""
    try:
        from launcher.game_mode import wrapper
        return wrapper(arguments)
    except BaseException:
        environment=dict(os.environ);environment.pop('GAMESCOPE_LEASE_SOCK',None)
        # Not PyInstaller's private library path: gamescope gets the session's own.
        if 'LD_LIBRARY_PATH_ORIG' in environment:environment['LD_LIBRARY_PATH']=environment['LD_LIBRARY_PATH_ORIG']
        else:environment.pop('LD_LIBRARY_PATH',None)
        os.execve('/usr/bin/gamescope',['/usr/bin/gamescope',*arguments],environment)


def main():
    if sys.argv[1:2]==['--gamescope-wrapper']:
        return gamescope_wrapper(sys.argv[2:])
    if sys.argv[1:2]==['--base-stations']:
        # 1.0.0.8: the base stations' power command on its own (started as the client closes); no startup log.
        sys.path.insert(0,str(Path(sys.executable).resolve().parent if getattr(sys,'frozen',False) else Path(__file__).resolve().parents[1]))
        from launcher.base_stations import main as base_stations
        return base_stations(sys.argv[2:])
    role='client' if '--role' in sys.argv and sys.argv[sys.argv.index('--role')+1:][:1] == ['client'] else 'host'
    base=Path(os.environ.get('LOCALAPPDATA', os.environ.get('XDG_STATE_HOME', str(Path.home()/'.local/state'))))/'VROSTBridge'
    log_path=base/(role+'-startup.log')
    try:
        base.mkdir(parents=True, exist_ok=True)
        with log_path.open('a', encoding='utf-8') as log:
            log.write('\nStarting VROSTBridge with Python '+sys.version.split()[0]+'\n')
        root=Path(sys.executable).resolve().parent if getattr(sys,'frozen',False) else Path(__file__).resolve().parents[1]
        sys.path.insert(0,str(root))
        if '--installer-maintenance' in sys.argv:
            from launcher.install_maintenance import main as maintain
            return maintain(sys.argv[sys.argv.index('--installer-maintenance')+1:])
        from launcher.app import main as run
        return run()
    except Exception:
        details=traceback.format_exc()
        try:
            with log_path.open('a', encoding='utf-8') as log:log.write(details)
        except OSError:pass
        if sys.stderr is not None:print(details,file=sys.stderr)
        if os.environ.get('VROSTBRIDGE_NATIVE_LAUNCHER') != '1':
            message='VROSTBridge could not open.\n\nDetails were written to:\n'+str(log_path)
            try:
                if sys.platform == 'win32':
                    import ctypes
                    ctypes.windll.user32.MessageBoxW(None,message,'VROSTBridge startup error',0x10)
                else:
                    from launcher.app import notify_desktop
                    notify_desktop(message,error=True)
            except Exception:pass
        return 1


if __name__=='__main__':sys.exit(main())
