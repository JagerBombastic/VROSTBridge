"""Native Windows notification-area icon. Shell calls stay on one message thread.

1.0.0.7: one for each program. The host's (role 'host') is as before: its state, Settings — open web UI and Exit.
The Windows client's (role 'client') names VROSTBridge Client and offers Open VROSTBridge, Start session, Stop
session (each enabled when it applies) and Quit."""
import ctypes as C
from ctypes import wintypes as W
import os,threading
from pathlib import Path
WM_TRAY=0x8047
TITLES={'host':'VROSTBridge Host','client':'VROSTBridge Client'}
OPEN,QUIT,START,STOP=1,2,3,4  # menu command IDs (TrackPopupMenu returns the chosen one)
# The client's states as its page names them (desktop.html), shortened for the tray.
CLIENT_LABELS={'stopped':'Ready to start','starting':'Starting session','searching':'Finding your host',
               'connecting':'Connecting to your host','restarting':'Host is restarting','streaming':'Streaming',
               'recovery':'Recovering video','unresponsive':'Not responding','error':'Needs attention'}
CLIENT_ACTIVE=('starting','searching','connecting','restarting','streaming','recovery','unresponsive')
CODECS={'Hevc':'HEVC','AV1':'AV1'}

def _menu_text(lines):
    # Windows menu ampersands designate accelerators. Escape device/log text.
    return tuple(' '.join(str(line).split())[:110].replace('&','&&') for line in lines)

def _part(value):
    return value if isinstance(value,dict) else {}

def client_view(status):
    """The client's tray: (label, details, offer), offer 'start', 'stop' or '' (neither: Stop is under way, another
    action runs, or the client is not installed). Stop is offered as soon as a Start shows, as on the page."""
    link=_part(status.get('session_link'));lease=_part(link.get('lease'))
    state=str(status.get('state') or '');linked=str(link.get('state') or '');running=bool(status.get('running'))
    active=running or link.get('active') is True or link.get('lease_active') is True or lease.get('active') is True
    error=status.get('startup_error') or link.get('last_error') or (link.get('message') if linked=='error' else '')
    missing=status.get('available') is False and not active
    if error or state=='error' or linked=='error' or missing:label='Needs attention'
    elif linked=='stopping':label='Stopping session'
    elif not running and (linked=='starting' or state=='starting'):label='Starting session'
    elif not running and linked in ('clientdiscover','discovering'):label='Finding your host'
    else:label=CLIENT_LABELS.get(state,'Checking this PC')
    if label=='Ready to start' and status.get('busy'):label='Please wait'  # an action such as Start is under way
    if linked=='stopping':offer=''
    elif active or state in CLIENT_ACTIVE or label in ('Starting session','Finding your host'):offer='stop'
    elif status.get('busy') or status.get('available') is not True:offer=''
    else:offer='start'
    restart=_part(status.get('runtime_restart')).get('message')
    detail=error or ('Required application files are missing. Repair or reinstall VROSTBridge, then reopen it.' if missing else '')
    details=[detail or restart or link.get('message') or ('Start a session here or on the VROSTBridge page.' if offer=='start' else '')]
    if running:
        host=link.get('host_name') or link.get('host_ip')
        if host:details.append('Host: '+str(host))
        video=_part(status.get('video'))
        if video.get('width') and video.get('height'):
            details.append(str(video['width'])+' × '+str(video['height'])+' per eye · '+CODECS.get(str(video.get('codec')),'H.264'))
    return label,_menu_text(line for line in details if line),offer

