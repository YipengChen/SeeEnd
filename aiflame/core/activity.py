"""Read system input idle time without capturing keys or pointer contents."""
import ctypes
import ctypes.util
import sys
import threading
import time


def idle_reader():
    if sys.platform == "darwin":
        library = ctypes.CDLL("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
        read = library.CGEventSourceSecondsSinceLastEventType
        read.argtypes = [ctypes.c_int, ctypes.c_uint32]
        read.restype = ctypes.c_double
        return lambda: read(0, 0xffffffff)
    if sys.platform == "win32":
        class LastInput(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint32)]
        def read():
            value = LastInput()
            value.cbSize = ctypes.sizeof(value)
            if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(value)):
                return None
            return ((ctypes.windll.kernel32.GetTickCount() - value.dwTime) & 0xffffffff) / 1000
        return read
    # X11 desktops expose idle time through XScreenSaver; Wayland may not.
    x11 = ctypes.CDLL(ctypes.util.find_library("X11"))
    xss = ctypes.CDLL(ctypes.util.find_library("Xss"))
    class Info(ctypes.Structure):
        _fields_ = [("window", ctypes.c_ulong), ("state", ctypes.c_int),
                    ("kind", ctypes.c_int), ("since", ctypes.c_ulong),
                    ("idle", ctypes.c_ulong), ("mask", ctypes.c_ulong)]
    x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
    x11.XOpenDisplay.restype = ctypes.c_void_p
    display = x11.XOpenDisplay(None)
    if not display:
        raise OSError("System input idle time unavailable")
    x11.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
    x11.XDefaultRootWindow.restype = ctypes.c_ulong
    xss.XScreenSaverQueryInfo.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(Info)]
    root = x11.XDefaultRootWindow(display)
    def read():
        info = Info()
        return info.idle / 1000 if xss.XScreenSaverQueryInfo(display, root, ctypes.byref(info)) else None
    return read


class ActivityMonitor(threading.Thread):
    def __init__(self, hub):
        super().__init__(daemon=True)
        self.hub = hub

    def run(self):
        try:
            read = idle_reader()
        except (OSError, AttributeError):
            return
        while not self.hub.stopping.wait(0.25):
            try:
                sampled_at = time.monotonic()
                idle = read()
                if idle is not None and idle >= 0:
                    occurred_at = sampled_at - idle - 0.05
                    with self.hub.lock:
                        self.hub.engine.human_activity(occurred_at)
            except (OSError, ValueError):
                continue
