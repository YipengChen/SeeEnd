"""Exercise the actual native widgets and HTTP lifecycle in an isolated Hub."""

import os
from importlib.util import find_spec
from pathlib import Path
from copy import deepcopy
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch
from aiflame.hub.server import Hub
from aiflame.core.events import EventError, make_event

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HAS_QT = find_spec("PySide6") is not None
if HAS_QT:
    from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QRect, QTimer, Qt
    from PySide6.QtGui import QImage, QTextDocument
    from PySide6.QtWidgets import QApplication, QPushButton, QToolTip, QMessageBox
    from aiflame.desktop.widgets import Dashboard


@unittest.skipUnless(HAS_QT, "Install the desktop extra to test native widgets")
class NativeDesktopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setStyle("Fusion")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.hub = Hub(
            port=0,
            runtime=Path(self.temp.name),
            config={
                "dashboard": "test-cookie",
                "tokens": {"local-script": "test-token"},
            },
        )
        self.hub.start(hardware=False)
        self.widget = Dashboard(port=self.hub.port)
        self.widget.resize(1180, 850)
        self.widget.show()
        self.wait(lambda: self.widget.model is not None and not self.widget.api.pending)

    def tearDown(self):
        self.widget.shutdown()
        self.widget.close()
        self.widget.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()
        self.hub.stop()
        self.temp.cleanup()

    def wait(self, predicate, timeout=4):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            self.app.processEvents()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            if predicate():
                return
            time.sleep(0.005)
        self.fail("Native UI condition timed out")

    def engine_snapshot(self):
        with self.hub.lock:
            return self.hub.engine.snapshot()

    def desired_state(self):
        # Queries run cleanup and must share the live Hub's database lock.
        with self.hub.lock:
            return self.hub.engine.desired()["state"]

    def test_device_model_selection_precedes_configuration(self):
        self.widget.navigate(1)
        self.assertTrue(self.widget.device_selector.isVisible())
        self.assertFalse(self.widget.device_details.isVisible())
        self.assertEqual(len(self.widget.device_model_buttons), 6)
        self.widget.device_model_buttons["yeelight-yldp005"].click()
        self.wait(lambda: self.widget.selected_device_model == "yeelight-yldp005" and not self.widget.job_busy and not self.widget.writing)
        self.assertTrue(self.widget.device_details.isVisible())
        self.assertFalse(self.widget.device_selector.isVisible())
        self.assertEqual(self.widget.device_model_title.text(), "Yeelight W3 彩色灯泡")
        self.assertIn("YLDP005", self.widget.device_model_description.text())
        self.assertNotIn("hardware_model", self.hub.config)
        self.assertEqual(self.hub.transport.records, {})
        self.widget.nav_buttons[1].click()
        self.assertTrue(self.widget.device_selector.isVisible())
        self.assertFalse(self.widget.device_details.isVisible())
        self.widget.device_model_buttons["yeelight-yltd003"].click()
        self.wait(lambda: self.widget.selected_device_model == "yeelight-yltd003" and not self.widget.job_busy and not self.widget.writing)
        self.assertEqual(self.widget.breadcrumb.text(), "我的设备")
        self.widget.device_back_button.click()
        self.assertTrue(self.widget.device_selector.isVisible())
        self.assertEqual(self.widget.breadcrumb.text(), "我的设备")

    def test_plug4_catalog_and_login_filter_preserve_model_identity(self):
        from aiflame.devices.models import MIJIA_PLUG_MODEL, MIJIA_PLUG4_MODEL, YEELIGHT_CUBE_MODEL
        self.widget.navigate(1)
        self.widget.choose_device_model(MIJIA_PLUG4_MODEL)
        self.wait(lambda: not self.widget.device_selection_loading)
        self.assertFalse(self.widget.plug_settings_panel.isHidden())
        self.assertFalse(self.widget.mijia_panel.isHidden())
        for model in (MIJIA_PLUG4_MODEL, YEELIGHT_CUBE_MODEL):
            self.assertFalse(self.widget.device_model_buttons[model].thumbnail.pixmap().isNull())
        rows = [{"did": "123", "host": "192.168.1.2", "name": "插座 3", "hardware_model": MIJIA_PLUG_MODEL},
                {"did": "456", "host": "192.168.1.3", "name": "插座 4", "hardware_model": MIJIA_PLUG4_MODEL}]
        self.widget.render_mijia_login({"id": "mixed-models", "state": "selecting", "devices": rows})
        self.assertEqual(self.widget.mijia_choices.count(), 2)
        self.assertEqual(self.widget.mijia_choices.itemData(1)["did"], "456")
        self.widget.choose_device_model(YEELIGHT_CUBE_MODEL)
        self.wait(lambda: not self.widget.device_selection_loading)
        self.assertTrue(self.widget.mijia_panel.isHidden())
        self.assertTrue(self.widget.plug_settings_panel.isHidden())

    def test_mijia_scan_login_import_and_switch_settings(self):
        from tests.test_mijia import FakeCloud, TOKEN
        from aiflame.devices.models import MIJIA_PLUG_MODEL

        class Vault:
            values = {}
            def __init__(self, runtime=None, service=None):
                self.service = service or "test-vault"
            def set(self, identity, token):
                self.values[identity] = token
            def get(self, identity):
                return self.values.get(identity)
            def delete(self, identity):
                self.values.pop(identity, None)

        class Client:
            actual_power = False
            def __init__(self, host, token, did, stopping=None):
                self.sequence, self.stopping = 0, stopping
            def open(self):
                return self
            def command(self, method, params=None):
                return {"result": {"model": "cuco.plug.v3", "fw_ver": "test"}}
            def power(self):
                return self.actual_power
            def set_power(self, value):
                Client.actual_power = value
                self.sequence += 1
                return {"id": self.sequence, "result": []}
            def close(self):
                pass

        self.hub.devices.mijia.factory = FakeCloud
        rows = [{"did": "123", "host": "192.168.1.2", "name": "米家设备"}]
        with patch("aiflame.devices.mijia.discover", return_value=rows), \
                patch("aiflame.transports.mijia.discover", return_value=rows), \
                patch("aiflame.devices.mijia.PlugClient", Client), \
                patch("aiflame.transports.mijia.PlugClient", Client), \
                patch("aiflame.devices.token_store.TokenStore", Vault), \
                patch("aiflame.transports.mijia.TokenStore", Vault):
            self.widget.act("/api/settings", {"hardware_notifications": True})
            self.wait(lambda: self.widget.device_reminders_enabled() and not self.widget.writing)
            self.widget.navigate(1)
            self.widget.device_model_buttons[MIJIA_PLUG_MODEL].click()
            self.wait(lambda: not self.widget.device_selection_loading and self.widget.selected_device_model == MIJIA_PLUG_MODEL)
            self.assertTrue(self.widget.yeelight_colors_panel.isHidden())
            self.assertFalse(self.widget.plug_settings_panel.isHidden())
            self.assertFalse(self.widget.mijia_panel.isHidden())
            self.assertFalse(self.widget.device_model_buttons[MIJIA_PLUG_MODEL].thumbnail.pixmap().isNull())
            self.assertTrue(self.widget.device_light_card.isHidden())
            self.assertEqual(self.widget.mijia_step, 0)
            self.widget.mijia_scan_button.click()
            self.wait(lambda: self.widget.mijia_scan_choices.count() == 2 and not self.widget.job_busy)
            self.widget.mijia_scan_choices.setCurrentIndex(1)
            self.assertEqual(self.widget.mijia_target_ip.text(), "192.168.1.2")
            self.assertEqual(self.widget.mijia_target_did.text(), "123")
            self.widget.mijia_next_button.click()
            self.assertEqual(self.widget.mijia_step, 1)
            self.widget.mijia_login_button.click()
            self.wait(lambda: self.widget.mijia_choices.count() == 3 and not self.widget.writing)
            self.assertEqual(self.widget.mijia_step, 2)
            self.assertEqual(self.widget.mijia_choices.currentData()["did"], "123")
            self.assertEqual(self.widget.mijia_target_did.text(), "123")
            self.assertTrue(self.widget.mijia_import_button.isEnabled())
            self.widget.mijia_import_button.click()
            self.wait(lambda: "miio:123" in self.hub.transport.records and self.widget.selected_device_id == "miio:123")
            self.wait(lambda: self.widget.detail_device().get("online") and not self.widget.writing)
            self.assertTrue(self.widget.mijia_panel.isHidden())
            self.assertFalse(self.widget.device_light_card.isHidden())
            self.wait(lambda: "123" in self.widget.mijia_connected_identity.text())
            self.assertIn("123", self.widget.mijia_connected_identity.text())
            self.assertIn("192.168.1.2", self.widget.mijia_connected_identity.text())
            self.assertEqual(Vault.values, {"miio:123": TOKEN})
            self.assertNotIn(TOKEN, (Path(self.temp.name) / "credentials.json").read_text())
            self.assertNotIn(TOKEN, str(self.hub.devices.status(MIJIA_PLUG_MODEL, "miio:123")))
            self.widget.plug_power_choices["working"].setCurrentIndex(1)
            self.wait(lambda: self.hub.transport.records["miio:123"]["light_settings"].get("state_power", {}).get("working") is True)
            with self.hub.lock:
                self.hub.engine.accept(make_event("manual", "plug-task", "working", 1), "manual")
                done = make_event("manual", "plug-task", "done", 2)
                self.hub.engine.accept(done, "manual")
            self.wait(lambda: Client.actual_power is True)
            self.wait(lambda: self.hub.transport.outputs["miio:123"].last_display_key == done["event_id"])
            self.assertFalse(hasattr(self.widget, "plug_enabled"))
            self.assertEqual(self.hub.transport.outputs["miio:123"].settings["brightness"], 100)
            with patch.object(Vault, "delete", side_effect=RuntimeError("vault denied")):
                with self.assertRaisesRegex(EventError, "设备已保留"):
                    self.hub.devices.delete({"hardware_model": MIJIA_PLUG_MODEL, "device_id": "miio:123"})
            self.assertIn("miio:123", self.hub.transport.records)
            self.assertIn("miio:123", (Path(self.temp.name) / "credentials.json").read_text())
            self.assertEqual(Vault.values, {"miio:123": TOKEN})
            self.widget.act("/api/device/delete", {"hardware_model": MIJIA_PLUG_MODEL, "device_id": "miio:123"})
            self.wait(lambda: "miio:123" not in self.hub.transport.records and not self.widget.writing)
            self.assertEqual(Vault.values, {})

    def test_mijia_one_confirmation_auto_advances_despite_callback_and_query_timeouts(self):
        import threading
        from tests.test_mijia import FakeCloud
        from aiflame.devices.models import MIJIA_PLUG_MODEL
        confirmed = threading.Event()
        class DelayedCloud(FakeCloud):
            polls = authorizations = queries = 0
            def poll(self, url, timeout=120):
                self.polls += 1
                if not confirmed.wait(3):
                    raise AssertionError("phone confirmation not released")
            def authorize(self):
                self.authorizations += 1
                if self.authorizations == 1:
                    raise TimeoutError("delayed callback")
            def plugs(self, region, stopping):
                self.queries += 1
                if self.queries == 1:
                    raise TimeoutError("delayed device query")
                return super().plugs(region, stopping)
        cloud = DelayedCloud()
        self.hub.devices.mijia.factory = lambda: cloud
        self.widget.act("/api/settings", {"hardware_notifications": True})
        self.wait(lambda: self.widget.device_reminders_enabled() and not self.widget.writing)
        self.widget.navigate(1)
        self.widget.choose_device_model(MIJIA_PLUG_MODEL)
        self.wait(lambda: not self.widget.device_selection_loading)
        self.widget.mijia_next_button.click()
        self.widget.mijia_login_button.click()
        try:
            self.wait(lambda: (self.widget.mijia_login_state or {}).get("state") == "waiting")
            self.assertEqual(self.widget.poll_timer.interval(), 300)
            confirmed.set()  # One phone confirmation; no second QR scan or refresh.
            self.wait(lambda: (self.widget.mijia_login_state or {}).get("state") == "authorizing")
            self.assertIn("手机已确认登录", self.widget.mijia_login_status.text())
            self.assertTrue(self.widget.mijia_qr.isHidden())
            self.assertFalse(self.widget.mijia_login_button.isEnabled())
            self.assertFalse(self.widget.mijia_region.isEnabled())
            self.assertTrue(self.widget.mijia_cancel_button.isEnabled())
            self.wait(lambda: self.widget.mijia_step == 2 and self.widget.mijia_choices.count() == 3)
            self.assertEqual((cloud.polls, cloud.authorizations, cloud.queries), (1, 2, 2))
            self.assertEqual(self.widget.poll_timer.interval(), 1500)
            self.widget.return_mijia_target()
            self.wait(lambda: not self.widget.writing)
            self.assertEqual(self.widget.mijia_step, 0)
        finally:
            confirmed.set()
            self.hub.devices.mijia.cancel()

    def test_mijia_login_view_hides_qr_and_cancels_when_leaving(self):
        from aiflame.devices.models import MIJIA_PLUG_MODEL
        self.widget.navigate(1)
        self.widget.device_model_buttons[MIJIA_PLUG_MODEL].click()
        self.wait(lambda: not self.widget.device_selection_loading)
        from PySide6.QtCore import QByteArray, QBuffer, QIODevice
        import base64
        image = QImage(200, 200, QImage.Format.Format_RGB32)
        image.fill(Qt.GlobalColor.white)
        encoded = QByteArray()
        buffer = QBuffer(encoded)
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        image.save(buffer, "PNG")
        self.widget.render_mijia_login({"id": "test-session", "state": "waiting", "qr_image": base64.b64encode(bytes(encoded)).decode(),
            "login_url": "https://account.xiaomi.com/test", "message": "请扫码"})
        self.assertFalse(self.widget.mijia_qr.isHidden())
        self.assertFalse(self.widget.mijia_cancel_button.isHidden())
        self.widget.navigate(0)
        self.wait(lambda: not self.widget.writing)
        self.assertTrue(self.widget.mijia_qr.isHidden())
        self.assertIsNone(self.widget.mijia_login_state)

    def test_mijia_login_response_after_navigation_is_cancelled(self):
        from aiflame.devices.models import MIJIA_PLUG_MODEL
        self.widget.navigate(1)
        self.widget.device_model_buttons[MIJIA_PLUG_MODEL].click()
        self.wait(lambda: not self.widget.device_selection_loading and not self.widget.api.pending)
        calls = []
        with patch.object(self.widget, "act", side_effect=lambda *args, **kwargs: calls.append((args, kwargs))):
            self.widget.start_mijia_login()
            callback = calls[0][0][2]
            self.widget.navigate(0)
            callback({"id": "late-session", "state": "starting"})
        self.assertEqual(calls[-1][0][:2], ("/api/device/mijia", {"action": "cancel", "id": "late-session"}))
        self.assertIsNone(self.widget.mijia_login_state)

    def test_mijia_invalid_qr_stays_hidden_on_repeated_poll(self):
        from aiflame.devices.models import MIJIA_PLUG_MODEL
        self.widget.navigate(1)
        self.widget.device_model_buttons[MIJIA_PLUG_MODEL].click()
        self.wait(lambda: not self.widget.device_selection_loading)
        state = {"id": "invalid-qr", "state": "waiting", "qr_image": "invalid image", "message": "请扫码"}
        self.widget.render_mijia_login(state)
        self.widget.render_mijia_login(state)
        self.assertTrue(self.widget.mijia_qr.isHidden())
        self.assertIn("二维码无法显示", self.widget.mijia_login_status.text())

    def test_mijia_target_validation_matching_and_back_navigation(self):
        from aiflame.devices.models import MIJIA_PLUG_MODEL
        self.widget.act("/api/settings", {"hardware_notifications": True})
        self.wait(lambda: self.widget.device_reminders_enabled() and not self.widget.writing)
        self.widget.navigate(1)
        self.widget.choose_device_model(MIJIA_PLUG_MODEL)
        self.wait(lambda: not self.widget.device_selection_loading)
        self.assertIn("网络信息", self.widget.mijia_app_hint.text())
        self.widget.mijia_target_ip.setText("8.8.8.8")
        self.assertFalse(self.widget.mijia_next_button.isEnabled())
        self.widget.mijia_target_ip.setText("192.168.1.20")
        for invalid in ("cuco.plug.v3", "00:11:22", "9" * 5000):
            self.widget.mijia_target_did.setText(invalid)
            self.assertFalse(self.widget.mijia_next_button.isEnabled())
        self.widget.mijia_target_did.setText("123")
        self.widget.continue_mijia_login()
        rows = [{"did": "456", "host": "192.168.1.21", "name": "米家智能插座 3"},
                {"did": "123", "host": "192.168.1.22", "name": "米家智能插座 3"}]
        state = {"id": "matching", "state": "selecting", "devices": rows, "message": "请选择"}
        self.widget.render_mijia_login(state)
        self.assertEqual(self.widget.mijia_choices.currentData()["did"], "123")
        self.assertIn("目标匹配", self.widget.mijia_choices.currentText())
        self.assertIn("IP", self.widget.mijia_choices.currentText())
        self.assertIn("设备 ID 已匹配", self.widget.mijia_match_hint.text())
        self.widget.mijia_choices.setCurrentIndex(2)
        self.assertFalse(self.widget.mijia_import_button.isEnabled())
        with patch.object(self.widget, "act") as request:
            self.widget.import_mijia_plug()
            request.assert_not_called()
            self.widget.return_mijia_target()
            request.assert_called_once_with("/api/device/mijia", {"action": "cancel", "id": "matching"})
        self.assertEqual(self.widget.mijia_step, 0)
        self.assertEqual(self.widget.mijia_target_did.text(), "123")
        self.widget.render_mijia_login(state)  # A queued poll cannot revive a cancelled login.
        self.assertIsNone(self.widget.mijia_login_state)
        self.assertEqual(self.widget.mijia_step, 0)
        self.widget.reset_finished({**self.engine_snapshot(), "devices": [], "maintenance": False})
        self.assertEqual(self.widget.mijia_target_ip.text(), "")
        self.assertEqual(self.widget.mijia_target_did.text(), "")
        self.assertEqual(self.widget.mijia_discovered, [])

    def test_mijia_scan_request_failure_is_shown_in_current_step(self):
        from aiflame.devices.models import MIJIA_PLUG_MODEL
        self.widget.act("/api/settings", {"hardware_notifications": True})
        self.wait(lambda: self.widget.device_reminders_enabled() and not self.widget.writing)
        self.widget.navigate(1)
        self.widget.choose_device_model(MIJIA_PLUG_MODEL)
        self.wait(lambda: not self.widget.device_selection_loading)
        with patch.object(self.widget, "act") as request:
            self.widget.scan_mijia_devices()
            self.widget.message.setText("扫描请求失败，请重试")
            request.call_args.args[3]()
        self.assertIn("扫描请求失败", self.widget.mijia_scan_status.text())
        self.assertTrue(self.widget.job_message.isHidden())
        self.assertTrue(self.widget.progress.isHidden())
        self.assertEqual(self.widget.mijia_step, 0)

    def test_mijia_multiple_plugs_require_selection_and_match_lan_identity(self):
        from aiflame.devices.models import MIJIA_PLUG_MODEL
        self.widget.navigate(1)
        self.widget.choose_device_model(MIJIA_PLUG_MODEL)
        self.wait(lambda: not self.widget.device_selection_loading)
        rows = [{"did": "123", "host": "192.168.1.2", "name": "插座"},
                {"did": "456", "host": "192.168.1.3", "name": "插座"}]
        self.widget.render_mijia_login({"id": "ambiguous", "state": "selecting", "devices": rows})
        self.assertIsNone(self.widget.mijia_choices.currentData())
        self.assertFalse(self.widget.mijia_can_import())
        self.widget.return_mijia_target()
        self.widget.mijia_target_ip.setText("192.168.1.40")
        self.widget.render_mijia_discovery([{"did": "123", "host": "192.168.1.40"}])
        self.widget.render_mijia_login({"id": "lan-match", "state": "selecting", "devices": rows})
        self.assertEqual(self.widget.mijia_choices.currentData()["did"], "123")
        self.assertEqual(self.widget.mijia_connect_ip.text(), "192.168.1.40")
        self.assertTrue(self.widget.mijia_can_import())
        self.widget.mijia_connect_ip.setText("8.8.8.8")
        self.assertFalse(self.widget.mijia_can_import())

    def test_yeelight_has_lan_controls_and_keeps_discovery_selection(self):
        self.widget.navigate(1)
        self.widget.device_model_buttons["yeelight-yltd003"].click()
        self.wait(lambda: self.widget.selected_device_model == "yeelight-yltd003" and not self.widget.device_selection_loading)
        self.assertFalse(self.widget.yeelight_card.isHidden())
        job = {"id": "search", "action": "discover", "state": "succeeded", "progress": 100,
               "message": "已发现", "result": {"devices": [{"name": "YLTD003", "host": "192.168.1.44"}]}}
        self.widget.render_device({"job": job})
        self.widget.yeelight_choices.setCurrentIndex(1)
        self.widget.render_device({"job": job})
        self.assertEqual(self.widget.yeelight_choices.currentIndex(), 1)
        self.assertEqual(self.widget.yeelight_host.text(), "192.168.1.44")
        self.widget.yeelight_host.setText("192.168.1.45")
        self.widget.render_device({"job": job})
        self.assertEqual(self.widget.yeelight_host.text(), "192.168.1.45")

    def test_new_yeelight_connection_failure_is_visible_without_saved_device(self):
        self.widget.act('/api/settings', {"hardware_notifications": True})
        self.wait(lambda: self.widget.model['settings']['hardware_notifications'] is True and not self.widget.writing)
        self.widget.navigate(1)
        for model, lan_model in (('yeelight-yltd003', 'lamp15'), ('yeelight-yldp005', 'colorb')):
            with self.subTest(model=model):
                info = {'device_id': 'yeelight:test', 'host': '192.168.1.44', 'port': 55443, 'model': lan_model}
                self.widget.choose_device_model(model)
                self.wait(lambda: not self.widget.device_selection_loading and not self.widget.writing)
                self.assertTrue(self.widget.device_model_title.wordWrap())
                self.assertEqual(self.widget.device_model_description.text(),
                                 self.widget.device_model_buttons[model].detail.text())
                self.widget.yeelight_host.setText(info['host'])
                client = Mock()
                client.command.side_effect = TimeoutError('get_prop 指令未收到回执')
                with patch('aiflame.devices.yeelight.discover', return_value=[info]), \
                     patch('aiflame.devices.yeelight.YeelightClient', return_value=client):
                    self.widget.yeelight_connect_button.click()
                    self.assertTrue(self.widget.job_message.isVisible())
                    self.assertTrue(self.widget.progress.isVisible())
                    self.wait(lambda: self.hub.devices.job and self.hub.devices.job['state'] == 'failed'
                              and self.hub.devices.job.get('hardware_model') == model
                              and '未收到回执' in self.widget.job_message.text())
                self.assertTrue(self.widget.device_status_card.isHidden())
                self.assertTrue(self.widget.job_message.isVisible())
                self.assertFalse(self.widget.progress.isVisible())
                self.assertTrue(self.widget.yeelight_connect_button.isEnabled())
                self.assertEqual(self.hub.transport.records, {})
                client.close.assert_called_once()
        self.widget.choose_device_model('yeelight-yltd003')
        self.wait(lambda: not self.widget.device_selection_loading)
        self.assertEqual(self.widget.device_job_feedback.parentWidget(), self.widget.yeelight_card)

    def test_yeelight_history_actions_and_per_device_colors(self):
        self.widget.act('/api/settings', {"hardware_notifications": True})
        self.wait(lambda: self.widget.model['settings']['hardware_notifications'] is True and not self.widget.writing)
        self.widget.navigate(1)
        for model in ('yeelight-yltd003', 'yeelight-yldp005'):
            with self.subTest(model=model):
                self.widget.choose_device_model(model)
                self.wait(lambda: not self.widget.device_selection_loading)
                self.assertTrue(self.widget.yeelight_add_panel.isVisible())
                self.assertTrue(self.widget.device_status_card.isHidden())
                self.assertTrue(self.widget.yeelight_history_heading.isVisible())
                self.assertTrue(self.widget.device_instance_panel.isVisible())
                self.assertEqual(self.widget.device_instances.itemText(0), '尚未添加设备')
                self.assertEqual(self.widget.yeelight_choices.itemText(0), '搜索后选择灯具…')
                identity = 'yeelight:' + model
                self.hub.transport.records[identity] = {'device_id': identity, 'hardware_model': model,
                    'yeelight_host': '192.168.1.44', 'connection_paused': True}
                self.widget.poll(force=True)
                self.wait(lambda: self.widget.device_instances.findData(identity) > 0)
                self.assertTrue(self.widget.yeelight_history_heading.isVisible())
                self.assertTrue(self.widget.device_instance_panel.isVisible())
                self.assertEqual(self.widget.device_instances.itemText(0), '添加此型号设备…')
                self.widget.device_instances.setCurrentIndex(self.widget.device_instances.findData(identity))
                self.wait(lambda: not self.widget.device_selection_loading)
                self.assertFalse(self.widget.yeelight_add_panel.isVisible())
                self.assertTrue(self.widget.device_status_card.isVisible())
                self.assertTrue(self.widget.yeelight_reconnect_button.isEnabled())
                self.assertTrue(self.widget.yeelight_delete_button.isVisible())
                self.assertTrue(self.widget.yeelight_delete_button.isEnabled())
                self.assertEqual(self.widget.yeelight_host.text(), '192.168.1.44')
                self.widget.yeelight_brightness_controls["working"].setValue(100)
                self.wait(lambda: self.hub.transport.records[identity].get('light_settings', {}).get('brightness') == 100)
                self.wait(lambda: not self.widget.writing)
                self.widget.yeelight_brightness_controls['working'].setValue(27)
                self.wait(lambda: self.hub.transport.records[identity].get('light_settings', {}).get('state_brightness', {}).get('working') == 27)
                self.wait(lambda: not self.widget.writing)
                self.widget.yeelight_brightness_controls['done'].setValue(65)
                self.wait(lambda: self.hub.transport.records[identity].get('light_settings', {}).get('state_brightness', {}).get('done') == 65)
                self.wait(lambda: not self.widget.writing)
                self.assertEqual(self.hub.transport.records[identity]['light_settings']['state_brightness']['working'], 27)
                choice = self.widget.yeelight_color_choices['working']
                self.assertGreaterEqual(choice.count(), 10)
                choice.setCurrentIndex(choice.findData('#FF72B6'))
                self.wait(lambda: self.hub.transport.records[identity].get('light_settings', {}).get('colors', {}).get('working') == '#FF72B6')
                self.wait(lambda: not self.widget.writing)
                for reset in self.widget.yeelight_state_resets.values():
                    reset.click()
                    self.wait(lambda: not self.widget.writing and not self.widget.api.pending)
                self.wait(lambda: self.hub.transport.records[identity]['light_settings']['colors']['working'] == '#FACC15'
                          and self.hub.transport.records[identity]['light_settings']['state_brightness'] == {'working': 50, 'done': 100, 'needs_input': 100})
                self.wait(lambda: not self.widget.writing)
                self.widget.yeelight_delete_button.click()
                dialog = self.widget.delete_confirmation
                next(button for button in dialog.buttons() if dialog.buttonRole(button) == QMessageBox.ButtonRole.DestructiveRole).click()
                self.wait(lambda: identity not in self.hub.transport.records and not self.widget.writing
                          and not self.widget.device_selection_loading and not self.widget.api.pending)
                self.assertNotIn(identity, [item['device_id'] for item in self.widget.model['devices']])
                self.assertEqual(self.widget.selected_device_id, '')
                self.assertEqual(self.widget.device_instances.currentText(), '尚未添加设备')
                self.assertTrue(self.widget.yeelight_delete_button.isHidden())
                self.assertTrue(self.widget.yeelight_add_panel.isVisible())
                self.assertTrue(self.widget.device_status_card.isHidden())

    def test_bulb_model_has_lan_setup_and_six_cards_fit_without_horizontal_scroll(self):
        self.widget.navigate(1)
        self.widget.device_model_buttons["yeelight-yldp005"].click()
        self.wait(lambda: self.widget.selected_device_model == "yeelight-yldp005" and not self.widget.device_selection_loading)
        self.assertFalse(self.widget.yeelight_card.isHidden())
        self.assertEqual(self.widget.yeelight_discover_button.text(), "搜索 W3 彩色灯泡")
        self.widget.show_device_selector()
        for width, height in ((920, 680), (1180, 850)):
            self.widget.resize(width, height)
            for _ in range(4):
                self.app.processEvents()
            self.assertIsNotNone(self.widget.device_cards_layout.itemAtPosition(0, 1))
            self.assertEqual(self.widget.device_cards_layout.itemAtPosition(1, 0).widget().model['id'], 'yeelight-ylfwd0021')
            self.assertEqual(self.widget.tabs.widget(1).horizontalScrollBar().maximum(), 0)
            self.assertEqual(self.widget.device_cards_layout.itemAtPosition(2, 1).widget().model['id'], 'mijia-zncz401kk')
            if height >= 850:
                self.assertEqual(self.widget.tabs.widget(1).verticalScrollBar().maximum(), 0)
            else:
                page = self.widget.tabs.widget(1)
                last = self.widget.device_cards_layout.itemAtPosition(2, 1).widget()
                page.ensureWidgetVisible(last)
                self.app.processEvents()
                self.assertLessEqual(last.mapTo(page.viewport(), last.rect().bottomRight()).y(), page.viewport().height())

    def test_beam_model_has_color_controls_and_network_loss_explanation(self):
        self.widget.navigate(1)
        self.widget.device_model_buttons['yeelight-ylfwd0021'].click()
        self.wait(lambda: self.widget.selected_device_model == 'yeelight-ylfwd0021' and not self.widget.device_selection_loading)
        self.assertEqual(self.widget.device_model_title.text(), 'Yeelight Beam 塔灯')
        self.assertFalse(self.widget.yeelight_card.isHidden())
        self.assertFalse(self.widget.yeelight_mode_panel.isHidden())
        self.assertEqual([self.widget.yeelight_reminder_mode.itemText(i) for i in range(2)], ['彩灯', '白灯'])
        self.assertTrue(self.widget.yeelight_flash_panel.isHidden())
        self.assertFalse(self.widget.device_model_buttons['yeelight-ylfwd0021'].thumbnail.pixmap().isNull())

    def test_cube_effect_modes_save_reopen_and_do_not_leak_to_other_lamps(self):
        from aiflame.devices.cube_effects import CUBE_EFFECTS
        self.select_light_settings_fixture(model_id='yeelight-cube-lite')
        mode = self.widget.yeelight_reminder_mode
        self.assertEqual(mode.count(), 2 + len(CUBE_EFFECTS))
        self.assertEqual(mode.findData('tasks'), -1)
        self.assertEqual(mode.findData('flame'), -1)
        for value, title, description in CUBE_EFFECTS:
            mode.setCurrentIndex(mode.findData(value))
            self.wait(lambda: self.hub.transport.records['one']['light_settings'].get('reminder_mode') == value
                      and not self.widget.writing and not self.widget.api.pending)
            self.assertEqual(mode.currentText(), title)
            self.assertEqual(self.widget.cube_effect_hint.text(), description)
            self.assertFalse(self.widget.cube_effect_hint.isHidden())
            self.assertTrue(self.widget.yeelight_flash_panel.isHidden())
        self.widget.show_device_selector()
        self.widget.choose_registered_device('one')
        self.wait(lambda: not self.widget.device_selection_loading and not self.widget.api.pending)
        self.assertEqual(mode.currentData(), 'text')
        self.widget.choose_device_model('yeelight-yldp005')
        self.wait(lambda: not self.widget.device_selection_loading and not self.widget.api.pending)
        self.assertEqual(mode.count(), 2)
        self.assertTrue(self.widget.cube_effect_hint.isHidden())
        self.widget.choose_registered_device('one')
        self.wait(lambda: not self.widget.device_selection_loading and not self.widget.api.pending)
        self.assertEqual(mode.count(), 2 + len(CUBE_EFFECTS))
        self.assertEqual(mode.currentData(), 'text')

    def test_preview_selection_reveals_only_selected_parameters_and_survives_refresh(self):
        from PySide6.QtTest import QTest
        self.select_light_settings_fixture(2, model_id='yeelight-cube-lite')
        w = self.widget
        self.assertTrue(all(section.isHidden() for section in w.yeelight_state_sections.values()))
        panel = w.light_previews
        QTest.mouseClick(panel.cards[2], Qt.MouseButton.LeftButton)
        self.assertEqual(panel.selected_state, 'done')
        self.assertEqual([key for key, section in w.yeelight_state_sections.items() if not section.isHidden()], ['done'])
        w.yeelight_brightness_controls['done'].setValue(37)
        self.wait(lambda: not w.brightness_timer.isActive() and not w.writing and not w.api.pending)
        self.assertEqual(self.hub.transport.records['one']['light_settings']['state_brightness']['done'], 37)
        w.poll(force=True)
        self.wait(lambda: not w.api.pending)
        self.assertEqual(panel.selected_state, 'done')
        QTest.keyClick(panel.cards[2], Qt.Key.Key_Left)
        self.assertEqual(panel.selected_state, 'needs_input')
        w.yeelight_reminder_mode.setCurrentIndex(w.yeelight_reminder_mode.findData('front'))
        self.wait(lambda: not w.writing and not w.api.pending)
        self.assertFalse(w.yeelight_flash_panel.isHidden())
        panel.cards[0].click()
        self.assertTrue(w.yeelight_flash_panel.isHidden())
        self.assertFalse(w.yeelight_state_sections['working'].isHidden())
        w.choose_registered_device('two')
        self.wait(lambda: not w.device_selection_loading and not w.api.pending)
        self.assertIsNone(panel.selected_state)
        self.assertTrue(all(section.isHidden() for section in w.yeelight_state_sections.values()))

    def test_light_settings_require_current_device_online(self):
        from aiflame.devices.models import SUPPORTED_MODELS
        for model in SUPPORTED_MODELS:
            with self.subTest(model=model):
                self.select_light_settings_fixture(2, model_id=model, online=False)
                w = self.widget
                self.assertFalse(w.device_light_card.isVisible())
                with self.hub.lock:
                    self.hub.transport.outputs['one'].device.update(online=True, connecting=False)
                w.poll(force=True)
                self.wait(lambda: not w.api.pending)
                self.assertTrue(w.device_light_card.isVisible())
                w.choose_registered_device('two')
                self.assertFalse(w.device_light_card.isVisible())
                self.wait(lambda: not w.device_selection_loading and not w.api.pending)
                self.assertFalse(w.device_light_card.isVisible())
                w.choose_registered_device('one')
                self.wait(lambda: not w.device_selection_loading and not w.api.pending)
                self.assertTrue(w.device_light_card.isVisible())
                for online, connecting in ((False, False), (True, True), (True, False)):
                    with self.hub.lock:
                        self.hub.transport.outputs['one'].device.update(online=online, connecting=connecting)
                    w.poll(force=True)
                    self.wait(lambda: not w.api.pending)
                    self.assertEqual(w.device_light_card.isVisible(), online and not connecting)

    def test_state_card_previews_current_device_and_saved_parameters_then_stops(self):
        self.select_light_settings_fixture(2, model_id='yeelight-cube-lite')
        w = self.widget
        w.light_previews.cards[2].click()
        self.wait(lambda: not w.writing and not w.api.pending)
        output = self.hub.transport.outputs['one']
        self.assertEqual(output.desired()['state'], 'done')
        self.assertTrue(output.desired()['device_test'])
        self.assertTrue(output.desired()['continuous'])
        self.assertEqual(w.device_test_caption.text(), '正在预览所选状态')
        key = output.desired()['key']
        self.assertIsNone(self.hub.transport.outputs['two'].light_test)
        w.yeelight_brightness_controls['done'].setValue(73)
        self.wait(lambda: not w.brightness_timer.isActive() and not w.writing and not w.api.pending)
        self.assertEqual(output.settings['state_brightness']['done'], 73)
        self.assertEqual(output.desired()['key'], key)
        w.light_previews.cards[0].click()
        self.wait(lambda: not w.writing and not w.api.pending)
        self.assertEqual(output.desired()['state'], 'working')
        w.navigate(0)
        self.wait(lambda: not w.writing and not w.api.pending)
        self.assertIsNone(output.light_test)

    def test_hiding_window_cancels_continuous_state_preview(self):
        self.select_light_settings_fixture(model_id='yeelight-cube-lite')
        w = self.widget
        w.light_previews.cards[0].click()
        self.wait(lambda: not w.writing and not w.api.pending)
        output = self.hub.transport.outputs['one']
        self.assertTrue(output.desired()['continuous'])
        w.hide()
        self.wait(lambda: not w.writing and not w.api.pending)
        self.assertIsNone(output.light_test)
        self.assertIsNone(w.light_previews.selected_state)

    def test_state_reset_preserves_other_states_and_other_modes(self):
        self.select_light_settings_fixture(model_id='yeelight-cube-lite')
        w = self.widget
        for state, value in (('working', 31), ('done', 67)):
            w.yeelight_brightness_controls[state].setValue(value)
            self.wait(lambda: not w.brightness_timer.isActive() and not w.writing and not w.api.pending)
        w.light_previews.select_state('working')
        self.wait(lambda: not w.writing and not w.api.pending)
        w.yeelight_state_resets['working'].click()
        self.wait(lambda: not w.writing and not w.api.pending)
        saved = self.hub.transport.records['one']['light_settings']
        self.assertEqual(saved['state_brightness']['working'], 50)
        self.assertEqual(saved['state_brightness']['done'], 67)
        w.yeelight_reminder_mode.setCurrentIndex(w.yeelight_reminder_mode.findData('front'))
        self.wait(lambda: not w.writing and not w.api.pending)
        w.light_previews.select_state('needs_input')
        self.wait(lambda: not w.writing and not w.api.pending)
        w.yeelight_flash_enabled.setChecked(False)
        self.wait(lambda: not w.writing and not w.api.pending)
        w.light_previews.select_state('done')
        self.wait(lambda: not w.writing and not w.api.pending)
        w.yeelight_state_resets['done'].click()
        self.wait(lambda: not w.writing and not w.api.pending)
        saved = self.hub.transport.records['one']['light_settings']
        self.assertFalse(saved['front_flash']['enabled'])
        self.assertEqual(saved['state_brightness']['done'], 67)
        w.light_previews.select_state('needs_input')
        self.wait(lambda: not w.writing and not w.api.pending)
        w.yeelight_state_resets['needs_input'].click()
        self.wait(lambda: not w.writing and not w.api.pending)
        self.assertTrue(self.hub.transport.records['one']['light_settings']['front_flash']['enabled'])

    def test_plug_preview_selection_edits_only_selected_power_rule(self):
        self.select_light_settings_fixture(model_id='mijia-zncz401kk')
        w = self.widget
        self.assertTrue(all(section.isHidden() for section in w.plug_state_sections.values()))
        w.plug_previews.cards[2].click()
        self.assertEqual([key for key, section in w.plug_state_sections.items() if not section.isHidden()], ['done'])
        w.plug_power_choices['done'].setCurrentIndex(0)
        self.wait(lambda: not w.writing and not w.api.pending)
        settings = self.hub.transport.records['one']['light_settings']['state_power']
        self.assertFalse(settings['done'])
        w.plug_previews.cards[1].click()
        self.assertTrue(w.plug_state_sections['done'].isHidden())
        self.assertFalse(w.plug_state_sections['needs_input'].isHidden())

    def test_light_preview_tracks_saved_controls_and_stops_outside_device_page(self):
        self.select_light_settings_fixture(model_id='yeelight-cube-lite')
        picker = self.widget.yeelight_reminder_mode
        picker.entries[picker.findData('text')][0].click()
        self.wait(lambda: self.hub.transport.records['one']['light_settings'].get('reminder_mode') == 'text'
                  and not self.widget.writing and not self.widget.api.pending)
        panel = self.widget.light_previews
        self.assertEqual(panel.settings['reminder_mode'], 'text')
        self.assertEqual(panel.captions['done'].text(), 'DONE')
        self.widget.yeelight_brightness_controls['done'].setValue(0)
        self.assertEqual(panel.canvases['done'].pixels(1), bytes(300))
        self.wait(lambda: not self.widget.brightness_timer.isActive() and not self.widget.writing and not self.widget.api.pending)
        revision = panel.revision
        self.widget.poll(force=True)
        self.wait(lambda: not self.widget.api.pending)
        self.assertEqual(panel.revision, revision)
        self.widget.navigate(0)
        self.assertFalse(panel.timer.isActive())
        self.widget.navigate(1)
        self.widget.choose_registered_device('one')
        self.wait(lambda: not self.widget.device_selection_loading and not self.widget.api.pending)
        self.assertEqual(picker.currentData(), 'text')
        self.assertTrue(picker.entries[picker.findData('text')][0].isChecked())
        self.assertEqual(panel.canvases['done'].pixels(1), bytes(300))
        self.assertTrue(panel.timer.isActive())

    def test_beam_white_controls_save_temperature_and_flash_settings(self):
        self.select_light_settings_fixture(model_id='yeelight-ylfwd0021')
        mode = self.widget.yeelight_reminder_mode
        mode.setCurrentIndex(mode.findData('front'))
        self.wait(lambda: self.hub.transport.records['one']['light_settings'].get('reminder_mode') == 'front' and not self.widget.writing and not self.widget.api.pending)
        self.widget.light_previews.select_state('needs_input')
        self.assertFalse(self.widget.yeelight_flash_panel.isHidden())
        self.assertEqual(self.widget.yeelight_temperature_controls['done'].minimum(), 2700)
        self.widget.yeelight_temperature_controls['done'].setValue(6500)
        self.wait(lambda: self.hub.transport.records['one']['light_settings']['front_state_temperature']['done'] == 6500 and not self.widget.writing and not self.widget.api.pending)
        mode.setCurrentIndex(mode.findData('back'))
        self.wait(lambda: self.hub.transport.records['one']['light_settings']['reminder_mode'] == 'back' and not self.widget.writing and not self.widget.api.pending)
        self.assertTrue(self.widget.yeelight_flash_panel.isHidden())
        self.assertEqual(self.widget.yeelight_brightness_controls['done'].value(), 100)

    def test_device_cards_keep_details_when_counts_change_and_thumbnail_is_clickable(self):
        self.widget.navigate(1)
        cards = self.widget.device_model_buttons
        first, bulb = cards["yeelight-yltd003"], cards["yeelight-yldp005"]
        title, detail = first.title.text(), first.detail.text()
        snapshot = {**self.widget.model, "devices": [
            {"device_id": "one", "hardware_model": "yeelight-yltd003", "online": True},
            {"device_id": "two", "hardware_model": "yeelight-yltd003", "online": True},
            {"device_id": "bulb", "hardware_model": "yeelight-yldp005", "online": True},
        ]}
        self.widget.render(snapshot)
        self.assertEqual(first.connection_count.text(), "在线 2 · 离线 0")
        self.assertEqual(bulb.connection_count.text(), "在线 1 · 离线 0")
        self.assertEqual(cards["yeelight-yldp005"].connection_count.text(), "在线 1 · 离线 0")
        snapshot["devices"][1]["online"] = False
        snapshot["devices"][2]["online"] = False
        self.widget.render(snapshot)
        self.assertEqual(first.connection_count.text(), "在线 1 · 离线 1")
        self.assertEqual(bulb.connection_count.text(), "在线 0 · 离线 1")
        snapshot["devices"][2]["online"] = True
        self.widget.render(snapshot)
        self.assertEqual(bulb.connection_count.text(), "在线 1 · 离线 0")
        self.assertEqual((first.title.text(), first.detail.text()), (title, detail))
        from PySide6.QtTest import QTest
        self.widget.show_device_catalog()
        point = first.thumbnail.mapTo(first, first.thumbnail.rect().center())
        first.setEnabled(False)
        QTest.mouseClick(first, Qt.MouseButton.LeftButton, pos=point)
        self.assertTrue(self.widget.device_selector.isVisible())
        first.setEnabled(True)
        QTest.mouseClick(first, Qt.MouseButton.LeftButton, pos=point)
        self.assertFalse(self.widget.device_selector.isVisible())
        self.assertEqual(self.widget.selected_device_model, "yeelight-yltd003")

    def test_device_light_uses_selected_identity_and_connection_buttons_share_style(self):
        self.connect_preview_fixture()
        self.widget.selected_device_id = "fixture-lamp"
        self.widget.selected_device_state = {"online": True}
        self.widget.selected_device_brightness = 50
        self.widget.device_selection_loading = False
        self.widget.update_busy()
        with patch.object(self.widget, "act") as act:
            self.widget.try_light_button.click()
            self.assertEqual(act.call_args.args[:2], ("/api/device/test-light", {"device_id": "fixture-lamp"}))
        self.widget.device_selection_loading = True
        with patch.object(self.widget, "act") as act:
            self.widget.test_current_device()
            act.assert_not_called()
        self.widget.device_selection_loading = False
        self.widget.device_test_started({"device_id": "fixture-lamp", "remaining_ms": 15000})
        self.assertEqual(self.widget.device_test_caption.text(), "试亮剩余 15 秒，当前状态为：工作中")
        self.widget.device_test_started({"device_id": "fixture-lamp", "remaining_ms": 10000})
        self.assertEqual(self.widget.device_test_caption.text(), "试亮剩余 10 秒，当前状态为：待输入")
        self.widget.device_test_deadlines["fixture-lamp"] -= 5
        self.widget.update_device_test_caption()
        self.assertEqual(self.widget.device_test_caption.text(), "试亮剩余 5 秒，当前状态为：已完成")
        self.widget.device_test_deadlines["fixture-lamp"] -= 5
        self.widget.update_device_test_caption()
        self.assertTrue(self.widget.device_test_caption.isHidden())

    def test_late_model_selection_does_not_override_navigation(self):
        self.widget.navigate(1)
        with patch.object(self.widget, "act") as act:
            self.widget.choose_device_model("yeelight-yldp005")
        act.assert_not_called()
        self.widget.navigate(1)
        self.assertTrue(self.widget.device_details.isHidden())
        self.widget.navigate(0)
        self.assertEqual(self.widget.breadcrumb.text(), "总览")
        self.widget.navigate(1)
        self.widget.open_device_details("yeelight-yldp005")
        self.widget.navigate(2)
        self.widget.render_device({"hardware_model": "yeelight-yldp005", "job": None})
        self.assertEqual(self.widget.breadcrumb.text(), "应用接入")

    def test_device_selector_does_not_scan_until_model_is_opened(self):
        self.widget.setting("hardware_notifications", True)
        self.wait(lambda: self.widget.device_reminders_enabled() and not self.widget.writing)
        with patch("aiflame.devices.yeelight.discover") as discover:
            self.widget.navigate(1)
            self.widget.device_model_buttons["yeelight-yltd003"].click()
            self.wait(lambda: not self.widget.device_selection_loading)
            discover.assert_not_called()
            self.assertTrue(self.widget.hardware_controls.isVisible())
            with patch.object(self.widget, "act") as act:
                self.widget.device_action("discover")
                self.assertEqual(act.call_args.args[1]["hardware_model"], "yeelight-yltd003")

    def test_all_sources_and_pages_without_loading_a_browser(self):
        self.assertEqual(len(self.widget.source_checks), 15)
        for source in ("claude-code", "deepseek-harness", "qoder", "gemini-cli", "opencode"):
            self.assertIn(source, self.widget.source_checks)
            self.assertTrue(self.widget.source_checks[source].isChecked())
        self.assertTrue({"local-script", "manual", "demo"}.isdisjoint(self.widget.source_checks))
        self.assertEqual(self.widget.tabs.count(), 6)
        self.assertFalse(any("QtWebEngine" in module for module in sys.modules))
        self.widget.tabs.setCurrentIndex(1)
        self.wait(lambda: not self.widget.api.pending)
        self.widget.tabs.setCurrentIndex(2)
        rows = list(self.widget.source_rows.values())
        self.assertTrue(all(not row.isHidden() for row in rows))
        self.assertEqual(set(self.widget.source_sections), {'web', 'cli'})
        self.assertFalse(hasattr(self.widget, "verification_source"))
        for source, row in self.widget.source_rows.items():
            mark = row.layout().itemAt(0).widget()
            pixmap = mark.icon().pixmap(30, 30) if isinstance(mark, QPushButton) else mark.pixmap()
            self.assertFalse(pixmap.isNull(), source)


    def test_plugin_management_has_bounded_longer_request_timeout(self):
        opener = Mock()
        response = Mock()
        response.read.return_value = b"{}"
        opener.open.return_value.__enter__ = Mock(return_value=response)
        opener.open.return_value.__exit__ = Mock(return_value=False)
        for path, timeout in (("/api/integrations/install", 35),
                              ("/api/integrations/uninstall", 35),
                              ("/api/reset", 35),
                              ("/api/device/delete", 35),
                              ("/api/status", 5)):
            result = self.widget.api._perform(opener, (1, path, None, False, None))
            self.assertEqual(result, (1, {}, None))
            self.assertEqual(opener.open.call_args.kwargs["timeout"], timeout)

    def test_manual_start_and_completion_use_real_api(self):
        responses = []
        self.widget.act("/api/test", {"state": "working", "seq": 1}, responses.append)
        self.wait(lambda: responses and not self.widget.writing)
        self.assertEqual(self.desired_state(), "working")
        self.widget.act("/api/test", {"state": "done", "seq": 2,
                                      "task_id": responses[0]["task_id"]})
        self.wait(lambda: not self.widget.writing)
        self.assertEqual(self.desired_state(), "done")




    def test_settings_and_rapid_source_toggles_are_preserved(self):
        self.widget.source_checks["codex"].setChecked(False)
        self.widget.source_checks["chatgpt-web"].setChecked(False)
        self.widget.setting("duration", 12)
        self.wait(
            lambda: (
                not self.widget.writing
                and not self.widget.actions
                and self.hub.engine.settings["duration"] == 12
            )
        )
        settings = self.hub.engine.settings
        self.assertFalse(settings["source_enabled"]["codex"])
        self.assertFalse(settings["source_enabled"]["chatgpt-web"])
        self.assertTrue(settings["source_enabled"]["deepseek-web"])
        self.wait(lambda: not self.widget.api.pending)
        self.assertTrue(self.widget.home_source_icons["codex"].isHidden())
        self.assertTrue(self.widget.home_source_icons["chatgpt-web"].isHidden())
        self.assertFalse(self.widget.home_source_icons["deepseek-web"].isHidden())
        self.assertTrue(self.widget.statusbar.isHidden())
        self.assertTrue(all(frame.isHidden() for frame, _, _ in self.widget.save_feedback.values()))

    def test_device_poll_updates_live_state_and_history_refreshes_on_navigation(self):
        with self.hub.lock:
            self.hub.engine.accept(make_event('manual', 'poll-test', 'working', 1), 'manual')
        self.widget.navigate(1)
        self.wait(lambda: not self.widget.api.pending)
        previous = deepcopy(self.widget.model['events'])
        self.assertTrue(previous)
        with self.hub.lock:
            result, _ = self.hub.engine.accept(make_event('manual', 'poll-test', 'done', 2), 'manual')
        with patch.object(self.widget.api, 'request', wraps=self.widget.api.request) as request:
            self.widget.poll()
            self.wait(lambda: not self.widget.api.pending)
            self.assertTrue(any(call.args[0] == '/api/status/live' for call in request.call_args_list))
        self.assertEqual(self.widget.model['events'], previous)
        self.assertEqual(self.widget.model['unfinished_count'], 0)
        self.widget.navigate(4)
        self.wait(lambda: not self.widget.api.pending)
        self.assertIn(result['event_id'], [event['id'] for event in self.widget.model['events']])

    def test_repeated_polling_releases_replies_and_reuses_unchanged_widgets(self):
        check = self.widget.source_checks["codex"]
        for _ in range(80):
            self.widget.poll(force=True)
            self.wait(lambda: not self.widget.api.pending)
        self.assertIs(self.widget.source_checks["codex"], check)
        self.assertFalse(self.widget.api.pending)
        self.assertEqual(self.widget.api.jobs.qsize(), 0)
        self.assertTrue(self.widget.api.worker.is_alive())

    def test_hidden_dashboard_stops_ui_polling_but_hub_keeps_working(self):
        self.widget.hide()
        self.wait(lambda: not self.widget.api.pending)
        self.assertFalse(self.widget.poll_timer.isActive())
        self.widget.poll()
        self.assertFalse(self.widget.api.pending)
        self.assertTrue(self.hub.thread.is_alive())
        self.widget.show()
        self.assertTrue(self.widget.poll_timer.isActive())
        self.wait(lambda: not self.widget.api.pending)

    def test_failed_write_unlocks_buttons_and_allows_the_next_action(self):
        self.widget.act("/api/settings", {"brightness": 100})
        self.widget.act("/api/settings", {"brightness": 11})
        self.wait(lambda: not self.widget.writing and not self.widget.api.pending)
        self.assertEqual(self.hub.engine.settings["brightness"], 11)
        self.assertTrue(self.widget.reset_button.isEnabled())
        self.assertTrue(self.widget.checks["hardware_notifications"].isEnabled())
        self.assertFalse(self.widget.try_light_button.isEnabled())

    def test_screen_preview_never_writes_settings_or_creates_events(self):
        before = self.engine_snapshot()
        self.assertIs(self.widget.preview_button.parentWidget(), self.widget.scene_frame)
        self.assertIs(self.widget.preview_caption.parentWidget(), self.widget.scene_frame)
        self.widget.preview_button.click()
        self.assertEqual(self.widget.flame.state, "working")
        self.assertEqual(self.widget.preview_button.text(), "退出演示")
        self.assertIn("15 秒", self.widget.preview_caption.text())
        self.widget.preview_started -= 5.5
        self.widget.advance_preview()
        self.assertEqual(self.widget.flame.state, "needs_input")
        self.assertIn("等待输入", self.widget.display_title.text())
        self.assertIn("10 秒", self.widget.preview_caption.text())
        self.widget.preview_started -= 5
        self.widget.advance_preview()
        self.assertEqual(self.widget.flame.state, "done")
        self.assertIn("5 秒", self.widget.preview_caption.text())
        self.widget.preview_started -= 5
        self.widget.advance_preview()
        self.assertIsNone(self.widget.preview_phase)
        self.assertFalse(self.widget.preview_timer.isActive())
        self.assertEqual(self.widget.preview_button.text(), "灯效演示")
        self.wait(lambda: not self.widget.flame.animation_timer.isActive())
        after = self.engine_snapshot()
        self.assertEqual(before["events"], after["events"])
        self.assertEqual(before["settings"], after["settings"])

    def flame_frame(self, state, milliseconds, reduced=False):
        flame = self.widget.flame
        flame.set_state(state, preview=True, reduced=reduced)
        with (
            patch.object(flame.clock, "elapsed", return_value=milliseconds),
            patch.object(flame.transition_clock, "elapsed", return_value=650),
        ):
            flame._animate()
            return self.capture_flame()

    def connect_preview_fixture(self):
        with self.hub.lock:
            self.hub.engine.update_settings({"hardware_notifications": True})
            self.hub.engine.device.update(online=True, port="wifi://192.0.2.1:55443", connected_at=1)
        self.widget.poll(force=True)
        self.wait(lambda: self.widget.device_reminders_enabled() and not self.widget.api.pending)

    def test_connection_status_icons_stay_wifi_across_lamp_hosts_and_disappear_offline(self):
        self.connect_preview_fixture()
        self.assertEqual(self.widget.device_badge.text(), "已连接 1 台设备")
        self.assertEqual(self.widget.device_summary.text(), "设备已连接")
        self.assertEqual(self.widget.device_connection_icon.property("connection"), "wifi")
        self.assertFalse(self.widget.device_connection_icon.isHidden())
        wifi_image = self.widget.device_connection_icon.pixmap().toImage()
        self.hub.engine.device["port"] = "wifi://192.0.2.2:55443"
        self.widget.poll(force=True)
        self.wait(lambda: not self.widget.api.pending)
        self.assertEqual(self.widget.device_badge.text(), "已连接 1 台设备")
        self.assertEqual(self.widget.device_connection_icon.property("connection"), "wifi")
        self.assertEqual(self.widget.device_status.toolTip(), "Wi-Fi 连接")
        self.assertEqual(wifi_image, self.widget.device_connection_icon.pixmap().toImage())
        self.assertEqual(self.widget.device_summary_icon.property("connection"), "wifi")
        self.hub.engine.device["online"] = False
        self.widget.poll(force=True)
        self.wait(lambda: not self.widget.api.pending)
        self.assertTrue(self.widget.device_connection_icon.isHidden())
        self.assertTrue(self.widget.device_summary_icon.isHidden())
        self.assertIsNone(self.widget.device_connection_icon.property("connection"))

    def test_removed_emergency_endpoints_cannot_change_reminders(self):
        for path in ("/api/stop", "/api/resume"):
            self.widget.act(path, {})
            self.wait(lambda: not self.widget.writing and not self.widget.api.pending)
            self.assertEqual(self.widget.message.text(), "接口不存在")
            self.assertFalse(self.hub.engine.muted())

    def test_overview_preview_uses_live_poll_and_restores_full_history_after_expiry(self):
        self.connect_preview_fixture()
        self.widget.start_preview()
        self.wait(lambda: self.hub.engine.preview and not self.widget.writing and not self.widget.api.pending)
        previous = deepcopy(self.widget.model['events'])
        with patch.object(self.widget.api, 'request', wraps=self.widget.api.request) as request:
            self.widget.poll(force=True)
            self.wait(lambda: not self.widget.api.pending)
            self.assertTrue(any(call.args[0] == '/api/status/live' for call in request.call_args_list))
            self.assertFalse(any(call.args[0] == '/api/status' for call in request.call_args_list))
        self.assertEqual(self.widget.model['events'], previous)
        with self.hub.lock:
            self.hub.engine.preview['started'] -= 16
        self.widget.poll()
        self.wait(lambda: self.widget.hardware_preview_id is None and not self.widget.api.pending)
        self.assertEqual(self.widget.poll_timer.interval(), 1500)
        with patch.object(self.widget.api, 'request', wraps=self.widget.api.request) as request:
            self.widget.poll()
            self.wait(lambda: not self.widget.api.pending)
            self.assertTrue(any(call.args[0] == '/api/status' for call in request.call_args_list))

    def test_live_overview_poll_interrupts_preview_when_real_task_arrives(self):
        self.connect_preview_fixture()
        self.widget.start_preview()
        self.wait(lambda: self.hub.engine.preview and not self.widget.writing and not self.widget.api.pending)
        with self.hub.lock:
            self.hub.engine.accept(make_event('local-script', 'preview-interrupt', 'working', 1), 'local-script')
        with patch.object(self.widget.api, 'request', wraps=self.widget.api.request) as request:
            self.widget.poll()
            self.wait(lambda: self.widget.hardware_preview_id is None and not self.widget.writing and not self.widget.api.pending)
            self.assertTrue(any(call.args[0] == '/api/status/live' for call in request.call_args_list))
        self.assertIsNone(self.hub.engine.preview)
        self.assertEqual(self.desired_state(), 'working')
        self.assertEqual(self.widget.model['unfinished_count'], 1)
        self.assertFalse(self.widget.flame.preview)

    def test_hardware_preview_screen_follows_shared_engine_timeline(self):
        self.connect_preview_fixture()
        self.app.processEvents()
        geometry = self.widget.scene_frame.geometry()
        self.widget.start_preview()
        self.wait(lambda: self.hub.engine.preview and not self.widget.writing and not self.widget.api.pending)
        self.assertTrue(self.widget.statusbar.isHidden())
        self.assertFalse(self.widget.message_timer.isActive())
        self.assertEqual(self.widget.scene_frame.geometry(), geometry)
        self.assertIsNone(self.widget.preview_phase)
        self.assertEqual(self.widget.flame.state, "working")
        self.assertEqual(self.widget.poll_timer.interval(), 150)
        with self.hub.lock:
            self.hub.engine.preview["started"] -= 5.2
            self.hub.engine.applied(self.hub.engine.desired(), {"result": "applied"})
        self.wait(lambda: self.widget.flame.state == "needs_input")
        self.assertEqual(self.widget.display_title.text(), "等待输入")
        with self.hub.lock:
            self.hub.engine.preview["started"] -= 5
            self.hub.engine.applied(self.hub.engine.desired(), {"result": "applied"})
        self.wait(lambda: self.widget.flame.state == "done")
        with self.hub.lock:
            self.hub.engine.preview["started"] -= 5
        self.wait(lambda: self.widget.hardware_preview_id is None)
        self.assertEqual(self.widget.flame.state, "idle")
        self.assertEqual(self.widget.poll_timer.interval(), 1500)
        self.assertEqual(self.widget.preview_button.text(), "灯效演示")
        self.assertTrue(self.widget.statusbar.isHidden())
        self.assertEqual(self.engine_snapshot()["events"], [])

    def test_hiding_before_preview_start_reply_cancels_pending_demo(self):
        self.connect_preview_fixture()
        self.widget.start_preview()
        self.widget.hide()
        self.wait(lambda: not self.widget.writing and not self.widget.api.pending)
        self.assertIsNone(self.hub.engine.preview)
        self.assertIsNone(self.widget.hardware_preview_id)
        self.assertFalse(self.widget.poll_timer.isActive())
        self.assertEqual(self.desired_state(), "idle")

    def test_hardware_preview_exit_and_mode_switch_cancel_only_demo(self):
        self.connect_preview_fixture()
        self.widget.start_preview()
        self.wait(lambda: self.hub.engine.preview and not self.widget.writing)
        self.widget.stop_preview()
        self.wait(lambda: not self.widget.writing and not self.widget.api.pending)
        self.assertTrue(self.widget.statusbar.isHidden())
        self.assertIsNone(self.hub.engine.preview)
        self.assertFalse(self.hub.engine.emergency)
        self.widget.start_preview()
        self.wait(lambda: self.hub.engine.preview and not self.widget.writing)
        self.widget.setting("hardware_notifications", False)
        self.wait(lambda: not self.widget.device_reminders_enabled() and not self.widget.writing)
        self.assertIsNone(self.hub.engine.preview)

    def test_offline_device_channel_uses_screen_demo_and_online_busy_still_rejects(self):
        self.connect_preview_fixture()
        self.hub.engine.device["online"] = False
        self.widget.poll(force=True)
        self.wait(lambda: not self.widget.api.pending)
        self.app.processEvents()
        # Finish the badge's offline layout change before measuring the demo.
        QCoreApplication.sendPostedEvents(None, QEvent.Type.LayoutRequest)
        self.app.processEvents()
        geometry = (self.widget.tabs.geometry(), self.widget.scene_frame.geometry())
        before = self.engine_snapshot()
        self.assertTrue(self.widget.preview_button.isEnabled())
        self.widget.start_preview()
        self.app.processEvents()
        self.assertEqual(self.widget.preview_phase, "working")
        self.assertTrue(self.widget.preview_timer.isActive())
        self.assertEqual(self.widget.preview_button.text(), "退出演示")
        self.assertTrue(self.widget.statusbar.isHidden())
        self.assertEqual((self.widget.tabs.geometry(), self.widget.scene_frame.geometry()), geometry)
        QToolTip.hideText()
        self.assertIsNone(self.widget.hardware_preview_id)
        self.assertIsNone(self.hub.engine.preview)
        after = self.engine_snapshot()
        self.assertEqual(after["settings"], before["settings"])
        self.assertEqual(after["events"], before["events"])
        self.widget.stop_preview()
        self.assertTrue(self.widget.preview_button.isEnabled())
        self.hub.engine.device["online"] = True
        self.widget.poll(force=True)
        self.wait(lambda: not self.widget.api.pending)
        with patch.object(self.hub.tests, "busy", return_value=True):
            self.widget.start_preview()
            self.wait(lambda: not self.widget.writing and not self.widget.api.pending)
        self.assertIsNone(self.hub.engine.preview)
        self.assertIsNone(self.widget.hardware_preview_id)
        self.assertEqual(self.widget.preview_button.text(), "灯效演示")

    def test_unfinished_task_rejects_preview_and_device_test_at_http_boundary(self):
        self.select_light_settings_fixture()
        with self.hub.lock:
            self.hub.transport.outputs["one"].device.update(online=True, connected_at=1, session_id="fixture")
            self.hub.engine.accept(make_event("local-script", "waiting", "needs_input"), "local-script")
            self.hub.engine.clear_prompts("suppressed")
        self.widget.poll(force=True)
        self.wait(lambda: self.widget.model.get("unfinished_count") == 1 and not self.widget.api.pending)
        self.assertEqual(self.widget.model["display"]["state"], "idle")
        self.assertFalse(self.widget.preview_button.isEnabled())
        self.assertFalse(self.widget.try_light_button.isEnabled())
        before = self.engine_snapshot()
        for path, body in (("/api/preview", {"action": "start", "id": "01234567-89ab-4cde-8f01-23456789abcd"}),
                           ("/api/device/test-light", {"device_id": "one"})):
            with self.subTest(path=path):
                self.widget.act(path, body)
                self.wait(lambda: not self.widget.writing and not self.widget.api.pending)
                self.assertIn("有任务尚未结束", self.widget.message.text())
                with self.hub.lock:
                    self.assertIsNone(self.hub.engine.preview)
                    self.assertIsNone(self.hub.transport.outputs["one"].light_test)
        after = self.engine_snapshot()
        self.assertEqual(after["settings"], before["settings"])
        self.assertEqual(after["tasks"], before["tasks"])

    def test_external_mode_change_cancels_local_preview(self):
        self.widget.start_preview()
        self.assertIsNotNone(self.widget.preview_phase)
        self.hub.update_settings({"hardware_notifications": True})
        self.widget.poll(force=True)
        self.wait(lambda: self.widget.device_reminders_enabled() and not self.widget.api.pending)
        self.assertIsNone(self.widget.preview_phase)
        self.assertEqual(self.widget.flame.state, "idle")

    def test_disconnected_service_resets_pending_preview_controls(self):
        self.connect_preview_fixture()

        def fail_request(path, callback=None, body=None, **options):
            self.widget.on_error("本地服务未连接，请重新打开 SeeEnd 后重试。")
            if options.get("failed"):
                options["failed"]()

        with patch.object(self.widget.api, "request", side_effect=fail_request):
            self.widget.start_preview()
        self.assertIsNone(self.widget.hardware_preview_id)
        self.assertEqual(self.widget.preview_button.text(), "灯效演示")
        self.assertTrue(self.widget.preview_caption.isHidden())
        self.assertEqual(self.widget.poll_timer.interval(), 1500)

    def capture_flame(self):
        flame = self.widget.flame
        # Render in its actual scene: grabbing a transparent child alone
        # omits the parent's dark background and gives false luminance.
        bounds = QRect(flame.mapTo(self.widget, QPoint()), flame.size())
        image = (
            self.widget.grab()
            .toImage()
            .copy(bounds)
            .convertToFormat(QImage.Format.Format_RGBA8888)
        )
        pixels = bytes(image.constBits())
        # Compare rendered luminance, not state names or configured palette values.
        return bytes(
            (r * 54 + g * 183 + b * 19) // 256
            for r, g, b in zip(pixels[0::4], pixels[1::4], pixels[2::4])
        )

    def test_rendered_completion_is_distinct_from_every_breathing_phase(self):
        done = self.flame_frame("done", 0)
        done_bright = sum(value > 120 for value in done)
        for milliseconds in (0, 1000, 2000, 3000):
            working = self.flame_frame("working", milliseconds)
            self.assertGreater(done_bright, 2 * sum(value > 120 for value in working))
            self.assertGreater(
                sum(abs(a - b) > 30 for a, b in zip(done, working)), 1000
            )

    def test_preview_animates_but_reduced_and_settled_static_views_do_not_run_timers(
        self,
    ):
        start = self.flame_frame("working", 0)
        breathing = self.flame_frame("working", 2000)
        self.assertNotEqual(start, breathing)
        self.assertTrue(self.widget.flame.animation_timer.isActive())
        done = self.flame_frame("done", 0)
        self.assertNotEqual(done, self.flame_frame("done", 200))
        for state in ("working", "done"):
            frame = self.flame_frame(state, 0, reduced=True)
            self.assertEqual(frame, self.flame_frame(state, 2000, reduced=True))
            self.assertFalse(self.widget.flame.animation_timer.isActive())
        self.assertNotEqual(
            self.flame_frame("working", 0, reduced=True),
            self.flame_frame("done", 0, reduced=True),
        )
        self.widget.flame.set_state("working")
        self.wait(lambda: not self.widget.flame.animation_timer.isActive())

    def test_all_state_transitions_start_at_current_frame_and_settle(self):
        flame = self.widget.flame
        for source in ("idle", "working", "done", "needs_input"):
            for target in ("idle", "working", "done", "needs_input"):
                if source == target:
                    continue
                with self.subTest(source=source, target=target):
                    flame.set_state(source)
                    with patch.object(
                        flame.transition_clock, "elapsed", return_value=650
                    ):
                        flame._animate()
                    before = self.capture_flame()
                    flame.set_state(target)
                    self.assertTrue(flame.animation_timer.isActive())
                    frames = []
                    for ms in (0, 325, 600, 650):
                        with patch.object(
                            flame.transition_clock, "elapsed", return_value=ms
                        ):
                            frames.append(self.capture_flame())
                    self.assertEqual(before, frames[0])
                    self.assertNotEqual(frames[0], frames[1])
                    self.assertNotEqual(frames[1], frames[3])
                    # The final 50 ms must be much gentler than the full change.
                    total = sum(abs(a - b) for a, b in zip(frames[0], frames[3]))
                    tail = sum(abs(a - b) for a, b in zip(frames[2], frames[3]))
                    self.assertLess(tail, total * 0.1)
                    with patch.object(
                        flame.transition_clock, "elapsed", return_value=650
                    ):
                        flame._animate()
                    self.assertFalse(flame.animation_timer.isActive())
                    self.assertEqual(frames[3], self.capture_flame())

    def test_interrupted_preview_and_exit_continue_from_the_current_frame(self):
        flame = self.widget.flame
        self.flame_frame("working", 2000)
        flame.set_state("done", preview=True)
        with (
            patch.object(flame.clock, "elapsed", return_value=325),
            patch.object(flame.transition_clock, "elapsed", return_value=325),
        ):
            middle = self.capture_flame()
            with patch.object(flame.transition_clock, "start") as restart:
                flame.set_state("done", preview=True)
                restart.assert_not_called()
            flame.set_state("needs_input", preview=True)
        with patch.object(flame.transition_clock, "elapsed", return_value=0):
            self.assertEqual(middle, self.capture_flame())
        with (
            patch.object(flame.clock, "elapsed", return_value=200),
            patch.object(flame.transition_clock, "elapsed", return_value=200),
        ):
            middle = self.capture_flame()
            flame.set_state("idle")
        with patch.object(flame.transition_clock, "elapsed", return_value=0):
            self.assertEqual(middle, self.capture_flame())
        self.assertTrue(flame.animation_timer.isActive())
        flame.hide()
        self.assertFalse(flame.animation_timer.isActive())
        flame.show()
        self.assertFalse(flame.animation_timer.isActive())

    def test_preview_respects_reduced_motion_and_repaint_timer_stops_on_hide(self):
        self.widget.setting("reduced", True)
        self.wait(lambda: not self.widget.writing and not self.widget.api.pending)
        self.widget.start_preview()
        self.assertTrue(self.widget.flame.reduced)
        self.assertFalse(self.widget.flame.animation_timer.isActive())
        self.widget.stop_preview()
        self.assertTrue(self.widget.flame.reduced)
        self.assertFalse(self.widget.flame.animation_timer.isActive())
        self.widget.start_preview()
        self.widget.setting("reduced", False)
        self.wait(lambda: not self.widget.writing and not self.widget.api.pending)
        self.assertTrue(self.widget.flame.animation_timer.isActive())
        self.widget.navigate(1)
        self.assertFalse(self.widget.flame.animation_timer.isActive())
        self.widget.navigate(0)
        self.widget.start_preview()
        self.widget.hide()
        self.assertFalse(self.widget.flame.animation_timer.isActive())

    def test_preview_stops_when_hidden_or_navigating_away(self):
        self.widget.start_preview()
        self.widget.navigate(3)
        self.assertIsNone(self.widget.preview_phase)
        self.assertTrue(self.widget.nav_buttons[3].isChecked())
        self.widget.navigate(0)
        self.widget.start_preview()
        self.widget.hide()
        self.assertFalse(self.widget.preview_timer.isActive())

    def test_accessibility_toggle_navigates_without_mouse_click_signal(self):
        self.widget.nav_buttons[1].setChecked(True)
        self.assertEqual(self.widget.tabs.currentIndex(), 1)
        self.assertIn("我的设备", self.widget.breadcrumb.text())
        self.widget.nav_buttons[2].setChecked(True)
        self.assertEqual(self.widget.tabs.currentIndex(), 2)
        self.assertFalse(self.widget.nav_buttons[1].isChecked())

    def test_zero_brightness_explains_and_blocks_physical_test(self):
        self.select_light_settings_fixture(online=False)
        for control in self.widget.yeelight_brightness_controls.values():
            control.setValue(0)
        self.wait(lambda: self.hub.transport.records["one"]["light_settings"]["brightness"] == 0 and not self.widget.writing and not self.widget.api.pending)
        self.assertEqual(self.widget.notice_kind, "brightness")
        self.assertIn("0%", self.widget.notice_text.text())
        before = len(self.engine_snapshot()["events"])
        with patch.object(self.widget, "detail_device", return_value={"online": True}), \
             patch.object(self.widget, "act") as request:
            self.widget.test_current_device()
            request.assert_not_called()
        self.assertIn("0%", self.widget.message.text())
        self.assertEqual(len(self.engine_snapshot()["events"]), before)
        self.widget.resolve_notice()
        self.assertEqual(self.widget.tabs.currentIndex(), 1)
        self.assertTrue(self.widget.preview_button.isEnabled())  # Offline hardware cannot block the screen demo.
        with self.hub.lock:
            self.hub.transport.outputs["one"].device.update(online=True, connected_at=1, session_id="fixture")
        self.widget.poll(force=True)
        self.wait(lambda: self.widget.model["device"]["online"] and not self.widget.api.pending)
        self.assertFalse(self.widget.preview_button.isEnabled())

    def select_light_settings_fixture(self, count=1, model_id='yeelight-yltd003', online=True):
        with self.hub.lock:
            for device_id in ("one", "two")[:count]:
                record = {"device_id": device_id, "hardware_model": model_id,
                          "connection_paused": True, "light_settings": {"brightness": 20, "reduced": False}}
                self.hub.transport.records[device_id] = record
                self.hub.transport.connection(device_id, record)
                self.hub.transport.outputs[device_id].device.update(online=online)
            self.hub.engine.update_settings({"hardware_notifications": True})
        self.widget.poll(force=True)
        self.wait(lambda: self.widget.device_reminders_enabled() and not self.widget.api.pending)
        self.widget.navigate(1)
        self.widget.choose_device_model(model_id)
        self.wait(lambda: self.widget.selected_device_id == "one" and not self.widget.device_selection_loading and not self.widget.api.pending)


    def test_front_white_mode_controls_save_separately_and_reset_only_current_mode(self):
        self.select_light_settings_fixture(2)
        mode = self.widget.yeelight_reminder_mode
        self.assertTrue(self.widget.yeelight_mode_panel.isVisible())
        self.assertTrue(self.widget.yeelight_mode_panel.rect().contains(mode.geometry()))
        self.assertTrue(all(control.isHidden() for control in self.widget.yeelight_temperature_controls.values()))
        title = self.widget.yeelight_light_title.text()
        state_titles = {state: label.text() for state, label in self.widget.yeelight_state_titles.items()}
        mode.setCurrentIndex(mode.findData('front'))
        self.wait(lambda: self.hub.transport.records['one']['light_settings'].get('reminder_mode') == 'front'
                  and not self.widget.writing and not self.widget.api.pending)
        self.assertEqual(self.widget.yeelight_brightness_controls['working'].value(), 50)
        self.assertEqual(self.widget.yeelight_brightness_controls['done'].value(), 100)
        self.assertEqual(self.widget.yeelight_brightness_controls['needs_input'].value(), 50)
        self.assertEqual(self.widget.yeelight_temperature_controls['needs_input'].value(), 2700)
        self.assertEqual(self.widget.yeelight_light_title.text(), "灯效设置")
        self.assertEqual({state: label.text() for state, label in self.widget.yeelight_state_titles.items()}, state_titles)
        self.widget.light_previews.select_state('done')
        self.assertTrue(self.widget.yeelight_temperature_controls['done'].isVisible())
        self.assertFalse(self.widget.yeelight_temperature_controls['working'].isVisible())
        self.assertTrue(all(control.isHidden() for control in self.widget.yeelight_color_choices.values()))
        self.assertEqual(self.widget.yeelight_state_titles['needs_input'].text(), '等待输入')
        swatch = self.widget.yeelight_temperature_swatches['done']
        before_color = swatch.grab().toImage().pixelColor(10, 10)
        self.widget.yeelight_temperature_controls['done'].setValue(5748)
        self.assertEqual(self.widget.yeelight_temperature_controls['done'].value(), 5700)
        self.assertEqual(swatch.kelvin, 5700)
        self.assertNotEqual(swatch.grab().toImage().pixelColor(10, 10), before_color)
        self.widget.yeelight_brightness_controls['working'].setValue(0)
        self.widget.yeelight_brightness_controls['done'].setValue(95)
        self.wait(lambda: self.hub.transport.records['one']['light_settings']['front_state_temperature']['done'] == 5700
                  and self.hub.transport.records['one']['light_settings']['front_state_brightness']['done'] == 95
                  and not self.widget.writing and not self.widget.api.pending)
        self.assertTrue(self.widget.statusbar.isHidden())
        saved = self.hub.transport.records['one']['light_settings']
        self.assertEqual(saved['front_state_brightness']['working'], 0)
        self.assertEqual(saved['state_brightness']['working'], 50)
        self.assertNotIn('reminder_mode', self.hub.transport.records['two']['light_settings'])
        mode.setCurrentIndex(mode.findData('back'))
        self.wait(lambda: self.hub.transport.records['one']['light_settings']['reminder_mode'] == 'back'
                  and not self.widget.writing and not self.widget.api.pending)
        self.assertEqual(self.widget.yeelight_brightness_controls['working'].value(), 50)
        for reset in self.widget.yeelight_state_resets.values():
            reset.click()
            self.wait(lambda: not self.widget.writing and not self.widget.api.pending)
        self.wait(lambda: not self.widget.writing and not self.widget.api.pending)
        self.assertEqual(self.hub.transport.records['one']['light_settings']['front_state_temperature']['done'], 5700)
        mode.setCurrentIndex(mode.findData('front'))
        self.wait(lambda: self.hub.transport.records['one']['light_settings']['reminder_mode'] == 'front'
                  and not self.widget.writing and not self.widget.api.pending)
        self.assertEqual(self.widget.yeelight_temperature_controls['done'].value(), 5700)
        self.assertEqual(self.widget.yeelight_brightness_controls['working'].value(), 0)
        for reset in self.widget.yeelight_state_resets.values():
            reset.click()
            self.wait(lambda: not self.widget.writing and not self.widget.api.pending)
        self.wait(lambda: self.hub.transport.records['one']['light_settings']['front_state_temperature']['done'] == 4000
                  and not self.widget.writing and not self.widget.api.pending)
        self.assertEqual(self.widget.yeelight_brightness_controls['working'].value(), 50)
        self.assertEqual(self.widget.yeelight_brightness_controls['done'].value(), 100)
        self.widget.choose_device_model('yeelight-yldp005')
        self.wait(lambda: not self.widget.device_selection_loading and not self.widget.api.pending)
        self.assertFalse(self.widget.device_light_card.isVisible())
        self.assertTrue(all(control.isHidden() for control in self.widget.yeelight_temperature_controls.values()))

    def test_bulb_white_mode_saves_temperature_and_keeps_layout_stable(self):
        from PySide6.QtTest import QTest
        self.select_light_settings_fixture(model_id='yeelight-yldp005')
        mode = self.widget.yeelight_reminder_mode
        self.assertEqual([mode.itemText(i) for i in range(mode.count())], ['彩灯', '白灯'])
        for width in (1180, 790):
            self.widget.resize(width, 850)
            QTest.qWait(100)
            before = {state: control.geometry() for state, control in self.widget.yeelight_brightness_controls.items()}
            title = self.widget.yeelight_light_title.text()
            mode.setCurrentIndex(mode.findData('front'))
            self.wait(lambda: self.hub.transport.records['one']['light_settings'].get('reminder_mode') == 'front'
                      and not self.widget.writing and not self.widget.api.pending)
            self.assertEqual(before, {state: control.geometry() for state, control in self.widget.yeelight_brightness_controls.items()})
            self.assertEqual(self.widget.yeelight_light_title.text(), "灯效设置")
            self.widget.yeelight_temperature_controls['done'].setValue(1700)
            self.wait(lambda: self.hub.transport.records['one']['light_settings']['front_state_temperature']['done'] == 1700
                      and not self.widget.writing and not self.widget.api.pending)
            mode.setCurrentIndex(mode.findData('back'))
            self.wait(lambda: self.hub.transport.records['one']['light_settings']['reminder_mode'] == 'back'
                      and not self.widget.writing and not self.widget.api.pending)
            self.assertEqual(before, {state: control.geometry() for state, control in self.widget.yeelight_brightness_controls.items()})
            self.assertEqual(self.widget.yeelight_brightness_controls['done'].value(), 100)

    def test_pending_brightness_save_stays_with_original_device_on_selection_change(self):
        self.select_light_settings_fixture(2)
        self.widget.yeelight_brightness_controls["working"].setValue(7)
        self.widget.device_instances.setCurrentIndex(self.widget.device_instances.findData("two"))
        self.wait(lambda: self.hub.transport.records["one"]["light_settings"].get("state_brightness", {}).get("working") == 7 and not self.widget.device_selection_loading and not self.widget.writing and not self.widget.api.pending)
        self.assertEqual(self.widget.selected_device_id, "two")
        self.assertEqual(self.hub.transport.records["two"]["light_settings"]["brightness"], 20)
        for control in self.widget.yeelight_brightness_controls.values():
            control.setValue(0)
        self.wait(lambda: self.hub.transport.records["two"]["light_settings"]["brightness"] == 0 and not self.widget.api.pending)
        self.assertFalse(self.widget.brightness_off())

    def test_white_flash_controls_save_disable_and_reset_only_white_mode(self):
        self.select_light_settings_fixture(model_id='yeelight-yldp005')
        mode = self.widget.yeelight_reminder_mode
        self.assertTrue(self.widget.yeelight_flash_panel.isHidden())
        mode.setCurrentIndex(mode.findData('front'))
        self.wait(lambda: not self.widget.writing and not self.widget.api.pending)
        self.widget.light_previews.select_state('needs_input')
        self.assertTrue(self.widget.yeelight_flash_panel.isVisible())
        self.widget.yeelight_flash_controls['on_ms'].setValue(1548)
        self.widget.yeelight_flash_controls['off_ms'].setValue(2500)
        self.wait(lambda: self.hub.transport.records['one']['light_settings']['front_flash'] ==
                  {'enabled': True, 'on_ms': 1500, 'off_ms': 2500}
                  and not self.widget.writing and not self.widget.api.pending)
        self.widget.yeelight_flash_enabled.setChecked(False)
        self.wait(lambda: not self.hub.transport.records['one']['light_settings']['front_flash']['enabled']
                  and not self.widget.writing and not self.widget.api.pending)
        self.assertTrue(all(not control.isEnabled() for control in self.widget.yeelight_flash_controls.values()))
        mode.setCurrentIndex(mode.findData('back'))
        self.wait(lambda: self.hub.transport.records['one']['light_settings']['reminder_mode'] == 'back'
                  and not self.widget.writing and not self.widget.api.pending)
        for reset in self.widget.yeelight_state_resets.values():
            reset.click()
            self.wait(lambda: not self.widget.writing and not self.widget.api.pending)
        self.wait(lambda: not self.widget.writing and not self.widget.api.pending)
        self.assertFalse(self.hub.transport.records['one']['light_settings']['front_flash']['enabled'])
        mode.setCurrentIndex(mode.findData('front'))
        self.wait(lambda: self.hub.transport.records['one']['light_settings']['reminder_mode'] == 'front'
                  and not self.widget.writing and not self.widget.api.pending)
        for reset in self.widget.yeelight_state_resets.values():
            reset.click()
            self.wait(lambda: not self.widget.writing and not self.widget.api.pending)
        self.wait(lambda: self.hub.transport.records['one']['light_settings']['front_flash'] ==
                  {'enabled': True, 'on_ms': 600, 'off_ms': 600}
                  and not self.widget.writing and not self.widget.api.pending)
        self.assertEqual(self.widget.yeelight_flash_controls['on_ms'].value(), 600)
        self.assertEqual(self.widget.yeelight_flash_controls['off_ms'].value(), 600)
        self.assertEqual(self.widget.yeelight_flash_labels['on_ms'].text(), '0.6 秒')
        self.assertEqual(self.widget.yeelight_flash_labels['off_ms'].text(), '0.6 秒')
        controls = list(self.widget.yeelight_flash_controls.values())
        self.assertEqual([(control.minimum(), control.maximum()) for control in controls], [(100, 10000), (100, 10000)])
        self.assertEqual(controls[0].geometry().x(), controls[1].geometry().x())
        self.assertEqual(controls[0].width(), controls[1].width())
        self.assertTrue(all(control.isEnabled() for control in self.widget.yeelight_flash_controls.values()))
        self.widget.yeelight_flash_controls['off_ms'].setValue(100)
        self.assertEqual(self.widget.yeelight_flash_controls['off_ms'].value(), 600)
        self.assertEqual(self.widget.yeelight_flash_cycle_label.text(), '亮 0.6 秒，灭 0.6 秒，每 1.2 秒循环。')
        self.wait(lambda: self.hub.transport.records['one']['light_settings']['front_flash']['off_ms'] == 600
                  and not self.widget.writing and not self.widget.api.pending)
        self.widget.yeelight_flash_controls['on_ms'].setValue(1100)
        self.widget.yeelight_flash_controls['off_ms'].setValue(100)
        self.wait(lambda: self.hub.transport.records['one']['light_settings']['front_flash'] ==
                  {'enabled': True, 'on_ms': 1100, 'off_ms': 100}
                  and not self.widget.writing and not self.widget.api.pending)
        self.assertEqual(self.widget.yeelight_flash_cycle_label.text(), '亮 1.1 秒，灭 0.1 秒，每 1.2 秒循环。')




    def test_activity_filters_tests_and_exports_only_selected_metadata(self):
        self.widget.act("/api/test", {"state": "done", "seq": 1})
        self.wait(lambda: not self.widget.writing and not self.widget.api.pending)
        self.widget.navigate(4)
        self.assertTrue(self.widget.event_detail_box.isHidden())
        self.widget.history_filter.setCurrentIndex(1)
        self.assertEqual(self.widget.history.rowCount(), 0)
        self.widget.history_filter.setCurrentIndex(2)
        self.assertEqual(self.widget.history.rowCount(), 1)
        self.widget.history.selectRow(0)
        self.assertFalse(self.widget.event_detail_box.isHidden())
        self.widget.history.clearSelection()
        self.assertTrue(self.widget.event_detail_box.isHidden())
        self.widget.history.selectRow(0)
        self.assertIn("本地状态已更新", self.activity_detail_text())
        self.widget.history_search.setText("没有匹配")
        self.assertEqual(self.widget.history.rowCount(), 0)
        self.assertTrue(self.widget.event_detail_box.isHidden())
        self.widget.history_search.clear()
        path = str(Path(self.temp.name) / "events.csv")
        with patch(
            "aiflame.desktop.widgets.QFileDialog.getSaveFileName",
            return_value=(path, ""),
        ):
            self.widget.export_history()
        exported = Path(path).read_text(encoding="utf-8-sig")
        self.assertIn("手动试亮 · 测试", exported)
        self.assertNotIn("test-token", exported)
        self.assertNotIn("task_id", exported)



    def test_disconnected_service_disables_writes_and_offers_retry(self):
        self.connect_preview_fixture()
        self.widget.on_error("本地服务未连接，请重新打开 SeeEnd 后重试。")
        self.assertEqual(self.widget.device_status.toolTip(), "")
        self.assertTrue(self.widget.device_connection_icon.isHidden())
        self.assertTrue(
            all(not control.isEnabled() for control in self.widget.yeelight_brightness_controls.values())
        )
        self.assertFalse(self.widget.retry_button.isHidden())
        self.assertFalse(self.widget.statusbar.isHidden())
        self.assertTrue(self.widget.notice_box.isHidden())
        self.assertTrue(all(not check.isEnabled() for check in self.widget.source_checks.values()))
        self.widget.poll(force=True)
        self.wait(lambda: self.widget.ready and not self.widget.api.pending)
        self.assertTrue(all(not control.isEnabled() for control in self.widget.yeelight_brightness_controls.values()))
        self.assertTrue(self.widget.duration.isEnabled())

    def test_real_trial_without_hardware_and_transition_to_device(self):
        self.assertEqual(self.engine_snapshot()["events"], [])
        with self.hub.lock:
            self.hub.engine.accept(
                make_event("local-script", "real-trial", "working"), "local-script"
            )
        self.widget.poll(force=True)
        self.wait(lambda: self.widget.flame.state == "working")
        self.assertEqual(self.widget.device_badge.text(), "设备提醒已关闭")
        self.assertEqual(self.widget.model["verification"]["local-script"]["status"], "working")
        self.assertTrue(self.widget.flame.live)
        self.assertFalse(self.widget.flame.preview)
        with self.hub.lock:
            self.hub.engine.accept(
                make_event("local-script", "real-trial", "done", 2), "local-script"
            )
        self.widget.poll(force=True)
        self.wait(lambda: self.widget.flame.state == "done")
        self.assertEqual(self.widget.model["verification"]["local-script"]["status"], "verified")
        self.assertEqual(self.widget.model["events"][0]["status"], "screen")
        self.assertIsNone(self.widget.model["last_ack"])
        self.hub.update_settings({"hardware_notifications": True})
        with self.hub.lock:
            self.hub.engine.device["online"] = True
        self.widget.poll(force=True)
        self.wait(lambda: self.widget.model["device"]["online"])
        self.assertEqual(self.widget.flame.state, "idle")
        self.assertFalse(self.widget.try_light_button.isEnabled())  # No physical device selected.
        self.assertEqual(self.widget.model["verification"]["local-script"]["status"], "verified")

    def test_overview_navigation_in_both_modes_and_every_device_state(self):
        snapshot = self.widget.model
        for enabled, paired, online in ((False, False, False), (True, False, False), (True, True, False), (True, True, True)):
            with self.subTest(enabled=enabled, paired=paired, online=online):
                self.widget.render({
                    **snapshot,
                    "hardware_paired": paired,
                    "settings": {**snapshot["settings"], "hardware_notifications": enabled},
                    "device": {**snapshot["device"], "online": online},
                })
                for shortcut, page in ((self.widget.home_sources_button, 2), (self.widget.recent_more, 4)):
                    self.widget.navigate(0)
                    self.assertFalse(shortcut.isHidden())
                    shortcut.click()
                    self.assertEqual(self.widget.tabs.currentIndex(), page)
                overview_buttons = self.widget.tabs.widget(0).findChildren(QPushButton)
                self.assertFalse(any(button.accessibleName() in ("活动记录", "连接设备") for button in overview_buttons))
                self.assertEqual(self.widget.hardware_notice.isHidden(), not enabled or online)

    def test_actual_mode_clicks_do_not_show_save_banner_or_resize_content(self):
        self.wait(lambda: not self.widget.writing and not self.widget.api.pending)
        self.app.processEvents()
        expected = (self.widget.tabs.geometry(), self.widget.scene_frame.geometry())
        observed = []
        timer = QTimer()
        timer.setInterval(5)
        timer.timeout.connect(lambda: observed.append((self.widget.tabs.geometry(), self.widget.scene_frame.geometry())))
        timer.start()
        try:
            for enabled in (True, False, True, False):
                self.widget.home_channel_checks["hardware_notifications"].click()
                self.wait(lambda: not self.widget.writing and self.widget.model["settings"]["hardware_notifications"] == enabled)
                self.app.processEvents()
                self.assertTrue(self.widget.statusbar.isHidden())
                self.assertFalse(self.widget.message_timer.isActive())
                self.assertEqual((self.widget.tabs.geometry(), self.widget.scene_frame.geometry()), expected)
            self.assertTrue(observed)
            self.assertTrue(all(position == expected for position in observed))
        finally:
            timer.stop()

    def test_mode_and_connection_changes_do_not_shift_overview(self):
        self.widget.poll_timer.stop()
        self.wait(lambda: not self.widget.api.pending)
        original = deepcopy(self.widget.model)
        for width, height in ((1180, 850), (1440, 960), (920, 680)):
            self.widget.resize(width, height)
            # Container resize and icon wrapping are queued native layout work.
            for _ in range(5):
                self.app.processEvents()
            expected = None
            for enabled, online, connecting in (
                (False, False, False), (True, False, False),
                (True, False, True), (True, True, False),
                (False, False, False),
            ):
                with self.subTest(width=width, enabled=enabled, online=online, connecting=connecting):
                    snapshot = deepcopy(original)
                    snapshot["settings"]["hardware_notifications"] = enabled
                    snapshot["device"].update(online=online, connecting=connecting)
                    self.widget.render(snapshot)
                    for _ in range(5):
                        self.app.processEvents()
                    positions = (
                        self.widget.tabs.geometry(), self.widget.scene_frame.geometry(),
                        self.widget.home_channel_checks["hardware_notifications"].parentWidget().geometry(),
                        tuple(button.geometry() for button in self.widget.nav_buttons),
                    )
                    if expected is None:
                        expected = positions
                    self.assertEqual(positions, expected)
                    self.assertEqual(self.widget.hardware_notice.isHidden(), not enabled or online or connecting)

    def test_overview_groups_enabled_sources_and_keeps_card_heights_stable(self):
        self.widget.poll_timer.stop()
        self.wait(lambda: not self.widget.api.pending)
        original = deepcopy(self.widget.model)
        enabled = {'codex', 'chatgpt-web', 'local-script'}
        for width in (1180, 1050, 920):
            self.widget.resize(width, 850)
            card_heights = set()
            for count in (0, 1, 4, 5):
                snapshot = deepcopy(original)
                for source in snapshot['sources']:
                    source['enabled'] = source['id'] in enabled
                snapshot['events'] = [
                    {'id': str(index), 'source': 'chatgpt-web', 'state': 'done', 'reason': 'completed',
                     'created': time.time(), 'status': 'applied'} for index in range(count)]
                snapshot['task_events'] = snapshot['events']
                self.widget.render(snapshot)
                for _ in range(5):
                    self.app.processEvents()
                self.assertGreaterEqual(self.widget.scene_frame.height(), 380)
                scene_bottom = self.widget.scene_frame.mapTo(self.widget, self.widget.scene_frame.rect().bottomLeft()).y()
                recent_bottom = self.widget.recent_frame.mapTo(self.widget, self.widget.recent_frame.rect().bottomLeft()).y()
                apps_top = self.widget.home_sources_frame.mapTo(self.widget, self.widget.home_sources_frame.rect().topLeft()).y()
                apps_bottom = self.widget.home_sources_frame.mapTo(self.widget, self.widget.home_sources_frame.rect().bottomLeft()).y()
                recent_top = self.widget.recent_frame.mapTo(self.widget, self.widget.recent_frame.rect().topLeft()).y()
                self.assertGreater(recent_top, apps_bottom)
                if width >= 1050:
                    self.assertEqual(scene_bottom, recent_bottom)
                    scene_top = self.widget.scene_frame.mapTo(self.widget, self.widget.scene_frame.rect().topLeft()).y()
                    self.assertEqual(scene_top, apps_top)
                else:
                    self.assertGreater(apps_top, scene_bottom)
                self.assertGreaterEqual(self.widget.recent_frame.height(), 140)
                card_heights.add((self.widget.scene_frame.height(), self.widget.recent_frame.height(), self.widget.home_sources_frame.height()))
                self.assertEqual(self.widget.recent_empty.isHidden(), bool(count))
                visible_rows = [row for row in self.widget.recent_row_frames if not row.isHidden()]
                self.assertLessEqual(len(visible_rows), count)
                self.assertEqual(bool(visible_rows), bool(count))
                for row in visible_rows:
                    bounds = row.rect().translated(row.mapTo(self.widget.recent_list_body, row.rect().topLeft()))
                    self.assertTrue(self.widget.recent_list_body.rect().contains(bounds))
                for source, button in self.widget.home_source_icons.items():
                    self.assertEqual(button.isHidden(), source not in enabled)
                rows = [group for group, _sources in self.widget.home_source_groups.values()]
                self.assertTrue(all(not group.isHidden() for group in rows))
                self.assertEqual(len({group.y() for group in rows}), 2)
                self.assertNotIn("本地 API 与测试来源", self.widget.home_source_groups)
                self.assertTrue({"local-script", "manual", "demo"}.isdisjoint(self.widget.home_source_icons))
            self.assertEqual(len(card_heights), 1)
        snapshot = deepcopy(original)
        for source in snapshot['sources']:
            source['enabled'] = False
        self.widget.render(snapshot)
        self.assertTrue(self.widget.home_sources_row.isHidden())
        self.assertFalse(self.widget.home_sources_empty.isHidden())


    def test_connecting_never_flashes_offline_or_claims_online(self):
        snapshot = self.widget.model
        for connecting, online in ((True, False), (False, False), (False, True)):
            self.widget.render({**snapshot,
                "settings": {**snapshot["settings"], "hardware_notifications": True},
                "device": {"online": online, "connecting": connecting},
            })
            self.assertEqual(self.widget.hardware_notice.isHidden(), connecting or online)
            if connecting:
                self.assertEqual(self.widget.device_badge.text(), "正在连接设备")
                self.assertEqual(self.widget.device_status.property("tone"), "neutral")
                self.assertEqual(self.widget.poll_timer.interval(), 150)
                self.assertFalse(self.widget.try_light_button.isEnabled())
            else:
                self.assertEqual(self.widget.poll_timer.interval(), 1500)

    def test_overview_scene_controls_fit_at_supported_window_sizes(self):
        for width, height in ((920, 680), (1180, 850), (1440, 960)):
            with self.subTest(width=width, height=height):
                self.widget.resize(width, height)
                self.app.processEvents()
                scene = self.widget.scene_frame
                controls = (
                    self.widget.flame, self.widget.display_title,
                    self.widget.display_hint,
                    self.widget.preview_button,
                )
                controls = tuple(control for control in controls if not control.isHidden())
                for control in controls:
                    self.assertTrue(scene.rect().contains(control.geometry()))
                self.assertGreater(self.widget.preview_button.geometry().center().x(), scene.rect().center().x())
                self.assertLessEqual(scene.rect().bottom() - self.widget.preview_button.geometry().bottom(), 24)
                for before, after in zip(controls, controls[1:]):
                    self.assertLess(before.geometry().bottom(), after.geometry().top())
                self.assertGreaterEqual(self.widget.flame.height(), 190)
                self.assertEqual(
                    self.widget.tabs.widget(0).horizontalScrollBar().maximum(), 0
                )

    def reset_dialog_fixture(self, confirmed):
        dialog = Mock()
        reset, cancel = QPushButton(), QPushButton()
        dialog.addButton.side_effect = [reset, cancel]
        dialog.clickedButton.return_value = reset if confirmed else cancel
        return dialog, cancel

    def test_app_reset_cancel_is_default_and_preserves_local_state_without_request(self):
        self.widget.yeelight_host.setText("Private network")
        self.widget.selected_device_id = "saved-device"
        dialog, cancel = self.reset_dialog_fixture(False)
        with patch("aiflame.desktop.widgets.QMessageBox", return_value=dialog), patch.object(self.widget.api, "request") as request:
            self.widget.reset_app()
        dialog.setDefaultButton.assert_called_once_with(cancel)
        dialog.setEscapeButton.assert_called_once_with(cancel)
        request.assert_not_called()
        self.assertEqual(self.widget.yeelight_host.text(), "Private network")
        self.assertEqual(self.widget.selected_device_id, "saved-device")
        self.assertFalse(self.widget.reset_pending)
        self.assertTrue(self.widget.reset_button.isEnabled())

    def test_app_reset_confirm_sends_once_and_restores_fresh_ui(self):
        self.widget.poll_timer.stop()
        self.wait(lambda: not self.widget.api.pending)
        fresh = deepcopy(self.widget.model)
        self.widget.navigate(5)
        self.widget.selected_device_id = "saved-device"
        self.widget.selected_device_model = "yeelight-yldp005"
        self.widget.yeelight_host.setText("Private network")
        self.widget.history_search.setText("Private password")
        self.widget.yeelight_host.setText("192.0.2.8")
        self.widget.skill_notice.setText("old installation feedback")
        self.widget.skill_notice.show()
        self.widget.notification_test_feedback.setText("old notification feedback")
        self.widget.notification_test_feedback.show()
        self.widget.history_search.setText("old query")
        self.widget.device_test_deadlines["saved-device"] = time.monotonic() + 10
        self.widget.brightness_timer.start()
        self.widget.pending_device_brightness = {"device_id": "saved-device", "settings": {"brightness": 8}}
        dialog, _ = self.reset_dialog_fixture(True)
        requests = []
        # Tray/notification consumers share this API and must finish their polls.
        self.widget.api.pending[999] = ("/api/notifications/claim", Mock(), Mock(), None)
        def request(path, callback=None, body=None, **kwargs):
            requests.append((path, callback, body, kwargs))
        with patch("aiflame.desktop.widgets.QMessageBox", return_value=dialog) as warning, patch.object(self.widget.api, "request", side_effect=request):
            self.widget.reset_app()
            self.assertTrue(self.widget.reset_pending)
            self.assertFalse(self.widget.reset_button.isEnabled())
            self.widget.reset_app()
            warning.assert_called_once()
            self.assertIn("配对与凭据", dialog.setInformativeText.call_args.args[0])
            resets = [call for call in requests if call[0] == "/api/reset"]
            self.assertEqual(len(resets), 1)
            self.assertEqual(resets[0][2], {"confirm": True})
            resets[0][1](fresh)
        self.assertFalse(self.widget.reset_pending)
        self.assertFalse(self.widget.writing)
        self.assertIn(999, self.widget.api.pending)
        self.assertFalse(self.widget.brightness_timer.isActive())
        self.assertIsNone(self.widget.pending_device_brightness)
        self.assertIsNone(self.widget.selected_device_id)
        self.assertEqual(self.widget.selected_device_model, "yeelight-yltd003")
        self.assertEqual(self.widget.device_test_deadlines, {})
        self.assertTrue(self.widget.skill_notice.isHidden())
        self.assertTrue(self.widget.notification_test_feedback.isHidden())
        self.assertEqual(self.widget.notification_test_feedback.text(), "")
        self.assertEqual(self.widget.source_category.currentData(), "web")
        self.assertEqual(self.widget.device_version.text(), "固件：未上报 · 协议：未上报")
        self.assertEqual(self.widget.device_model_title.text(), "Yeelight 屏幕挂灯 Pro")
        for edit in (self.widget.yeelight_host, self.widget.history_search, self.widget.yeelight_host,
                     self.widget.skill_notice):
            self.assertEqual(edit.text(), "")
        self.assertEqual(self.widget.tabs.currentIndex(), 0)
        self.assertEqual(self.widget.history.rowCount(), 0)
        self.assertEqual(self.widget.model, fresh)
        self.assertIn("APP 已重置", self.widget.message.text())
        self.assertTrue(self.widget.reset_button.isEnabled())
        self.assertEqual([call[0] for call in requests if call[2] is not None], ["/api/reset"])

    def test_app_reset_failure_preserves_inputs_and_allows_retry(self):
        self.widget.yeelight_host.setText("Private network")
        self.widget.history_search.setText("Private password")
        self.widget.selected_device_id = "saved-device"
        dialog, _ = self.reset_dialog_fixture(True)
        requests = []
        with patch("aiflame.desktop.widgets.QMessageBox", return_value=dialog), patch.object(self.widget.api, "request", side_effect=lambda *args, **kwargs: requests.append((args, kwargs))):
            self.widget.reset_app()
            self.widget.on_error("重置失败")
            requests[0][1]["failed"]()
        self.assertFalse(self.widget.reset_pending)
        self.assertFalse(self.widget.writing)
        self.assertEqual(self.widget.yeelight_host.text(), "Private network")
        self.assertEqual(self.widget.history_search.text(), "Private password")
        self.assertEqual(self.widget.selected_device_id, "saved-device")
        self.assertTrue(self.widget.reset_button.isEnabled())
        self.assertEqual(self.widget.message.text(), "重置失败")

    def test_feedback_expires_but_errors_survive_polling(self):
        self.assertTrue(self.widget.statusbar.isHidden())
        self.widget.tell("任务标识已复制。")
        self.assertFalse(self.widget.statusbar.isHidden())
        self.assertEqual(self.widget.message_timer.interval(), 5000)
        self.assertTrue(self.widget.message_timer.isSingleShot())
        self.widget.message_timer.start(20)
        self.wait(self.widget.statusbar.isHidden)
        self.assertEqual(self.widget.message.text(), "任务标识已复制。")
        self.widget.tell("操作成功")
        self.widget.on_error("保存失败")
        self.assertTrue(self.widget.message_timer.isActive())
        self.widget.render(self.widget.model)
        self.assertFalse(self.widget.statusbar.isHidden())
        self.assertEqual(self.widget.message.text(), "保存失败")
        self.widget.setting("duration", 11)
        self.wait(lambda: not self.widget.writing and not self.widget.api.pending)
        self.assertFalse(self.widget.message_timer.isActive())
        self.assertTrue(self.widget.statusbar.isHidden())
        self.assertFalse(self.widget.message_error)

    def test_activity_selection_survives_new_event_and_deselection_survives_refresh(self):
        with self.hub.lock:
            self.hub.engine.accept(make_event("local-script", "first", "done"), "local-script")
        self.widget.poll(force=True)
        self.wait(lambda: len(self.widget.visible_events) == 1)
        self.widget.navigate(4)
        self.widget.history.selectRow(0)
        selected_id = self.widget.visible_events[0]["id"]
        with self.hub.lock:
            self.hub.engine.accept(make_event("local-script", "second", "working"), "local-script")
        self.widget.poll(force=True)
        self.wait(lambda: len(self.widget.visible_events) == 2)
        self.assertEqual(self.widget.visible_events[self.widget.history.currentRow()]["id"], selected_id)
        self.assertFalse(self.widget.event_detail_box.isHidden())
        self.widget.history.clearSelection()
        self.widget.render_history()
        self.assertTrue(self.widget.event_detail_box.isHidden())
        self.assertFalse(self.widget.history.selectionModel().hasSelection())

    def test_recent_tasks_survive_a_task_with_fifty_events(self):
        # Keep room for all four task rows; responsive checks cover row capacity.
        self.widget.resize(1180, 960)
        with self.hub.lock:
            for index in range(3):
                self.hub.engine.accept(make_event("local-script", f"earlier-{index}", "done"), "local-script")
            for seq in range(1, 61):
                self.hub.engine.accept(make_event("local-script", "busy-task", "working", seq), "local-script")
        self.widget.poll(force=True)
        self.wait(lambda: len(self.widget.visible_events) == 4)
        self.assertEqual({e["task"] for e in self.widget.model["events"]}, {"busy-task"})
        self.assertEqual(sum(not row.isHidden() for row in self.widget.recent_row_frames), 4)
        for labels, task in zip(self.widget.recent_rows, ("busy-task", "earlier-2", "earlier-1", "earlier-0")):
            self.assertIn(task, labels[1].toolTip())
        self.assertEqual(self.widget.recent_rows[0][2].text(), "任务进行中")
        with self.hub.lock:
            self.hub.engine.accept(make_event("local-script", "busy-task", "done", 61), "local-script")
        self.widget.poll(force=True)
        self.wait(lambda: self.widget.recent_rows[0][2].text() == "任务已完成")
        self.assertEqual(sum(not row.isHidden() for row in self.widget.recent_row_frames), 4)
        self.widget.history_filter.setCurrentIndex(self.widget.history_filter.findData("test"))
        self.widget.history_search.setText("no-matching-task")
        self.assertEqual(len(self.widget.visible_events), 0)
        self.assertIn("busy-task", self.widget.recent_rows[0][1].toolTip())
        self.assertEqual(sum(not row.isHidden() for row in self.widget.recent_row_frames), 4)

    def test_activity_task_limit_is_independent_of_event_limit(self):
        with self.hub.lock:
            for index in range(51):
                task = f"history-{index}"
                self.hub.engine.accept(make_event("local-script", task, "working", 1), "local-script")
                self.hub.engine.accept(make_event("local-script", task, "done", 2), "local-script")
        self.widget.poll(force=True)
        self.wait(lambda: len(self.widget.visible_events) == 50)
        self.assertEqual(len(self.widget.model["task_events"]), 100)
        self.assertEqual(len(self.widget.event_groups), 50)
        self.assertTrue(all(len(items) == 2 for items in self.widget.event_groups.values()))
        self.assertIn("最近 50 项任务", self.widget.history_count.text())
        self.widget.history_view.setCurrentIndex(self.widget.history_view.findData("events"))
        self.assertEqual(len(self.widget.visible_events), 50)
        self.assertEqual(len(self.widget.event_groups), 25)
        self.assertIn("最近 50 条事件", self.widget.history_count.text())

    def test_reset_roundtrip_restores_defaults_and_refreshes_dashboard_cookie(self):
        self.widget.poll_timer.stop()
        self.wait(lambda: not self.widget.api.pending)
        old_cookie = self.hub.config['dashboard']
        with self.hub.lock:
            self.hub.engine.update_settings({'duration': 45})
            self.hub.engine.accept(make_event('local-script', 'before-reset', 'done'), 'local-script')
        self.widget.history_search.setText('old draft')
        self.widget.reset_pending = True
        self.widget.act('/api/reset', {'confirm': True}, self.widget.reset_finished)
        self.wait(lambda: not self.widget.writing and not self.widget.api.pending)
        self.assertNotEqual(self.hub.config['dashboard'], old_cookie)
        self.assertEqual(self.widget.model['events'], [])
        self.assertEqual(self.widget.model['settings']['duration'], 10)
        self.assertEqual(self.widget.history_search.text(), '')
        self.assertEqual(self.widget.tabs.currentIndex(), 0)
        self.widget.act('/api/settings', {'duration': 9})
        self.wait(lambda: not self.widget.writing and not self.widget.api.pending)
        self.assertEqual(self.hub.engine.settings['duration'], 9)
        self.assertEqual(self.widget.model['settings']['duration'], 9)
        self.widget.act('/api/settings', {"hardware_notifications": True})
        self.wait(lambda: not self.widget.writing and not self.widget.api.pending)
        self.widget.navigate(1)
        for model in ('yeelight-yltd003', 'yeelight-yldp005'):
            with self.subTest(model=model):
                self.widget.choose_device_model(model)
                self.wait(lambda: not self.widget.device_selection_loading and not self.widget.api.pending)
                self.assertTrue(self.widget.yeelight_history_heading.isVisible())
                self.assertTrue(self.widget.device_instance_panel.isVisible())
                self.assertEqual(self.widget.device_instances.count(), 1)
                self.assertEqual(self.widget.device_instances.currentText(), '尚未添加设备')
                self.assertTrue(self.widget.yeelight_add_panel.isVisible())

    def activity_detail_text(self):
        document = QTextDocument()
        document.setHtml(self.widget.event_detail.text())
        return document.toPlainText()

    def activity_delivery_fixture(self):
        self.widget.poll_timer.stop()
        self.wait(lambda: not self.widget.api.pending)
        snapshot = deepcopy(self.widget.model)
        event = {"id": "receipt-event", "source": "local-script", "state": "done",
                 "reason": "completed", "created": time.time(), "status": "applied",
                 "delivery_summary": {"reason": "设备已确认接收指令", "devices": [
                     {"device_id": "desk-one", "name": "Yeelight 屏幕挂灯 Pro", "status": "applied",
                      "reason": "设备已确认接收指令"},
                     {"device_id": "desk-two", "name": "Yeelight W3 彩色灯泡", "status": "offline",
                      "reason": "事件到达时设备离线，未收到设备回执"},
                 ]}}
        snapshot["events"] = [event]
        snapshot["task_events"] = snapshot["events"]
        snapshot["deliveries"] = [
            {"event": event["id"], "output": "flame:desk-one", "status": "applied"},
            {"event": event["id"], "output": "flame:desk-two", "status": "offline"},
            {"event": event["id"], "output": "desktop", "status": "denied"},
        ]
        self.widget.render(snapshot)
        self.widget.navigate(4)
        self.widget.history.selectRow(0)
        return snapshot

    def test_activity_lists_device_acknowledgments_and_refreshes_partial_receipts(self):
        snapshot = self.activity_delivery_fixture()
        result = self.widget.history.item(0, 3).text()
        self.assertEqual(result, "部分确认 · 1/2 台设备")
        self.assertNotIn("desk-one", result)
        self.assertNotIn("AI Flame", result)
        detail = self.activity_detail_text()
        self.assertIn("处理结果：\n部分确认 · 1/2 台设备", detail)
        self.assertIn("设备回执\nYeelight 屏幕挂灯 Pro（desk-one）\n设备已确认接收指令", detail)
        self.assertIn("Yeelight W3 彩色灯泡（desk-two）\n事件到达时设备离线", detail)
        self.assertIn("系统通知\n系统通知权限未允许", detail)
        self.assertNotIn("已收到事件 →", detail)
        self.assertNotIn("未确认：", detail)
        self.assertEqual(detail.count("设备已确认接收指令"), 1)
        self.assertNotIn("flame:", detail)
        changed = deepcopy(snapshot)
        changed["events"][0]["delivery_summary"]["devices"][1].update(
            status="applied", reason="设备已确认接收指令")
        # The overall event stays applied; the second device's ACK alone refreshes it.
        self.widget.render(changed)
        result = self.widget.history.item(0, 3).text()
        self.assertEqual(result, "全部确认 · 2/2 台设备")
        self.assertEqual(self.widget.history.currentRow(), 0)
        self.assertTrue(self.widget.history.selectionModel().hasSelection())
        self.assertIn("Yeelight W3 彩色灯泡（desk-two）\n设备已确认接收指令", self.activity_detail_text())

    def test_activity_detail_emphasizes_result_and_escapes_device_text(self):
        snapshot = deepcopy(self.activity_delivery_fixture())
        snapshot['events'][0]['delivery_summary']['devices'][0]['name'] = '<b>Desk & lamp</b>'
        self.widget.render(snapshot)
        self.assertEqual(self.widget.event_detail.textFormat(), Qt.TextFormat.RichText)
        document = QTextDocument()
        document.setHtml(self.widget.event_detail.text())
        self.assertIn('<b>Desk & lamp</b>', document.toPlainText())
        result = document.find('部分确认 · 1/2 台设备').charFormat()
        section = document.find('设备回执').charFormat()
        device_id = document.find('desk-one').charFormat()
        self.assertGreater(result.font().pixelSize(), section.font().pixelSize())
        self.assertGreater(section.font().pixelSize(), device_id.font().pixelSize())
        self.assertGreater(result.fontWeight(), device_id.fontWeight())

    def test_activity_zero_confirmed_devices_show_pending_count_without_claiming_failure(self):
        snapshot = self.activity_delivery_fixture()
        changed = deepcopy(snapshot)
        changed["events"][0]["status"] = "queued"
        changed["events"][0]["delivery_summary"]["reason"] = "已进入提醒调度，等待设备回执"
        changed["events"][0]["delivery_summary"]["devices"][0].update(
            status="queued", reason="等待设备确认")
        changed["deliveries"].extend([
            {"event": "receipt-event", "output": "flame", "status": "queued"},
            {"event": "receipt-event", "output": "internal-output", "status": "failed"},
        ])
        self.widget.render(changed)
        self.assertEqual(self.widget.history.item(0, 3).text(), "等待发送 · 0/2 台设备确认")
        detail = self.activity_detail_text()
        self.assertIn("已进入提醒调度，等待设备回执", detail)
        self.assertIn("Yeelight 屏幕挂灯 Pro（desk-one）\n等待设备确认", detail)
        self.assertNotIn("失败", detail)
        self.assertNotIn("internal-output", detail)
        self.assertNotIn("火焰灯：", detail)

    def test_activity_delivery_details_and_export_follow_language_with_compact_counts(self):
        snapshot = self.activity_delivery_fixture()
        snapshot = deepcopy(snapshot)
        snapshot["settings"]["language"] = "en"
        self.widget.render(snapshot)
        result = self.widget.history.item(0, 3).text()
        detail = self.activity_detail_text()
        self.assertIn("1/2", result)
        self.assertNotIn("desk-one", result)
        self.assertNotIn("desk-two", result)
        self.assertIn("desk-one", detail)
        self.assertIn("desk-two", detail)
        self.assertNotIn("已确认", result)
        self.assertNotIn("未收到设备回执", detail)
        self.assertIn("Yeelight W3 Color Bulb", detail)
        self.assertEqual(self.widget.history.item(0, 3).toolTip(), result)
        self.widget.history_search.setText("desk-two")
        self.assertEqual(self.widget.history.rowCount(), 1)
        path = str(Path(self.temp.name) / "receipts.csv")
        with patch("aiflame.desktop.widgets.QFileDialog.getSaveFileName", return_value=(path, "")):
            self.widget.export_history()
        exported = Path(path).read_text(encoding="utf-8-sig")
        self.assertIn("1/2", exported)
        self.assertNotIn("desk-one", exported)
        self.assertNotIn("desk-two", exported)
        self.assertNotIn("未确认", exported)
        self.assertNotIn("test-token", exported)

    def test_activity_without_device_receipts_explains_the_queue_reason(self):
        snapshot = self.activity_delivery_fixture()
        changed = deepcopy(snapshot)
        changed["events"][0].update(status="queued", delivery_summary={
            "reason": "排队中，当前完成提醒等待键鼠活动结束", "devices": []})
        changed["deliveries"] = []
        self.widget.render(changed)
        self.assertEqual(self.widget.history.item(0, 3).text(), "等待发送")
        self.assertIn("当前完成提醒等待键鼠活动结束", self.activity_detail_text())
        # Snapshots from an older service retain their plain processing result.
        changed = deepcopy(changed)
        changed["events"][0].pop("delivery_summary")
        self.widget.render(changed)
        self.assertEqual(self.widget.history.item(0, 3).text(), "等待发送")



    def test_first_use_extension_guide_opens_store_only_from_step_one(self):
        from aiflame.integrations.chatgpt_web.guide import STORE_URL
        with patch('aiflame.desktop.extension_guide.open_in_chrome', return_value=True) as launch:
            self.widget.open_browser_extension_guide()
            launch.assert_not_called()
            guide = self.widget.extension_guide
            guide.close()
            self.widget.open_browser_extension_guide()
            launch.assert_not_called()
            guide.primary.click()
        launch.assert_called_once_with(STORE_URL)
        guide = self.widget.extension_guide
        self.assertFalse(guide.installed)
        self.assertEqual(guide.primary.text(), "在 Chrome 安装扩展")
        guide.go(1)
        self.assertEqual(guide.step, 0)
        guide.copy_store()
        self.assertEqual(self.app.clipboard().text(), STORE_URL)
        guide.checkpoint.setChecked(True)
        guide.primary.click()
        self.assertEqual(guide.step, 1)
        self.wait(lambda: not self.widget.api.pending)
        with patch.object(self.widget, "act") as action:
            guide.connect()
            self.assertFalse(guide.primary.isEnabled())
            self.assertEqual(action.call_args.args[:2], ("/api/integrations/install", {"source": "chatgpt-web"}))
            action.call_args.args[3]()
            self.assertTrue(guide.primary.isEnabled())
            self.assertFalse(guide.configured)
            guide.connect()
            action.call_args.args[2]({"message": "通信配置已准备。"})
        self.wait(lambda: not self.widget.api.pending)
        self.assertTrue(guide.configured)
        self.assertEqual(guide.primary.text(), "完成设置")
        self.assertNotIn("已验证", guide.status.text())
        guide.close()
        self.assertFalse(guide.timer.isActive())
        with patch('aiflame.desktop.extension_guide.open_in_chrome') as launch:
            self.widget.open_browser_extension_guide()
        launch.assert_not_called()
        self.assertEqual(self.widget.extension_guide.step, 1)
        guide.close()



    def test_source_help_is_local_and_available_on_hover_or_activation(self):
        self.widget.navigate(2)
        self.assertEqual(self.widget.source_category.currentData(), "web")
        help_button = self.widget.integration_help
        self.assertTrue(self.widget.tabs.widget(2).isAncestorOf(help_button))
        self.assertTrue(help_button.isVisible())
        self.assertEqual(help_button.accessibleName(), "接入说明")
        with patch("aiflame.desktop.components.QToolTip.showText") as show:
            help_button.click()
        self.assertIn(help_button.toolTip(), show.call_args.args[1])
        self.widget.navigate(5)
        self.assertTrue(all(not item.button.isChecked() for item in self.widget.help_disclosures))
        self.assertFalse(self.widget.privacy_disclosure.button.isChecked())
        self.assertFalse(self.widget.about_disclosure.button.isChecked())
        self.assertFalse(any(button.text() == "开始诊断"
                             for button in self.widget.findChildren(QPushButton)))
        self.wait(lambda: not self.widget.api.pending)
        with patch.object(self.widget.api, "request") as request:
            self.widget.poll(force=True)
        self.assertNotIn("/api/trigger-test", [call.args[0] for call in request.call_args_list])

    def test_simulated_events_do_not_change_source_switches_or_real_evidence(self):
        enabled = self.widget.source_checks["codex"].isChecked()
        self.widget.act("/api/test", {"state": "done", "seq": 1})
        self.wait(lambda: not self.widget.writing and not self.widget.api.pending)
        self.assertEqual(self.widget.source_checks["codex"].isChecked(), enabled)
        self.assertEqual(self.widget.model["verification"]["codex"]["complete_tasks"], 0)
        self.assertFalse(any(
            button.text() == "开始本轮验证"
            for button in self.widget.findChildren(QPushButton)
        ))

    def test_live_screen_animation_continues_after_demo_limit_and_stops_hidden(self):
        flame = self.widget.flame
        flame.set_state("working", live=True)
        with (
            patch.object(flame.clock, "elapsed", return_value=10000),
            patch.object(flame.transition_clock, "elapsed", return_value=10000),
        ):
            flame._animate()
            self.assertTrue(flame.animation_timer.isActive())
        self.widget.hide()
        self.assertFalse(flame.animation_timer.isActive())

    def test_virtual_mode_never_requests_devices_even_with_saved_pairing(self):
        self.hub.config["device_id"] = "fixture-lamp"
        with patch("aiflame.devices.yeelight.discover") as ports, patch.object(self.hub.devices, "status") as status:
            self.widget.navigate(1)
            self.widget.device_action("discover")
            self.widget.poll(force=True)
            self.wait(lambda: not self.widget.api.pending)
            ports.assert_not_called()
            status.assert_not_called()
        self.assertTrue(self.widget.hardware_notice.isHidden())
        self.assertFalse(self.widget.hardware_controls.isHidden())
        self.assertFalse(self.widget.device_disabled_card.isHidden())
        self.assertFalse(self.widget.yeelight_discover_button.isEnabled())

    def test_mode_selection_and_persistent_hardware_warning(self):
        self.assertFalse(self.widget.home_channel_checks["hardware_notifications"].isChecked())
        self.widget.home_channel_checks["hardware_notifications"].click()
        self.wait(lambda: not self.widget.writing and not self.widget.api.pending)
        self.assertEqual(self.hub.engine.settings["hardware_notifications"], True)
        self.assertTrue(self.widget.home_channel_checks["hardware_notifications"].isChecked())
        self.assertFalse(self.widget.hardware_notice.isHidden())
        self.widget.hardware_notice_action.click()
        self.wait(lambda: not self.widget.api.pending)
        self.assertEqual(self.widget.tabs.currentIndex(), 1)
        self.assertFalse(self.widget.hardware_controls.isHidden())
        for patch_value in ({"brightness": 0}, {"brightness": 20}):
            self.hub.update_settings(patch_value)
            self.widget.poll(force=True)
            self.wait(lambda: not self.widget.api.pending)
            for page in range(6):
                self.widget.navigate(page)
                self.assertFalse(self.widget.hardware_notice.isHidden())
        with self.hub.lock:
            self.hub.engine.device["online"] = True
        self.widget.poll(force=True)
        self.wait(lambda: self.widget.hardware_notice.isHidden())
        with self.hub.lock:
            self.hub.engine.device["online"] = False
        self.widget.poll(force=True)
        self.wait(lambda: not self.widget.hardware_notice.isHidden())
        self.widget.setting("hardware_notifications", False)
        self.wait(lambda: not self.widget.writing and not self.widget.api.pending)
        self.assertTrue(self.widget.hardware_notice.isHidden())
        self.assertEqual(self.hub.engine.settings["hardware_notifications"], False)

    def test_mode_rejection_preserves_saved_selection(self):
        with patch.object(self.hub.tests, "busy", return_value=True):
            self.widget.setting("hardware_notifications", True)
            self.wait(lambda: not self.widget.writing and not self.widget.api.pending)
        self.assertEqual(self.hub.engine.settings["hardware_notifications"], False)
        self.assertFalse(self.widget.home_channel_checks["hardware_notifications"].isChecked())
        self.assertTrue(self.widget.hardware_notice.isHidden())
        self.assertIn("结束后再切换", self.widget.home_channel_checks["hardware_notifications"].toolTip())
        self.assertTrue(self.widget.statusbar.isHidden())
        self.assertNotIn("mode", self.widget.model["settings"])

    def test_mode_click_responds_immediately_and_uses_saved_response(self):
        self.widget.render({**self.widget.model, **self.hub.update_settings({"hardware_notifications": True})})
        requests = []

        def capture(path, callback=None, body=None, **_):
            requests.append((path, callback, body))

        with patch.object(self.widget.api, "request", side_effect=capture):
            self.widget.home_channel_checks["hardware_notifications"].click()
            self.assertFalse(self.widget.home_channel_checks["hardware_notifications"].isChecked())
            self.assertNotIn("hardware_notifications", self.widget.save_feedback)
            self.assertEqual(self.widget.model["settings"]["hardware_notifications"], True)
            self.widget.render(self.widget.model)
            self.assertFalse(self.widget.home_channel_checks["hardware_notifications"].isChecked())
            self.assertNotIn("hardware_notifications", self.widget.save_feedback)
            ticks = []
            QTimer.singleShot(0, lambda: ticks.append(True))
            self.widget.navigate(4)
            self.app.processEvents()
            self.assertTrue(ticks)
            self.assertEqual(self.widget.tabs.currentIndex(), 4)
            saved = self.hub.update_settings({"hardware_notifications": False})
            next(callback for path, callback, body in requests if path == "/api/settings")(saved)
            # No status GET has returned: the save response itself updates UI.
            self.assertEqual(self.widget.model["settings"]["hardware_notifications"], False)
            self.assertTrue(self.widget.hardware_notice.isHidden())
            self.assertNotIn("hardware_notifications", self.widget.save_feedback)
            self.assertNotIn("mode", self.widget.model["settings"])


if __name__ == "__main__":
    unittest.main()
