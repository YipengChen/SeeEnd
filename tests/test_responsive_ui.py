"""Native display changes, stable design relationships and DPI independence."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from importlib.util import find_spec
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HAS_QT = find_spec("PySide6") is not None
if HAS_QT:
    from PySide6.QtCore import QObject, QPoint, QRect, Signal
    from PySide6.QtWidgets import QApplication, QBoxLayout, QMainWindow
    from aiflame.core.engine import Engine
    from aiflame.desktop.localization import set_language
    from aiflame.desktop.widgets import Dashboard
    from aiflame.integrations.registry import SOURCES
    from tests.test_ui_recovery import ControlledAPI

    class ScreenFixture(QObject):
        geometryChanged = Signal(QRect)
        availableGeometryChanged = Signal(QRect)
        logicalDotsPerInchChanged = Signal(float)

        def __init__(self, area):
            super().__init__()
            self.area = area

        def availableGeometry(self):
            return self.area

        def change_area(self, area):
            self.area = area
            self.availableGeometryChanged.emit(area)

    class HandleFixture(QObject):
        screenChanged = Signal(object)

        def __init__(self, screen):
            super().__init__()
            self.current = screen

        def screen(self):
            return self.current

        def change_screen(self, screen):
            self.current = screen
            self.screenChanged.emit(screen)


@unittest.skipUnless(HAS_QT, "Install desktop dependencies")
class ResponsiveUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setStyle("Fusion")

    def setUp(self):
        set_language("zh-CN")
        self.work = tempfile.TemporaryDirectory()
        self.engine = Engine(Path(self.work.name) / "events.sqlite", sources=SOURCES)
        self.window = QMainWindow()
        with patch("aiflame.desktop.widgets.LocalAPI", ControlledAPI):
            self.widget = Dashboard(self.window)
        self.window.setCentralWidget(self.widget)
        self.screen = ScreenFixture(QRect(0, 0, 1512, 870))
        self.handle = HandleFixture(self.screen)
        self.handle_patch = patch.object(self.window, "windowHandle", return_value=self.handle)
        self.handle_patch.start()
        self.widget.ready = True
        self.window.resize(1180, 850)
        self.window.show()
        self.render()

    def tearDown(self):
        self.widget.shutdown()
        self.window.close()
        self.handle_patch.stop()
        self.engine.db.close()
        self.work.cleanup()
        set_language("zh-CN")

    def settle(self):
        for _ in range(8):
            self.app.processEvents()

    def render(self, language="zh-CN", count=4):
        snapshot = self.engine.snapshot()
        snapshot["settings"]["language"] = language
        for source in snapshot["sources"]:
            source["enabled"] = source["id"] in self.widget.home_source_icons
        events = [dict(id=str(index), source="codex", task=f"display-fixture-{index}",
                       state="done", reason="completed", created=time.time() - index * 60,
                       status="applied") for index in range(count)]
        snapshot.update(events=events, task_events=events, maintenance=False)
        self.widget.render(snapshot)
        self.settle()

    def geometry_in(self, widget, root):
        return widget.rect().translated(widget.mapTo(root, QPoint()))

    def assert_recent_rows_fit(self, available_events):
        body = self.widget.recent_list_body
        rows = [row for row in self.widget.recent_row_frames if not row.isHidden()]
        self.assertLessEqual(len(rows), available_events)
        previous = None
        for index, row in enumerate(rows):
            bounds = self.geometry_in(row, body)
            self.assertTrue(body.contentsRect().contains(bounds), (body.size(), bounds))
            self.assertEqual(row.property("last"), index == len(rows) - 1)
            if previous is not None:
                self.assertEqual(bounds.top() - previous.bottom() - 1, 8)
            previous = bounds
        if rows and len(rows) < available_events:
            # The next complete row and its gap must not fit in the remainder.
            self.assertLess(body.contentsRect().bottom() - previous.bottom(), rows[0].height() + 8)
        return rows

    def test_recent_quantity_follows_height_without_waiting_for_new_events(self):
        self.screen.change_area(QRect(0, 0, 3840, 2160))
        for language in ("zh-CN", "en"):
            self.render(language, count=40)
            counts = []
            for height in (600, 870, 1400, 600):
                self.window.resize(1512, height)
                self.settle()
                rows = self.assert_recent_rows_fit(40)
                counts.append(len(rows))
                self.assertTrue(rows)
                self.assertTrue(self.widget.recent_empty.isHidden())
                for index, labels in enumerate(self.widget.recent_rows[:len(rows)]):
                    self.assertIn(f"display-fixture-{index}", labels[1].toolTip())
            self.assertLess(counts[0], counts[1])
            self.assertLess(counts[1], counts[2])
            self.assertGreater(counts[2], 4)
            self.assertEqual(counts[0], counts[3])
        self.widget.recent_more.click()
        self.settle()
        self.assertEqual(self.widget.tabs.currentIndex(), 4)
        self.assertEqual(len(self.widget.visible_events), 40)

    def test_recent_event_count_does_not_change_card_height_or_empty_state(self):
        for language in ("zh-CN", "en"):
            for width in (1512, 920):
                self.window.resize(width, 850)
                sizes = set()
                for count in (0, 1, 40, 0):
                    self.render(language, count=count)
                    sizes.add((self.widget.recent_frame.height(), self.widget.home_sources_frame.height(),
                               self.widget.scene_frame.height()))
                    rows = self.assert_recent_rows_fit(count)
                    self.assertEqual(self.widget.recent_empty.isHidden(), bool(count))
                    self.assertEqual(bool(rows), bool(count))
                self.assertEqual(len(sizes), 1, (language, width, sizes))

    def test_resolution_changes_preserve_type_spacing_and_navigation(self):
        expected = None
        for width, height in ((1280, 720), (1512, 870), (1920, 1080), (2560, 1440), (1024, 600)):
            self.screen.change_area(QRect(0, 0, width, height))
            self.window.resize(width - 20, height - 30)
            self.settle()
            metrics = (self.widget.home_channel_title.font().pixelSize(),
                       self.widget.home_sources_button.font().pixelSize(),
                       self.widget.display_title.font().pixelSize(),
                       self.widget.home_sources_row.layout().contentsMargins().top(),
                       self.widget.home_sources_row.layout().spacing(),
                       self.widget.home_source_icons["codex"].size(),
                       self.widget.home_source_icons["codex"].iconSize())
            if expected is None:
                expected = metrics
            self.assertEqual(metrics, expected)
            self.assertTrue(self.screen.area.contains(self.window.frameGeometry()))
            for language in ("zh-CN", "en"):
                self.render(language)
                for page_index in range(6):
                    self.widget.navigate(page_index)
                    self.settle()
                    page = self.widget.tabs.widget(page_index)
                    self.assertEqual(page.horizontalScrollBar().maximum(), 0,
                                     (language, width, height, page_index))
                    self.assertLessEqual(page.widget().width(), page.viewport().width())

    def test_wide_workspace_is_bounded_centered_and_aligned_with_heading(self):
        for width, height in ((1920, 1080), (2560, 1440), (3440, 1440), (3840, 2160)):
            self.screen.change_area(QRect(0, 0, width, height))
            self.window.resize(width - 10, height - 10)
            self.render()
            page = self.widget.tabs.widget(0)
            content = page.widget().layout().itemAt(0).widget()
            self.assertLessEqual(content.width(), 1440)
            self.assertLessEqual(self.widget.home_body_frame.height(), 820)
            position = self.geometry_in(content, page.viewport())
            self.assertLessEqual(abs(position.center().x() - page.viewport().rect().center().x()), 1)
            apps = self.geometry_in(self.widget.home_sources_frame, self.widget)
            scene = self.geometry_in(self.widget.scene_frame, self.widget)
            recent = self.geometry_in(self.widget.recent_frame, self.widget)
            title = self.geometry_in(self.widget.breadcrumb, self.widget)
            self.assertEqual(apps.top(), scene.top())
            self.assertEqual(recent.bottom(), scene.bottom())
            self.assertEqual(apps.left(), recent.left())
            self.assertEqual(recent.top() - apps.bottom() - 1, 16)
            self.assertLessEqual(abs(title.left() - scene.left()), 1)
            self.assertAlmostEqual(scene.width() / apps.width(), 6 / 5, delta=.04)
            self.assertAlmostEqual(apps.height() / (apps.height() + recent.height()), .4, delta=.03)

    def test_sidebar_language_and_actual_available_width_drive_reflow(self):
        self.window.resize(1040, 800)
        self.render("zh-CN")
        self.assertEqual(self.widget.home_top.direction(), QBoxLayout.Direction.LeftToRight)
        self.render("en")
        self.assertEqual(self.widget.home_top.direction(), QBoxLayout.Direction.TopToBottom)
        # Five channels wrap to three rows at this English sidebar width.
        self.assertEqual(len({item.y() for item in self.widget.home_channel_items}), 3)
        for item in self.widget.home_channel_items:
            self.assertTrue(item.parentWidget().rect().contains(self.geometry_in(item, item.parentWidget())))
        self.widget.navigate(0)
        self.settle()
        apps = self.geometry_in(self.widget.home_sources_frame, self.widget)
        scene = self.geometry_in(self.widget.scene_frame, self.widget)
        self.assertGreater(apps.top(), scene.bottom())
        for group, sources in self.widget.home_source_groups.values():
            for source in sources:
                button = self.widget.home_source_icons[source]
                self.assertTrue(group.rect().contains(self.geometry_in(button, group)))

    def test_preference_channels_fit_when_wechat_connection_state_changes(self):
        from PySide6.QtWidgets import QPushButton
        from aiflame.desktop.localization import t

        self.screen.change_area(QRect(0, 0, 1920, 1440))
        for language in ("zh-CN", "en"):
            for width in (920, 1512):
                self.window.resize(width, 850)
                self.render(language)
                self.widget.navigate(3)
                for state in ({}, {"bound": True, "available": True},
                              {"bound": True, "available": False, "error": "Connection failed"},
                              {"login": {"state": "need_verifycode", "image": ""}}):
                    with self.subTest(language=language, width=width, state=state):
                        self.widget.render_wechat(state)
                        self.widget.update_notification_status("not_determined" if not state else "authorized")
                        self.settle()
                        page = self.widget.tabs.widget(3)
                        self.assertEqual(page.horizontalScrollBar().maximum(), 0)
                        self.assertFalse(any(button.text() == t("检查权限") for button in page.findChildren(QPushButton)))
                        for group in page.findChildren(QPushButton):
                            if not group.isVisible() or group.parentWidget().objectName() != "preferenceActions":
                                continue
                            bounds = self.geometry_in(group, page.viewport())
                            self.assertGreaterEqual(bounds.left(), 0)
                            self.assertLessEqual(bounds.right(), page.viewport().width())
                            self.assertGreaterEqual(group.width(), group.minimumSizeHint().width())

    def test_disconnected_wechat_actions_fit_narrow_english_with_wider_font(self):
        self.screen.change_area(QRect(0, 0, 1920, 1440))
        self.window.resize(920, 850)
        self.render("en")
        self.widget.navigate(3)
        buttons = (self.widget.wechat_prepare_button, self.widget.wechat_test_button,
                   self.widget.wechat_disconnect_button)
        # Font metrics vary across platforms; exercise a wider face even on macOS.
        for button in buttons:
            font = button.font()
            font.setStretch(115)
            button.setFont(font)
        self.widget.render_wechat({"bound": True, "available": False,
                                   "error": "Connection failed"})
        self.settle()
        page = self.widget.tabs.widget(3)
        self.assertEqual(page.horizontalScrollBar().maximum(), 0)
        for button in buttons:
            self.assertTrue(button.isVisible())
            bounds = self.geometry_in(button, page.viewport())
            self.assertGreaterEqual(bounds.left(), 0)
            self.assertLess(bounds.right(), page.viewport().width())
            self.assertGreaterEqual(button.width(), button.minimumSizeHint().width())

    def test_help_details_expand_without_truncating_bounded_paragraphs(self):
        from PySide6.QtWidgets import QLabel

        self.screen.change_area(QRect(0, 0, 1920, 1440))
        common = self.widget.help_disclosures[0].parentWidget()
        self.assertIs(self.widget.privacy_disclosure.parentWidget(), common)
        self.assertIs(self.widget.about_disclosure.parentWidget(), common)
        for language in ("zh-CN", "en"):
            for width in (920, 1512):
                with self.subTest(language=language, width=width):
                    self.window.resize(width, 720)
                    self.render(language, count=0)
                    self.widget.navigate(5)
                    for disclosure in self.widget.help_disclosures:
                        disclosure.button.setChecked(True)
                    self.settle()
                    page = self.widget.tabs.widget(5)
                    self.assertEqual(page.horizontalScrollBar().maximum(), 0)
                    for disclosure in self.widget.help_disclosures:
                        self.assertTrue(disclosure.body.isVisible())
                        for text in disclosure.body.findChildren(QLabel):
                            self.assertGreaterEqual(text.height() + 1, text.heightForWidth(text.width()))
                            self.assertTrue(disclosure.body.rect().contains(self.geometry_in(text, disclosure.body)))
                    page.ensureWidgetVisible(self.widget.about_disclosure.body)
                    self.settle()
                    self.assertGreater(page.verticalScrollBar().maximum(), 0)
                    self.assertTrue(page.viewport().rect().intersects(
                        self.geometry_in(self.widget.about_disclosure.body, page.viewport())))
        self.widget.about_disclosure.button.setChecked(False)
        self.settle()
        self.assertTrue(self.widget.about_disclosure.body.isHidden())

    def test_switching_screens_rebinds_signals_and_keeps_page_and_drafts(self):
        self.widget.navigate(4)
        self.widget.history_search.setText("keep this filter")
        second = ScreenFixture(QRect(1920, 0, 1280, 720))
        self.handle.change_screen(second)
        self.settle()
        self.assertIs(self.widget.adaptation.screen, second)
        self.assertTrue(second.area.contains(self.window.frameGeometry()))
        self.assertEqual(self.widget.tabs.currentIndex(), 4)
        self.assertEqual(self.widget.history_search.text(), "keep this filter")
        self.screen.change_area(QRect(0, 0, 1000, 600))
        self.assertFalse(self.widget.adaptation.timer.isActive())
        second.logicalDotsPerInchChanged.emit(144)
        self.assertTrue(self.widget.adaptation.timer.isActive())
        self.settle()
        self.widget.adaptation.close()
        second.change_area(QRect(1920, 0, 1100, 680))
        self.assertFalse(self.widget.adaptation.timer.isActive())

    def test_fitting_preserves_normal_geometry_and_maximized_state(self):
        self.window.move(100, 5)
        self.settle()
        before = self.window.geometry()
        self.screen.geometryChanged.emit(self.screen.area)
        self.settle()
        self.assertEqual(self.window.geometry(), before)
        self.window.showMaximized()
        self.screen.change_area(QRect(0, 0, 1024, 600))
        self.settle()
        self.assertTrue(self.window.isMaximized())

    def test_retina_scale_changes_raster_density_without_double_scaling_design(self):
        script = '''
import json, tempfile
from pathlib import Path
from unittest.mock import patch
from PySide6.QtWidgets import QApplication
from aiflame.core.engine import Engine
from aiflame.desktop.widgets import Dashboard
from aiflame.integrations.registry import SOURCES
from tests.test_ui_recovery import ControlledAPI
app = QApplication([])
app.setStyle('Fusion')
with tempfile.TemporaryDirectory() as work:
    engine = Engine(Path(work) / 'events.sqlite', sources=SOURCES)
    with patch('aiflame.desktop.widgets.LocalAPI', ControlledAPI):
        widget = Dashboard()
    widget.resize(1180, 850)
    widget.show()
    widget.render({**engine.snapshot(), 'maintenance': False})
    for _ in range(8): app.processEvents()
    print(json.dumps({'density': widget.devicePixelRatioF(),
        'font': widget.home_channel_title.font().pixelSize(),
        'scene': widget.scene_frame.geometry().getRect(),
        'icons': widget.home_source_icons['codex'].size().toTuple()}))
    widget.shutdown()
    engine.db.close()
'''
        observations = []
        for factor in ("1", "2"):
            result = subprocess.run([sys.executable, "-c", script], text=True, capture_output=True,
                                    env={**os.environ, "QT_SCALE_FACTOR": factor}, check=True)
            observations.append(json.loads(result.stdout.splitlines()[-1]))
        self.assertEqual(observations[1].pop("density"), 2 * observations[0].pop("density"))
        self.assertEqual(observations[0], observations[1])
