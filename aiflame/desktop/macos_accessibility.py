"""Read current AX selection without Qt Cocoa's dangling selectedItems pointers.

Qt 6.11's accessibilitySelectedChildren iterates raw QAccessibleInterface pointers.
macOS can request that attribute after a table model has invalidated its cells.
Use the current native children and registered IDs instead. This is local to this
process; accessibility, selection and keyboard actions stay enabled.
"""

import sys

_selector = None


def _selected_children(element, objc, accessible, item_view):
    if objc.getInstanceVariable(element, "synthesizedRole") is None:
        interface = accessible.accessibleInterface(objc.getInstanceVariable(element, "axid"))
        if interface is not None and interface.isValid():
            view = interface.object()
            if isinstance(view, item_view) and element.accessibilityRole() == "AXTable":
                # Even unchanged dimensions may contain native cells whose
                # registered IDs were removed by a model update. Recreate the
                # synthetic containers before reading current selection.
                element.updateTableModel()

    def selected(child):
        interface = accessible.accessibleInterface(objc.getInstanceVariable(child, "axid"))
        if interface is None or not interface.isValid():
            return False
        # Synthetic table cells share the table's AX ID. Test their current
        # model index, rather than treating them as the parent table.
        role = objc.getInstanceVariable(child, "synthesizedRole")
        if role == "AXCell":
            view = interface.object()
            if not isinstance(view, item_view) or view.selectionModel() is None:
                return False
            row = objc.getInstanceVariable(child, "m_rowIndex")
            column = objc.getInstanceVariable(child, "m_columnIndex")
            hidden_column = getattr(view, "isColumnHidden", None)
            if hidden_column and hidden_column(column):
                return False
            index = view.model().index(row, column)
            return index.isValid() and view.selectionModel().isSelected(index)
        return interface.state().selected

    result = []
    for child in element.accessibilityChildren() or ():
        try:
            role = objc.getInstanceVariable(child, "synthesizedRole")
            if role == "AXRow":
                # AX rows are native containers; return their selected cells,
                # as Qt's table selection interface does. Hidden columns are
                # excluded by the current view, independent of native caches.
                result.extend(_selected_children(child, objc, accessible, item_view))
            elif role != "AXColumn" and selected(child):
                result.append(child)
        except Exception:
            # A removed or non-Qt native child has no valid Qt selection.
            continue
    return result


def install_selection_guard():
    """Install after QApplication creates the Cocoa platform plugin, once."""
    global _selector
    if sys.platform != "darwin":
        return False
    if _selector is not None:
        return True
    import objc
    from PySide6.QtGui import QAccessible
    from PySide6.QtWidgets import QAbstractItemView

    try:
        element_class = objc.lookUpClass("QMacAccessibilityElement")
    except objc.nosuchclass_error:
        # Isolated offscreen tests and non-Cocoa Qt platforms have no bridge.
        return False

    def accessibilitySelectedChildren(element):
        try:
            return _selected_children(element, objc, QAccessible, QAbstractItemView)
        except Exception:
            return []

    selector = objc.selector(accessibilitySelectedChildren,
                             selector=b"accessibilitySelectedChildren", signature=b"@@:")
    objc.classAddMethods(element_class, [selector])
    _selector = selector  # Keep the Python implementation alive with the native method.
    return True