class ClientNotices:
    """1.0.0.7: what the Windows client's tray says out loud (a Windows notification) for a session started from the
    tray, which has no page open to show it: its first picture, and a session that ended or never started because of
    a problem. A session started from the page says it there instead. Pure: update(status) returns (title, text,
    error) or None."""
    def __init__(self):
        self.armed=False;self.seen_running=False;self.streamed=False;self.error=''
    def arm(self):
        """The tray's Start session was chosen: speak up for this session."""
        self.armed=True;self.seen_running=False;self.streamed=False;self.error=''
    def update(self,status):
        if not self.armed or not isinstance(status,dict):return None
        running=bool(status.get('running'));state=str(status.get('state') or '')
        error=' '.join(str(status.get('startup_error') or '').split())
        if running:self.seen_running=True
        if running and state=='streaming' and not self.streamed:
            self.streamed=True
            link=_part(status.get('session_link'))
            host=link.get('host_name') or link.get('host_ip')
            return ('Streaming','The headset now shows SteamVR from '+(str(host) if host else 'the rendering PC')+'.',False)
        if error and error!=self.error:
            self.error=error;self.armed=False
            return ('Session stopped' if self.seen_running else 'Session did not start',error[:250],True)
        if self.seen_running and not running and not status.get('busy'):self.armed=False  # stopped as asked
        return None

def tray_view(status,role='host'):
    """Pure view model shared by tooltip/menu, with no network or shell calls.
    The host's is (label, details); the client's (label, details, offer), see client_view."""
    if isinstance(status,str):status={'state':status}
    if role=='client':return client_view(status)
    host=status.get('host_control') or {};link=status.get('session_link') or {}
    error=status.get('startup_error') or host.get('error') or link.get('last_error')
    handshake=(status.get('host_events') or {}).get('connection_error') if status.get('running') else ''
    state=status.get('state');rows=host.get('clients') or []
    if error or handshake:label='Needs attention'
    elif link.get('state')=='stopping' or state=='host-stopping':label='Stopping session'
    elif link.get('state')=='starting' or state=='host-starting':label='Starting SteamVR'
    elif any(row.get('connection_state')=='Streaming' for row in rows) and status.get('running'):label='Streaming'
    else:label={'host-connected':'Headset connected','host-connecting':'Connecting headset','host-pairing':'Awaiting headset approval','host-ready':'Ready for headset'}.get(state,'Waiting for headset')
    detail=error or handshake or host.get('message') or link.get('message') or 'Start a session from the headset client.'
    quality=status.get('settings') or {};audio=status.get('audio') or {}
    details=[str(detail)]
    if status.get('running'):
        ip=link.get('client_ip')
        if ip:details.append('Client: '+str(ip))
        if quality.get('codec') and quality.get('bitrate'):details.append(str(quality['codec'])+' / '+str(quality['bitrate'])+' Mbit/s')
    if audio.get('warning') and status.get('running'):details.append('Microphone unavailable; session continues without it')
    # Windows menu ampersands designate accelerators. Escape device/log text.
    return label,tuple(' '.join(line.split())[:110].replace('&','&&') for line in details)

def light_taskbar(registry=None):
    """Whether the taskbar uses Windows' light theme (then the tray needs the dark glyph). Windows 10 before
    version 1903 has no light taskbar and no such value: dark, as there."""
    try:
        if registry is None:
            import winreg as registry
        with registry.OpenKey(registry.HKEY_CURRENT_USER,r'Software\Microsoft\Windows\CurrentVersion\Themes\Personalize') as key:
            value,_=registry.QueryValueEx(key,'SystemUsesLightTheme')
        return value==1
    except (OSError,ImportError,ValueError):return False

def tray_icon(icons,light=None):
    """The tray icon file: VROSTBridge's white glyph on a dark taskbar, its dark glyph on a light one (the
    colour app icon if neither is there). icons: a path, or {'light':white glyph,'dark':dark glyph,'app':colour}."""
    if not isinstance(icons,dict):return icons
    light=light_taskbar() if light is None else light
    for name in (('dark','app','light') if light else ('light','app','dark')):
        if icons.get(name) and Path(icons[name]).is_file():return icons[name]
    return None

