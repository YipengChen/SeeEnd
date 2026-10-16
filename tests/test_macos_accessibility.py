"""Exercise the Cocoa selector that crashed the frozen app, in its own process."""

import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from aiflame.desktop.macos_accessibility import _selected_children, install_selection_guard


class SelectionTests(unittest.TestCase):
    def test_registered_ids_reject_removed_children_and_preserve_selection(self):
        class Node:
            synthesizedRole = None
            def __init__(self, identity):
                self.axid = identity
            def accessibilityChildren(self):
                return []
        children = [Node(1), Node(2), Node(3), Node(4)]
        root = SimpleNamespace(accessibilityChildren=lambda: children, synthesizedRole="AXRow")
        interfaces = {1: SimpleNamespace(isValid=lambda: True, state=lambda: SimpleNamespace(selected=True)),
                      2: SimpleNamespace(isValid=lambda: True, state=lambda: SimpleNamespace(selected=False)),
                      3: SimpleNamespace(isValid=lambda: False)}
        accessible = SimpleNamespace(accessibleInterface=interfaces.get)
        objc = SimpleNamespace(getInstanceVariable=getattr)
        self.assertEqual(_selected_children(root, objc, accessible, object), [children[0]])
        interfaces.pop(1)
        self.assertEqual(_selected_children(root, objc, accessible, object), [])

    def test_unreadable_native_child_does_not_drop_other_selection(self):
        selected = SimpleNamespace(axid=1, synthesizedRole=None)
        root = SimpleNamespace(accessibilityChildren=lambda: [object(), selected], synthesizedRole="AXRow")
        interface = SimpleNamespace(isValid=lambda: True, state=lambda: SimpleNamespace(selected=True))
        self.assertEqual(_selected_children(root, SimpleNamespace(getInstanceVariable=getattr),
                                          SimpleNamespace(accessibleInterface=lambda _: interface), object), [selected])

    def test_other_platforms_do_not_install_a_native_override(self):
        with patch("aiflame.desktop.macos_accessibility.sys.platform", "linux"):
            self.assertFalse(install_selection_guard())

    @unittest.skipUnless(sys.platform == "darwin", "requires the actual Qt Cocoa platform")
    def test_actual_cocoa_selection_after_resize_and_destroy(self):
        script = r'''
import json
import objc
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtGui import QAccessible
from PySide6.QtWidgets import QApplication, QAbstractItemView, QListWidget, QTableWidget, QTableWidgetItem
from aiflame.desktop.macos_accessibility import install_selection_guard

app = QApplication([])
assert install_selection_guard() and install_selection_guard()
element_class = objc.lookUpClass("QMacAccessibilityElement")
table = QTableWidget(3, 6)
table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
for row in range(3):
    for column in range(6):
        table.setItem(row, column, QTableWidgetItem(f"{row}/{column}"))
table.setColumnHidden(4, True)
table.show()
app.processEvents()
def current_element():
    # Qt can retire a whole accessible node on model changes. An AX client
    # refreshes its current tree; old nodes must be safe to query as well.
    return element_class.elementWithId_(QAccessible.uniqueId(QAccessible.queryAccessibleInterface(table)))

element = current_element()
queries = 0
for iteration in range(100):
    table.setRowCount(3)
    table.selectRow(2)
    app.processEvents()
    element = current_element()
    selected = element.accessibilitySelectedChildren()
    assert len(selected) == 5, len(selected)
    queries += 1
    for row_element in element.accessibilityRows():
        assert len(row_element.accessibilitySelectedChildren()) == (5 if objc.getInstanceVariable(row_element, "m_rowIndex") == 2 else 0)
        queries += 1
    table.setRowCount(1)
    table.clearSelection()
    app.processEvents()
    element = current_element()
    assert not element.accessibilitySelectedChildren()
    table.selectRow(0)
    element = current_element()
    assert len(element.accessibilitySelectedChildren()) == 5
    queries += 2
    table.clearSelection()
    assert not element.accessibilitySelectedChildren()
    queries += 1
table.close()
table.deleteLater()
QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
app.processEvents()
assert not element.accessibilitySelectedChildren()
queries += 1
items = QListWidget()
items.addItems(["one", "two", "three"])
items.show()
items.setCurrentRow(1)
app.processEvents()
list_element = element_class.elementWithId_(QAccessible.uniqueId(QAccessible.queryAccessibleInterface(items)))
assert len(list_element.accessibilitySelectedChildren()) == 1
items.clear()
app.processEvents()
assert not list_element.accessibilitySelectedChildren()
queries += 2
items.close()
print(json.dumps({"native_selection_queries": queries}))
'''
        root = Path(__file__).resolve().parents[1]
        env = {**os.environ, "QT_QPA_PLATFORM": "cocoa", "PYTHONPATH": str(root)}
        result = subprocess.run([sys.executable, "-c", script], cwd=root, env=env,
                                capture_output=True, text=True, timeout=45)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertGreaterEqual(json.loads(result.stdout)["native_selection_queries"], 700)


if __name__ == "__main__":
    unittest.main()
