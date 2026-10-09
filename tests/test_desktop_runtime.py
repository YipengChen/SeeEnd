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

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HAS_QT = find_spec("PySide6") is not None
if HAS_QT:
    from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QRect, QTimer, Qt
    from PySide6.QtGui import QImage, QTextDocument
    from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QToolTip
    from aiflame.desktop.widgets import Dashboard


from aiflame.hub.server import Hub
from aiflame.core.events import EventError, make_event


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

    def test_device_model_selection_precedes_configuration(self):
        self.widget.navigate(1)
        self.assertTrue(self.widget.device_selector.isVisible())
        self.assertFalse(self.widget.device_details.isVisible())
        self.assertEqual(len(self.widget.device_model_buttons), 2)
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
        self.assertEqual(self.widget.breadcrumb.text(), "我的设备 / Yeelight 屏幕挂灯 Pro")
        self.widget.device_back_button.click()
        self.assertTrue(self.widget.device_selector.isVisible())
        self.assertEqual(self.widget.breadcrumb.text(), "我的设备")

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
        self.widget.act('/api/settings', {'mode': 'hardware'})
        self.wait(lambda: self.widget.model['settings']['mode'] == 'hardware' and not self.widget.writing)
        self.widget.navigate(1)
        for model, lan_model in (('yeelight-yltd003', 'lamp15'), ('yeelight-yldp005', 'colorb')):
            with self.subTest(model=model):
                info = {'device_id': 'yeelight:test', 'host': '192.168.1.44', 'port': 55443, 'model': lan_model}
                self.widget.choose_device_model(model)
                self.wait(lambda: not self.widget.device_selection_loading and not self.widget.writing)
                self.assertFalse(self.widget.device_model_title.wordWrap())
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
        self.widget.act('/api/settings', {'mode': 'hardware'})
        self.wait(lambda: self.widget.model['settings']['mode'] == 'hardware' and not self.widget.writing)
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
                self.widget.yeelight_color_reset.click()
                self.wait(lambda: self.hub.transport.records[identity]['light_settings']['colors']['working'] == '#4A90E2'
                          and self.hub.transport.records[identity]['light_settings']['state_brightness'] == {'working': 20, 'done': 50, 'error': 50})
                self.wait(lambda: not self.widget.writing)
                self.widget.yeelight_delete_button.click()
                self.wait(lambda: identity not in self.hub.transport.records and not self.widget.writing
                          and not self.widget.device_selection_loading and not self.widget.api.pending)
                self.assertNotIn(identity, [item['device_id'] for item in self.widget.model['devices']])
                self.assertEqual(self.widget.selected_device_id, '')
                self.assertEqual(self.widget.device_instances.currentText(), '尚未添加设备')
                self.assertTrue(self.widget.yeelight_delete_button.isHidden())
                self.assertTrue(self.widget.yeelight_add_panel.isVisible())
                self.assertTrue(self.widget.device_status_card.isHidden())

    def test_bulb_model_has_lan_setup_and_four_cards_fit_without_horizontal_scroll(self):
        self.widget.navigate(1)
        self.widget.device_model_buttons["yeelight-yldp005"].click()
        self.wait(lambda: self.widget.selected_device_model == "yeelight-yldp005" and not self.widget.device_selection_loading)
        self.assertFalse(self.widget.yeelight_card.isHidden())
        self.assertEqual(self.widget.yeelight_discover_button.text(), "搜索 W3 彩色灯泡")
        self.assertIn("整颗灯泡", self.widget.yeelight_light_hint.text())
        self.widget.show_device_selector()
        for width, height in ((920, 680), (1180, 850)):
            self.widget.resize(width, height)
            for _ in range(4):
                self.app.processEvents()
            self.assertEqual(self.widget.device_cards_layout.rowCount(), 1)
            self.assertEqual(self.widget.tabs.widget(1).horizontalScrollBar().maximum(), 0)
            self.assertEqual(self.widget.tabs.widget(1).verticalScrollBar().maximum(), 0)

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
        self.widget.device_test_started({"device_id": "fixture-lamp", "remaining_ms": 10000})
        self.assertEqual(self.widget.device_test_caption.text(), "试亮剩余 10 秒")
        self.widget.device_test_deadlines["fixture-lamp"] -= 5
        self.widget.update_device_test_caption()
        self.assertEqual(self.widget.device_test_caption.text(), "试亮剩余 5 秒")
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
        self.widget.choose_mode("hardware")
        self.wait(lambda: self.widget.hardware_mode() and not self.widget.writing)
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
        self.assertEqual(len(self.widget.source_checks), 22)
        for source in ("claude-code", "deepseek-harness", "qoder", "gemini-cli", "opencode"):
            self.assertIn(source, self.widget.source_checks)
            self.assertFalse(self.widget.source_checks[source].isChecked())
        self.assertIn("chatgpt-desktop", self.widget.source_checks)
        self.assertFalse(self.widget.source_checks["chatgpt-desktop"].isChecked())
        self.assertEqual(self.widget.tabs.count(), 6)
        self.assertFalse(any("QtWebEngine" in module for module in sys.modules))
        self.widget.tabs.setCurrentIndex(1)
        self.wait(lambda: not self.widget.api.pending)
        self.widget.tabs.setCurrentIndex(2)
        rows = list(self.widget.source_rows.values())
        self.assertTrue(all(not row.isHidden() for row in rows))
        self.assertTrue(all(row.parentWidget() is rows[0].parentWidget() for row in rows))
        self.assertFalse(hasattr(self.widget, "verification_source"))
        for source, row in self.widget.source_rows.items():
            mark = row.layout().itemAt(0).widget()
            pixmap = mark.icon().pixmap(30, 30) if isinstance(mark, QPushButton) else mark.pixmap()
            self.assertFalse(pixmap.isNull(), source)
        self.wait(
            lambda: (
                "辅助功能" in self.widget.client_details.text()
                and not self.widget.api.pending
            )
        )

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

    def test_manual_start_completion_and_pause_use_real_api(self):
        self.widget.manual("working")
        self.wait(lambda: self.widget.manual_task and not self.widget.writing)
        self.assertEqual(self.hub.engine.desired()["state"], "working")
        self.widget.manual("done")
        self.wait(lambda: not self.widget.writing)
        self.assertEqual(self.hub.engine.desired()["state"], "done")
        self.widget.setting("paused", True)
        self.wait(lambda: self.hub.engine.settings["paused"] and not self.widget.api.pending)
        self.assertEqual(self.hub.engine.desired()["state"], "idle")

    def test_chatgpt_checkbox_checks_permissions_only_on_user_enable(self):
        result = {
            "permissions": {"accessibility": False, "input_monitoring": False},
            "message": "已申请接入权限，请开启辅助功能和输入监控。",
        }
        with patch.object(
            self.hub.desktop_clients, "request_permissions", return_value=result
        ) as request:
            check = self.widget.source_checks["chatgpt-desktop"]
            # Restoring saved settings must not open an OS permission prompt.
            self.hub.engine.settings["source_enabled"]["chatgpt-desktop"] = True
            self.widget.poll(force=True)
            self.wait(lambda: check.isChecked() and not self.widget.api.pending)
            request.assert_not_called()
            check.setChecked(False)
            self.wait(lambda: not self.widget.writing)
            check.setChecked(True)
            self.wait(lambda: not self.widget.writing and not self.widget.api.pending)
            request.assert_called_once_with()
            self.assertTrue(
                self.hub.engine.settings["source_enabled"]["chatgpt-desktop"]
            )
            self.assertEqual(
                self.widget.chatgpt_permission_hint.text(), result["message"]
            )
            self.assertFalse(self.widget.chatgpt_permission_hint.isHidden())
            self.widget.poll(force=True)
            self.wait(lambda: not self.widget.api.pending)
            request.assert_called_once_with()
            check.setChecked(False)
            self.wait(lambda: not self.widget.writing)
            self.assertTrue(self.widget.chatgpt_permission_hint.isHidden())
            request.assert_called_once_with()

    def test_cancel_enable_before_save_skips_permission_request(self):
        with patch.object(self.hub.desktop_clients, "request_permissions") as request:
            check = self.widget.source_checks["chatgpt-desktop"]
            check.setChecked(True)
            check.setChecked(False)
            self.wait(lambda: not self.widget.writing and not self.widget.api.pending)
            request.assert_not_called()
            self.assertFalse(
                self.hub.engine.settings["source_enabled"]["chatgpt-desktop"]
            )

    def test_permission_failure_can_retry_without_locking_ui(self):
        with patch.object(
            self.hub.desktop_clients,
            "request_permissions",
            side_effect=EventError("无法请求权限", 409),
        ) as request:
            check = self.widget.source_checks["chatgpt-desktop"]
            check.setChecked(True)
            self.wait(lambda: not self.widget.writing and not self.widget.api.pending)
            self.assertFalse(self.widget.chatgpt_permission_pending)
            self.assertIn("重试", self.widget.chatgpt_permission_hint.text())
            request.side_effect = None
            request.return_value = {
                "permissions": {"accessibility": True, "input_monitoring": True},
                "message": "权限已就绪",
            }
            check.setChecked(False)
            check.setChecked(True)
            self.wait(lambda: not self.widget.writing and not self.widget.api.pending)
            self.assertEqual(request.call_count, 2)
            self.assertEqual(self.widget.chatgpt_permission_hint.text(), "权限已就绪")

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
        self.assertTrue(
            all(
                button.isEnabled()
                for button in self.widget.manual_buttons
                if button is not self.widget.try_light_button
            )
        )
        self.assertFalse(self.widget.try_light_button.isEnabled())

    def test_screen_preview_never_writes_settings_or_creates_events(self):
        before = self.hub.engine.snapshot()
        self.assertIs(self.widget.preview_button.parentWidget(), self.widget.scene_frame)
        self.assertIs(self.widget.preview_caption.parentWidget(), self.widget.scene_frame)
        self.widget.preview_button.click()
        self.assertEqual(self.widget.flame.state, "working")
        self.assertEqual(self.widget.preview_button.text(), "退出演示")
        self.assertIn("10 秒", self.widget.preview_caption.text())
        self.widget.preview_started -= 5.5
        self.widget.advance_preview()
        self.assertEqual(self.widget.flame.state, "done")
        self.assertIn("5 秒", self.widget.preview_caption.text())
        self.widget.preview_started -= 5
        self.widget.advance_preview()
        self.assertIsNone(self.widget.preview_phase)
        self.assertFalse(self.widget.preview_timer.isActive())
        self.assertEqual(self.widget.preview_button.text(), "灯效演示")
        self.wait(lambda: not self.widget.flame.animation_timer.isActive())
        after = self.hub.engine.snapshot()
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
            self.hub.engine.update_settings({"mode": "hardware"})
            self.hub.engine.device.update(online=True, port="wifi://192.0.2.1:55443", connected_at=1)
        self.widget.poll(force=True)
        self.wait(lambda: self.widget.hardware_mode() and not self.widget.api.pending)

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
        self.wait(lambda: self.widget.flame.state == "done")
        with self.hub.lock:
            self.hub.engine.preview["started"] -= 5
        self.wait(lambda: self.widget.hardware_preview_id is None)
        self.assertEqual(self.widget.flame.state, "idle")
        self.assertEqual(self.widget.poll_timer.interval(), 1500)
        self.assertEqual(self.widget.preview_button.text(), "灯效演示")
        self.assertTrue(self.widget.statusbar.isHidden())
        self.assertEqual(self.hub.engine.snapshot()["events"], [])

    def test_hiding_before_preview_start_reply_cancels_pending_demo(self):
        self.connect_preview_fixture()
        self.widget.start_preview()
        self.widget.hide()
        self.wait(lambda: not self.widget.writing and not self.widget.api.pending)
        self.assertIsNone(self.hub.engine.preview)
        self.assertIsNone(self.widget.hardware_preview_id)
        self.assertFalse(self.widget.poll_timer.isActive())
        self.assertEqual(self.hub.engine.desired()["state"], "idle")

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
        self.widget.choose_mode("virtual")
        self.wait(lambda: not self.widget.hardware_mode() and not self.widget.writing)
        self.assertIsNone(self.hub.engine.preview)

    def test_hardware_preview_rejects_offline_and_busy_without_local_animation(self):
        self.connect_preview_fixture()
        self.hub.engine.device["online"] = False
        self.widget.poll(force=True)
        self.wait(lambda: not self.widget.api.pending)
        self.app.processEvents()
        geometry = (self.widget.tabs.geometry(), self.widget.scene_frame.geometry())
        self.widget.start_preview()
        self.app.processEvents()
        self.assertIn("请先连接设备", QToolTip.text())
        self.assertTrue(self.widget.statusbar.isHidden())
        self.assertEqual((self.widget.tabs.geometry(), self.widget.scene_frame.geometry()), geometry)
        QToolTip.hideText()
        self.assertIsNone(self.widget.preview_phase)
        self.assertIsNone(self.hub.engine.preview)
        self.hub.engine.device["online"] = True
        self.widget.poll(force=True)
        self.wait(lambda: not self.widget.api.pending)
        with patch.object(self.hub.tests, "busy", return_value=True):
            self.widget.start_preview()
            self.wait(lambda: not self.widget.writing and not self.widget.api.pending)
        self.assertIsNone(self.hub.engine.preview)
        self.assertIsNone(self.widget.hardware_preview_id)
        self.assertEqual(self.widget.preview_button.text(), "灯效演示")

    def test_external_mode_change_cancels_local_preview(self):
        self.widget.start_preview()
        self.assertIsNotNone(self.widget.preview_phase)
        self.hub.update_settings({"mode": "hardware"})
        self.widget.poll(force=True)
        self.wait(lambda: self.widget.hardware_mode() and not self.widget.api.pending)
        self.assertIsNone(self.widget.preview_phase)
        self.assertEqual(self.widget.flame.state, "idle")

    def test_disconnected_service_resets_pending_preview_controls(self):
        self.connect_preview_fixture()

        def fail_request(path, callback=None, body=None, **options):
            self.widget.on_error("Hub 未连接，请检查 SeeEnd 服务")
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
        for source in ("idle", "working", "done", "error"):
            for target in ("idle", "working", "done", "error"):
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
            flame.set_state("error", preview=True)
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
        self.select_light_settings_fixture()
        for control in self.widget.yeelight_brightness_controls.values():
            control.setValue(0)
        self.wait(lambda: self.hub.transport.records["one"]["light_settings"]["brightness"] == 0 and not self.widget.writing and not self.widget.api.pending)
        self.assertEqual(self.widget.notice_kind, "brightness")
        self.assertIn("0%", self.widget.notice_text.text())
        before = len(self.hub.engine.snapshot()["events"])
        self.widget.manual("done")
        self.assertEqual(len(self.hub.engine.snapshot()["events"]), before)
        self.widget.resolve_notice()
        self.assertEqual(self.widget.tabs.currentIndex(), 1)
        self.assertTrue(self.widget.preview_button.isEnabled())

    def test_pause_and_resume_notice_uses_persisted_settings(self):
        self.widget.setting("paused", True)
        self.wait(lambda: not self.widget.writing and not self.widget.api.pending)
        self.assertEqual(self.widget.notice_kind, "paused")
        self.assertFalse(self.widget.manual_buttons[-1].isEnabled())
        self.widget.resolve_notice()
        self.wait(lambda: not self.widget.writing and not self.widget.api.pending)
        self.assertFalse(self.hub.engine.settings["paused"])

    def select_light_settings_fixture(self, count=1):
        with self.hub.lock:
            for device_id in ("one", "two")[:count]:
                record = {"device_id": device_id, "hardware_model": "yeelight-yltd003",
                          "connection_paused": True, "light_settings": {"brightness": 20, "reduced": False}}
                self.hub.transport.records[device_id] = record
                self.hub.transport.connection(device_id, record)
            self.hub.engine.update_settings({"mode": "hardware"})
        self.widget.poll(force=True)
        self.wait(lambda: self.widget.hardware_mode() and not self.widget.api.pending)
        self.widget.navigate(1)
        self.widget.choose_device_model("yeelight-yltd003")
        self.wait(lambda: self.widget.selected_device_id == "one" and not self.widget.device_selection_loading and not self.widget.api.pending)


    def test_pending_brightness_save_stays_with_original_device_on_selection_change(self):
        self.select_light_settings_fixture(2)
        self.widget.yeelight_brightness_controls["working"].setValue(7)
        self.widget.device_instances.setCurrentIndex(self.widget.device_instances.findData("two"))
        self.wait(lambda: self.hub.transport.records["one"]["light_settings"]["state_brightness"]["working"] == 7 and not self.widget.device_selection_loading and not self.widget.writing and not self.widget.api.pending)
        self.assertEqual(self.widget.selected_device_id, "two")
        self.assertEqual(self.hub.transport.records["two"]["light_settings"]["brightness"], 20)
        for control in self.widget.yeelight_brightness_controls.values():
            control.setValue(0)
        self.wait(lambda: self.hub.transport.records["two"]["light_settings"]["brightness"] == 0 and not self.widget.api.pending)
        self.assertFalse(self.widget.brightness_off())




    def test_activity_filters_tests_and_exports_only_selected_metadata(self):
        self.widget.manual("done")
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
        self.assertIn("APP 已呈现", self.activity_detail_text())
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
        self.widget.on_error("Hub 未连接，请检查 SeeEnd 服务")
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
        self.assertEqual(self.hub.engine.snapshot()["events"], [])
        with self.hub.lock:
            self.hub.engine.accept(
                make_event("local-script", "real-trial", "working"), "local-script"
            )
        self.widget.poll(force=True)
        self.wait(lambda: self.widget.flame.state == "working")
        self.assertIn("屏幕提醒", self.widget.device_badge.text())
        self.assertEqual(self.widget.source_status["local-script"].text(), "任务进行中")
        self.assertTrue(self.widget.flame.live)
        self.assertFalse(self.widget.flame.preview)
        with self.hub.lock:
            self.hub.engine.accept(
                make_event("local-script", "real-trial", "done", 2), "local-script"
            )
        self.widget.poll(force=True)
        self.wait(lambda: self.widget.flame.state == "done")
        self.assertEqual(self.widget.source_status["local-script"].text(), "已收到开始与完成")
        self.assertEqual(self.widget.model["events"][0]["status"], "screen")
        self.assertIsNone(self.widget.model["last_ack"])
        self.hub.update_settings({"mode": "hardware"})
        with self.hub.lock:
            self.hub.engine.device["online"] = True
        self.widget.poll(force=True)
        self.wait(lambda: self.widget.model["device"]["online"])
        self.assertEqual(self.widget.flame.state, "idle")
        self.assertFalse(self.widget.try_light_button.isEnabled())  # No physical device selected.
        self.assertEqual(self.widget.source_status["local-script"].text(), "已收到开始与完成")

    def test_overview_navigation_in_both_modes_and_every_device_state(self):
        snapshot = self.widget.model
        for mode, paired, online in (("virtual", False, False), ("hardware", False, False), ("hardware", True, False), ("hardware", True, True)):
            with self.subTest(mode=mode, paired=paired, online=online):
                self.widget.render({
                    **snapshot,
                    "hardware_paired": paired,
                    "settings": {**snapshot["settings"], "mode": mode},
                    "device": {**snapshot["device"], "online": online},
                })
                for shortcut, page in ((self.widget.home_sources_button, 2), (self.widget.recent_more, 4)):
                    self.widget.navigate(0)
                    self.assertFalse(shortcut.isHidden())
                    shortcut.click()
                    self.assertEqual(self.widget.tabs.currentIndex(), page)
                overview_buttons = self.widget.tabs.widget(0).findChildren(QPushButton)
                self.assertFalse(any(button.accessibleName() in ("活动记录", "连接设备") for button in overview_buttons))
                self.assertEqual(self.widget.hardware_notice.isHidden(), mode == "virtual" or online)

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
            for mode in ("hardware", "virtual", "hardware", "virtual"):
                self.widget.mode_buttons[mode].click()
                self.wait(lambda: not self.widget.writing and self.widget.mode_pending is None and self.widget.model["settings"]["mode"] == mode)
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
            self.app.processEvents()
            expected = None
            for mode, online, connecting in (
                ("virtual", False, False), ("hardware", False, False),
                ("hardware", False, True), ("hardware", True, False),
                ("virtual", False, False),
            ):
                with self.subTest(width=width, mode=mode, online=online, connecting=connecting):
                    snapshot = deepcopy(original)
                    snapshot["settings"]["mode"] = mode
                    snapshot["device"].update(online=online, connecting=connecting)
                    self.widget.render(snapshot)
                    self.app.processEvents()
                    positions = (
                        self.widget.tabs.geometry(), self.widget.scene_frame.geometry(),
                        self.widget.mode_buttons["virtual"].parentWidget().geometry(),
                        tuple(button.geometry() for button in self.widget.nav_buttons),
                    )
                    if expected is None:
                        expected = positions
                    self.assertEqual(positions, expected)
                    self.assertEqual(self.widget.hardware_notice.isHidden(), mode == "virtual" or online or connecting)

    def test_overview_groups_enabled_sources_and_keeps_card_heights_stable(self):
        self.widget.poll_timer.stop()
        self.wait(lambda: not self.widget.api.pending)
        original = deepcopy(self.widget.model)
        enabled = {'codex', 'chatgpt-web', 'chatgpt-desktop', 'local-script'}
        for width in (1180, 790):
            self.widget.resize(width, 850)
            for count in (0, 1, 4, 5):
                snapshot = deepcopy(original)
                for source in snapshot['sources']:
                    source['enabled'] = source['id'] in enabled
                snapshot['events'] = [
                    {'id': str(index), 'source': 'chatgpt-web', 'state': 'done', 'reason': 'completed',
                     'created': time.time(), 'status': 'applied'} for index in range(count)]
                self.widget.render(snapshot)
                self.app.processEvents()
                self.assertGreaterEqual(self.widget.scene_frame.height(), 500)
                if width >= 1050:
                    scene_bottom = self.widget.scene_frame.mapTo(self.widget, self.widget.scene_frame.rect().bottomLeft()).y()
                    recent_bottom = self.widget.recent_frame.mapTo(self.widget, self.widget.recent_frame.rect().bottomLeft()).y()
                    self.assertEqual(scene_bottom, recent_bottom)
                self.assertGreaterEqual(self.widget.recent_frame.height(), 360)
                self.assertEqual(sum(not row.isHidden() for row in self.widget.recent_row_frames), min(count, 4))
                for source, button in self.widget.home_source_icons.items():
                    self.assertEqual(button.isHidden(), source not in enabled)
                rows = [group for group, _sources in self.widget.home_source_groups.values()]
                self.assertTrue(all(not group.isHidden() for group in rows))
                self.assertEqual(len({group.y() for group in rows}), 3)
                self.assertNotIn("本地 API 与测试来源", self.widget.home_source_groups)
                self.assertTrue({"local-script", "manual", "demo"}.isdisjoint(self.widget.home_source_icons))
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
                "settings": {**snapshot["settings"], "mode": "hardware"},
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
        reset, cancel = object(), object()
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
        self.widget.cli_config_dirs["codex"].setText("/tmp/custom-cli")
        self.widget.history_search.setText("old query")
        self.widget.manual_task = "old-task"
        self.widget.integration_plans["codex"] = {"snippet": "old configuration"}
        self.widget.device_test_deadlines["saved-device"] = time.monotonic() + 10
        self.widget.brightness_timer.start()
        self.widget.pending_device_brightness = {"device_id": "saved-device", "settings": {"brightness": 8}}
        dialog, _ = self.reset_dialog_fixture(True)
        requests = []
        # Tray/notification consumers share this API and must finish their polls.
        self.widget.api.pending[999] = ("/api/notifications/claim", Mock(), Mock())
        def request(path, callback=None, body=None, **kwargs):
            requests.append((path, callback, body, kwargs))
        with patch("aiflame.desktop.widgets.QMessageBox", return_value=dialog) as warning, patch.object(self.widget.api, "request", side_effect=request):
            self.widget.reset_app()
            self.assertTrue(self.widget.reset_pending)
            self.assertFalse(self.widget.reset_button.isEnabled())
            self.widget.reset_app()
            warning.assert_called_once()
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
        self.assertIsNone(self.widget.manual_task)
        self.assertEqual(self.widget.device_test_deadlines, {})
        self.assertEqual(self.widget.integration_plans, {})
        self.assertEqual(self.widget.device_version.text(), "连接后读取固件版本")
        self.assertEqual(self.widget.device_model_title.text(), "Yeelight 屏幕挂灯 Pro")
        for edit in (self.widget.yeelight_host, self.widget.history_search, self.widget.yeelight_host,
                     self.widget.cli_config_dirs["codex"], self.widget.history_search):
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
        self.widget.tell("偏好已保存")
        self.assertFalse(self.widget.statusbar.isHidden())
        self.assertEqual(self.widget.message_timer.interval(), 3000)
        self.assertTrue(self.widget.message_timer.isSingleShot())
        self.widget.message_timer.start(20)
        self.wait(self.widget.statusbar.isHidden)
        self.assertEqual(self.widget.message.text(), "偏好已保存")
        self.widget.tell("操作成功")
        self.widget.on_error("保存失败")
        self.assertFalse(self.widget.message_timer.isActive())
        self.widget.render(self.widget.model)
        self.assertFalse(self.widget.statusbar.isHidden())
        self.assertEqual(self.widget.message.text(), "保存失败")
        self.widget.setting("duration", 11)
        self.wait(lambda: not self.widget.writing and not self.widget.api.pending)
        self.assertTrue(self.widget.message_timer.isActive())
        self.assertNotEqual(self.widget.message.text(), "保存失败")

    def test_quiet_times_and_zero_brightness_explanation_are_conditional(self):
        self.widget.navigate(3)
        self.assertTrue(self.widget.quiet_times_box.isHidden())
        self.widget.checks["quiet_enabled"].setChecked(True)
        self.wait(lambda: not self.widget.writing and not self.widget.api.pending)
        self.assertFalse(self.widget.quiet_times_box.isHidden())
        self.assertTrue(all(edit.isEnabled() for edit in self.widget.times.values()))
        with self.hub.lock:
            self.hub.engine.settings["quiet_start"] = "12:00"
            self.hub.engine.settings["quiet_end"] = "12:00"
        self.widget.poll(force=True)
        self.wait(lambda: "全天静默" in self.widget.quiet_note.text())
        self.widget.checks["quiet_enabled"].setChecked(False)
        self.wait(lambda: not self.widget.writing and not self.widget.api.pending)
        self.assertTrue(self.widget.quiet_times_box.isHidden())
        self.select_light_settings_fixture()
        for control in self.widget.yeelight_brightness_controls.values():
            control.setValue(0)
        self.wait(lambda: self.hub.transport.records["one"]["light_settings"]["brightness"] == 0 and not self.widget.writing and not self.widget.api.pending)

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
        self.widget.act('/api/settings', {'mode': 'hardware'})
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
        self.assertEqual(result, "已确认 1/2 台设备")
        self.assertNotIn("desk-one", result)
        self.assertNotIn("AI Flame", result)
        detail = self.activity_detail_text()
        self.assertIn("处理结果：\n已确认 1/2 台设备", detail)
        self.assertIn("设备回执\nYeelight 屏幕挂灯 Pro（desk-one）\n设备已确认接收指令", detail)
        self.assertIn("Yeelight W3 彩色灯泡（desk-two）\n事件到达时设备离线", detail)
        self.assertIn("桌面通知\n系统通知权限未允许", detail)
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
        self.assertEqual(result, "已确认 2/2 台设备")
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
        result = document.find('已确认 1/2 台设备').charFormat()
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
        self.assertEqual(self.widget.history.item(0, 3).text(), "等待发送 · 已确认 0/2 台设备")
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

    def test_client_failures_remain_visible_when_permissions_are_granted(self):
        self.widget.source_values["chatgpt-desktop"] = True
        payload = {
            "supported": True,
            "permissions": {"accessibility": True, "input_monitoring": True},
            "clients": [{
                "source_id": "chatgpt-desktop", "name": "ChatGPT",
                "status": "input_unavailable", "versions": ["fixture-version"],
            }],
        }
        with patch.object(self.widget.api, "request", side_effect=lambda path, callback: callback(payload)):
            self.widget.check_clients()
            self.assertFalse(self.widget.client_message.isHidden())
            self.assertIn("重启", self.widget.client_message.text())
            self.assertNotIn("fixture-version", self.widget.client_message.text())
            self.assertIn("fixture-version", self.widget.client_details.text())
            payload["clients"][0]["status"] = "ready"
            self.widget.check_clients()
            self.assertTrue(self.widget.client_message.isHidden())

    def test_browser_connection_preserves_required_next_steps(self):
        with patch.object(self.widget, "act") as action:
            self.widget.install_integration("chatgpt-web")
            action.call_args.args[2]({"message": "请重新加载浏览器扩展，并刷新 AI 网页。"})
        self.assertFalse(self.widget.browser_message.isHidden())
        self.assertIn("扩展", self.widget.browser_message.text())
        self.assertIn("刷新", self.widget.browser_message.text())

    def test_permission_button_preserves_restart_instruction(self):
        message = "权限已申请，请开启辅助功能和输入监控后重新打开 SeeEnd。"
        with patch.object(self.hub.desktop_clients, "request_permissions", return_value={
            "permissions": {"accessibility": False, "input_monitoring": False},
            "message": message,
        }):
            self.widget.permission_button.click()
            self.wait(lambda: not self.widget.writing and not self.widget.api.pending)
        self.assertEqual(self.widget.permission_button.text(), "授权接入")
        self.assertFalse(self.widget.chatgpt_permission_hint.isHidden())
        self.assertEqual(self.widget.chatgpt_permission_hint.text(), message)

    def test_source_help_is_local_and_collapsed_until_requested(self):
        self.widget.navigate(2)
        disclosure = self.widget.integration_help_disclosure
        self.assertTrue(self.widget.tabs.widget(2).isAncestorOf(disclosure))
        self.assertFalse(disclosure.button.isChecked())
        self.assertFalse(self.widget.codex_details.isVisible())
        disclosure.button.click()
        self.assertTrue(self.widget.codex_details.isVisible())
        self.assertIn("真实任务", self.widget.source_details["codex"].text())
        self.widget.navigate(5)
        self.assertTrue(all(not item.button.isChecked() for item in self.widget.help_disclosures))
        self.assertFalse(self.widget.privacy_disclosure.button.isChecked())
        self.assertFalse(self.widget.about_disclosure.button.isChecked())

    def test_simulated_events_do_not_change_real_source_status(self):
        self.widget.manual("done")
        self.wait(lambda: not self.widget.writing and not self.widget.api.pending)
        self.assertIn("等待新任务", self.widget.source_status["codex"].text())
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
        self.assertTrue(self.widget.hardware_controls.isHidden())
        self.assertFalse(self.widget.virtual_device_card.isHidden())
        self.assertFalse(self.widget.yeelight_discover_button.isEnabled())

    def test_mode_selection_and_persistent_hardware_warning(self):
        self.assertTrue(self.widget.mode_buttons["virtual"].isChecked())
        self.widget.mode_buttons["hardware"].click()
        self.wait(lambda: not self.widget.writing and not self.widget.api.pending)
        self.assertEqual(self.hub.engine.settings["mode"], "hardware")
        self.assertTrue(self.widget.mode_buttons["hardware"].isChecked())
        self.assertFalse(self.widget.hardware_notice.isHidden())
        self.widget.hardware_notice_action.click()
        self.wait(lambda: not self.widget.api.pending)
        self.assertEqual(self.widget.tabs.currentIndex(), 1)
        self.assertFalse(self.widget.hardware_controls.isHidden())
        for patch_value in ({"paused": True}, {"brightness": 0}, {"quiet_enabled": True, "quiet_start": "12:00", "quiet_end": "12:00"}):
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
        self.widget.choose_mode("virtual")
        self.wait(lambda: not self.widget.writing and not self.widget.api.pending)
        self.assertTrue(self.widget.hardware_notice.isHidden())
        self.assertEqual(self.hub.engine.settings["mode"], "virtual")

    def test_mode_rejection_preserves_saved_selection(self):
        with patch.object(self.hub.tests, "busy", return_value=True):
            self.widget.choose_mode("hardware")
            self.wait(lambda: not self.widget.writing and not self.widget.api.pending)
        self.assertEqual(self.hub.engine.settings["mode"], "virtual")
        self.assertTrue(self.widget.mode_buttons["virtual"].isChecked())
        self.assertTrue(self.widget.hardware_notice.isHidden())
        self.assertIn("结束后再切换", self.widget.message.text())
        self.assertIsNone(self.widget.mode_pending)

    def test_mode_click_responds_immediately_and_uses_saved_response(self):
        self.widget.render({**self.widget.model, **self.hub.update_settings({"mode": "hardware"})})
        requests = []

        def capture(path, callback=None, body=None, **_):
            requests.append((path, callback, body))

        with patch.object(self.widget.api, "request", side_effect=capture):
            self.widget.mode_buttons["virtual"].click()
            self.assertTrue(self.widget.mode_buttons["virtual"].isChecked())
            self.assertIn("正在切换", self.widget.mode_hint.text())
            self.assertEqual(self.widget.model["settings"]["mode"], "hardware")
            self.widget.render(self.widget.model)
            self.assertTrue(self.widget.mode_buttons["virtual"].isChecked())
            self.assertIn("正在切换", self.widget.mode_hint.text())
            ticks = []
            QTimer.singleShot(0, lambda: ticks.append(True))
            self.widget.navigate(4)
            self.app.processEvents()
            self.assertTrue(ticks)
            self.assertEqual(self.widget.tabs.currentIndex(), 4)
            saved = self.hub.update_settings({"mode": "virtual"})
            next(callback for path, callback, body in requests if path == "/api/settings")(saved)
            # No status GET has returned: the save response itself updates UI.
            self.assertEqual(self.widget.model["settings"]["mode"], "virtual")
            self.assertTrue(self.widget.hardware_notice.isHidden())
            self.assertNotIn("正在切换", self.widget.mode_hint.text())
            self.assertIsNone(self.widget.mode_pending)


if __name__ == "__main__":
    unittest.main()
