"""Read macOS control metadata and interruption signals, without AXValue text.

No UI actions, client injection, debugger ports or conversation storage. The
listen-only event tap stores only interrupted PIDs, never keys or input text.
"""

import ctypes as C
from contextlib import contextmanager
from pathlib import Path
import plistlib
import threading
import time

from .providers import (
    COPY,
    ERROR,
    FINAL,
    SEND,
    STOP,
    CHATGPT_COMPOSERS,
    CHATGPT_SEND,
    CHATGPT_STOP,
    CHATGPT_FINAL,
)
from .state import Frame

P = C.c_void_p
LONG = C.c_long


class Unavailable(RuntimeError):
    pass


class Point(C.Structure):
    _fields_ = [("x", C.c_double), ("y", C.c_double)]


class Size(C.Structure):
    _fields_ = [("width", C.c_double), ("height", C.c_double)]


def bind(library, name, result, *args):
    function = getattr(library, name)
    function.restype, function.argtypes = result, args
    return function


class MacReader:
    def __init__(self):
        self.cf = C.CDLL(
            "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
        )
        self.ax = C.CDLL(
            "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices"
        )
        self.cg = C.CDLL(
            "/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics"
        )
        self.appkit = C.CDLL("/System/Library/Frameworks/AppKit.framework/AppKit")
        self.objc = C.CDLL("/usr/lib/libobjc.A.dylib")
        self.release = bind(self.cf, "CFRelease", None, P)
        self.hash = bind(self.cf, "CFHash", C.c_ulong, P)
        self.string_create = bind(
            self.cf, "CFStringCreateWithCString", P, P, C.c_char_p, C.c_uint
        )
        self.string_get = bind(
            self.cf, "CFStringGetCString", C.c_bool, P, P, LONG, C.c_uint
        )
        self.type_id = bind(self.cf, "CFGetTypeID", C.c_ulong, P)
        self.string_type = bind(self.cf, "CFStringGetTypeID", C.c_ulong)
        self.boolean_get = bind(self.cf, "CFBooleanGetValue", C.c_bool, P)
        self.boolean_type = bind(self.cf, "CFBooleanGetTypeID", C.c_ulong)
        self.array_count = bind(self.cf, "CFArrayGetCount", LONG, P)
        self.array_item = bind(self.cf, "CFArrayGetValueAtIndex", P, P, LONG)
        self.copy_attr = bind(
            self.ax, "AXUIElementCopyAttributeValue", C.c_int, P, P, C.POINTER(P)
        )
        self.create_app = bind(self.ax, "AXUIElementCreateApplication", P, C.c_int)
        self.timeout = bind(
            self.ax, "AXUIElementSetMessagingTimeout", C.c_int, P, C.c_float
        )
        self.trust = bind(self.ax, "AXIsProcessTrustedWithOptions", C.c_bool, P)
        self.listen_allowed = bind(self.cg, "CGPreflightListenEventAccess", C.c_bool)
        self.listen_request = bind(self.cg, "CGRequestListenEventAccess", C.c_bool)
        self.value_get = bind(self.ax, "AXValueGetValue", C.c_bool, P, C.c_int, P)
        self.get_class = bind(self.objc, "objc_getClass", P, C.c_char_p)
        self.selector = bind(self.objc, "sel_registerName", P, C.c_char_p)
        self.attrs = {}
        self.tap = None
        self.tap_loop = None
        self.tap_thread = None
        self.tap_ready = False
        self.interrupted = set()
        self.guard = threading.Lock()
        self.bounds = {}
        self.tap_stopping = threading.Event()

    def message(self, obj, selector, result=P, argument=None):
        args = (P, P) if argument is None else (P, P, C.c_ulong)
        function = C.CFUNCTYPE(result, *args)(("objc_msgSend", self.objc))
        values = (obj, self.selector(selector.encode()))
        return function(*values) if argument is None else function(*values, argument)

    @contextmanager
    def pool(self):
        pool = self.message(
            self.message(self.get_class(b"NSAutoreleasePool"), "alloc"), "init"
        )
        try:
            yield
        finally:
            self.message(pool, "drain", None)

    def workspace(self):
        return self.message(self.get_class(b"NSWorkspace"), "sharedWorkspace")

    def front_pid(self):
        with self.pool():
            app = self.message(self.workspace(), "frontmostApplication")
            return self.message(app, "processIdentifier", C.c_int) if app else 0

    def running(self):
        result = {}
        with self.pool():
            apps = self.message(self.workspace(), "runningApplications")
            for i in range(self.message(apps, "count", C.c_ulong)):
                app = self.message(apps, "objectAtIndex:", argument=i)
                bundle = self.message(app, "bundleIdentifier")
                if bundle:
                    identifier = self.message(bundle, "UTF8String", C.c_char_p)
                    if identifier:
                        result[identifier.decode()] = self.message(
                            app, "processIdentifier", C.c_int
                        )
        return result

    def installed(self, client):
        result = {}
        for root in (Path("/Applications"), Path.home() / "Applications"):
            if not root.exists():
                continue
            for app in root.glob("*.app"):
                try:
                    info = plistlib.loads((app / "Contents/Info.plist").read_bytes())
                    identifier = info.get("CFBundleIdentifier")
                    if identifier and (
                        identifier in client.bundle_ids or app.stem in client.app_names
                    ):
                        result[identifier] = str(
                            info.get("CFBundleShortVersionString", "未知")
                        )[:40]
                except (OSError, ValueError, plistlib.InvalidFileException):
                    continue
        return result

    def permissions(self):
        return {
            "accessibility": bool(self.trust(None)),
            "input_monitoring": bool(self.listen_allowed()),
        }

    def request_permissions(self):
        # Explicit permission button or enabling ChatGPT from the dashboard.
        permissions = self.permissions()
        if not permissions["accessibility"]:
            create = bind(self.cf, "CFDictionaryCreate", P, P, P, P, LONG, P, P)
            key = P.in_dll(self.ax, "kAXTrustedCheckOptionPrompt")
            yes = P.in_dll(self.cf, "kCFBooleanTrue")
            options = create(None, C.pointer(key), C.pointer(yes), 1, None, None)
            try:
                self.trust(options)
            finally:
                self.release(options)
        if not permissions["input_monitoring"]:
            self.listen_request()

    @contextmanager
    def attribute(self, element, name):
        if name not in self.attrs:
            self.attrs[name] = self.string_create(None, name.encode(), 0x08000100)
        result = P()
        error = self.copy_attr(element, self.attrs[name], C.byref(result))
        if error not in (0, -25205, -25212):  # unsupported attribute / no value
            if result.value:
                self.release(result)
            raise Unavailable("控件读取失败")
        try:
            yield result.value
        finally:
            if result.value:
                self.release(result)

    def text(self, value):
        if not value or self.type_id(value) != self.string_type():
            return ""
        buffer = C.create_string_buffer(256)
        return (
            buffer.value.decode("utf-8")
            if self.string_get(value, buffer, 256, 0x08000100)
            else ""
        )

    def label(self, element, name):
        with self.attribute(element, name) as value:
            return self.text(value)

    def flag(self, element, name, default):
        with self.attribute(element, name) as value:
            return (
                bool(self.boolean_get(value))
                if value and self.type_id(value) == self.boolean_type()
                else default
            )

    def window_bounds(self, window):
        point, size = Point(), Size()
        with self.attribute(window, "AXPosition") as value:
            if not value or not self.value_get(value, 1, C.byref(point)):
                return None
        with self.attribute(window, "AXSize") as value:
            if not value or not self.value_get(value, 2, C.byref(size)):
                return None
        return (point.x, point.y, size.width, size.height)

    def scan(self, pid, *, chatgpt=False):
        app = self.create_app(pid)
        self.timeout(app, 0.15)
        frames, rectangles = [], []
        deadline = time.monotonic() + 2
        visited = set()
        try:
            with self.attribute(app, "AXWindows") as windows:
                for i in range(self.array_count(windows) if windows else 0):
                    if time.monotonic() >= deadline:
                        raise Unavailable("窗口读取超时")
                    window = self.array_item(windows, i)
                    self.timeout(window, 0.15)
                    if self.flag(window, "AXMinimized", False):
                        continue
                    rect = self.window_bounds(window)
                    if not rect:
                        continue
                    composers, buttons = [], []
                    chat_composers = set()

                    def visit(element):
                        self.timeout(element, 0.15)
                        identity = int(self.hash(element))
                        if identity in visited:
                            return
                        if len(visited) >= 5000 or time.monotonic() >= deadline:
                            raise Unavailable("控件树过大或读取超时")
                        visited.add(identity)
                        if self.flag(element, "AXHidden", False):
                            return
                        role = self.label(element, "AXRole")
                        if role in ("AXTextArea", "AXTextField"):
                            # Never query editor values or descendants.
                            composers.append(identity)
                            if chatgpt:
                                # Read only the static accessible label/placeholder.
                                # In this client it identifies Chat, Work or Codex.
                                labels = {
                                    self.label(element, attr).strip()
                                    for attr in ("AXDescription", "AXPlaceholderValue")
                                }
                                labels.discard("")
                                if labels and labels <= CHATGPT_COMPOSERS:
                                    chat_composers.add(identity)
                            return
                        if role == "AXStaticText":
                            return
                        if role == "AXButton":
                            labels = {
                                self.label(element, attr).strip()
                                for attr in ("AXTitle", "AXDescription")
                            }
                            buttons.append(
                                (
                                    identity,
                                    labels,
                                    self.flag(element, "AXEnabled", True),
                                )
                            )
                            return
                        with self.attribute(element, "AXChildren") as children:
                            for index in range(
                                self.array_count(children) if children else 0
                            ):
                                visit(self.array_item(children, index))

                    visit(window)
                    if len(composers) != 1 or (
                        chatgpt and composers[0] not in chat_composers
                    ):
                        continue

                    def matching(labels, include_disabled=False):
                        return frozenset(
                            identity
                            for identity, names, enabled in buttons
                            if names & labels and (enabled or include_disabled)
                        )

                    send = bool(matching(CHATGPT_SEND if chatgpt else SEND, True))
                    generating = bool(matching(CHATGPT_STOP if chatgpt else STOP))
                    if chatgpt:
                        # Chat's submit button changes from Send to Stop. Ignore
                        # unrelated Stop controls while the Send button is present.
                        generating = generating and not send
                    frames.append(
                        Frame(
                            (pid, int(self.hash(window)), composers[0]),
                            generating,
                            send,
                            matching(COPY),
                            matching(CHATGPT_FINAL if chatgpt else FINAL),
                            matching(ERROR),
                        )
                    )
                    rectangles.append(rect)
            with self.guard:
                self.bounds[pid] = rectangles
            # Multiwindow conversations cannot be safely attributed.
            return frames[0] if len(frames) == 1 else None
        finally:
            self.release(app)

    def start_tap(self):
        if self.tap_thread and self.tap_thread.is_alive():
            return
        self.tap_stopping.clear()
        self.tap_thread = threading.Thread(target=self._tap_loop, daemon=True)
        self.tap_thread.start()

    def _tap_loop(self):
        callback_type = C.CFUNCTYPE(P, P, C.c_uint, P, P)
        location = bind(self.cg, "CGEventGetLocation", Point, P)
        create = bind(
            self.cg,
            "CGEventTapCreate",
            P,
            C.c_uint,
            C.c_uint,
            C.c_uint,
            C.c_ulonglong,
            callback_type,
            P,
        )
        source_create = bind(self.cf, "CFMachPortCreateRunLoopSource", P, P, P, LONG)
        current = bind(self.cf, "CFRunLoopGetCurrent", P)
        add = bind(self.cf, "CFRunLoopAddSource", None, P, P, P)
        run = bind(self.cf, "CFRunLoopRun", None)
        enabled = bind(self.cg, "CGEventTapIsEnabled", C.c_bool, P)
        invalidate = bind(self.cf, "CFMachPortInvalidate", None, P)

        def callback(proxy, kind, event, context):
            try:
                if kind in (0xFFFFFFFE, 0xFFFFFFFF):
                    self.tap_ready = False
                    return event
                pid = self.front_pid()
                with self.guard:
                    if pid in self.bounds:
                        point = location(event) if kind != 10 else None
                        if point is None or any(
                            x <= point.x <= x + w and y <= point.y <= y + h
                            for x, y, w, h in self.bounds[pid]
                        ):
                            self.interrupted.add(pid)
            except Exception:
                self.tap_ready = False
            return event

        self.callback = callback_type(callback)
        mask = sum(1 << kind for kind in (1, 3, 10, 22, 25))
        tap = create(1, 0, 1, mask, self.callback, None)
        if not tap:
            self.tap_ready = False
            return
        source = source_create(None, tap, 0)
        if not source:
            self.release(tap)
            return
        self.tap, self.tap_loop = tap, current()
        mode = P.in_dll(self.cf, "kCFRunLoopCommonModes")
        add(self.tap_loop, source, mode)
        self.tap_ready = bool(enabled(tap))
        try:
            if not self.tap_stopping.is_set():
                run()
        finally:
            self.tap_ready = False
            invalidate(tap)
            self.release(source)
            self.release(tap)
            self.tap = self.tap_loop = None

    def take_interruptions(self):
        with self.guard:
            result, self.interrupted = self.interrupted, set()
            return result

    def stop_tap(self):
        self.tap_stopping.set()
        if self.tap_loop:
            bind(self.cf, "CFRunLoopStop", None, P)(self.tap_loop)
        if self.tap_thread:
            self.tap_thread.join(timeout=3)
        with self.guard:
            self.bounds.clear()
            self.interrupted.clear()

    def close(self):
        self.stop_tap()
        for value in self.attrs.values():
            self.release(value)
        self.attrs.clear()
