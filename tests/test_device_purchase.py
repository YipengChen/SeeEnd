"""Offline purchase-card interactions and layout; no Hub or browser required."""

import os
from importlib.util import find_spec
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HAS_QT = find_spec("PySide6") is not None
if HAS_QT:
    from PySide6.QtCore import QCoreApplication, QEvent, QPoint, Qt
    from PySide6.QtGui import QImage
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    from aiflame.desktop.components import DeviceModelCard
    from aiflame.desktop.localization import language, set_language, t
    from aiflame.desktop.theme import STYLE

from aiflame.devices.models import MODELS
from aiflame.devices.purchase import DEVICE_PURCHASES


class DevicePurchaseConfigurationTests(unittest.TestCase):
    def test_all_models_have_advice_and_valid_optional_channels(self):
        self.assertEqual(set(DEVICE_PURCHASES), {model["id"] for model in MODELS})
        for model in MODELS:
            with self.subTest(model=model["id"]):
                purchase = DEVICE_PURCHASES[model["id"]]
                for key in ("advice", "advice_en"):
                    self.assertTrue(purchase[key].strip())
                    self.assertNotIn("\n", purchase[key])
                for channel in ("taobao", "jd"):
                    url = purchase.get(channel, "")
                    self.assertIsInstance(url, str)
                    if not url:
                        continue
                    self.assertEqual(urlsplit(url).scheme, "https")
                    self.assertTrue(urlsplit(url).hostname)


