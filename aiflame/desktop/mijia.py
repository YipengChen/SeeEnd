"""Login first, then match LAN scan identities to account-owned plugs."""
import base64
import time

from PySide6.QtCore import Qt, QSignalBlocker, QTimer
from PySide6.QtGui import QPixmap

from aiflame.devices.models import MIJIA_PLUG_MODEL, MIJIA_PLUG_MODELS, hardware_model
from aiflame.devices.mijia import lan_host
from .components import restyle
from .localization import source_text


class MijiaConnection:
    def mijia_account_rows(self):
        return [row for row in (self.mijia_login_state or {}).get("devices", [])
                if row.get("hardware_model", MIJIA_PLUG_MODEL) == self.selected_device_model]

    def mijia_session_valid(self):
        session = self.mijia_login_state or {}
        return (session.get("state") == "selecting"
                and session.get("expires_at", time.time() + 1) > time.time())

    def mijia_scan_identity(self):
        return (self.device_view_epoch, self.mijia_login_epoch,
                (self.mijia_login_state or {}).get("id"), self.selected_device_model)

    def mijia_scan_current(self, context):
        return (context == self.mijia_scan_identity() and self.mijia_session_valid()
                and self.tabs.currentIndex() == 1 and not self.device_details.isHidden())

    def clear_mijia_scan(self):
        self.mijia_discovered = []
        self.mijia_scan_pending = self.mijia_scan_complete = False
        self.mijia_scan_job_id = self.mijia_scan_context = None
        self.mijia_auto_scan_session = None
        self.mijia_scan_error = ""
        self.rendered_mijia_matches = None
        with QSignalBlocker(self.mijia_choices):
            self.mijia_choices.clear()
            self.mijia_choices.addItem("请选择要连接的插座…", None)

    def set_mijia_step(self, index):
        self.mijia_step = index
        for position, panel in enumerate(self.mijia_steps):
            panel.setVisible(position == index)
            restyle(self.mijia_step_labels[position], "role", "title" if position == index else "muted")

    def scan_mijia_devices(self):
        if (not self.mijia_session_valid() or not self.mijia_account_rows()
                or not self.ready or not self.device_reminders_enabled()
                or self.device_selection_loading or self.reset_pending or self.job_busy
                or self.model.get("maintenance")
                or self.writing or self.mijia_scan_pending):
            return
        context = self.mijia_scan_identity()
        self.clear_mijia_scan()
        self.mijia_auto_scan_session = context
        self.mijia_scan_context = context
        self.mijia_scan_pending = True
        self.update_busy()

        def received(job):
            if not self.mijia_scan_current(context):
                return
            self.mijia_scan_job_id = job["id"]
            self.device_operation_id = job["id"]
            self.rendered_yeelight_discovery = None
            self.render_device({"job": job})

        def failed():
            if self.mijia_scan_current(context):
                self.mijia_discovery_failed(source_text(self.message))

        self.act("/api/device", {"action": "discover", "hardware_model": self.selected_device_model,
                                "device_id": ""}, received, failed)

    def render_mijia_discovery(self, rows, job_id=None):
        if not self.mijia_session_valid():
            return
        if job_id is not None and (job_id != self.mijia_scan_job_id
                                   or not self.mijia_scan_current(self.mijia_scan_context)):
            return
        self.mijia_discovered = rows
        self.mijia_scan_pending = False
        self.mijia_scan_complete = True
        self.mijia_scan_error = ""
        self.rebuild_mijia_matches()
        self.update_busy()

    def mijia_discovery_failed(self, message, job_id=None):
        if job_id is not None and (job_id != self.mijia_scan_job_id
                                   or not self.mijia_scan_current(self.mijia_scan_context)):
            return
        self.mijia_scan_pending = self.mijia_scan_complete = False
        self.mijia_discovered = []
        self.mijia_scan_error = message
        self.rebuild_mijia_matches()
        self.update_busy()

    def rebuild_mijia_matches(self):
        accounts = {str(row["did"]): row for row in self.mijia_account_rows()}
        matched = {}
        if self.mijia_session_valid():
            for scanned in self.mijia_discovered:
                did = str(scanned.get("did", ""))
                if did not in accounts or not did.isascii() or not did.isdigit() or len(did) > 10 or not 0 < int(did) < 0xffffffff:
                    continue
                try:
                    host = lan_host(scanned.get("host", ""))
                except ValueError:
                    continue
                matched[did] = {"did": did, "host": host, "name": accounts[did]["name"],
                                "hardware_model": self.selected_device_model}
        signature = tuple((row["did"], row["host"], row["name"], row["hardware_model"])
                          for row in matched.values())
        if signature == self.rendered_mijia_matches:
            return
        self.rendered_mijia_matches = signature
        previous = self.mijia_choices.currentData() or {}
        with QSignalBlocker(self.mijia_choices):
            self.mijia_choices.clear()
            self.mijia_choices.addItem("请选择要连接的插座…", None)
            for row in matched.values():
                self.mijia_choices.addItem(row["name"] + " · " + hardware_model(row["hardware_model"])["name"]
                                          + " · IP " + row["host"], row)
                if row["did"] == previous.get("did") or len(matched) == 1:
                    self.mijia_choices.setCurrentIndex(self.mijia_choices.count() - 1)

    def mijia_target(self):
        row = self.mijia_choices.currentData()
        if not row:
            return "", "", "请先扫描并选择匹配到的插座。"
        return row["host"], row["did"], ""

    def start_mijia_login(self):
        epoch = self.device_view_epoch
        self.mijia_login_epoch += 1
        login_epoch = self.mijia_login_epoch
        if self.mijia_login_state and self.mijia_login_state.get("id"):
            self.mijia_ignored_sessions.add(self.mijia_login_state["id"])
        self.clear_mijia_scan()
        self.mijia_login_state = {"id": "", "state": "starting"}
        self.rendered_mijia_login = None
        self.mijia_qr.clear()
        self.mijia_qr.hide()
        self.mijia_login_status.setText("正在获取小米登录二维码")
        self.set_mijia_step(0)

        def received(data):
            if (epoch != self.device_view_epoch or login_epoch != self.mijia_login_epoch
                    or self.tabs.currentIndex() != 1 or self.selected_device_model not in MIJIA_PLUG_MODELS):
                self.mijia_ignored_sessions.add(data["id"])
                self.act("/api/device/mijia", {"action": "cancel", "id": data["id"]})
                return
            self.render_mijia_login(data)

        def failed():
            if login_epoch == self.mijia_login_epoch:
                self.mijia_login_state = None
                self.mijia_login_status.setText(source_text(self.message))
                self.update_busy()

        self.act("/api/device/mijia", {"action": "login", "region": "cn"}, received, failed)

    def cancel_mijia_login(self):
        if not hasattr(self, "mijia_qr"):
            return
        self.mijia_login_epoch += 1
        session = self.mijia_login_state or {}
        if session.get("id"):
            self.mijia_ignored_sessions.add(session["id"])
            if session.get("state") in ("starting", "waiting", "authorizing", "loading", "selecting"):
                self.act("/api/device/mijia", {"action": "cancel", "id": session["id"]})
        self.mijia_qr.clear()
        self.mijia_qr.hide()
        self.mijia_login_state = None
        self.rendered_mijia_login = None
        self.mijia_login_status.clear()
        self.clear_mijia_scan()
        self.set_mijia_step(0)

    def reset_mijia_draft(self):
        self.cancel_mijia_login()

    def return_mijia_target(self):
        self.cancel_mijia_login()
        self.update_busy()

    def retry_mijia_login(self):
        self.cancel_mijia_login()
        self.start_mijia_login()

    def choose_mijia_plug(self, _index):
        self.update_busy()

    def mijia_can_import(self):
        if not self.mijia_session_valid() or self.mijia_scan_pending:
            return False
        row = self.mijia_choices.currentData()
        return bool(row and row["hardware_model"] == self.selected_device_model
                    and any(str(account["did"]) == row["did"] for account in self.mijia_account_rows())
                    and any(str(scanned["did"]) == row["did"] and scanned["host"] == row["host"]
                            for scanned in self.mijia_discovered))

    def import_mijia_plug(self):
        if not self.mijia_can_import():
            return
        row, session = self.mijia_choices.currentData(), self.mijia_login_state

        def received(job):
            self.device_operation_id = job.get("id")
            self.selected_device_id = ""
            self.render_device({"job": job})

        self.act("/api/device/mijia", {"action": "import", "id": session["id"], "did": row["did"],
                                     "host": row["host"]}, received)

    def render_mijia_login(self, session):
        if (self.selected_device_model not in MIJIA_PLUG_MODELS or not session or self.tabs.currentIndex() != 1
                or self.device_details.isHidden() or session["id"] in self.mijia_ignored_sessions
                or session.get("state") == "cancelled"):
            return
        if session["id"] != (self.mijia_login_state or {}).get("id"):
            self.clear_mijia_scan()
        self.mijia_login_state = session
        state = session.get("state")
        self.set_mijia_step(0 if state in ("starting", "waiting", "authorizing", "loading") else 1)
        self.mijia_login_status.setText(session.get("message", ""))
        signature = session["id"], state, session.get("qr_image", "")
        if signature != self.rendered_mijia_login:
            self.rendered_mijia_login = signature
            self.mijia_qr.clear()
            if state == "waiting":
                pixmap = QPixmap()
                try:
                    valid = pixmap.loadFromData(base64.b64decode(session.get("qr_image", ""), validate=True))
                except ValueError:
                    valid = False
                if valid:
                    self.mijia_qr.setPixmap(pixmap.scaled(200, 200, Qt.AspectRatioMode.KeepAspectRatio,
                                                       Qt.TransformationMode.SmoothTransformation))
        has_qr = state == "waiting" and not self.mijia_qr.pixmap().isNull()
        self.mijia_qr.setVisible(has_qr)
        if state == "waiting" and not has_qr:
            self.mijia_login_status.setText("二维码无法显示，请点击「重新获取二维码」重试。")
        self.rebuild_mijia_matches()
        self.update_busy()

    def update_mijia_controls(self, hardware, busy):
        plug = self.selected_device_model in MIJIA_PLUG_MODELS
        session = self.mijia_login_state or {}
        state = session.get("state")
        valid = self.mijia_session_valid()
        accounts = self.mijia_account_rows()
        self.mijia_panel.setVisible(plug and not self.selected_device_id)
        self.mijia_scan_button.setEnabled(hardware and not busy and valid and bool(accounts)
                                          and not self.mijia_scan_pending)
        self.mijia_choices.setVisible(self.mijia_choices.count() > 1)
        self.mijia_choices.setEnabled(hardware and not busy and valid and not self.mijia_scan_pending)
        self.mijia_login_button.setEnabled(hardware and not busy and state not in ("starting", "authorizing", "loading"))
        self.mijia_login_button.setText("正在完成登录…" if state in ("authorizing", "loading") else
                                       "重新获取二维码" if state == "waiting" else "获取登录二维码")
        self.mijia_cancel_button.setVisible(state in ("starting", "waiting", "authorizing", "loading"))
        self.mijia_cancel_button.setEnabled(hardware and not self.reset_pending and not self.job_busy)
        self.mijia_back_button.setEnabled(hardware and not busy)
        self.mijia_import_button.setVisible(state == "selecting" and self.mijia_choices.count() > 1)
        self.mijia_import_button.setEnabled(hardware and not busy and self.mijia_can_import())
        self.mijia_retry_button.setVisible(state in ("finished", "expired", "failed") or
                                           state == "selecting" and not accounts)
        self.mijia_retry_button.setEnabled(hardware and not busy)
        if state == "selecting":
            message = ("选择已过期，请重新扫码" if not valid else
                       "账号中未找到此型号插座，请确认账号与所选型号。" if not accounts else
                       "正在扫描并匹配米家插座…" if self.mijia_scan_pending else
                       self.mijia_scan_error if self.mijia_scan_error else
                       "已匹配到此账号的插座，请按名称选择并连接。" if self.mijia_choices.count() > 1 else
                       "未匹配到局域网插座。请确认电脑与插座在同一局域网，再重新扫描。" if self.mijia_scan_complete else
                       "登录已完成，即将扫描局域网并匹配插座。")
        else:
            message = session.get("message", "")
        self.mijia_scan_status.setText(message)
        syncing = (plug and state in ("starting", "waiting", "authorizing", "loading")
                   and self.tabs.currentIndex() == 1 and not self.device_details.isHidden())
        if syncing and self.poll_timer.interval() > 300:
            self.poll_timer.setInterval(300)
        elif not syncing and self.poll_timer.interval() == 300:
            self.poll_timer.setInterval(1500)
        context = self.mijia_scan_identity()
        if (plug and hardware and not busy and valid and accounts and not self.mijia_scan_pending
                and self.mijia_auto_scan_session != context and self.mijia_scan_current(context)):
            self.mijia_auto_scan_session = context
            def scan_when_ready():
                if (self.mijia_scan_current(context) and not self.mijia_scan_complete
                        and self.mijia_auto_scan_session == context):
                    # A queued write can start before this timer. Only consume
                    # the once-per-session marker when a scan is submitted.
                    self.mijia_auto_scan_session = None
                    self.scan_mijia_devices()
            QTimer.singleShot(0, self, scan_when_ready)