class WindowsTray:
    def __init__(self,icons,open_callback,quit_callback,log=lambda x:None,role='host',start_callback=None,stop_callback=None):
        if role not in TITLES:raise ValueError('Unknown tray role: '+str(role))
        self.icons=icons;self.icon=tray_icon(icons,light=False if os.name!='nt' else None)
        self.open_ui=open_callback;self.quit=quit_callback;self.log=log
        # 1.0.0.7: the client's Start session and Stop session (never offered without their callbacks).
        self.role,self.title=role,TITLES[role];self.start_session=start_callback;self.stop_session=stop_callback
        self.ready=threading.Event();self.done=threading.Event();self.error=None;self.hwnd=None;self.thread=None
        self.lock=threading.Lock();self._show(tray_view({},role));self.pending=None
    def notify(self,title,text,error=False):
        """A Windows notification from the tray icon (1.0.0.7, the client's ClientNotices); shown by the tray's own
        thread, which owns the icon."""
        with self.lock:self.pending=(str(title)[:63],str(text)[:255],bool(error))
        if self.hwnd:self.user.PostMessageW(self.hwnd,0x8049,0,0)
    def _show(self,view):
        """Keep a view (label, details[, offer]); caller holds the lock or owns the object."""
        self.view=view;self.state,self.details=view[0],view[1];self.offer=view[2] if len(view)>2 else ''
    def start(self):
        if os.name!='nt' or not self.icon or not Path(self.icon).is_file():raise RuntimeError('Tray icon is unavailable.')
        self.thread=threading.Thread(target=self._run,daemon=True,name='VROSTBridge tray');self.thread.start()
        if not self.ready.wait(5):raise RuntimeError('Windows tray did not initialize.')
        if self.error:raise RuntimeError(str(self.error))
        return self
    def update(self,status):
        view=tray_view(status,self.role)
        with self.lock:
            if view==self.view:return
            self._show(view)
        if self.hwnd:self.user.PostMessageW(self.hwnd,0x8048,0,0)
    def menu_items(self):
        """The menu's commands as (flags, id, text): MF_STRING 0, MF_GRAYED 1, MF_SEPARATOR 0x800."""
        if self.role!='client':return ((0,OPEN,'Settings — open web UI'),(0,QUIT,'Exit'))
        with self.lock:offer=self.offer
        return ((0,OPEN,'Open VROSTBridge'),
                (0 if offer=='start' and self.start_session else 1,START,'Start session'),
                (0 if offer=='stop' and self.stop_session else 1,STOP,'Stop session'),
                (0x800,0,None),(0,QUIT,'Quit'))
    def _activate(self,choice):
        fn={OPEN:self.open_ui,QUIT:self.quit,START:self.start_session,STOP:self.stop_session}.get(choice)
        if fn:
            def run():
                try:fn()
                except Exception as error:self.log('Tray action: '+str(error))
            threading.Thread(target=run,daemon=True).start()
    def close(self):
        self.done.set()
        if self.hwnd:self.user.PostMessageW(self.hwnd,0x10,0,0)
        if self.thread:self.thread.join(3)
    def _run(self):
        try:self._pump()
        except BaseException as e:self.error=e;self.log('Tray: '+str(e));self.ready.set()
    def _pump(self):
        user=C.WinDLL('user32',use_last_error=True);shell=C.WinDLL('shell32',use_last_error=True);kernel=C.WinDLL('kernel32',use_last_error=True);self.user=user
        LRESULT=C.c_ssize_t;WPARAM=C.c_size_t;LPARAM=C.c_ssize_t;HANDLE=C.c_void_p
        WNDPROC=C.WINFUNCTYPE(LRESULT,HANDLE,W.UINT,WPARAM,LPARAM)
        class WC(C.Structure):_fields_=[('style',W.UINT),('proc',WNDPROC),('extra',C.c_int),('winextra',C.c_int),('instance',HANDLE),('icon',HANDLE),('cursor',HANDLE),('brush',HANDLE),('menu',W.LPCWSTR),('name',W.LPCWSTR)]
        class NID(C.Structure):_fields_=[('size',W.DWORD),('hwnd',HANDLE),('id',W.UINT),('flags',W.UINT),('callback',W.UINT),('icon',HANDLE),('tip',W.WCHAR*128),('state',W.DWORD),('mask',W.DWORD),('info',W.WCHAR*256),('version',W.UINT),('title',W.WCHAR*64),('infoflags',W.DWORD),('guid',C.c_byte*16),('balloon',HANDLE)]
        def bind(lib,name,ret,args):
            f=getattr(lib,name);f.restype=ret;f.argtypes=args;return f
        bind(kernel,'GetModuleHandleW',HANDLE,[W.LPCWSTR]);bind(user,'RegisterClassW',W.ATOM,[C.POINTER(WC)])
        bind(user,'CreateWindowExW',HANDLE,[W.DWORD,W.LPCWSTR,W.LPCWSTR,W.DWORD,C.c_int,C.c_int,C.c_int,C.c_int,HANDLE,HANDLE,HANDLE,HANDLE])
        bind(user,'DefWindowProcW',LRESULT,[HANDLE,W.UINT,WPARAM,LPARAM]);bind(user,'DestroyWindow',W.BOOL,[HANDLE])
        bind(user,'PostMessageW',W.BOOL,[HANDLE,W.UINT,WPARAM,LPARAM]);bind(user,'GetMessageW',C.c_int,[C.POINTER(W.MSG),HANDLE,W.UINT,W.UINT])
        bind(user,'TranslateMessage',W.BOOL,[C.POINTER(W.MSG)]);bind(user,'DispatchMessageW',LRESULT,[C.POINTER(W.MSG)])
        bind(user,'PostQuitMessage',None,[C.c_int]);bind(user,'RegisterWindowMessageW',W.UINT,[W.LPCWSTR])
        bind(user,'LoadImageW',HANDLE,[HANDLE,W.LPCWSTR,W.UINT,C.c_int,C.c_int,W.UINT]);bind(user,'DestroyIcon',W.BOOL,[HANDLE])
        bind(user,'CreatePopupMenu',HANDLE,[]);bind(user,'AppendMenuW',W.BOOL,[HANDLE,W.UINT,WPARAM,W.LPCWSTR]);bind(user,'DestroyMenu',W.BOOL,[HANDLE])
        bind(user,'TrackPopupMenu',W.UINT,[HANDLE,W.UINT,C.c_int,C.c_int,C.c_int,HANDLE,HANDLE]);bind(user,'SetForegroundWindow',W.BOOL,[HANDLE]);bind(user,'GetCursorPos',W.BOOL,[C.POINTER(W.POINT)])
        bind(user,'SetWindowPos',W.BOOL,[HANDLE,HANDLE,C.c_int,C.c_int,C.c_int,C.c_int,W.UINT])
        bind(user,'ShowWindow',W.BOOL,[HANDLE,C.c_int])
        bind(user,'SetTimer',WPARAM,[HANDLE,WPARAM,W.UINT,HANDLE]);bind(user,'KillTimer',W.BOOL,[HANDLE,WPARAM]);bind(user,'UnregisterClassW',W.BOOL,[W.LPCWSTR,HANDLE])
        bind(shell,'Shell_NotifyIconW',W.BOOL,[W.DWORD,C.POINTER(NID)])
        instance=kernel.GetModuleHandleW(None);name='VROSTBridgeTray-'+str(os.getpid());nid=NID();nid.size=C.sizeof(NID);nid.id=1;nid.flags=1|2|4|0x80;nid.callback=WM_TRAY
        nid.icon=user.LoadImageW(None,str(self.icon),1,0,0,0x10|0x40)
        if not nid.icon:raise C.WinError(C.get_last_error())
        taskbar=user.RegisterWindowMessageW('TaskbarCreated');added=False
        if not taskbar:user.DestroyIcon(nid.icon);raise C.WinError(C.get_last_error())
        def add():
            nonlocal added
            nid.tip=self.title+' — '+self.state
            added=bool(shell.Shell_NotifyIconW(0,C.byref(nid)))
            if added:
                nid.version=4;shell.Shell_NotifyIconW(4,C.byref(nid))
        menu_open=False
        def menu(hwnd):
            nonlocal menu_open
            if menu_open:return
            h=user.CreatePopupMenu()
            if not h:raise C.WinError(C.get_last_error())
            menu_open=True
            pt=W.POINT();user.GetCursorPos(C.byref(pt))
            with self.lock:label,details=self.state,self.details
            try:
                user.AppendMenuW(h,2,0,self.title+' — '+label)
                for detail in details:user.AppendMenuW(h,2,0,detail)
                user.AppendMenuW(h,0x800,0,None)
                for flags,command,text in self.menu_items():user.AppendMenuW(h,flags,command,text)
                # A permanently hidden owner cannot reliably take foreground
                # from Windows 11's topmost overflow panel. Show a zero-area
                # tool popup only for the menu's lifetime, in the topmost band.
                # It has no pixels, taskbar button or Alt-Tab entry.
                if not user.SetWindowPos(hwnd,HANDLE(-1),pt.x,pt.y,0,0,0x10|0x40):
                    raise C.WinError(C.get_last_error())
                if not user.SetForegroundWindow(hwnd):self.log('Tray foreground activation was refused by Windows.')
                choice=user.TrackPopupMenu(h,0x100|2,pt.x,pt.y,0,hwnd,None)
                user.PostMessageW(hwnd,0,0,0)
                self._activate(choice)
                shell.Shell_NotifyIconW(3,C.byref(nid))
            finally:
                user.ShowWindow(hwnd,0)
                user.SetWindowPos(hwnd,HANDLE(-2),0,0,0,0,0x1|0x2|0x10)
                user.DestroyMenu(h)
                menu_open=False
        @WNDPROC
        def proc(hwnd,msg,wp,lp):
            try:
                if msg==taskbar:add();return 0
                if msg==WM_TRAY:
                    event=lp&0xffff
                    if event in (0x7b,0x205):menu(hwnd)
                    elif event in (0x400,0x401,0x203):self._activate(1)
                    return 0
                if msg==0x7b:menu(hwnd);return 0
                if msg==0x8048:
                    nid.tip=self.title+' — '+self.state
                    if added:shell.Shell_NotifyIconW(1,C.byref(nid))
                    return 0
                if msg==0x8049:
                    with self.lock:pending,self.pending=self.pending,None
                    if pending and added:
                        # NIF_INFO with NIIF_ERROR or NIIF_INFO: Windows shows it as a notification, then the flag goes
                        # so later tooltip updates do not show it again.
                        nid.title,nid.info,nid.infoflags=pending[0],pending[1],3 if pending[2] else 1
                        nid.flags|=0x10;shell.Shell_NotifyIconW(1,C.byref(nid));nid.flags&=~0x10
                    return 0
                if msg==0x1A and lp and C.wstring_at(lp)=='ImmersiveColorSet':
                    # The taskbar switched between light and dark: the glyph follows it.
                    chosen=tray_icon(self.icons)
                    if chosen and str(chosen)!=str(self.icon):
                        icon=user.LoadImageW(None,str(chosen),1,0,0,0x10|0x40)
                        if icon:
                            previous,nid.icon,self.icon=nid.icon,icon,chosen
                            if added:shell.Shell_NotifyIconW(1,C.byref(nid))
                            user.DestroyIcon(previous)
                if msg==0x113:
                    if not added:add()
                    return 0
                if msg==0x10:user.DestroyWindow(hwnd);return 0
                if msg==2:shell.Shell_NotifyIconW(2,C.byref(nid));user.PostQuitMessage(0);return 0
            except Exception as e:self.log('Tray callback: '+str(e))
            return user.DefWindowProcW(hwnd,msg,wp,lp)
        wc=WC();wc.proc=proc;wc.instance=instance;wc.name=name
        if not user.RegisterClassW(C.byref(wc)):raise C.WinError(C.get_last_error())
        try:
            self.hwnd=user.CreateWindowExW(0x80,name,name,0x80000000,0,0,0,0,None,None,instance,None)
            if not self.hwnd:raise C.WinError(C.get_last_error())
            nid.hwnd=self.hwnd;add();user.SetTimer(self.hwnd,1,3000,None);self.ready.set()
            if self.done.is_set():user.PostMessageW(self.hwnd,0x10,0,0)
            message=W.MSG()
            while True:
                result=user.GetMessageW(C.byref(message),None,0,0)
                if result<=0:break
                user.TranslateMessage(C.byref(message));user.DispatchMessageW(C.byref(message))
        finally:
            if self.hwnd:shell.Shell_NotifyIconW(2,C.byref(nid));user.KillTimer(self.hwnd,1)
            self.hwnd=None;user.DestroyIcon(nid.icon);user.UnregisterClassW(name,instance)
