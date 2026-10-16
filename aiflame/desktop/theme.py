"""Shared, deliberately small design system for the native desktop app."""

from pathlib import Path

COLORS = {"ink": "#292824", "secondary": "#625f57", "muted": "#75736b",
          "canvas": "#f6f5f1", "surface": "#ffffff", "accent": "#b34a1e",
          "success": "#38735b", "warning": "#9a5e24", "danger": "#a0472c",
          "tooltip_surface": "#fffdf9", "tooltip_border": "#dedcd4"}

TOOLTIP_STYLE = f"""
QToolTip {{
    background: {COLORS['tooltip_surface']}; color: {COLORS['ink']};
    border: 1px solid {COLORS['tooltip_border']}; border-radius: 8px;
    padding: 10px 12px; font-size: 13px; font-weight: 400;
    qproperty-margin: 0; qproperty-indent: 0;
}}
"""

STYLE = """
QWidget { color: #292824; font-size: 14px; font-weight: 400; }
QWidget#dashboard, QScrollArea, QWidget#page { background: #f6f5f1; }
QDialog { background: #f6f5f1; }
QMessageBox#confirmation QLabel#qt_msgbox_label { font-size: 16px; font-weight: 600; }
QMessageBox#confirmation QLabel#qt_msgbox_informativelabel { font-size: 14px; font-weight: 400; }
QDialog#extensionGuide, QWidget#extensionGuideBody { background: #f6f5f1; }
QDialog#extensionGuide QLabel[role="guideTitle"] { font-size: 20px; font-weight: 600; }
QDialog#extensionGuide QLabel[role="guideProgress"] { color: #625f57; font-size: 12px; }
QDialog#extensionGuide QLabel[role="muted"] { color: #625f57; }
QDialog#extensionGuide QLabel[role="guideStatus"] { padding: 10px 12px; background: #eeede7; border-radius: 8px; }
QDialog#extensionGuide QLabel[guideTone="success"] { color: #285c43; background: #e7f0e9; }
QDialog#extensionGuide QLabel[guideTone="warning"] { color: #814019; background: #fbeddc; }
QWidget#sidebar { background: #eeede8; border-right: 1px solid #e0dfd9; }
QWidget#topbar { background: #f6f5f1; }
QWidget#statusbar { background: #f6f5f1; border-top: 1px solid #e4e2dc; }
QLabel { background: transparent; border: none; }
QLabel[role="muted"] { color: #75736b; font-size: 12px; }
QLabel[role="stepProgress"] { color: #75736b; font-size: 16px; }
QLabel[role="eyebrow"] { color: #8c7561; font-size: 10px; font-weight: 600; letter-spacing: 2px; }
QLabel[role="heading"] { font-size: 28px; font-weight: 600; color: #24241f; }
QLabel[role="moduleTitle"] { font-size: 24px; font-weight: 600; color: #292824; }
QLabel[role="pageTitle"] { font-size: 20px; font-weight: 600; color: #292824; }
QLabel[role="title"], QLabel[role="sectionTitle"], QPushButton[variant="sectionTitle"] { font-size: 16px; font-weight: 600; color: #292824; }
QLabel[role="homeSourceGroup"] { font-size: 14px; font-weight: 500; color: #625f57; }
QLabel[role="lightStateTitle"] { font-size: 14px; font-weight: 600; }
QLabel[role="lightStateTitle"][tone="success"] { color: #a34235; background: transparent; }
QLabel[role="lightValue"] { font-size: 14px; font-weight: 500; color: #59564e; }
QLabel[role="preferenceLabel"] { font-size: 14px; font-weight: 500; }
QLabel[role="homeChannelTitle"] { color: #625f57; font-size: 14px; font-weight: 700; }
QLabel[role="preferenceHint"], QLabel[role="preferenceStatus"] { color: #625f57; font-size: 12px; }
QLabel[role="preferenceStatus"][tone="success"] { color: #38735b; background: transparent; }
QLabel[role="preferenceStatus"][tone="warning"], QLabel[role="preferenceHint"][tone="warning"] { color: #9a5e24; background: transparent; }
QLabel[role="brand"] { font-size: 21px; font-weight: 600; letter-spacing: -0.5px; }
QLabel[role="tagline"] { color: #625f57; font-size: 12px; }
QLabel[role="number"] { font-size: 24px; font-weight: 500; }
QLabel[role="badge"] { background: #ecebe6; color: #625f57; border-radius: 10px; padding: 5px 10px; font-size: 12px; }
QLabel[tone="success"] { color: #38735b; background: #e7f0e9; }
QLabel[tone="warning"] { color: #9a5e24; background: #fbeddc; }
QFrame#deviceStatus { background: #ecebe6; border-radius: 10px; }
QFrame#deviceStatus QLabel { color: #625f57; font-size: 12px; }
QFrame#deviceStatus[tone="success"] { background: #e7f0e9; }
QFrame#deviceStatus[tone="success"] QLabel { color: #38735b; }
QFrame#deviceStatus[tone="warning"] { background: #fbeddc; }
QFrame#deviceStatus[tone="warning"] QLabel { color: #9a5e24; }
QFrame#card, QFrame#homeChannels { background: #ffffff; border: 1px solid #e5e3dd; border-radius: 16px; }
QFrame#scene { background: #252723; border: 1px solid #30332c; border-radius: 18px; }
QFrame#scene QLabel { color: #f6f2e9; }
QFrame#scene QLabel[role="muted"] { color: #b1b3a7; }
QFrame#scene QLabel[role="eyebrow"] { color: #bbb7a8; }
QFrame#scene QLabel[role="badge"] { background: #35382f; color: #d9dacd; }
QFrame#notice { background: #fff1df; border: 1px solid #f0d9b8; border-radius: 10px; }
QFrame#notice QLabel { color: #805523; }
QFrame#hardwareNotice { background: #fff1df; border: 1px solid #f0d9b8; border-radius: 10px; }
QFrame#hardwareNotice QLabel { color: #805523; }
QFrame#sourceRow { background: transparent; border: none; }
QFrame#recentEvent { background: transparent; border: none; border-bottom: 1px solid #e9e7e1; }
QFrame#recentEvent[last="true"] { border-bottom: none; }
QLabel[role="recentSource"] { font-size: 14px; font-weight: 500; }
QLabel[role="recentState"] { font-size: 12px; color: #75736b; }
QLabel[role="recentState"][recentTone="working"] { color: #b34a1e; }
QLabel[role="recentState"][recentTone="done"] { color: #38735b; }
QLabel[role="recentState"][recentTone="needs_input"] { color: #9a5e24; }
QLabel[tone="error"] { color: #a0472c; background: #fcf0eb; }
QLabel[role="sourceStatus"] { font-size: 12px; padding: 3px 8px; border-radius: 6px; }
QLabel[role="sourceStatus"][tone="neutral"] { color: #625f57; background: #eeede8; }
QFrame#separator { background: #e9e7e1; border: none; max-height: 1px; }
QFrame#sourceDivider { background: rgba(117, 115, 107, 35); border: none; max-height: 1px; }
QPushButton#lightModeChoice { background: #f6f5f1; border: 1px solid #e5e3dd; border-radius: 9px; padding: 8px 14px; color: #625f57; }
QPushButton#lightModeChoice:hover { background: #eeede7; border-color: #c7c3b8; }
QPushButton#lightModeChoice:checked { background: #fbede5; color: #9e401b; border-color: #b34a1e; font-weight: 600; }
QPushButton#lightModeChoice:focus { border: 2px solid #b34a1e; padding: 7px 13px; }
QPushButton#lightModeChoice:disabled { color: #969287; border-color: #e5e3dd; background: #f3f2ee; }
QPushButton#lightModeChoice:checked:disabled { color: #8e6f5f; background: #f5ede7; border-color: #cdbcae; }
QPushButton#lightPreviewTile { background: #f6f5f1; border: 1px solid #eeece6; border-radius: 10px; padding: 0; }
QPushButton#lightPreviewTile:hover { background: #f0eee8; border-color: #d9d4ca; }
QPushButton#lightPreviewTile:checked { background: #fbefe7; border-color: #cf5c32; }
QPushButton#lightPreviewTile:focus { border: 2px solid #a64725; }
QPushButton#lightPreviewTile:disabled { background: #f6f5f1; border-color: #eeece6; color: #99958c; }
QPushButton#deviceModelCard { background: #ffffff; border: 1px solid #e2dfd7; border-radius: 14px; padding: 0; }
QPushButton#deviceModelCard:hover { background: #fffdf9; border-color: #d1a487; }
QPushButton#deviceModelCard:pressed { background: #faf2e9; border-color: #c85b2b; }
QPushButton#deviceModelCard:focus { border: 2px solid #c85b2b; }
QPushButton#deviceModelCard:disabled { background: #f1f0ec; border-color: #e8e6df; }
QPushButton#deviceModelCard[instance="true"] { min-height: 88px; max-height: 88px; }
QLabel#deviceThumbnail { background: #f5f0e7; border-radius: 10px; }
QLabel#deviceThumbnail[kind="rgb"] { background: #f0f3f1; }
QLabel[role="deviceBrand"] { color: #625f57; font-size: 12px; font-weight: 500; }
QLabel[role="deviceName"] { color: #33332d; font-size: 16px; font-weight: 600; }
QLabel[role="deviceMeta"], QLabel[role="deviceCount"] { color: #625f57; font-size: 12px; }
QLabel[role="deviceCount"][online="true"] { color: #38735b; font-weight: 600; }
QLabel[role="deviceArrow"] { color: #b38b70; font-size: 20px; }
QLabel[role="deviceName"]:disabled, QLabel[role="deviceCount"]:disabled { color: #aaa69b; }
QPushButton { background: #ffffff; border: 1px solid #dedcd4; border-radius: 8px; padding: 8px 12px; font-weight: 500; min-height: 20px; }
QPushButton:hover { background: #f4f2ed; border-color: #bebbb1; }
QPushButton:pressed { background: #e9e6df; }
QPushButton:focus { border: 1px solid #c85b2b; }
QPushButton:disabled { color: #a5a299; background: #f1f0ec; border-color: #e8e6df; }
QPushButton#preferenceHelp { background: transparent; border: 1px solid transparent; border-radius: 6px; padding: 0; min-height: 0; }
QPushButton#sourceLink { padding: 0; min-height: 0; }
QPushButton#preferenceHelp:hover { background: #f4f2ed; }
QPushButton#preferenceHelp:focus { border-color: #c85b2b; }
QPushButton[variant="primary"] { background: #b34a1e; border-color: #b34a1e; color: #ffffff; font-weight: 600; }
QPushButton[variant="primary"]:hover { background: #963c18; border-color: #963c18; }
QPushButton[variant="primary"]:pressed { background: #803315; border-color: #803315; }
QPushButton[variant="primary"]:disabled { background: #e7cabb; border-color: #e7cabb; color: #faf5f1; }
QPushButton[deviceAction="preview"] { background: #b34a1e; border-color: #b34a1e; color: #ffffff; font-weight: 600; }
QPushButton[deviceAction="preview"]:hover { background: #963c18; border-color: #963c18; }
QPushButton[deviceAction="preview"]:pressed { background: #803315; border-color: #803315; }
QPushButton[deviceAction="disconnect"] { background: #f4f2ed; border-color: #dedbd2; color: #625f57; }
QPushButton[deviceAction="disconnect"]:hover { background: #eae7df; border-color: #bdb8ac; color: #292824; }
QPushButton[deviceAction="disconnect"]:pressed { background: #dfdbd1; }
QPushButton[deviceAction="delete"] { background: #ffffff; border-color: #e5c9c4; color: #a34235; }
QPushButton[deviceAction="delete"]:hover { background: #fbefed; border-color: #c97c70; }
QPushButton[deviceAction="delete"]:pressed { background: #f4ded9; }
QPushButton[deviceAction]:focus { border: 2px solid #292824; padding: 7px 11px; }
QPushButton[deviceAction]:disabled { background: #f1f0ec; border-color: #e8e6df; color: #a5a299; }
QPushButton[variant="quiet"] { background: transparent; border-color: transparent; color: #736e63; padding: 7px 9px; }
QPushButton[variant="quiet"]:hover { background: #e9e6de; color: #39372f; }
QPushButton[variant="quiet"]:focus { border-color: #c85b2b; }
QPushButton#returnTaskAction { min-height: 0; padding: 0 5px; border-radius: 4px; font-size: 12px; }
QPushButton#returnTaskAction:disabled { color: #a5a299; background: transparent; border-color: transparent; }
QPushButton#faqQuestion { padding: 7px 0; text-align: left; }
QWidget#preferenceActions QPushButton { padding: 5px 8px; }
QWidget#preferenceActions QPushButton[variant="quiet"] { padding: 5px 6px; }
QPushButton#homeChannelSettings { padding: 0; min-height: 0; }
QPushButton[variant="shortcut"] { background: transparent; border-color: transparent; text-align: left; padding: 12px 10px; color: #39372f; }
QPushButton[variant="shortcut"]:hover { background: #f4f2ed; }
QPushButton[variant="shortcut"]:focus { border-color: #c85b2b; }
QPushButton[variant="sectionTitle"] { background: transparent; border-color: transparent; text-align: left; padding: 0; min-height: 32px; }
QPushButton[variant="sectionTitle"]:hover { background: #f4f2ed; }
QPushButton[variant="sectionTitle"]:focus { border-color: #c85b2b; }
QPushButton[variant="webSourceTitle"] { background: transparent; border-color: transparent; text-align: left; padding: 0; min-height: 0; font-size: 15px; font-weight: 600; color: #292824; }
QPushButton[variant="webSourceTitle"]:hover { color: #b34a1e; }
QPushButton[variant="webSourceTitle"]:focus { border-color: #c85b2b; }
QPushButton[variant="brand"] { background: transparent; border-color: transparent; padding: 0; min-height: 0; }
QPushButton[variant="brand"]:hover { background: #f4f2ed; }
QPushButton[variant="brand"]:focus { border-color: #c85b2b; }
QPushButton[variant="scene"] { background: transparent; color: #f6f2e9; border-color: #73766a; padding: 8px 16px; }
QPushButton[variant="scene"]:hover { background: #35382f; border-color: #b1b3a7; }
QPushButton[variant="scene"]:pressed { background: #404436; }
QPushButton[variant="scene"]:focus { border-color: #edac77; }
QPushButton[variant="scene"]:disabled { background: #2a2d25; color: #8e9186; border-color: #484c40; }
QPushButton[variant="danger"] { color: #a0472c; background: #ffffff; border-color: #e4c8bf; }
QPushButton[variant="danger"]:hover { background: #fcf0eb; border-color: #a0472c; }
QPushButton[variant="danger"]:pressed { background: #f3ded6; }
QPushButton[variant="nav"] { background: transparent; border: 1px solid transparent; color: #747268; border-radius: 9px; text-align: left; padding: 12px 16px; }
QPushButton[variant="nav"]:hover { background: #e6e4de; }
QPushButton[variant="nav"]:checked { background: #fff9ee; border-color: #e8decc; color: #a34d27; font-weight: 600; }
QPushButton[variant="nav"]:focus { border-color: #c85b2b; }
QPushButton[variant="choice"] { min-height: 20px; }
QPushButton[variant="choice"]:checked { background: #fff0e5; border-color: #d48760; color: #a04a24; }
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QTimeEdit { background: #fbfaf7; border: 1px solid #dedcd3; border-radius: 8px; padding: 7px 10px; min-height: 22px; selection-background-color: #f4d6c4; selection-color: #563323; }
QLineEdit { placeholder-text-color: #75736b; }
QComboBox { padding-right: 30px; }
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QTimeEdit:focus { border-color: #c85b2b; background: #ffffff; }
QFrame#durationControl { background: #fbfaf7; border: 1px solid #dedcd3; border-radius: 8px; }
QSpinBox#durationValue { background: transparent; border: none; border-radius: 0; padding: 0; min-height: 0; font-size: 14px; font-weight: 500; }
QSpinBox#durationValue:focus { background: #ffffff; border-radius: 4px; }
QSpinBox#durationValue:disabled { color: #a19c90; }
QPushButton#durationStep { background: transparent; border: 1px solid transparent; border-radius: 5px; padding: 0; min-height: 0; }
QPushButton#durationStep:hover { background: #eeebe4; }
QPushButton#durationStep:pressed { background: #e4dfd4; }
QPushButton#durationStep:focus { border-color: #c85b2b; }
QPushButton#durationStep:disabled { background: transparent; }
QLineEdit[invalid="true"] { border-color: #b65034; background: #fff8f3; }
QLineEdit:disabled, QTimeEdit:disabled { background: #efeee9; color: #a19c90; }
QComboBox::drop-down { border: none; width: 24px; }
QComboBox::down-arrow { image: url("__CHEVRON__"); width: 14px; height: 14px; }
QComboBox QAbstractItemView { background: #ffffff; color: #292824; selection-background-color: #f4e9da; selection-color: #7e421f; padding: 6px; }
QComboBox#yeelightColorChoice { background: #ffffff; padding: 6px 10px; min-height: 18px; }
QPushButton#yeelightColorCustom { background: #fbfaf7; padding: 6px 10px; min-height: 18px; }
QPushButton#yeelightColorCustom:hover { background: #f4f2ed; }
QPushButton#yeelightReset { padding: 6px 12px; min-height: 18px; font-size: 12px; color: #736e63; background: #fbfaf7; }
QPushButton#yeelightReset:hover { background: #f4f2ed; color: #39372f; }
QComboBox#yeelightColorChoice:disabled, QPushButton#yeelightColorCustom:disabled, QPushButton#yeelightReset:disabled { color: #a5a299; background: #f1f0ec; border-color: #e8e6df; }
QCheckBox { background: transparent; spacing: 10px; padding: 5px 0; }
QCheckBox::indicator { width: 18px; height: 18px; border-radius: 5px; border: 1px solid #c9c5b9; background: #ffffff; }
QCheckBox::indicator:checked { background: #c85b2b; border: 4px solid #f4d6c3; }
QCheckBox:focus { color: #a74923; }
QSlider { min-height: 28px; }
QSlider::groove:horizontal { height: 4px; background: #e7e3d9; border-radius: 2px; }
QSlider::sub-page:horizontal { background: #c85b2b; border-radius: 2px; }
QSlider::handle:horizontal { width: 16px; height: 16px; margin: -7px 0; border-radius: 9px; background: #c85b2b; border: 2px solid #ffffff; }
QSlider::handle:horizontal:focus { border-color: #7f3617; }
QScrollArea { border: none; }
QScrollBar:vertical { background: transparent; width: 8px; margin: 3px; }
QScrollBar::handle:vertical { background: #ccc8bd; border-radius: 2px; min-height: 32px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: none; }
QTableWidget { background: #ffffff; alternate-background-color: #fbfaf7; border: none; gridline-color: #eeece5; selection-background-color: #f8e9dc; selection-color: #513827; }
QTableWidget::item { padding: 6px 10px; border-bottom: 1px solid #f0eee8; }
QHeaderView::section { background: #f8f7f3; color: #625f57; border: none; padding: 10px; font-size: 12px; font-weight: 500; }
QTabBar::tab { background: transparent; color: #625f57; border: 1px solid transparent; border-radius: 8px; padding: 9px 16px; margin-right: 4px; }
QTabBar::tab:selected { background: #fff0e5; border-color: #d48760; color: #a04a24; font-weight: 600; }
QTabBar::tab:hover { background: #f4f2ed; }
QTabBar::tab:focus { border-color: #c85b2b; }
QFrame#inlinePanel { background: transparent; border: none; }
QFrame#toast { background: #ffffff; border: 1px solid #dedcd4; border-radius: 8px; }
QProgressBar { background: #eae6dc; border: none; border-radius: 4px; min-height: 8px; max-height: 8px; }
QProgressBar::chunk { background: #c85b2b; border-radius: 4px; }
""".replace("__CHEVRON__", (Path(__file__).resolve().parents[1] / "assets/chevron-down.svg").as_posix()) + TOOLTIP_STYLE
