"""System notifications on the GUI thread, with explicit submission receipts."""
import sys
import time
from PySide6.QtCore import QObject, QTimer, Signal, Qt
from PySide6.QtWidgets import QSystemTrayIcon
from .localization import t


class SystemNotifications(QObject):
    activated = Signal(str)

    def __init__(self, tray):
        super().__init__()
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
                    def userNotificationCenter_didReceiveNotificationResponse_withCompletionHandler_(self, center, response, completion):
                        backend.activated.emit(str(response.notification().request().identifier()))
                        completion()
                backend = self
                self.delegate = FlameNotificationDelegate.alloc().init()
                self.center = UN.UNUserNotificationCenter.currentNotificationCenter()
                self.center.setDelegate_(self.delegate)
            except (ImportError, RuntimeError):
                self.center = None

    def status(self, completed):
        """Read authorization without prompting or posting a notification."""
        if sys.platform == "darwin":
            if self.center is None:
                completed("unavailable")
                return
            try:
                def received(settings):
                    code = int(settings.authorizationStatus())
                    # Provisional/ephemeral authorization can also submit alerts.
                    completed({0: "not_determined", 1: "denied", 2: "authorized",
                               3: "authorized", 4: "authorized"}.get(code, "unknown"))
                self.center.getNotificationSettingsWithCompletionHandler_(received)
            except Exception:
                completed("unknown")
        else:
            completed("available" if self.tray.isSystemTrayAvailable() and self.tray.supportsMessages()
                      else "unavailable")

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
                def submitted(error):
                    # Native void callbacks must not return Signal.emit's bool.
                    completed("failed" if error else "submitted")
                self.center.addNotificationRequest_withCompletionHandler_(request, submitted)
            self.center.requestAuthorizationWithOptions_completionHandler_(UN.UNAuthorizationOptionAlert, authorized)
        elif self.tray.isSystemTrayAvailable() and self.tray.supportsMessages():
            self.tray.showMessage(message["title"], message["body"], QSystemTrayIcon.MessageIcon.Information, 10000)
            completed("submitted")
        else:
            completed("unavailable")

    def authorize(self, completed):
        """Request permission separately from posting a test notification."""
        if sys.platform != "darwin":
            self.status(completed)
            return
        if self.center is None:
            completed("unavailable")
            return
        try:
            import UserNotifications as UN
            def received(granted, error):
                completed("unknown" if error else "authorized" if granted else "denied")
            self.center.requestAuthorizationWithOptions_completionHandler_(UN.UNAuthorizationOptionAlert, received)
        except Exception:
            completed("unknown")


class NotificationConsumer(QObject):
    receipt = Signal(object)
    permission_status = Signal(str)
    test_result = Signal(str)
    enable_result = Signal(int, str)
    enable_phase = Signal(int, bool)

    def __init__(self, dashboard, backend, parent=None):
        super().__init__(parent)
        self.dashboard, self.backend = dashboard, backend
        self.closed = False
        self.pending = False
        self.last_permission_check = time.monotonic()
        self.permission_generation = 0
        self.authorizing = False
        self.receipt.connect(self.acknowledge, Qt.ConnectionType.QueuedConnection)
        if hasattr(backend, "activated"):
            backend.activated.connect(self.return_task, Qt.ConnectionType.QueuedConnection)
        if hasattr(dashboard, "notification_status_requested"):
            dashboard.notification_status_requested.connect(self.check_status)
            dashboard.notification_test_requested.connect(self.test_notification)
            dashboard.notification_enable_requested.connect(self.check_enable_status)
            self.enable_result.connect(dashboard.notification_enable_verified, Qt.ConnectionType.QueuedConnection)
            self.enable_phase.connect(dashboard.notification_enable_phase, Qt.ConnectionType.QueuedConnection)
            self.permission_status.connect(dashboard.update_notification_status, Qt.ConnectionType.QueuedConnection)
            self.test_result.connect(dashboard.notification_test_finished, Qt.ConnectionType.QueuedConnection)
            self.check_status()
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.poll)
        self.timer.start()

    def check_status(self):
        if not self.closed and not self.authorizing and getattr(self.dashboard, "notification_enable_pending", None) is None:
            self.last_permission_check = time.monotonic()
            self.permission_generation += 1
            generation = self.permission_generation
            def received(status):
                if not self.closed and generation == self.permission_generation:
                    self.permission_status.emit(status)
            try:
                self.backend.status(received)
            except Exception:
                received("unknown")

    def check_enable_status(self, request):
        """Verify a user enable request with fresh OS settings, never cached state."""
        self.permission_generation += 1

        def current():
            return not self.closed and getattr(self.dashboard, "notification_enable_pending", None) == request

        def finished(status):
            if current():
                self.last_permission_check = time.monotonic()
                self.enable_result.emit(request, status)

        def read(*, after_authorization=False):
            def received(status):
                if not current():
                    return
                if status == "not_determined" and not after_authorization:
                    self.authorizing = True
                    self.enable_phase.emit(request, True)

                    def authorized(status):
                        self.authorizing = False
                        if not current():
                            return
                        if status in ("authorized", "available"):
                            self.enable_phase.emit(request, False)
                            read(after_authorization=True)
                        else:
                            finished(status)
                    try:
                        self.backend.authorize(authorized)
                    except Exception:
                        authorized("unknown")
                else:
                    finished(status)
            try:
                self.backend.status(received)
            except Exception:
                received("unknown")

        if current():
            read()

    def authorize_notifications(self):
        if not self.closed and not self.authorizing:
            self.authorizing = True
            self.permission_generation += 1
            def received(status):
                self.authorizing = False
                self.last_permission_check = time.monotonic()
                if not self.closed:
                    self.permission_status.emit(status)
            try:
                self.backend.authorize(received)
            except Exception:
                received("unknown")

    def test_notification(self):
        if not self.closed:
            try:
                self.backend.submit({"event_id": "seeend-notification-test", "title": t("SeeEnd · 测试通知"),
                                     "body": t("你已收到 SeeEnd 测试通知。新任务的提醒也会显示在这里。")},
                                    self.test_result.emit)
            except Exception:
                self.test_result.emit("failed")

    def poll(self):
        if not self.closed and hasattr(self.dashboard, "notification_status") and time.monotonic() - self.last_permission_check >= 15:
            self.check_status()
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
            permission = getattr(self.dashboard, "notification_status", None)
            if permission is not None and permission not in ("authorized", "available"):
                completed("denied" if permission in ("denied", "not_determined") else "unavailable")
                continue
            try:
                localized = {**message, **{key: t(message[key]) for key in ("title", "body") if key in message}}
                self.backend.submit(localized, completed)
            except Exception:
                completed("failed")

    def acknowledge(self, receipt):
        if not self.closed:
            self.dashboard.api.request("/api/notifications/ack", body=receipt)

    def return_task(self, event_id):
        if not self.closed and event_id != "seeend-notification-test" and hasattr(self.dashboard, "return_task"):
            self.dashboard.return_task(event_id)

    def shutdown(self):
        self.closed = True
        self.timer.stop()
