"""Language preferences and reversible native presentation without real devices."""
import json
import os
from importlib.util import find_spec
from pathlib import Path
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from aiflame.core.engine import Engine
from aiflame.core.events import EventError
from aiflame.core.i18n import english_catalog, translate


class LanguageSettingsTests(unittest.TestCase):
    def test_language_is_persisted_without_changing_other_preferences(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.sqlite"
            engine = Engine(path)
            self.assertEqual(engine.settings["language"], "zh-CN")
            original = dict(engine.settings)
            engine.update_settings({"language": "en"})
            self.assertEqual(engine.settings, {**original, "language": "en"})
            engine.db.close()
            restored = Engine(path)
            self.assertEqual(restored.settings["language"], "en")
            for invalid in ("fr", None, 1, [], {}):
                with self.assertRaises(EventError):
                    restored.update_settings({"language": invalid})
            self.assertEqual(restored.settings["language"], "en")
            restored.db.close()

    def test_existing_settings_without_language_keep_chinese_default(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.sqlite"
            engine = Engine(path)
            old = {key: value for key, value in engine.settings.items() if key != "language"}
            engine.db.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", ("main", json.dumps(old)))
            engine.db.commit()
            engine.db.close()
            restored = Engine(path)
            self.assertEqual(restored.settings["language"], "zh-CN")
            restored.db.close()

    def test_complete_phrase_wins_over_short_fragment_with_leading_space(self):
        self.assertEqual(translate("＋  连接灯具需要重新接入吗？", "en"), "＋  Do I need to set up integrations again when connecting a lamp?")
        self.assertEqual(translate("试亮剩余 10 秒", "en"), "Light test: 10 seconds")
        self.assertEqual(translate("SeeEnd · 任务已完成", "en"), "SeeEnd · Task completed")
        self.assertEqual(translate("工作中"), "工作中")
        self.assertTrue(all(isinstance(value, str) and value for value in english_catalog().values()))


QApplication = None
if find_spec("PySide6") is not None:
    from PySide6.QtGui import QIcon, QPixmap
    from PySide6.QtWidgets import QApplication
    from aiflame.desktop.localization import QLabel, QComboBox, QLineEdit, QTableWidget, QTableWidgetItem, QAction, set_language


@unittest.skipIf(QApplication is None, "Desktop dependencies unavailable")
class NativeLanguageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        set_language("zh-CN")
        self.addCleanup(set_language, "zh-CN")

    def test_static_dynamic_accessible_and_menu_text_switch_without_rebuilding(self):
        label = QLabel("工作中")
        label.setAccessibleName("任务状态")
        label.setToolTip("正在连接设备")
        action = QAction("退出 SeeEnd")
        set_language("en")
        self.assertEqual(label.text(), "Working")
        self.assertEqual(label.accessibleName(), "Task status")
        self.assertEqual(label.toolTip(), "Connecting to device")
        self.assertEqual(action.text(), "Quit SeeEnd")
        label.setText("已完成")
        self.assertEqual(label.text(), "Completed")
        set_language("zh-CN")
        self.assertEqual(label.text(), "已完成")
        self.assertEqual(action.text(), "退出 SeeEnd")
        label.clear()
        set_language("en")
        self.assertEqual(label.text(), "")

    def test_inputs_and_item_values_are_preserved_and_signals_are_not_emitted(self):
        edit = QLineEdit("我的设备")
        edit.setPlaceholderText("Wi-Fi 名称")
        networks = QComboBox()
        networks.setEditable(True)
        networks.addItem("我的设备", "ssid")
        choice = QComboBox()
        swatch = QPixmap(12, 12)
        swatch.fill()
        choice.addItem(QIcon(swatch), "虚拟模式", "virtual")
        emitted = []
        choice.currentTextChanged.connect(emitted.append)
        set_language("en")
        self.assertEqual(edit.text(), "我的设备")
        self.assertEqual(edit.placeholderText(), "Wi-Fi name")
        self.assertEqual(networks.currentText(), "我的设备")
        self.assertEqual(choice.currentText(), "Virtual mode")
        self.assertEqual(choice.currentData(), "virtual")
        self.assertFalse(choice.itemIcon(0).isNull())
        self.assertEqual(emitted, [])
        set_language("zh-CN")
        self.assertEqual(choice.currentText(), "虚拟模式")

    def test_existing_table_rows_and_headers_switch_both_directions(self):
        table = QTableWidget(1, 1)
        table.setHorizontalHeaderLabels(["任务状态"])
        table.setItem(0, 0, QTableWidgetItem("已完成"))
        set_language("en")
        self.assertEqual(table.horizontalHeaderItem(0).text(), "Task status")
        self.assertEqual(table.item(0, 0).text(), "Completed")
        set_language("zh-CN")
        self.assertEqual(table.horizontalHeaderItem(0).text(), "任务状态")
        self.assertEqual(table.item(0, 0).text(), "已完成")
