import os
from importlib.util import find_spec
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@unittest.skipUnless(sys.platform == "darwin" and find_spec("PySide6") is not None, "macOS native tray requires desktop dependencies")
class MacTrayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from aiflame.desktop.macos_shell import MacTray
        from PySide6.QtGui import QIcon, QPixmap
        from PySide6.QtWidgets import QMenu
        class Button:
            def setImage_(self, value): self.image = value
            def setToolTip_(self, value): self.tooltip = value
        class Item:
            def __init__(self): self.control = Button()
            def button(self): return self.control
            def setMenu_(self, value): self.menu = value
        class Bar:
            def __init__(self): self.items = []
            def statusItemWithLength_(self, length):
                item = Item()
                self.items.append(item)
                return item
            def removeStatusItem_(self, item): self.items.remove(item)
        self.bar = Bar()
        pixmap = QPixmap(36, 36)
        pixmap.fill()
        self.tray = MacTray(QIcon(pixmap), status_bar=self.bar)
        self.menu = QMenu()
        self.action = self.menu.addAction("暂停提醒")
        self.action.setCheckable(True)
        self.menu.addSeparator()
        self.tray.setContextMenu(self.menu)
        self.native = self.tray._native_menu.itemAtIndex_(0)

    def tearDown(self):
        self.tray.shutdown()

    def test_state_and_disabled_dispatch(self):
        self.action.setText("恢复提醒")
        self.action.setChecked(True)
        self.action.setEnabled(False)
        self.assertEqual(self.native.title(), "恢复提醒")
        self.assertEqual(self.native.state(), 1)
        self.assertFalse(self.native.isEnabled())
        calls = []
        self.action.triggered.connect(lambda checked: calls.append(checked))
        self.tray._target.invoke_(self.native)
        self.app.processEvents()
        self.assertEqual(calls, [])
        self.action.setEnabled(True)
        self.tray._target.invoke_(self.native)
        self.app.processEvents()
        self.assertEqual(calls, [False])
        self.assertEqual(self.native.state(), 0)

    def test_menu_callbacks_defer_until_closed(self):
        calls = []
        self.menu.aboutToShow.connect(lambda: calls.append("open"))
        self.action.triggered.connect(lambda: calls.append("trigger"))
        self.tray._target.menuWillOpen_(self.tray._native_menu)
        self.tray._target.invoke_(self.native)
        self.app.processEvents()
        self.assertEqual(calls, ["open"])
        self.tray._target.menuDidClose_(self.tray._native_menu)
        self.app.processEvents()
        self.assertEqual(calls, ["open", "trigger"])

    def test_disabled_before_delivery_and_replaced_menu(self):
        from PySide6.QtWidgets import QMenu
        calls = []
        self.action.triggered.connect(lambda checked: calls.append(checked))
        self.tray._target.invoke_(self.native)
        self.action.setEnabled(False)
        self.app.processEvents()
        self.assertEqual(calls, [])
        old_native = self.native
        replacement = QMenu()
        replacement.addAction("新菜单")
        self.tray.setContextMenu(replacement)
        self.assertIsNone(old_native.target())
        self.action.setText("已脱离")
        self.assertNotEqual(old_native.title(), "已脱离")

    def test_retina_icon_and_idempotent_cleanup(self):
        self.tray.setToolTip("SeeEnd")
        self.tray.show()
        self.tray.show()
        self.assertEqual(len(self.bar.items), 1)
        item = self.bar.items[0]
        self.assertEqual(item.control.image.size(), (18, 18))
        self.assertEqual(item.control.tooltip, "SeeEnd")
        self.assertEqual(item.control.image.representations()[0].pixelsWide(), 36)
        self.tray._target.invoke_(self.native)
        calls = []
        self.action.triggered.connect(lambda checked: calls.append(checked))
        self.tray.shutdown()
        self.tray.shutdown()
        self.app.processEvents()
        self.assertEqual(calls, [])
        self.assertEqual(self.bar.items, [])
        self.assertIsNone(self.native.target())
        self.action.setText("after shutdown")
        self.assertNotEqual(self.native.title(), "after shutdown")

    def test_repeated_native_menu_tracking_and_dispatch(self):
        calls = []
        self.action.triggered.connect(lambda checked: calls.append(checked))
        for _ in range(50):
            self.tray._target.menuWillOpen_(self.tray._native_menu)
            self.tray._target.invoke_(self.native)
            self.app.processEvents()
            self.tray._target.menuDidClose_(self.tray._native_menu)
            self.app.processEvents()
        self.assertEqual(len(calls), 50)
        self.assertEqual(calls, [True, False] * 25)


if __name__ == "__main__":
    unittest.main()
