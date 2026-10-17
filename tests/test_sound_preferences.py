"""Sound preferences through the real dashboard, without sockets or speakers."""

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import wave

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication
from aiflame.core.engine import Engine
from aiflame.desktop.localization import set_language
from aiflame.desktop.widgets import Dashboard
from aiflame.integrations.registry import SOURCES


class OfflineAPI(QObject):
    error = Signal(str)

    def __init__(self, parent, port):
        super().__init__(parent)

    def request(self, *args, **kwargs):
        pass

    def close(self):
        pass


class SoundPreferencesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.engine = Engine(Path(self.temp.name) / "events.sqlite", sources=SOURCES)
        self.api_patch = patch("aiflame.desktop.widgets.LocalAPI", OfflineAPI)
        self.api_patch.start()
        self.widget = Dashboard()
        self.widget.ready = True
        self.widget.resize(920, 680)
        self.widget.render({**self.engine.snapshot(), "maintenance": False})
        self.widget.navigate(3)
        self.widget.show()
        self.settle()

    def tearDown(self):
        self.widget.shutdown()
        self.widget.close()
        self.widget.deleteLater()
        self.app.processEvents()
        self.api_patch.stop()
        self.engine.db.close()
        self.temp.cleanup()
        set_language("zh-CN")

    def settle(self):
        for _ in range(4):
            self.app.processEvents()

    def test_controls_save_independent_preferences_silently_and_restore(self):
        original = dict(self.engine.settings)
        def request(path, callback=None, body=None, **kwargs):
            if path == "/api/settings":
                self.engine.update_settings(body)
                callback(self.engine.snapshot())
        self.widget.api.request = request
        self.widget.checks["sound_notifications"].click()
        self.widget.sound_category.setCurrentIndex(self.widget.sound_category.findData("cat"))
        self.widget.render({**self.engine.snapshot(), "maintenance": False})
        self.assertEqual(self.engine.settings, {**original, "sound_notifications": True, "sound_category": "cat"})
        self.assertEqual(self.widget.sound_category.currentData(), "cat")
        self.assertTrue(self.widget.checks["sound_notifications"].isChecked())
        for key in ("sound_notifications", "sound_category"):
            if key in self.widget.home_channel_checks:
                self.assertNotIn(key, self.widget.save_feedback)
                self.assertNotIn("未保存", self.widget.checks[key].toolTip())
            else:
                self.assertTrue(self.widget.save_feedback[key][0].isHidden())
        self.assertTrue(self.widget.statusbar.isHidden())

    def test_preview_works_with_alerts_off_and_english_layout_fits(self):
        emitted = []
        self.widget.sound_preview_requested.connect(emitted.append)
        self.widget.sound_category.setCurrentIndex(self.widget.sound_category.findData("cat"))
        self.widget.sound_test_button.click()
        self.assertEqual(emitted, ["cat"])
        self.assertFalse(self.engine.settings["sound_notifications"])
        self.engine.update_settings({"language": "en"})
        self.widget.writing = False
        self.widget.render({**self.engine.snapshot(), "maintenance": False})
        self.settle()
        self.assertEqual(self.widget.sound_category.itemText(2), "Cat meow")
        self.assertEqual(self.widget.sound_test_button.text(), "Preview")
        self.assertEqual(self.widget.tabs.widget(3).horizontalScrollBar().maximum(), 0)

    def test_all_shipped_sounds_are_short_non_silent_pcm(self):
        sounds = Path(__file__).resolve().parents[1] / "aiflame/assets/sounds"
        for name in ("chime", "pop", "cat"):
            with self.subTest(name=name), wave.open(str(sounds / (name + ".wav")), "rb") as sound:
                self.assertEqual(sound.getcomptype(), "NONE")
                self.assertEqual(sound.getsampwidth(), 2)
                self.assertEqual(sound.getnchannels(), 1)
                self.assertGreater(sound.getnframes() / sound.getframerate(), 0.2)
                self.assertLess(sound.getnframes() / sound.getframerate(), 1.6)
                self.assertNotEqual(set(sound.readframes(sound.getnframes())), {0})
