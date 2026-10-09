"""Shared, deliberately small design system for the native desktop app."""

STYLE = """
QWidget { color: #292824; font-family: 'Inter', 'SF Pro Text', 'PingFang SC', 'Microsoft YaHei', sans-serif; font-size: 13px; }
QWidget#dashboard, QScrollArea, QWidget#page { background: #f6f5f1; }
QWidget#sidebar { background: #eeede8; border-right: 1px solid #e0dfd9; }
QWidget#topbar { background: #f6f5f1; border-bottom: 1px solid #e4e2dc; }
QWidget#statusbar { background: #f6f5f1; border-top: 1px solid #e4e2dc; }
QLabel { background: transparent; border: none; }
QLabel[role="muted"] { color: #75736b; font-size: 12px; }
QLabel[role="eyebrow"] { color: #8c7561; font-size: 10px; font-weight: 600; letter-spacing: 2px; }
QLabel[role="heading"] { font-size: 28px; font-weight: 600; color: #24241f; }
QLabel[role="pageTitle"] { font-size: 19px; font-weight: 600; color: #292824; }
QLabel[role="title"] { font-size: 15px; font-weight: 600; }
QLabel[role="lightStateTitle"] { font-size: 13px; font-weight: 600; }
QLabel[role="lightValue"] { font-size: 13px; font-weight: 600; color: #59564e; }
QLabel[role="sectionTitle"] { font-size: 18px; font-weight: 700; color: #292824; padding: 4px 0; }
QLabel[role="brand"] { font-size: 21px; font-weight: 600; letter-spacing: -0.5px; }
QLabel[role="tagline"] { color: #888171; font-size: 11px; }
QLabel[role="number"] { font-size: 24px; font-weight: 500; }
QLabel[role="badge"] { background: #ecebe6; color: #706d65; border-radius: 10px; padding: 5px 10px; font-size: 11px; }
QLabel[tone="success"] { color: #38735b; background: #e7f0e9; }
QLabel[tone="warning"] { color: #9a5e24; background: #fbeddc; }
QFrame#deviceStatus { background: #ecebe6; border-radius: 10px; }
QFrame#deviceStatus QLabel { color: #706d65; font-size: 11px; }
QFrame#deviceStatus[tone="success"] { background: #e7f0e9; }
QFrame#deviceStatus[tone="success"] QLabel { color: #38735b; }
QFrame#deviceStatus[tone="warning"] { background: #fbeddc; }
QFrame#deviceStatus[tone="warning"] QLabel { color: #9a5e24; }
QFrame#card { background: #ffffff; border: 1px solid #e5e3dd; border-radius: 16px; }
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
QLabel[role="recentSource"] { font-size: 13px; font-weight: 600; }
QLabel[role="recentState"] { font-size: 12px; color: #75736b; }
QLabel[role="recentState"][recentTone="working"] { color: #9a5e24; }
QLabel[role="recentState"][recentTone="done"] { color: #38735b; }
QLabel[role="recentState"][recentTone="error"] { color: #a0472c; }
QFrame#separator { background: #e9e7e1; border: none; max-height: 1px; }
QFrame#sourceDivider { background: rgba(117, 115, 107, 35); border: none; max-height: 1px; }
QPushButton#deviceModelCard { background: #ffffff; border: 1px solid #e2dfd7; border-radius: 14px; padding: 0; }
QPushButton#deviceModelCard:hover { background: #fffdf9; border-color: #d1a487; }
QPushButton#deviceModelCard:pressed { background: #faf2e9; border-color: #c85b2b; }
QPushButton#deviceModelCard:focus { border: 2px solid #c85b2b; }
QPushButton#deviceModelCard:disabled { background: #f1f0ec; border-color: #e8e6df; }
QLabel#deviceThumbnail { background: #f5f0e7; border-radius: 10px; }
QLabel#deviceThumbnail[kind="rgb"] { background: #f0f3f1; }
QLabel[role="deviceBrand"] { color: #9b8a76; font-size: 9px; font-weight: 600; letter-spacing: 1px; }
QLabel[role="deviceName"] { color: #33332d; font-size: 16px; font-weight: 600; }
QLabel[role="deviceMeta"] { color: #958d80; font-size: 11px; }
QLabel[role="deviceCount"] { color: #8a877e; font-size: 11px; }
QLabel[role="deviceCount"][online="true"] { color: #38735b; font-weight: 600; }
QLabel[role="deviceArrow"] { color: #b38b70; font-size: 20px; }
QLabel[role="deviceName"]:disabled, QLabel[role="deviceCount"]:disabled { color: #aaa69b; }
QPushButton { background: #ffffff; border: 1px solid #dedcd4; border-radius: 8px; padding: 9px 14px; font-weight: 500; min-height: 18px; }
QPushButton:hover { background: #f4f2ed; border-color: #bebbb1; }
QPushButton:pressed { background: #e9e6df; }
QPushButton:focus { border: 1px solid #c85b2b; }
QPushButton:disabled { color: #a5a299; background: #f1f0ec; border-color: #e8e6df; }
QPushButton[variant="primary"] { background: #c85b2b; border-color: #c85b2b; color: #ffffff; font-weight: 600; }
QPushButton[variant="primary"]:hover { background: #b34a1e; border-color: #b34a1e; }
QPushButton[variant="primary"]:disabled { background: #e7cabb; border-color: #e7cabb; color: #faf5f1; }
QPushButton[variant="quiet"] { background: transparent; border-color: transparent; color: #736e63; padding: 7px 9px; }
QPushButton[variant="quiet"]:hover { background: #e9e6de; color: #39372f; }
QPushButton[variant="quiet"]:focus { border-color: #c85b2b; }
QPushButton[variant="shortcut"] { background: transparent; border-color: transparent; text-align: left; padding: 12px 10px; color: #39372f; }
QPushButton[variant="shortcut"]:hover { background: #f4f2ed; }
QPushButton[variant="shortcut"]:focus { border-color: #c85b2b; }
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
QPushButton[variant="danger"] { color: #98543d; background: #faf5ee; border-color: #e4d6c9; }
QPushButton[variant="nav"] { background: transparent; border: 1px solid transparent; color: #747268; border-radius: 9px; text-align: left; padding: 12px 16px; }
QPushButton[variant="nav"]:hover { background: #e6e4de; }
QPushButton[variant="nav"]:checked { background: #fff9ee; border-color: #e8decc; color: #a34d27; font-weight: 600; }
QPushButton[variant="nav"]:focus { border-color: #c85b2b; }
QPushButton[variant="choice"] { min-height: 40px; }
QPushButton[variant="choice"]:checked { background: #fff0e5; border-color: #d48760; color: #a04a24; }
QLineEdit, QComboBox, QSpinBox, QTimeEdit { background: #fbfaf7; border: 1px solid #dedcd3; border-radius: 8px; padding: 8px 10px; min-height: 22px; selection-background-color: #f4d6c4; selection-color: #563323; }
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QTimeEdit:focus { border-color: #c85b2b; background: #ffffff; }
QLineEdit[invalid="true"] { border-color: #b65034; background: #fff8f3; }
QLineEdit:disabled, QTimeEdit:disabled { background: #efeee9; color: #a19c90; }
QComboBox::drop-down { border: none; width: 24px; }
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
QHeaderView::section { background: #f8f7f3; color: #817b6d; border: none; padding: 10px; font-size: 11px; text-align: left; }
QProgressBar { background: #eae6dc; border: none; border-radius: 4px; min-height: 8px; max-height: 8px; }
QProgressBar::chunk { background: #c85b2b; border-radius: 4px; }
QToolTip { background: #302e28; color: #faf7ef; border: none; padding: 8px; }
"""
