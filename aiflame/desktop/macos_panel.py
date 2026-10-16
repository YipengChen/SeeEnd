"""Create Qt's desktop panel with native activation protection from birth."""

from contextvars import ContextVar

import objc
from AppKit import NSPanel, NSWindowStyleMaskNonactivatingPanel


_creating_desktop_panel = ContextVar("creating_desktop_panel", default=False)
_installed = False


def _install_initializer():
    global _installed
    if _installed:
        return
    qt_panel = objc.lookUpClass("QNSPanel")
    selector = b"initWithContentRect:styleMask:backing:defer:screen:"
    original = NSPanel.instanceMethodForSelector_(selector)

    def initialize(panel, rect, style, backing, defer, screen):
        if _creating_desktop_panel.get() and panel.isKindOfClass_(qt_panel):
            style |= NSWindowStyleMaskNonactivatingPanel
        return original(panel, rect, style, backing, defer, screen)

    # Qt calls this public NSPanel initializer from its QNSPanel initializer.
    # Scope the extra flag to this widget's synchronous native creation; menus,
    # dialogs, and ordinary tool windows retain their original initialization.
    objc.classAddMethods(NSPanel, [objc.selector(
        initialize, selector=selector, signature=original.signature,
    )])
    _installed = True


def create_nonactivating_panel(widget):
    _install_initializer()
    token = _creating_desktop_panel.set(True)
    try:
        view = objc.objc_object(c_void_p=int(widget.winId()))
    finally:
        _creating_desktop_panel.reset(token)
    view.window().setBecomesKeyOnlyIfNeeded_(True)
