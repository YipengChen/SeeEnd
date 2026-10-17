"""Consume live sound requests independently from system notification permission."""

from PySide6.QtCore import QObject, QTimer
from .sounds import SoundPlayer
from .notifications import ClaimGuard


class SoundConsumer(QObject):
    def __init__(self, dashboard, player=None, parent=None):
        super().__init__(parent)
        self.dashboard = dashboard
        self.player = player if player is not None else SoundPlayer(self)
        self.closed = False
        self.pending = False
        self.active = None
        self.active_stamp = None
        self.guard = ClaimGuard(dashboard, "sound_notifications")
        self.guard.on_revoke = self.revoked
        self.enabled = self.allowed()
        self.player.failed.connect(dashboard.sound_failed)
        self.player.completed.connect(self.completed)
        dashboard.sound_preview_requested.connect(self.preview)
        dashboard.state_changed.connect(self.state_changed)
        dashboard.checks["sound_notifications"].toggled.connect(self.toggled)
        home_toggle = getattr(dashboard, "home_channel_checks", {}).get("sound_notifications")
        if home_toggle is not None:
            home_toggle.toggled.connect(self.toggled)
        self.deadline = QTimer(self)
        self.deadline.setSingleShot(True)
        self.deadline.setInterval(5000)
        self.deadline.timeout.connect(self.timed_out)
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.poll)
        self.timer.start()

    def allowed(self):
        home_toggle = getattr(self.dashboard, "home_channel_checks", {}).get("sound_notifications")
        return bool(self.dashboard.model and self.dashboard.model["settings"].get("sound_notifications")
                    and self.dashboard.checks["sound_notifications"].isChecked()
                    and (home_toggle is None or home_toggle.isChecked()))

    def state_changed(self, data):
        self.guard.sync(data)
        enabled = bool(data["settings"].get("sound_notifications"))
        if ((self.enabled and not enabled) or (self.active is not None
                and not self.guard.current(self.active, self.active_stamp))):
            self.cancel()
        self.enabled = enabled

    def toggled(self, enabled):
        if not enabled:
            self.cancel()

    def revoked(self, source):
        if self.active is not None and (source is None or self.active.get("source_id") == source):
            self.cancel()

    def cancel(self):
        self.player.stop()
        self.completed("unavailable")

    def preview(self, category):
        if not self.closed:
            self.cancel()
            self.player.play(category)

    def poll(self):
        if (self.closed or self.pending or self.active is not None or not self.dashboard.ready
                or self.dashboard.writing or not self.allowed() or self.player.is_playing()):
            return
        self.pending = True
        self.guard.sync()
        stamp = self.guard.epoch, dict(self.guard.sources)
        self.dashboard.api.request("/api/sounds/claim", lambda data: self.received(data, stamp), body={}, failed=self.failed)

    def failed(self):
        self.pending = False

    def received(self, data, epoch=None):
        self.pending = False
        if self.closed:
            return
        for message in data.get("sounds", []):
            self.active = message
            stamp = (epoch[0], epoch[1].get(message.get("source_id"), 0)) if epoch is not None else self.guard.stamp(message)
            self.active_stamp = stamp
            if not self.allowed() or not self.guard.current(message, stamp):
                self.completed("unavailable")
                continue
            def validated(result, item=message, stamp=stamp):
                # A late response cannot stop/acknowledge a replacement preview
                # or a newer task's playback.
                if self.active is not item:
                    return
                if result.get("valid") is not True or self.closed or not self.guard.current(item, stamp):
                    self.completed("unavailable")
                    return
                self.deadline.start()
                if not self.player.play(item["category"]):
                    self.completed("failed")
            def failed(item=message):
                if self.active is item:
                    self.completed("unavailable")
            self.dashboard.api.request("/api/sounds/validate", validated,
                body={key: message[key] for key in ("event_id", "claim", "generation")},
                failed=failed, error_callback=lambda text: None)

    def completed(self, status):
        message, self.active = self.active, None
        self.active_stamp = None
        self.deadline.stop()
        if message is not None and not self.closed:
            # A disabled/expired request may already have been removed by the Hub.
            self.dashboard.api.request("/api/sounds/ack", body={
                "event_id": message["event_id"], "claim": message["claim"], "status": status},
                error_callback=lambda text: None)

    def timed_out(self):
        self.player.stop()
        self.dashboard.sound_failed("音效播放超时，请检查系统声音输出后重试。")
        self.completed("failed")

    def shutdown(self):
        self.closed = True
        self.timer.stop()
        self.deadline.stop()
        self.active = None
        self.player.shutdown()
