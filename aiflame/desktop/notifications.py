"""System notifications on the GUI thread, with explicit submission receipts."""
import sys
from PySide6.QtCore import QObject, QTimer, Signal, Qt
from PySide6.QtWidgets import QSystemTrayIcon
from .localization import t


class SystemNotifications:
    def __init__(self, tray):
        self.tray = tray
        self.center = None
        self.delegate = None
        if sys.platform == "darwin":
            try:
                import Foundation
                import objc
                import UserNotifications as UN
                # The notification center requires a real application identity.
                if Foundation.NSBundle.mainBundle().bundleIdentifier() != "com.aiflame.desktop" or not str(Foundation.NSBundle.mainBundle().bundlePath()).endswith(".app"):
                    return
                class FlameNotificationDelegate(Foundation.NSObject, protocols=[objc.protocolNamed("UNUserNotificationCenterDelegate")]):
                    def userNotificationCenter_willPresentNotification_withCompletionHandler_(self, center, notification, completion):
                        completion(UN.UNNotificationPresentationOptionBanner | UN.UNNotificationPresentationOptionList)
                self.delegate = FlameNotificationDelegate.alloc().init()
                self.center = UN.UNUserNotificationCenter.currentNotificationCenter()
                self.center.setDelegate_(self.delegate)
            except (ImportError, RuntimeError):
                self.center = None

    def submit(self, message, completed):
        if sys.platform == "darwin":
            if self.center is None:
                completed("unavailable")
                return
            import UserNotifications as UN
            def authorized(granted, error):
                if error or not granted:
                    completed("failed" if error else "denied")
                    return
                content = UN.UNMutableNotificationContent.alloc().init()
                content.setTitle_(message["title"])
                content.setBody_(message["body"])
                request = UN.UNNotificationRequest.requestWithIdentifier_content_trigger_(message["event_id"], content, None)
                self.center.addNotificationRequest_withCompletionHandler_(request, lambda error: completed("failed" if error else "submitted"))
            self.center.requestAuthorizationWithOptions_completionHandler_(UN.UNAuthorizationOptionAlert, authorized)
        elif self.tray.isSystemTrayAvailable() and self.tray.supportsMessages():
            self.tray.showMessage(message["title"], message["body"], QSystemTrayIcon.MessageIcon.Information, 10000)
            completed("submitted")
        else:
            completed("unavailable")


class NotificationConsumer(QObject):
    receipt = Signal(object)

    def __init__(self, dashboard, backend, parent=None):
        super().__init__(parent)
        self.dashboard, self.backend = dashboard, backend
        self.closed = False
        self.pending = False
        self.receipt.connect(self.acknowledge, Qt.ConnectionType.QueuedConnection)
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.poll)
        self.timer.start()

    def poll(self):
        if self.closed or self.pending or not self.dashboard.ready:
            return
        self.pending = True
        self.dashboard.api.request("/api/notifications/claim", self.received, body={}, failed=self.failed)

    def failed(self):
        self.pending = False

    def received(self, data):
        self.pending = False
        if self.closed:
            return
        for message in data["notifications"]:
            def completed(status, item=message):
                self.receipt.emit({"event_id": item["event_id"], "claim": item["claim"], "status": status})
            try:
                localized = {**message, **{key: t(message[key]) for key in ("title", "body") if key in message}}
                self.backend.submit(localized, completed)
            except Exception:
                completed("failed")

    def acknowledge(self, receipt):
        if not self.closed:
            self.dashboard.api.request("/api/notifications/ack", body=receipt)

    def shutdown(self):
        self.closed = True
        self.timer.stop()
