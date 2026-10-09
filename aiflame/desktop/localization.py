"""Localized Qt presentation controls that retain their original source strings.

Reapply only displayed text when the language changes. Never translate editable
values, configuration snippets, protocol identifiers, or data attached to items.
"""

import weakref
from PySide6 import QtGui, QtWidgets
from PySide6.QtCore import QSignalBlocker, Qt
from aiflame.core.i18n import translate

_language = "zh-CN"
_controls = weakref.WeakSet()


def t(text):
    return translate(text, _language)


def language():
    return _language


def set_language(value):
    global _language
    if value not in ("zh-CN", "en") or value == _language:
        return
    _language = value
    for control in list(_controls):
        try:
            control.retranslate()
        except RuntimeError:
            # Qt can have deleted an object before Python releases its wrapper.
            _controls.discard(control)


class Localized:
    def __init__(self, *args, **kwargs):
        self._source_text = {}
        super().__init__(*args, **kwargs)
        _controls.add(self)
        if isinstance(self, (QtWidgets.QLabel, QtWidgets.QAbstractButton, QtGui.QAction)):
            self.setText(self.text())

    def retranslate(self):
        with QSignalBlocker(self):
            for method, source in self._source_text.items():
                getattr(super(), method)(t(source))
            if isinstance(self, QtWidgets.QComboBox) and not self.isEditable():
                for index in range(self.count()):
                    source = self.itemData(index, Qt.ItemDataRole.UserRole + 99)
                    if source is not None:
                        super().setItemText(index, t(source))
            if isinstance(self, QtWidgets.QTableWidget):
                for index in range(self.columnCount()):
                    item = self.horizontalHeaderItem(index)
                    if item:
                        QtWidgets.QTableWidgetItem.setText(item, t(item.data(Qt.ItemDataRole.UserRole + 99) or item.text()))
                for row in range(self.rowCount()):
                    for column in range(self.columnCount()):
                        item = self.item(row, column)
                        if item:
                            QtWidgets.QTableWidgetItem.setText(item, t(item.data(Qt.ItemDataRole.UserRole + 99) or item.text()))


def source_text(control):
    return control._source_text.get("setText", control.text())


def text_setter(base, method):
    def setter(self, text):
        self._source_text[method] = text
        getattr(base, method)(self, t(text))
    return setter


def add_item(self, text, *args):
    # Editable combo boxes contain user Wi-Fi names; preserve them verbatim.
    if self.isEditable():
        return QtWidgets.QComboBox.addItem(self, text, *args)
    if isinstance(text, QtGui.QIcon):
        icon, text, args = text, args[0], args[1:]
        QtWidgets.QComboBox.addItem(self, icon, t(text), *args)
    else:
        QtWidgets.QComboBox.addItem(self, t(text), *args)
    self.setItemData(self.count() - 1, text, Qt.ItemDataRole.UserRole + 99)


def set_item_text(self, index, text):
    QtWidgets.QComboBox.setItemText(self, index, text if self.isEditable() else t(text))
    if not self.isEditable():
        self.setItemData(index, text, Qt.ItemDataRole.UserRole + 99)


def add_items(self, texts):
    for text in texts:
        self.addItem(text)


def clear_label(self):
    self._source_text["setText"] = ""
    QtWidgets.QLabel.clear(self)


def set_headers(self, texts):
    QtWidgets.QTableWidget.setHorizontalHeaderLabels(self, [t(text) for text in texts])
    for index, text in enumerate(texts):
        self.horizontalHeaderItem(index).setData(Qt.ItemDataRole.UserRole + 99, text)


def localized_type(base):
    methods = {}
    for method in ("setToolTip", "setAccessibleName", "setAccessibleDescription", "setPlaceholderText", "setSuffix"):
        if hasattr(base, method):
            methods[method] = text_setter(base, method)
    if issubclass(base, (QtWidgets.QLabel, QtWidgets.QAbstractButton, QtGui.QAction)):
        methods["setText"] = text_setter(base, "setText")
    if issubclass(base, QtWidgets.QLabel):
        methods["clear"] = clear_label
    if issubclass(base, QtWidgets.QComboBox):
        methods.update(addItem=add_item, addItems=add_items, setItemText=set_item_text)
    if issubclass(base, QtWidgets.QTableWidget):
        methods["setHorizontalHeaderLabels"] = set_headers
    return type(base.__name__, (Localized, base), methods)


for _name in (
    "QLabel", "QPushButton", "QCheckBox", "QComboBox", "QFrame", "QLineEdit",
    "QPlainTextEdit", "QProgressBar", "QScrollArea", "QSlider", "QSpinBox",
    "QStackedWidget", "QTableWidget", "QTimeEdit", "QWidget", "QSystemTrayIcon",
):
    globals()[_name] = localized_type(getattr(QtWidgets, _name))
QAction = localized_type(QtGui.QAction)


class QTableWidgetItem(QtWidgets.QTableWidgetItem):
    def __init__(self, text=""):
        super().__init__(t(text))
        self.setData(Qt.ItemDataRole.UserRole + 99, text)

    def setText(self, text):
        self.setData(Qt.ItemDataRole.UserRole + 99, text)
        super().setText(t(text))
