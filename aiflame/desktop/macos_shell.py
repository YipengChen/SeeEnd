"""Native macOS tray bridge, independent of Qt's Cocoa status-item delegate."""

from AppKit import (
    NSApplication, NSApplicationActivationPolicyRegular, NSImage,
    NSMenu, NSMenuItem, NSStatusBar, NSSquareStatusItemLength,
)
from Foundation import NSData, NSObject
from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QObject, QTimer
from PySide6.QtGui import QIcon


def activate_application():
    application = NSApplication.sharedApplication()
    application.setActivationPolicy_(NSApplicationActivationPolicyRegular)
    application.activateIgnoringOtherApps_(True)


class _MenuTarget(NSObject):
    def invoke_(self, sender):
        if self.owner is not None:
            self.owner._enqueue(sender)

    def menuWillOpen_(self, menu):
        if self.owner is not None and self.owner._menu is not None:
            self.owner._tracking = True
            self.owner._menu.aboutToShow.emit()

    def menuDidClose_(self, menu):
        if self.owner is not None:
            self.owner._tracking = False
            self.owner._schedule_dispatch()


class MacTray(QObject):
    """Subset of QSystemTrayIcon used by the desktop shell.

    Native callbacks queue QAction delivery onto Qt after menu tracking ends.
    The optional status_bar supports tests without creating a real menu-bar item.
    """

    def __init__(self, icon, parent=None, *, status_bar=None):
        super().__init__(parent)
        self._status_bar = status_bar or NSStatusBar.systemStatusBar()
        self._item = None
        self._menu = None
        self._native_menu = None
        self._target = _MenuTarget.alloc().init()
        self._target.owner = self
        self._bindings = []
        self._pending = []
        self._tracking = False
        self._closed = False
        self._icon = QIcon(icon)
        self._tooltip = ""
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._dispatch)

    @staticmethod
    def isSystemTrayAvailable():
        return True

    def setIcon(self, icon):
        self._icon = QIcon(icon)
        if self._item is not None:
            self._item.button().setImage_(self._native_icon())

    def _native_icon(self):
        # Encode a 2x representation but retain an 18-point logical size.
        pixmap = self._icon.pixmap(36, 36)
        data = QByteArray()
        buffer = QBuffer(data)
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        pixmap.save(buffer, "PNG")
        buffer.close()
        raw = bytes(data)
        image = NSImage.alloc().initWithData_(NSData.dataWithBytes_length_(raw, len(raw)))
        if image is not None:
            image.setSize_((18, 18))
            image.setTemplate_(self._icon.isMask())
        return image

    def setToolTip(self, text):
        self._tooltip = text
        if self._item is not None:
            self._item.button().setToolTip_(text)

    def _disconnect(self):
        for action, item, callback in self._bindings:
            try:
                action.changed.disconnect(callback)
            except (RuntimeError, TypeError):
                pass
            item.setTarget_(None)
        self._bindings.clear()
        if self._native_menu is not None:
            self._native_menu.setDelegate_(None)

    def setContextMenu(self, menu):
        self._disconnect()
        self._pending.clear()
        self._menu = menu
        native = NSMenu.alloc().initWithTitle_("")
        native.setAutoenablesItems_(False)
        native.setDelegate_(self._target)
        self._native_menu = native
        for action in menu.actions():
            if action.isSeparator():
                native.addItem_(NSMenuItem.separatorItem())
                continue
            item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                action.text(), "invoke:", ""
            )
            item.setTarget_(self._target)
            def sync(action=action, item=item):
                item.setTitle_(action.text())
                item.setEnabled_(action.isEnabled())
                item.setHidden_(not action.isVisible())
                item.setState_(int(action.isCheckable() and action.isChecked()))
            sync()
            action.changed.connect(sync)
            self._bindings.append((action, item, sync))
            native.addItem_(item)
        if self._item is not None:
            self._item.setMenu_(native)

    def _enqueue(self, sender):
        if self._closed:
            return
        for action, item, _ in self._bindings:
            if item == sender and action.isEnabled():
                self._pending.append(action)
                self._schedule_dispatch()
                break

    def _schedule_dispatch(self):
        if self._pending and not self._tracking and not self._closed:
            self._timer.start(0)

    def _dispatch(self):
        if self._tracking or self._closed:
            return
        pending, self._pending = self._pending, []
        for action in pending:
            if self._closed:
                break
            try:
                if action.isEnabled():
                    action.trigger()
            except RuntimeError:
                # QAction may have been deleted while the native menu closed.
                pass

    def show(self):
        if self._closed or self._item is not None:
            return
        self._item = self._status_bar.statusItemWithLength_(NSSquareStatusItemLength)
        self._item.setMenu_(self._native_menu)
        self.setIcon(self._icon)
        self.setToolTip(self._tooltip)

    def hide(self):
        if self._item is not None:
            self._item.setMenu_(None)
            self._status_bar.removeStatusItem_(self._item)
            self._item = None

    def shutdown(self):
        if self._closed:
            return
        self._closed = True
        self._timer.stop()
        self._pending.clear()
        self.hide()
        self._disconnect()
        self._menu = self._native_menu = None
        self._target.owner = None