@unittest.skipUnless(HAS_QT, "Install the desktop extra to test native widgets")
class DevicePurchaseCardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setStyle("Fusion")
        cls.previous_style_sheet = cls.app.styleSheet()
        cls.app.setStyleSheet(STYLE)

    @classmethod
    def tearDownClass(cls):
        cls.app.setStyleSheet(cls.previous_style_sheet)

    def setUp(self):
        self.previous_language = language()
        set_language("zh-CN")
        # Exercise both actions independently of editable production links.
        configured = DEVICE_PURCHASES[MODELS[0]["id"]]
        fixture = {
            "advice": configured["advice"],
            "advice_en": configured["advice_en"],
            "taobao": "https://example.com/test/taobao",
            "jd": "https://example.com/test/jd",
        }
        purchase_patch = patch.dict(DEVICE_PURCHASES, {MODELS[0]["id"]: fixture})
        purchase_patch.start()
        self.addCleanup(purchase_patch.stop)
        self.card = DeviceModelCard(MODELS[0])
        self.card.resize(max(430, self.card.minimumWidth()), self.card.height())
        self.card.show()
        self.app.processEvents()

    def tearDown(self):
        self.card.close()
        self.card.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()
        set_language(self.previous_language)

    def test_purchase_click_opens_only_its_channel_and_thumbnail_still_selects(self):
        requested, selections = [], []
        self.card.purchase_requested.connect(requested.append)
        self.card.clicked.connect(lambda: selections.append(True))
        purchase = DEVICE_PURCHASES[MODELS[0]["id"]]
        for channel in ("taobao", "jd"):
            button = self.card.purchase_buttons[channel]
            QTest.mouseClick(button, Qt.MouseButton.LeftButton)
            self.assertEqual(requested[-1], purchase[channel])
            self.assertEqual(selections, [])
        self.assertEqual(requested, [purchase["taobao"], purchase["jd"]])
        point = self.card.thumbnail.mapTo(self.card, self.card.thumbnail.rect().center())
        QTest.mouseClick(self.card, Qt.MouseButton.LeftButton, pos=point)
        self.assertEqual(selections, [True])
        self.assertEqual(len(requested), 2)

    def test_optional_channels_follow_config_order_and_keep_first_link_aligned(self):
        purchase = DEVICE_PURCHASES[MODELS[0]["id"]]
        for channels in ({"taobao": purchase["taobao"]},
                         {"jd": purchase["jd"]},
                         {"taobao": "", "jd": purchase["jd"]},
                         {"taobao": purchase["taobao"], "jd": purchase["jd"]},
                         {"jd": purchase["jd"], "taobao": purchase["taobao"]}, {}):
            with self.subTest(channels=tuple(channels)):
                entry = {key: purchase[key] for key in ("advice", "advice_en")}
                entry.update(channels)
                with patch.dict(DEVICE_PURCHASES, {MODELS[0]["id"]: entry}):
                    card = DeviceModelCard(MODELS[0])
                try:
                    card.show()
                    self.app.processEvents()
                    expected = {key: url for key, url in channels.items() if url}
                    self.assertEqual(list(card.purchase_buttons), list(expected))
                    requested, selections = [], []
                    card.purchase_requested.connect(requested.append)
                    card.clicked.connect(lambda: selections.append(True))
                    if expected:
                        first = next(iter(card.purchase_buttons.values()))
                        self.assertEqual(first.mapTo(card, QPoint()).x(),
                                         card.purchase_advice.mapTo(card, QPoint()).x())
                        positions = [button.mapTo(card, QPoint()).x()
                                     for button in card.purchase_buttons.values()]
                        self.assertEqual(positions, sorted(positions))
                    for channel, button in card.purchase_buttons.items():
                        QTest.mouseClick(button, Qt.MouseButton.LeftButton)
                        self.assertEqual(requested[-1], expected[channel])
                    self.assertEqual(len(requested), len(expected))
                    self.assertEqual(selections, [])
                finally:
                    card.close()
                    card.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_advice_and_small_purchase_icons_share_a_text_column(self):
        self.assertFalse(self.card.purchase_advice.wordWrap())
        self.assertNotIn("\n", self.card.purchase_advice.text())
        self.assertEqual(self.card.purchase_advice_label.text(), "使用建议：")
        self.assertEqual(self.card.purchase_channels_label.text(), "购买渠道：")
        advice_x = self.card.purchase_advice.mapTo(self.card, QPoint()).x()
        links_x = self.card.purchase_links.mapTo(self.card, QPoint()).x()
        taobao_x = self.card.purchase_buttons["taobao"].mapTo(self.card, QPoint()).x()
        self.assertEqual(advice_x, links_x)
        self.assertEqual(advice_x, taobao_x)
        for button in self.card.purchase_buttons.values():
            self.assertTrue(button.renderer.isValid())
            self.assertGreater(button.logo_size.width(), 0)
            self.assertLessEqual(button.logo_size.height(), 18)
            self.assertTrue(button.accessibleName())
            self.assertTrue(button.toolTip())

    def test_advice_and_captions_switch_language_without_changing_links(self):
        purchase = DEVICE_PURCHASES[MODELS[0]["id"]]
        self.assertEqual(self.card.purchase_advice.text(), purchase["advice"])
        set_language("en")
        self.app.processEvents()
        self.assertEqual(self.card.purchase_advice.text(), purchase["advice_en"])
        self.assertEqual(self.card.purchase_advice_label.text(), t("使用建议："))
        self.assertEqual(self.card.purchase_channels_label.text(), t("购买渠道："))
        self.assertNotEqual(self.card.purchase_advice_label.text(), "使用建议：")
        requested = []
        self.card.purchase_requested.connect(requested.append)
        self.card.purchase_buttons["taobao"].click()
        self.assertEqual(requested, [purchase["taobao"]])
        set_language("zh-CN")
        self.app.processEvents()
        self.assertEqual(self.card.purchase_advice.text(), purchase["advice"])

    def test_visible_brand_bounds_share_a_horizontal_center_at_both_pixel_ratios(self):
        for button in self.card.purchase_buttons.values():
            button.clearFocus()
        self.app.processEvents()
        for pixel_ratio in (1, 2):
            with self.subTest(pixel_ratio=pixel_ratio):
                centers = {}
                for channel, button in self.card.purchase_buttons.items():
                    image = QImage(button.width() * pixel_ratio,
                                   button.height() * pixel_ratio,
                                   QImage.Format.Format_ARGB32_Premultiplied)
                    image.setDevicePixelRatio(pixel_ratio)
                    image.fill(Qt.GlobalColor.transparent)
                    button.render(image)
                    colored_rows = []
                    for y in range(image.height()):
                        for x in range(image.width()):
                            color = image.pixelColor(x, y)
                            if (color.alpha() and color.red() > 1.5 * color.green()
                                    and color.red() > 1.5 * color.blue()):
                                colored_rows.append(y)
                                break
                    self.assertTrue(colored_rows, channel)
                    # Use the visible bounds, including the bottom pixel's extent,
                    # rather than a centroid affected by the artwork's shape.
                    center_y = (min(colored_rows) + max(colored_rows) + 1) / (2 * pixel_ratio)
                    centers[channel] = button.mapTo(self.card, QPoint()).y() + center_y
                self.assertEqual(centers["taobao"], centers["jd"])

    def test_purchase_content_stays_inside_each_narrow_card_in_both_languages(self):
        for locale in ("zh-CN", "en"):
            set_language(locale)
            for model in MODELS:
                with self.subTest(locale=locale, model=model["id"]):
                    card = DeviceModelCard(model)
                    try:
                        card.resize(card.minimumWidth(), card.height())
                        card.show()
                        self.app.processEvents()
                        self.assertFalse(card.purchase_advice.wordWrap())
                        for control in (card.purchase_advice_label, card.purchase_advice,
                                        card.purchase_channels_label, card.purchase_links,
                                        *card.purchase_buttons.values()):
                            top_left = control.mapTo(card, QPoint())
                            bottom_right = control.mapTo(card, control.rect().bottomRight())
                            self.assertTrue(card.rect().contains(top_left), control.objectName())
                            self.assertTrue(card.rect().contains(bottom_right), control.objectName())
                    finally:
                        card.close()
                        card.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


if __name__ == "__main__":
    unittest.main()
