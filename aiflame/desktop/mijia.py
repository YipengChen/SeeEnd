"""Three-stage plug onboarding with explicit target identity and bounded login."""
import base64
import time

from PySide6.QtCore import Qt, QSignalBlocker, QUrl
from PySide6.QtGui import QDesktopServices, QPixmap

from aiflame.devices.models import MIJIA_PLUG_MODEL, MIJIA_PLUG_MODELS
from aiflame.devices.mijia import lan_host
from .components import restyle
from .localization import source_text


class MijiaConnection:
    def mijia_target(self):
        host, did = self.mijia_target_ip.text().strip(), self.mijia_target_did.text().strip()
        try:
            if host:
                host = lan_host(host)
        except ValueError:
            return host, did, "请填写插座的局域网 IPv4 地址，例如 192.168.1.100。"
        if did and (not did.isascii() or not did.isdigit() or len(did) > 10 or not 0 < int(did) < 0xffffffff):
            return host, did, "设备 ID 请填写米家显示的纯数字 DID，不是型号、MAC 地址或小米账号 ID。"
        return host, str(int(did)) if did else "", ""

    def mijia_target_changed(self, _text=None):
        host, did, problem = self.mijia_target()
        self.mijia_target_note.setText(problem or ("扫码后将按填写的设备 ID 或 IP 匹配插座。" if host or did else
                                                  "有多台插座时，建议填写其中一项；留空则在扫码后选择。"))
        restyle(self.mijia_target_note, "tone", "warning" if problem else "")
        self.update_busy()

    def set_mijia_step(self, index):
        self.mijia_step = index
        for position, panel in enumerate(self.mijia_steps):
            panel.setVisible(position == index)
            restyle(self.mijia_step_labels[position], "role", "title" if position == index else "muted")
        host, did, _ = self.mijia_target()
        self.mijia_target_summary.setText("目标 IP：" + (host or "未指定") + "   ·   设备 ID：" + (did or "未指定"))
        self.mijia_target_summary.setVisible(index > 0)

    def continue_mijia_login(self):
        _, _, problem = self.mijia_target()
        if problem:
            self.tell(problem, error=True)
            return
        self.set_mijia_step(1)
        self.update_busy()

    def scan_mijia_devices(self):
        host, _, problem = self.mijia_target()
        if problem:
            self.tell(problem, error=True)
            return
        self.mijia_scan_status.setText("正在扫描局域网设备…")
        self.device_action("discover", host=host)

    def render_mijia_discovery(self, rows):
        self.mijia_discovered = rows
        with QSignalBlocker(self.mijia_scan_choices):
            self.mijia_scan_choices.clear()
            self.mijia_scan_choices.addItem("选择扫描到的设备…", None)
            for row in rows:
                self.mijia_scan_choices.addItem("IP " + row["host"] + "   ·   ID " + row["did"], row)
        self.mijia_scan_choices.setVisible(bool(rows))
        self.mijia_scan_status.setText("扫描到设备，按米家中的 IP / 设备 ID 选择；型号将在扫码后核对。" if rows else
                                       "未扫描到设备。可手动填写米家中的 IP / 设备 ID，或直接继续扫码。")

    def choose_mijia_discovery(self, _index):
        row = self.mijia_scan_choices.currentData()
        if row:
            self.mijia_target_ip.setText(row["host"])
            self.mijia_target_did.setText(row["did"])

    def mijia_row_host(self, row):
        return next((item["host"] for item in self.mijia_discovered if item["did"] == row["did"]), row.get("host", ""))

    def mijia_matches(self, row):
        host, did, problem = self.mijia_target()
        if problem or not row:
            return False
        if did:
            return did == row["did"]
        return not host or host == row.get("host") or host == self.mijia_row_host(row)

    def start_mijia_login(self):
        if self.mijia_target()[2]:
            return
        epoch = self.device_view_epoch
        self.mijia_login_epoch += 1
        login_epoch = self.mijia_login_epoch
        if self.mijia_login_state and self.mijia_login_state.get("id"):
            self.mijia_ignored_sessions.add(self.mijia_login_state["id"])
        self.mijia_login_state = {"id": "", "state": "starting"}
        self.rendered_mijia_login = None
        self.mijia_qr.clear()
        self.mijia_qr.hide()
        self.mijia_login_link_button.hide()
        self.mijia_login_status.setText("正在获取小米登录二维码")
        self.set_mijia_step(1)

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

        self.act("/api/device/mijia", {"action": "login", "region": self.mijia_region.currentData()}, received, failed)

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
        self.mijia_login_link_button.hide()
        with QSignalBlocker(self.mijia_choices):
            self.mijia_choices.clear()
            self.mijia_choices.addItem("请选择要连接的插座…", None)
        self.mijia_login_state = None
        self.rendered_mijia_login = None
        self.mijia_login_status.clear()
        self.mijia_connect_ip.clear()
        self.set_mijia_step(0)

    def reset_mijia_draft(self):
        self.cancel_mijia_login()
        self.mijia_target_ip.clear()
        self.mijia_target_did.clear()
        self.render_mijia_discovery([])
        self.mijia_scan_status.setText("扫描只读取 IP 和设备 ID；型号将在扫码后核对。")

    def return_mijia_target(self):
        self.cancel_mijia_login()
        self.update_busy()

    def retry_mijia_login(self):
        self.cancel_mijia_login()
        self.set_mijia_step(1)
        self.update_busy()

    def open_mijia_login(self):
        session = self.mijia_login_state or {}
        if session.get("state") == "waiting" and session.get("login_url"):
            from aiflame.devices.mijia_cloud import xiaomi_url
            QDesktopServices.openUrl(QUrl(xiaomi_url(session["login_url"])))

    def choose_mijia_plug(self, _index):
        row = self.mijia_choices.currentData()
        host, _, _ = self.mijia_target()
        self.mijia_connect_ip.setText((host if host and self.mijia_matches(row) else self.mijia_row_host(row)) if row else "")
        self.update_busy()

    def mijia_can_import(self):
        session = self.mijia_login_state or {}
        if (session.get("state") != "selecting" or session.get("expires_at", time.time() + 1) <= time.time()
                or not self.mijia_matches(self.mijia_choices.currentData())):
            return False
        try:
            if self.mijia_connect_ip.text().strip():
                lan_host(self.mijia_connect_ip.text().strip())
        except ValueError:
            return False
        return True

    def import_mijia_plug(self):
        if not self.mijia_can_import():
            return
        row, session = self.mijia_choices.currentData(), self.mijia_login_state

        def received(job):
            self.device_operation_id = job.get("id")
            self.selected_device_id = ""
            self.render_device({"job": job})

        self.act("/api/device/mijia", {"action": "import", "id": session["id"], "did": row["did"],
                 "host": self.mijia_connect_ip.text().strip()}, received)

    def render_mijia_login(self, session):
        if (self.selected_device_model not in MIJIA_PLUG_MODELS or not session or self.tabs.currentIndex() != 1
                or self.device_details.isHidden() or session["id"] in self.mijia_ignored_sessions
                or session.get("state") == "cancelled"):
            return
        self.mijia_login_state = session
        state = session.get("state")
        rows = [row for row in session.get("devices", [])
                if row.get("hardware_model", MIJIA_PLUG_MODEL) == self.selected_device_model]
        if state in ("starting", "waiting", "authorizing", "loading"):
            self.set_mijia_step(1)
        elif state in ("selecting", "finished"):
            self.set_mijia_step(2)
        self.mijia_login_status.setText(session.get("message", ""))
        self.mijia_selection_status.setText(session.get("message", ""))
        signature = (session["id"], state, tuple((row["did"], row.get("host", ""), row["name"]) for row in rows))
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
            if state == "selecting":
                matched = [row for row in rows if self.mijia_matches(row)]
                with QSignalBlocker(self.mijia_choices):
                    self.mijia_choices.clear()
                    self.mijia_choices.addItem("请选择要连接的插座…", None)
                    ordered = matched + [row for row in rows if row not in matched]
                    for row in ordered:
                        host = self.mijia_row_host(row) or "IP 未提供"
                        prefix = "目标匹配 · " if self.mijia_target()[:2] != ("", "") and row in matched else ""
                        self.mijia_choices.addItem(prefix + row["name"] + "   ·   IP " + host + "   ·   ID " + row["did"], row)
                    if len(matched) == 1:
                        self.mijia_choices.setCurrentIndex(1)
                self.choose_mijia_plug(self.mijia_choices.currentIndex())
        if state == "selecting" and rows and self.mijia_target()[:2] != ("", "") and not any(self.mijia_matches(row) for row in rows):
            self.mijia_selection_status.setText("账号中未找到与目标 IP / 设备 ID 匹配的插座。请核对米家信息，返回修改或重新登录正确账号。")
        has_qr = state == "waiting" and not self.mijia_qr.pixmap().isNull()
        self.mijia_qr.setVisible(has_qr)
        self.mijia_login_link_button.setVisible(state == "waiting" and bool(session.get("login_url")))
        if state == "waiting" and not has_qr:
            self.mijia_login_status.setText("二维码无法显示，请在浏览器打开小米登录页。")
        self.update_busy()

    def update_mijia_controls(self, hardware, busy):
        plug = self.selected_device_model in MIJIA_PLUG_MODELS
        session = self.mijia_login_state or {}
        state = session.get("state")
        self.mijia_panel.setVisible(plug and not self.selected_device_id)
        self.mijia_next_button.setEnabled(hardware and not busy and not self.mijia_target()[2])
        self.mijia_scan_button.setEnabled(hardware and not busy and not self.mijia_target()[2])
        for control in (self.mijia_target_ip, self.mijia_target_did, self.mijia_scan_choices):
            control.setEnabled(hardware and not busy)
        self.mijia_login_button.setEnabled(hardware and not busy and state not in ("starting", "authorizing", "loading"))
        self.mijia_login_button.setText("正在完成登录…" if state in ("authorizing", "loading") else
                                        "重新获取二维码" if state == "waiting" else "获取登录二维码")
        self.mijia_region.setEnabled(hardware and not busy and state not in ("starting", "waiting", "authorizing", "loading"))
        self.mijia_cancel_button.setEnabled(hardware and not self.reset_pending and not self.job_busy)
        self.mijia_back_button.setEnabled(hardware and not busy)
        self.mijia_choices.setEnabled(hardware and not busy and state == "selecting")
        self.mijia_connect_ip.setEnabled(hardware and not busy and state == "selecting" and bool(self.mijia_choices.currentData()))
        self.mijia_import_button.setVisible(state == "selecting" and self.mijia_choices.count() > 1)
        self.mijia_import_button.setEnabled(hardware and not busy and self.mijia_can_import())
        self.mijia_retry_button.setVisible(state in ("finished", "expired", "failed") or (state == "selecting" and self.mijia_choices.count() == 1))
        self.mijia_retry_button.setEnabled(hardware and not busy)
        row = self.mijia_choices.currentData()
        self.mijia_selected_info.setText(("设备 ID：" + row["did"] + "\n当前 IP：" + (self.mijia_row_host(row) or "未提供，可自动查找")) if row else "")
        hint = "请选择插座后核对设备信息。"
        tone = ""
        if row:
            if not self.mijia_matches(row):
                hint, tone = "这只插座与填写的目标不一致。请选择匹配项，或返回修改目标信息。", "warning"
            else:
                host, did, _ = self.mijia_target()
                if did and host and host != self.mijia_row_host(row):
                    hint = "设备 ID 已匹配，但 IP 与填写值不同。请到米家核对当前 IP；连接时仍会核验同一设备 ID。"
                else:
                    hint = "目标信息已匹配，可以连接。" if host or did else "请与米家中的目标插座核对名称、IP 和设备 ID，再连接。"
                try:
                    if self.mijia_connect_ip.text().strip():
                        lan_host(self.mijia_connect_ip.text().strip())
                except ValueError:
                    hint, tone = "连接 IP 无效，请填写局域网 IPv4 地址，或留空自动查找。", "warning"
        self.mijia_match_hint.setText(hint)
        restyle(self.mijia_match_hint, "tone", tone)
        syncing = (plug and state in ("starting", "waiting", "authorizing", "loading")
                   and self.tabs.currentIndex() == 1 and not self.device_details.isHidden())
        if syncing and self.poll_timer.interval() > 300:
            self.poll_timer.setInterval(300)
        elif not syncing and self.poll_timer.interval() == 300:
            self.poll_timer.setInterval(1500)
