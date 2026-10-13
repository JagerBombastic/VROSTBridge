"""Minimise and show another program's window through X11 (RC4.6).

Baballonia's window opened over the VROSTBridge page in the browser whenever a session started. Baballonia
(Avalonia) draws through X11, under KDE Plasma through XWayland, so VROSTBridge asks the window manager to
minimise it (ICCCM WM_CHANGE_STATE, what XIconifyWindow sends) and to show it again (EWMH _NET_ACTIVE_WINDOW,
as a taskbar does). Windows are found by the process they belong to (_NET_WM_PID) among the windows the window
manager lists (_NET_CLIENT_LIST), or by their class name. ctypes only; the connection is closed after each use.
Nothing here runs on Windows."""
import ctypes
import ctypes.util
import os
import threading

ANY_PROPERTY_TYPE = 0
BABALLONIA_CLASS = 'Baballonia.Desktop'  # its WM_CLASS (Avalonia uses the program's name)
CLIENT_MESSAGE = 33
SUBSTRUCTURE_NOTIFY_MASK = 1 << 19
SUBSTRUCTURE_REDIRECT_MASK = 1 << 20


class XClientMessageEvent(ctypes.Structure):
    _fields_ = [('type', ctypes.c_int), ('serial', ctypes.c_ulong), ('send_event', ctypes.c_int),
                ('display', ctypes.c_void_p), ('window', ctypes.c_ulong), ('message_type', ctypes.c_ulong),
                ('format', ctypes.c_int), ('data', ctypes.c_long * 5)]


class XEvent(ctypes.Union):
    _fields_ = [('client', XClientMessageEvent), ('pad', ctypes.c_long * 24)]


def _library():
    name = ctypes.util.find_library('X11') or 'libX11.so.6'
    library = ctypes.CDLL(name)
    library.XOpenDisplay.restype = ctypes.c_void_p
    library.XOpenDisplay.argtypes = [ctypes.c_char_p]
    library.XCloseDisplay.argtypes = [ctypes.c_void_p]
    library.XDefaultRootWindow.restype = ctypes.c_ulong
    library.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
    library.XDefaultScreen.argtypes = [ctypes.c_void_p]
    library.XInternAtom.restype = ctypes.c_ulong
    library.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
    library.XGetWindowProperty.argtypes = [
        ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_long, ctypes.c_long, ctypes.c_int, ctypes.c_ulong,
        ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_ulong),
        ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.c_void_p)]
    library.XQueryTree.argtypes = [
        ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.c_ulong),
        ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_uint)]
    library.XFree.argtypes = [ctypes.c_void_p]
    library.XIconifyWindow.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int]
    library.XSendEvent.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_long,
                                   ctypes.POINTER(XEvent)]
    library.XFlush.argtypes = [ctypes.c_void_p]
    library.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]
    library.XSetErrorHandler.restype = ctypes.c_void_p
    library.XSetErrorHandler.argtypes = [ctypes.c_void_p]
    return library


# A window can vanish between being listed and being read: X errors are ignored (Xlib's default handler
# prints the error and exits the process). The handler is one per process, shared by every connection, so
# it is installed once and never put back: restoring it when one connection closed while another was still
# in use (two threads) brought the default handler back, and a BadWindow then ended the companion.
_ERROR_HANDLER = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)(lambda display, event: 0)
_HANDLER_LOCK = threading.Lock()
_handler_installed = False


def _ignore_errors(library):
    global _handler_installed
    with _HANDLER_LOCK:
        if not _handler_installed:
            library.XSetErrorHandler(ctypes.cast(_ERROR_HANDLER, ctypes.c_void_p))
            _handler_installed = True


class Display:
    """One connection to the X server named by DISPLAY (or the given name)."""

    def __init__(self, name=None, library=None):
        name = name if name is not None else os.environ.get('DISPLAY')
        if not name:
            raise OSError('no X display (DISPLAY is not set)')
        self.x = library or _library()
        self.display = self.x.XOpenDisplay(name.encode())
        if not self.display:
            raise OSError('the X display ' + name + ' could not be opened')
        _ignore_errors(self.x)
        self.root = self.x.XDefaultRootWindow(self.display)

    def close(self):
        if self.display:
            self.x.XCloseDisplay(self.display)
            self.display = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def atom(self, name):
        return self.x.XInternAtom(self.display, name.encode(), 0)

    def _property(self, window, name, item_size):
        kind, form, count, after = ctypes.c_ulong(), ctypes.c_int(), ctypes.c_ulong(), ctypes.c_ulong()
        data = ctypes.c_void_p()
        status = self.x.XGetWindowProperty(self.display, window, self.atom(name), 0, 1 << 16, 0, ANY_PROPERTY_TYPE,
                                           ctypes.byref(kind), ctypes.byref(form), ctypes.byref(count),
                                           ctypes.byref(after), ctypes.byref(data))
        if status != 0 or not data.value:
            return None
        try:
            if form.value == 32:  # 32-bit items are delivered as C longs
                return list((ctypes.c_ulong * count.value).from_address(data.value))
            if form.value == 8:
                return ctypes.string_at(data.value, count.value)
            return None
        finally:
            self.x.XFree(data)

    def windows(self):
        """The windows the window manager lists, or else the top-level windows."""
        listed = self._property(self.root, '_NET_CLIENT_LIST', 32)
        if listed:
            return listed
        root, parent, children, count = ctypes.c_ulong(), ctypes.c_ulong(), ctypes.c_void_p(), ctypes.c_uint()
        if not self.x.XQueryTree(self.display, self.root, ctypes.byref(root), ctypes.byref(parent),
                                 ctypes.byref(children), ctypes.byref(count)) or not children.value:
            return []
        try:
            return list((ctypes.c_ulong * count.value).from_address(children.value))
        finally:
            self.x.XFree(children)

    def pid(self, window):
        value = self._property(window, '_NET_WM_PID', 32)
        return int(value[0]) if value else None

    def window_class(self, window):
        value = self._property(window, 'WM_CLASS', 8)
        return value.replace(b'\0', b' ').decode('utf-8', 'replace').strip() if value else ''

    def find(self, pids=(), class_name=None):
        """Windows of these processes (or, failing that, whose class contains class_name)."""
        pids = set(pids)
        found = []
        for window in self.windows():
            if pids and self.pid(window) in pids:
                found.append(window)
            elif class_name and class_name.lower() in self.window_class(window).lower():
                found.append(window)
        return found

    def minimise(self, window):
        self.x.XIconifyWindow(self.display, window, self.x.XDefaultScreen(self.display))
        self.x.XSync(self.display, 0)

    def activate(self, window):
        """Ask the window manager to show and focus the window, as a taskbar does (source indication 2)."""
        event = XEvent()
        event.client.type = CLIENT_MESSAGE
        event.client.send_event = 1
        event.client.display = self.display
        event.client.window = window
        event.client.message_type = self.atom('_NET_ACTIVE_WINDOW')
        event.client.format = 32
        event.client.data[0], event.client.data[1], event.client.data[2] = 2, 0, 0
        self.x.XSendEvent(self.display, self.root, 0, SUBSTRUCTURE_REDIRECT_MASK | SUBSTRUCTURE_NOTIFY_MASK,
                          ctypes.byref(event))
        self.x.XSync(self.display, 0)

    def state(self, window):
        """ICCCM WM_STATE: 1 normal, 3 minimised (iconic), None when not managed."""
        value = self._property(window, 'WM_STATE', 32)
        return int(value[0]) if value else None
