"""Preview fidelity, native selection semantics and hidden animation lifecycle."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import unittest
import io
from pathlib import Path
from unittest.mock import patch
from PySide6.QtCore import Qt, QSignalBlocker
from PySide6.QtTest import QTest, QSignalSpy
from PySide6.QtWidgets import QApplication
from aiflame.desktop.light_controls import LightModePicker, LightPreviewPanel, device_svg
from aiflame.devices.models import SUPPORTED_MODELS, MIJIA_PLUG_MODELS, YEELIGHT_CUBE_MODEL
from aiflame.devices.light_settings import light_settings
from aiflame.devices.cube_effects import frame
from aiflame.desktop.localization import set_language


class LightControlsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def tearDown(self):
        set_language('zh-CN')

    def test_device_art_preserves_utf8_with_ansi_default(self):
        original_open = io.open

        def ansi_open(file, mode='r', buffering=-1, encoding=None, *args, **kwargs):
            encoding = 'cp1252' if encoding in (None, 'locale') else encoding
            return original_open(file, mode, buffering, encoding, *args, **kwargs)

        assets = Path(__file__).resolve().parents[1] / 'aiflame/assets/devices'
        expected = {path.stem: path.read_text(encoding='utf-8')
                    for path in assets.glob('*.svg')}
        self.assertTrue(any(not text.isascii() for text in expected.values()))
        device_svg.cache_clear()
        self.addCleanup(device_svg.cache_clear)
        with patch.object(io, 'open', side_effect=ansi_open):
            for model, text in expected.items():
                with self.subTest(model=model):
                    self.assertEqual(device_svg(model), text)

    def test_visible_choices_save_one_value_and_keyboard_works(self):
        picker = LightModePicker(columns=2)
        picker.addItem('彩灯', 'back')
        picker.addItem('白灯', 'front')
        picker.show()
        self.addCleanup(picker.close)
        spy = QSignalSpy(picker.currentIndexChanged)
        picker.entries[1][0].click()
        self.assertEqual(picker.currentData(), 'front')
        self.assertEqual(spy.count(), 1)
        picker.entries[1][0].click()
        self.assertEqual(spy.count(), 1)
        QTest.keyClick(picker.entries[1][0], Qt.Key.Key_Left)
        self.assertEqual(picker.currentData(), 'back')
        self.assertTrue(picker.entries[0][0].isChecked())
        self.assertFalse(picker.entries[1][0].isChecked())
        with QSignalBlocker(picker):
            picker.setCurrentIndex(1)
        self.assertEqual(spy.count(), 2)
        set_language('en')
        self.assertEqual(picker.currentData(), 'front')
        self.assertNotEqual(picker.currentText(), '白灯')
        self.assertEqual(spy.count(), 2)

    def test_each_device_has_four_previews_and_hidden_timer_stops(self):
        panel = LightPreviewPanel()
        self.addCleanup(panel.close)
        panel.resize(820, 250)
        panel.show()
        self.app.processEvents()
        self.assertTrue(panel.timer.isActive())
        for model in SUPPORTED_MODELS:
            settings = light_settings({'hardware_model': model})
            panel.configure(model, settings)
            self.assertEqual(len(panel.canvases), 3)
            self.assertTrue(all(label.text() for label in panel.captions.values()))
            self.assertFalse(panel.grab().isNull())
            if model in MIJIA_PLUG_MODELS:
                self.assertEqual(panel.captions['working'].text(), '灯具熄灭')
                self.assertEqual(panel.captions['done'].text(), '灯具亮起')
                settings['state_power'] = dict.fromkeys(('working', 'needs_input', 'done'), False)
                panel.configure(model, settings)
                self.assertTrue(all(label.text() == '灯具熄灭' for label in panel.captions.values()))
        self.assertEqual(panel.columns, 3)
        panel.resize(400, 700)
        self.app.processEvents()
        self.assertEqual(panel.columns, 1)
        self.assertTrue(all(panel.rect().contains(card.geometry()) for card in panel.cards))
        panel.hide()
        self.assertFalse(panel.timer.isActive())

    def test_cube_preview_uses_real_effects_color_brightness_and_white_flash(self):
        panel = LightPreviewPanel()
        self.addCleanup(panel.close)
        settings = light_settings({'hardware_model': YEELIGHT_CUBE_MODEL})
        settings['reminder_mode'] = 'text'
        settings['colors']['done'] = '#123456'
        settings['state_brightness']['done'] = 70
        panel.configure(YEELIGHT_CUBE_MODEL, settings)
        actual = panel.canvases['done'].pixels(1)
        self.assertEqual(actual, frame({'key':'test', 'state':'done'}, settings, 1))
        revision = panel.revision
        panel.configure(YEELIGHT_CUBE_MODEL, settings)
        self.assertEqual(panel.revision, revision)
        settings['reminder_mode'] = 'front'
        panel.configure(YEELIGHT_CUBE_MODEL, settings)
        canvas = panel.canvases['needs_input']
        self.assertNotEqual(canvas.pixels(.1), bytes(300))
        self.assertEqual(canvas.pixels(.8), bytes(300))
        settings['front_flash']['enabled'] = False
        panel.configure(YEELIGHT_CUBE_MODEL, settings)
        self.assertEqual(canvas.pixels(.1), canvas.pixels(.8))
        self.assertEqual(panel.captions['needs_input'].text(), '白灯常亮')
