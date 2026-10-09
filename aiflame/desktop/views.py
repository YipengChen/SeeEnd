"""Native page composition. Operations and state live in Dashboard."""

from PySide6.QtCore import Qt, QTime, QSize
from PySide6.QtGui import QColor, QIcon, QPixmap, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QHBoxLayout,
    QGridLayout,
    QHeaderView,
    QVBoxLayout,
)
from aiflame.desktop.localization import (
    QCheckBox,
    QComboBox,
    QFrame,
    QLineEdit,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QStackedWidget,
    QTableWidget,
    QTimeEdit,
    QWidget,
)

from aiflame import __version__
from aiflame.devices.models import MODELS, DEFAULT_MODEL
from aiflame.integrations.registry import SOURCES
from aiflame.integrations.cli_tools.providers import CLI_SOURCES
from aiflame.integrations.chatgpt_web.host import WEB_SOURCES, WEB_URLS
from aiflame.integrations.desktop_clients.providers import DESKTOP_SOURCES
from .components import DeviceModelCard, Disclosure, FlameVisual, Toggle, brand_icon, card, icon, label, separator

PAGES = (
    ("总览", "home"),
    ("我的设备", "device"),
    ("应用接入", "apps"),
    ("提醒偏好", "settings"),
    ("活动记录", "history"),
    ("帮助与诊断", "help"),
)


class DashboardViews:
    def button(self, layout, text, action, collection=None, variant=None, glyph=None):
        result = QPushButton(text)
        result.setCursor(Qt.CursorShape.PointingHandCursor)
        if variant:
            result.setProperty("variant", variant)
        if glyph:
            result.setIcon(
                icon(
                    glyph,
                    "#ffffff" if variant == "primary"
                    else "#f6f2e9" if variant == "scene"
                    else "#81745f",
                )
            )
        result.clicked.connect(lambda checked=False: action())
        layout.addWidget(result)
        if collection is not None:
            collection.append(result)
        return result

    def build_views(self):
        self.checks = {}
        self.source_rows = {}
        self.source_status = {}
        self.source_details = {}
        self.manual_buttons = []
        self.nav_buttons = []
        self.source_action_buttons = []
        self.shortcuts = []
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        sidebar = QWidget()
        self.sidebar = sidebar
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(198)
        rail = QVBoxLayout(sidebar)
        rail.setContentsMargins(18, 30, 18, 22)
        rail.setSpacing(7)
        brand = QHBoxLayout()
        brand.setContentsMargins(12, 0, 0, 0)
        brand_copy = QVBoxLayout()
        brand_copy.setSpacing(2)
        brand_copy.addWidget(label("SeeEnd", role="brand"))
        self.brand_tagline = label("让完成，被看见", role="tagline")
        self.brand_tagline.setWordWrap(False)
        brand_copy.addWidget(self.brand_tagline)
        brand.addLayout(brand_copy)
        rail.addLayout(brand)
        rail.addSpacing(30)
        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)
        for index, (title, glyph, *_) in enumerate(PAGES):
            button = self.button(
                rail,
                "  " + title,
                lambda i=index: self.navigate(i),
                variant="nav",
                glyph=glyph,
            )
            button.setCheckable(True)
            button.toggled.connect(
                lambda checked, i=index: self.navigate(i) if checked else None
            )
            button.setAccessibleName(title)
            button.setToolTip(f"{title} · ⌘ / Ctrl + {index + 1}")
            self.nav_group.addButton(button)
            self.nav_buttons.append(button)
            shortcut = QShortcut(QKeySequence(f"Ctrl+{index + 1}"), self)
            shortcut.activated.connect(lambda i=index: self.navigate(i))
            self.shortcuts.append(shortcut)
        rail.addStretch(1)
        root.addWidget(sidebar)
        main = QWidget()
        column = QVBoxLayout(main)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        topbar = QWidget()
        topbar.setObjectName("topbar")
        bar = QHBoxLayout(topbar)
        bar.setContentsMargins(30, 30, 30, 14)
        self.breadcrumb = label("总览", role="pageTitle")
        self.breadcrumb.setWordWrap(False)
        bar.addWidget(self.breadcrumb, alignment=Qt.AlignmentFlag.AlignTop)
        bar.addStretch()
        self.device_status = QFrame()
        self.device_status.setObjectName("deviceStatus")
        status = QHBoxLayout(self.device_status)
        status.setContentsMargins(10, 5, 10, 5)
        status.setSpacing(6)
        self.device_badge = label("正在连接…", role="connectionText")
        self.device_badge.setWordWrap(False)
        status.addWidget(self.device_badge)
        self.device_connection_icon = label()
        self.device_connection_icon.setFixedSize(16, 16)
        self.device_connection_icon.hide()
        status.addWidget(self.device_connection_icon)
        bar.addWidget(self.device_status)
        topbar.hide()
        column.addWidget(topbar)
        self.hardware_notice = QFrame()
        self.hardware_notice.setObjectName("hardwareNotice")
        warning = QVBoxLayout(self.hardware_notice)
        warning.setContentsMargins(12, 12, 12, 12)
        warning.setSpacing(6)
        self.hardware_notice_text = label("尚未连接设备，硬件灯光暂不可用。")
        warning.addWidget(self.hardware_notice_text)
        self.hardware_notice_action = self.button(
            warning, "连接设备", lambda: self.navigate(1), variant="quiet"
        )
        rail.addWidget(self.hardware_notice)
        self.hardware_notice.hide()
        self.tabs = QStackedWidget()
        column.addWidget(self.tabs, 1)
        builders = (
            self.home,
            self.devices,
            self.sources,
            self.preferences,
            self.activity,
            self.testing,
        )
        for builder in builders:
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            widget = QWidget()
            widget.setObjectName("page")
            layout = QVBoxLayout(widget)
            layout.setContentsMargins(30, 26, 30, 26)
            layout.setSpacing(18)
            builder(layout)
            if builder != self.home:
                layout.addStretch()
            scroll.setWidget(widget)
            self.tabs.addWidget(scroll)
        self.statusbar = QWidget()
        self.statusbar.setObjectName("statusbar")
        bottom = QHBoxLayout(self.statusbar)
        bottom.setContentsMargins(30, 10, 30, 10)
        self.message = label("", True)
        bottom.addWidget(self.message, 1)
        self.retry_button = self.button(
            bottom, "重新连接", lambda: self.poll(force=True), variant="quiet"
        )
        self.retry_button.hide()
        column.addWidget(self.statusbar)
        self.statusbar.hide()
        root.addWidget(main, 1)
        self.nav_buttons[0].setChecked(True)


    def home(self, layout):
        mode_card = card(layout)
        mode_card.setContentsMargins(22, 20, 22, 20)
        mode_card.setSpacing(14)
        mode_card.parentWidget().setMinimumHeight(176)
        heading = QHBoxLayout()
        heading.addWidget(label("运行模式", role="title"))
        heading.addStretch()
        heading.addWidget(label("自动记住选择", True))
        mode_card.addLayout(heading)
        choices = QHBoxLayout()
        self.mode_group = QButtonGroup(self)
        self.mode_group.setExclusive(True)
        self.mode_buttons = {}
        for mode, title, glyph in (
            ("virtual", "虚拟提示", "flame"),
            ("hardware", "硬件提示", "device"),
        ):
            button = self.button(
                choices, title, lambda m=mode: self.choose_mode(m),
                variant="choice", glyph=glyph,
            )
            button.setCheckable(True)
            button.setAccessibleName(title)
            self.mode_group.addButton(button)
            self.mode_buttons[mode] = button
        mode_card.addLayout(choices)
        self.mode_hint = label("屏幕烛火随任务变化，无需连接设备。", True)
        mode_card.addWidget(self.mode_hint)
        self.notice_box = QFrame()
        self.notice_box.setObjectName("notice")
        notice = QHBoxLayout(self.notice_box)
        notice.setContentsMargins(16, 10, 12, 10)
        self.notice_text = label()
        notice.addWidget(self.notice_text, 1)
        self.notice_action = self.button(
            notice, "查看", self.resolve_notice, variant="quiet"
        )
        layout.addWidget(self.notice_box)
        self.notice_box.hide()
        top = QVBoxLayout()
        top.setSpacing(18)
        layout.addLayout(top, 1)
        scene = card(top, name="scene")
        scene.setSpacing(10)
        self.home_top = top
        self.scene_frame = scene.parentWidget()
        self.scene_frame.setMinimumHeight(500)
        scene.parentWidget().setMinimumWidth(300)
        top.setStretch(0, 6)
        scene_header = QHBoxLayout()
        self.scene_title = label("虚拟烛火", role="title")
        scene_header.addWidget(self.scene_title)
        scene_header.addStretch()
        scene.addLayout(scene_header)
        self.flame = FlameVisual()
        scene.addWidget(self.flame, 1)
        self.display_title = label("当前无任务")
        self.display_title.setStyleSheet("font-size: 24px; font-weight: 500;")
        self.display_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        scene.addWidget(self.display_title)
        self.display_hint = label("", True)
        self.display_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        scene.addWidget(self.display_hint)
        preview = QHBoxLayout()
        preview.addStretch()
        self.preview_caption = label("", True)
        preview.addWidget(self.preview_caption)
        self.preview_caption.hide()
        self.preview_button = self.button(
            preview,
            "灯效演示",
            self.start_preview,
            variant="scene",
            glyph="play",
        )
        scene.addLayout(preview)
        aside = QVBoxLayout()
        aside.setSpacing(14)
        top.addLayout(aside, 5)
        quick = card(aside)
        quick.setContentsMargins(18, 16, 18, 16)
        quick.setSpacing(4)
        self.home_sources_button = self.button(
            quick, "应用接入  →", lambda: self.navigate(2),
            variant="shortcut", glyph="apps",
        )
        self.home_sources_button.setAccessibleName("应用接入")
        self.home_sources_row = QWidget()
        source_groups = QVBoxLayout(self.home_sources_row)
        source_groups.setContentsMargins(0, 0, 0, 0)
        source_groups.setSpacing(10)
        source_groups.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.home_source_icons = {}
        self.home_source_groups = {}
        categories = (
            ("网页应用", WEB_SOURCES),
            ("编程工具", {"codex": "Codex", **CLI_SOURCES}),
            ("桌面客户端", DESKTOP_SOURCES),
        )
        for title, sources in categories:
            group = QWidget()
            group_layout = QVBoxLayout(group)
            group_layout.setContentsMargins(0, 0, 0, 0)
            group_layout.setSpacing(3)
            group_layout.addWidget(label(title, True))
            source_icons = QHBoxLayout()
            source_icons.setSpacing(6)
            for source, name in sources.items():
                button = self.button(source_icons, "", lambda s=source: self.open_web_app(s) if s in WEB_URLS else self.navigate(2), variant="brand")
                button.setIcon(brand_icon(source))
                button.setIconSize(QSize(24, 24))
                button.setFixedSize(32, 32)
                button.setAccessibleName(title + " · " + name + (" · 打开网页" if source in WEB_URLS else " · 查看应用接入"))
                button.hide()
                self.home_source_icons[source] = button
            source_icons.addStretch()
            group_layout.addLayout(source_icons)
            source_groups.addWidget(group)
            self.home_source_groups[title] = (group, tuple(sources))
        quick.addWidget(self.home_sources_row)
        self.home_sources_empty = label("尚未开启应用接入", True)
        quick.addWidget(self.home_sources_empty)
        self.home_sources_row.hide()
        self.home_sources_empty.hide()
        recent = card(aside)
        self.recent_frame = recent.parentWidget()
        self.recent_frame.setMinimumHeight(360)
        aside.setStretch(1, 1)
        recent.setSpacing(8)
        heading = QHBoxLayout()
        heading.addWidget(label("最近动态", role="title"))
        heading.addStretch()
        self.recent_more = self.button(
            heading, "更多", lambda: self.navigate(4), variant="quiet",
        )
        self.recent_more.setAccessibleName("更多活动记录")
        recent.addLayout(heading)
        self.recent_empty = label("暂无记录", True)
        recent.addWidget(self.recent_empty)
        self.recent_rows = []
        self.recent_row_frames = []
        self.recent_list = QVBoxLayout()
        self.recent_list.setSpacing(0)
        self.recent_gaps = []
        recent.addLayout(self.recent_list, 1)
        for index in range(4):
            frame = QFrame()
            frame.setObjectName("recentEvent")
            frame.setProperty("last", index == 3)
            frame.setFixedHeight(58)
            row = QVBoxLayout(frame)
            row.setContentsMargins(0, 6, 0, 8)
            row.setSpacing(4)
            summary = QHBoxLayout()
            outcome = QHBoxLayout()
            when, source, state, result = (
                label("", True),
                label("", role="recentSource"),
                label("", role="recentState"),
                label("", True),
            )
            for item in (when, source, state, result):
                item.setWordWrap(False)
            result.setAlignment(Qt.AlignmentFlag.AlignRight)
            summary.addWidget(source, 1)
            summary.addWidget(when)
            outcome.addWidget(state)
            outcome.addStretch()
            outcome.addWidget(result)
            row.addLayout(summary)
            row.addLayout(outcome)
            self.recent_list.addWidget(frame)
            self.recent_row_frames.append(frame)
            self.recent_rows.append((when, source, state, result))
            if index < 3:
                self.recent_list.addSpacing(8)
                gap_index = self.recent_list.count() - 1
                self.recent_gaps.append((gap_index, self.recent_list.itemAt(gap_index).spacerItem()))
        self.recent_list.addStretch()

    def devices(self, layout):
        self.device_view_epoch = 0
        self.selected_device_id = None
        self.port_restore_pending = False
        self.device_operation_id = None
        self.rendered_yeelight_discovery = None
        self.device_selection_loading = False
        self.selected_device_state = {"online": False}
        self.selected_device_model = DEFAULT_MODEL
        self.device_selector = QWidget()
        choices = QVBoxLayout(self.device_selector)
        choices.setContentsMargins(0, 0, 0, 0)
        choices.setSpacing(14)
        choices.addWidget(label("选择你的设备", role="pageTitle"))
        choices.addWidget(label("选择型号，添加或管理设备。已连接的设备会同步 AI 任务状态。", True))
        self.device_cards_layout = QGridLayout()
        self.device_card_columns = 2
        self.device_cards_layout.setSpacing(16)
        choices.addLayout(self.device_cards_layout)
        self.device_model_buttons = {}
        for index, model in enumerate(MODELS):
            button = DeviceModelCard(model)
            button.clicked.connect(lambda checked=False, model_id=model["id"]: self.choose_device_model(model_id))
            self.device_cards_layout.addWidget(button, index // 2, index % 2)
            self.device_model_buttons[model["id"]] = button
        layout.addWidget(self.device_selector)
        self.device_details = QWidget()
        details = QVBoxLayout(self.device_details)
        self.device_details_layout = details
        details.setContentsMargins(0, 0, 0, 0)
        details.setSpacing(18)
        heading = QHBoxLayout()
        self.device_back_button = self.button(heading, "← 选择其他设备", self.show_device_selector, variant="quiet")
        heading.addStretch()
        details.addLayout(heading)
        model_heading = QHBoxLayout()
        model_heading.setSpacing(16)
        self.device_model_title = label("Yeelight 屏幕挂灯 Pro", role="pageTitle")
        self.device_model_title.setWordWrap(False)
        model_heading.addWidget(self.device_model_title)
        self.device_model_description = label("YLTD003 · RGB 背光", True)
        self.device_model_description.setWordWrap(False)
        model_heading.addWidget(self.device_model_description)
        model_heading.addStretch()
        details.addLayout(model_heading)
        self.device_instance_panel = QWidget()
        instance_row = QHBoxLayout(self.device_instance_panel)
        instance_row.setContentsMargins(0, 0, 0, 0)
        self.device_instances = QComboBox()
        self.device_instances.setAccessibleName("选择具体设备或添加同型号设备")
        self.device_instances.addItem("添加此型号设备…", "")
        self.device_instances.currentIndexChanged.connect(self.choose_device_instance)
        instance_row.addWidget(self.device_instances, 1)
        details.addWidget(self.device_instance_panel)
        self.build_device_details(details)
        layout.addWidget(self.device_details)
        self.device_details.hide()

    def build_device_details(self, layout):
        details = layout
        virtual = card(layout, "当前使用虚拟模式", "任务状态通过屏幕烛火呈现，设备查找与连接已关闭。")
        self.virtual_device_card = virtual.parentWidget()
        self.button(virtual, "前往总览切换模式", lambda: self.navigate(0), variant="primary")
        self.hardware_controls = QWidget()
        layout.addWidget(self.hardware_controls)
        layout = QVBoxLayout(self.hardware_controls)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(18)
        self.hardware_controls.hide()
        top = QVBoxLayout()
        top.setSpacing(18)
        self.device_connections_layout = top
        layout.addLayout(top)
        yeelight = card(top, "连接准备", "在 Yeelight APP 中配置灯具，接入2.4 GHz WiFi，开启「局域网控制」。此电脑需与灯具在同一局域网内。")
        self.yeelight_card = yeelight.parentWidget()
        yeelight.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.yeelight_history_layout = QVBoxLayout()
        self.yeelight_history_layout.setSpacing(12)
        yeelight.addLayout(self.yeelight_history_layout)
        self.yeelight_history_heading = QWidget()
        history_heading = QHBoxLayout(self.yeelight_history_heading)
        history_heading.setContentsMargins(0, 0, 0, 0)
        history_heading.addWidget(label("连接设备", role="title"))
        history_heading.addStretch()
        self.yeelight_history_layout.addWidget(self.yeelight_history_heading)
        add = card(yeelight, subtitle="先搜索局域网中的灯具；未找到时，可以手动输入 IP 地址。")
        self.yeelight_add_panel = add.parentWidget()
        row = QHBoxLayout()
        add.addLayout(row)
        self.yeelight_discover_button = self.button(row, "搜索 YLTD003", lambda: self.device_action("discover"), self.maintenance_buttons)
        self.yeelight_choices = QComboBox()
        self.yeelight_choices.setAccessibleName("搜索到的 Yeelight 灯具")
        self.yeelight_choices.addItem("搜索后选择灯具…", "")
        self.yeelight_choices.currentIndexChanged.connect(self.choose_yeelight_address)
        row.addWidget(self.yeelight_choices, 1)
        self.yeelight_host = QLineEdit()
        self.yeelight_host.setPlaceholderText("灯具 IP，例如 192.168.1.100")
        self.yeelight_host.setAccessibleName("Yeelight 灯具 IP 地址")
        self.yeelight_host.textChanged.connect(lambda _: self.update_busy())
        ip_row = QHBoxLayout()
        ip_row.addWidget(self.yeelight_host, 1)
        add.addLayout(ip_row)
        self.yeelight_connect_button = self.button(ip_row, "添加并连接", lambda: self.device_action("connect", transport="yeelight", host=self.yeelight_host.text().strip()), self.maintenance_buttons, "primary")
        self.yeelight_card.hide()
        progress = card(top)
        self.device_status_layout = progress
        self.device_status_card = progress.parentWidget()
        top.removeWidget(progress.parentWidget())
        top.insertWidget(0, progress.parentWidget())
        self.device_summary = label("正在检查设备", role="title")
        summary = QHBoxLayout()
        summary.addWidget(self.device_summary)
        self.device_summary_icon = label()
        self.device_summary_icon.setFixedSize(18, 18)
        self.device_summary_icon.hide()
        summary.addWidget(self.device_summary_icon)
        summary.addStretch()
        progress.addLayout(summary)
        self.device_version = label("连接后读取固件版本", True)
        progress.addWidget(self.device_version)
        self.device_job_feedback = QWidget()
        feedback = QVBoxLayout(self.device_job_feedback)
        feedback.setContentsMargins(0, 0, 0, 0)
        self.job_message = label("", True)
        self.job_message.hide()
        feedback.addWidget(self.job_message)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setAccessibleName("设备操作进度")
        feedback.addWidget(self.progress)
        progress.addWidget(self.device_job_feedback)
        self.progress.hide()
        device_actions = QHBoxLayout()
        progress.addLayout(device_actions)
        self.yeelight_reconnect_button = self.button(device_actions, "连接此设备", lambda: self.device_action("connect", transport="yeelight", host=self.yeelight_host.text().strip()), self.maintenance_buttons, "primary")
        self.yeelight_reconnect_button.hide()
        self.try_light_button = self.button(
            device_actions,
            "试亮此设备",
            self.test_current_device,
            self.manual_buttons,
            "primary",
        )
        self.disconnect_button = self.button(device_actions, "断开此设备", lambda: self.device_action("disconnect"), self.maintenance_buttons)
        self.yeelight_delete_button = self.button(device_actions, "删除此设备", self.delete_current_device, self.maintenance_buttons)
        self.yeelight_delete_button.hide()
        self.device_test_caption = label("", True)
        device_actions.addWidget(self.device_test_caption)
        self.device_test_caption.hide()
        device_actions.addStretch()
        self.connection_paused = False
        self.yeelight_history_layout.addWidget(self.device_instance_panel)
        self.yeelight_history_layout.addWidget(self.device_status_card)
        self.yeelight_card.layout().addWidget(self.device_job_feedback)
        layout = details
        light = card(layout)
        self.device_light_hint = label("先连接并添加设备，即可设置此设备的灯光表现。", True)
        light.addWidget(self.device_light_hint)
        self.yeelight_colors_panel = QWidget()
        colors = QVBoxLayout(self.yeelight_colors_panel)
        colors.setContentsMargins(0, 0, 0, 0)
        colors.setSpacing(18)
        heading = QHBoxLayout()
        heading.setSpacing(16)
        heading.addWidget(label("任务提醒颜色与亮度", role="title"), 0, Qt.AlignmentFlag.AlignVCenter)
        heading.addStretch()
        self.yeelight_color_reset = self.button(heading, "恢复默认颜色与亮度", self.reset_yeelight_colors)
        self.yeelight_color_reset.setObjectName("yeelightReset")
        header = QVBoxLayout()
        header.setSpacing(4)
        header.addLayout(heading)
        self.yeelight_light_hint = label("", True)
        header.addWidget(self.yeelight_light_hint)
        header.addSpacing(6)
        colors.addLayout(header)
        self.yeelight_color_choices = {}
        self.yeelight_color_buttons = {}
        self.yeelight_brightness_controls = {}
        self.yeelight_brightness_labels = {}
        palette = (("天空蓝", "#4A90E2"), ("薄荷绿", "#52C788"), ("琥珀橙", "#FF9F43"),
                   ("珊瑚红", "#FF6B6B"), ("玫瑰粉", "#FF72B6"), ("薰衣草紫", "#A78BFA"),
                   ("靛蓝", "#6366F1"), ("湖水青", "#22D3EE"), ("柠檬黄", "#FACC15"), ("暖白", "#FFE7C2"))
        for number, (state, title) in enumerate((("working", "任务进行中"), ("done", "任务已完成"), ("error", "需关注 / 等待输入"))):
            if number:
                separator(colors)
            section = QVBoxLayout()
            section.setSpacing(10)
            section.addWidget(label(title, role="lightStateTitle"))
            fields = QGridLayout()
            fields.setHorizontalSpacing(14)
            fields.setVerticalSpacing(8)
            fields.setColumnMinimumWidth(0, 40)
            fields.setColumnStretch(1, 1)
            fields.setColumnMinimumWidth(2, 96)
            fields.addWidget(label("颜色", True), 0, 0)
            choice = QComboBox()
            choice.setObjectName("yeelightColorChoice")
            choice.setFixedHeight(36)
            choice.setAccessibleName(title + "颜色")
            for name, value in palette:
                swatch = QPixmap(16, 16)
                swatch.fill(QColor(value))
                choice.addItem(name + " · " + value, value)
                choice.setItemIcon(choice.count() - 1, QIcon(swatch))
            choice.currentIndexChanged.connect(lambda _, key=state: self.yeelight_color_changed(key))
            fields.addWidget(choice, 0, 1)
            self.yeelight_color_choices[state] = choice
            color_row = QHBoxLayout()
            button = self.button(color_row, "自定义…", lambda key=state: self.choose_yeelight_color(key))
            button.setObjectName("yeelightColorCustom")
            button.setFixedSize(96, 36)
            self.yeelight_color_buttons[state] = button
            fields.addLayout(color_row, 0, 2)
            fields.addWidget(label("亮度", True), 1, 0)
            control = QSlider(Qt.Orientation.Horizontal)
            control.setRange(0, 100)
            control.setValue(20 if state == "working" else 50)
            control.setAccessibleName(title + "亮度")
            value_label = label(f"{control.value()}%", role="lightValue")
            value_label.setFixedWidth(40)
            value_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            control.valueChanged.connect(lambda value, key=state: self.yeelight_brightness_changed(key, value))
            fields.addWidget(control, 1, 1)
            fields.addWidget(value_label, 1, 2, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            self.yeelight_brightness_controls[state] = control
            self.yeelight_brightness_labels[state] = value_label
            section.addLayout(fields)
            colors.addLayout(section)
        light.addWidget(self.yeelight_colors_panel)
        self.yeelight_colors_panel.hide()
    def source_row(self, layout, source, short_name=None):
        policy = next(item for item in SOURCES if item.id == source)
        frame = QFrame()
        frame.setObjectName("sourceRow")
        row = QHBoxLayout(frame)
        row.setContentsMargins(0, 9, 0, 9)
        row.setSpacing(12)
        name = short_name or policy.name.replace("（实验）", "").replace(
            " 任务状态", ""
        )
        if source in WEB_URLS:
            mark = self.button(row, "", lambda s=source: self.open_web_app(s), variant="brand")
            mark.setIcon(brand_icon(source))
            mark.setIconSize(QSize(30, 30))
            mark.setAccessibleName(name + " · 打开网页")
            mark.setToolTip(WEB_URLS[source])
        else:
            mark = label()
            mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
            mark.setPixmap(brand_icon(source).pixmap(30, 30))
            mark.setAccessibleName(name + " 图标")
            row.addWidget(mark)
        mark.setFixedSize(36, 36)
        copy = QVBoxLayout()
        copy.setSpacing(3)
        if source in WEB_URLS:
            title = self.button(copy, name, lambda s=source: self.open_web_app(s), variant="webSourceTitle")
            title.setToolTip(WEB_URLS[source])
        else:
            copy.addWidget(label(name, role="title"))
        status = label("等待同步", True)
        self.source_status[source] = status
        copy.addWidget(status)
        row.addLayout(copy, 1)
        toggle = Toggle()
        toggle.setAccessibleName("启用" + policy.name)
        toggle.setToolTip(policy.description)
        toggle.toggled.connect(lambda value, s=source: self.source_changed(s, value))
        row.addWidget(toggle)
        self.source_checks[source] = toggle
        self.source_rows[source] = frame
        layout.addWidget(frame)

    def sources(self, layout):
        sources = card(layout)
        sources.setSpacing(8)
        self.integration_guides, self.integration_plans, self.integration_copy_buttons = {}, {}, {}
        self.integration_connect_buttons = {}
        self.cli_messages, self.cli_config_dirs, self.cli_profiles = {}, {}, {}
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(label("网页应用", role="sectionTitle"))
        row.addStretch()
        actions_column = QVBoxLayout()
        actions_column.setContentsMargins(0, 4, 0, 0)
        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions_column.addLayout(actions)
        row.addLayout(actions_column)
        self.button(
            actions,
            "下载浏览器扩展",
            lambda: self.download(
                "/api/browser-extension/download", "SeeEnd-Web-extension.zip"
            ),
        )
        self.button(
            actions,
            "连接 Chrome",
            lambda: self.install_integration("chatgpt-web"),
            self.source_action_buttons,
        )
        sources.addLayout(row)
        sources.addWidget(label("仅支持 Chrome 系列浏览器。安装并启用扩展后，点击「连接 Chrome」。", True))
        self.browser_message = label("", True)
        sources.addWidget(self.browser_message)
        self.browser_message.hide()
        for source, name in WEB_SOURCES.items():
            self.source_row(sources, source, name + " 网页")

        separator(sources, "sourceDivider")
        sources.addWidget(label("编程工具", role="sectionTitle"))
        sources.addWidget(label("① 连接　→　② 在工具中发一条新任务　→　③ 检查\n自动备份并保留原配置；工具提示信任时，确认后再发任务。", True))
        self.source_row(sources, "codex", "Codex")
        row = QHBoxLayout()
        sources.addLayout(row)
        self.integration_connect_buttons["codex"] = self.button(
            row,
            "连接",
            lambda: self.install_integration("codex"),
            self.source_action_buttons,
        )
        self.button(row, "检查", self.check_codex)
        row.addStretch()
        self.codex_message = label("", True)
        sources.addWidget(self.codex_message)
        self.codex_message.hide()
        codex_detail = Disclosure("高级设置", sources)
        directory = QLineEdit()
        directory.setPlaceholderText("Codex 配置目录（留空沿用已连接或默认目录）")
        directory.setAccessibleName("Codex 配置目录")
        self.cli_config_dirs["codex"] = directory
        codex_detail.content.addWidget(directory)
        directory.editingFinished.connect(lambda: self.load_integration_guide("codex"))
        self.integration_guide_widgets(codex_detail, "codex")
        self.codex_connection_details = label("尚未检查。", True)
        diagnosis = Disclosure("诊断详情", codex_detail.content)
        diagnosis.content.addWidget(self.codex_connection_details)
        self.cli_details = {}
        for source, name in CLI_SOURCES.items():
            self.source_row(sources, source, name)
            detail = Disclosure("高级设置", sources)
            config = QHBoxLayout()
            if source == "deepseek-harness":
                config.addWidget(label("已有 Profile", True))
                selector = QComboBox()
                selector.setAccessibleName(name + " Profile")
                selector.addItem("正在查找已有 Profile…", "")
                self.cli_profiles[source] = selector
                selector.currentIndexChanged.connect(lambda _, s=source: self.load_integration_guide(s))
                config.addWidget(selector, 1)
            else:
                directory = QLineEdit()
                directory.setPlaceholderText("配置目录（留空沿用已连接或默认目录）")
                directory.setAccessibleName(name + " 配置目录")
                self.cli_config_dirs[source] = directory
                directory.editingFinished.connect(lambda s=source: self.load_integration_guide(s))
                config.addWidget(directory)
            (sources if source == "deepseek-harness" else detail.content).addLayout(config)
            actions = QHBoxLayout()
            for text, action in (
                ("连接", lambda s=source: self.cli_action(s, "install")),
                ("移除接入", lambda s=source: self.cli_action(s, "uninstall")),
                ("检查", lambda s=source: self.check_cli(s)),
                ("模拟测试", lambda s=source: self.trigger(s)),
            ):
                button = self.button(actions if text in ("连接", "检查") else detail.content, text, action, self.source_action_buttons)
                if text == "连接":
                    self.integration_connect_buttons[source] = button
            actions.addStretch()
            sources.addLayout(actions)
            message = label("尚未检查接入。", True)
            self.cli_messages[source] = message
            sources.addWidget(message)
            diagnostic = label("尚未检查。", True)
            self.cli_details[source] = diagnostic
            self.integration_guide_widgets(detail, source)
            diagnosis = Disclosure("诊断详情", detail.content)
            diagnosis.content.addWidget(diagnostic)
            sources.removeWidget(detail)
            sources.addWidget(detail)

        separator(sources, "sourceDivider")
        row = QHBoxLayout()
        row.addWidget(label("桌面客户端", role="sectionTitle"))
        row.addWidget(label("实验", role="badge"))
        row.addStretch()
        self.permission_button = self.button(
            row,
            "授权接入",
            self.request_client_permissions,
            self.source_action_buttons,
        )
        self.button(row, "刷新状态", self.check_clients)
        sources.addLayout(row)
        self.chatgpt_permission_hint = label("", True)
        sources.addWidget(self.chatgpt_permission_hint)
        self.chatgpt_permission_hint.hide()
        self.client_message = label("", True)
        sources.addWidget(self.client_message)
        self.client_message.hide()
        for source, name in DESKTOP_SOURCES.items():
            self.source_row(sources, source, name + " 桌面客户端")

        separator(sources, "sourceDivider")
        sources.addWidget(label("本地 API 与测试来源", role="sectionTitle"))
        for source in ("local-script", "manual", "demo"):
            self.source_row(sources, source)
        self.source_help(sources)

    def integration_guide_widgets(self, detail, source):
        copy = label("展开后显示实际配置文件、增量修改方式和验证步骤。", True)
        copy.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        detail.content.addWidget(copy)
        self.integration_guides[source] = copy
        manual = Disclosure("手动配置", detail.content)
        explanation = label("", True)
        manual.content.addWidget(explanation)
        if not hasattr(self, "integration_manual_guides"):
            self.integration_manual_guides = {}
        self.integration_manual_guides[source] = explanation
        row = QHBoxLayout()
        manual.content.addLayout(row)
        button = self.button(row, "复制增量配置 / 命令", lambda: self.copy_integration_config(source))
        button.setEnabled(False)
        self.integration_copy_buttons[source] = button
        self.button(row, "官方配置说明", lambda: self.open_integration_docs(source))
        row.addStretch()
        snippet = QPlainTextEdit()
        snippet.setReadOnly(True)
        snippet.setAccessibleName(source + " 增量配置片段")
        snippet.setMaximumHeight(180)
        manual.content.addWidget(snippet)
        if not hasattr(self, "integration_snippets"):
            self.integration_snippets = {}
        self.integration_snippets[source] = snippet
        detail.button.toggled.connect(lambda expanded: self.load_integration_guide(source) if expanded else None)

    def source_help(self, layout):
        self.integration_help_disclosure = Disclosure("接入说明", layout)
        instructions = self.integration_help_disclosure.content
        for title, content in (
            (
                "Codex",
                "展开 Codex 的配置文件与步骤，核对 hooks.json 的实际位置，再点击「连接」。在 Codex 中完成 Hook 信任确认，重新打开会话并完成新任务后检查。",
            ),
            (
                "其他编程工具",
                "Claude Code、Qoder、Gemini CLI 增量添加用户级 Hooks；OpenCode 添加独立插件；DeepSeek Harness 先选择已有 Profile，再由插件管理器安装。各工具可展开查看实际文件、修改范围与操作步骤。模拟测试不计入真实接入验证。",
            ),
            (
                "网页应用",
                "所有网页共用一份浏览器扩展。下载并解压，在 Chrome 扩展页开启开发者模式、加载已解压的扩展，再点击「连接 Chrome」并刷新 AI 网页。请用真实新任务确认网页接入。",
            ),
            (
                "桌面客户端",
                "macOS 实验接入适用于普通文字聊天。点击「授权接入」申请辅助功能与输入监控权限；兼容性以实际客户端版本为准。网页接入结果不能替代桌面客户端验证。",
            ),
            (
                "灯光与测试",
                "在「我的设备」设置每台灯具的灯光表现，Yeelight 亮度上限为 100%；设为 0% 只关闭对应灯具，任务仍会记录。暂停、静默后不补播；模拟诊断和手动试亮不计入真实任务验证。",
            ),
        ):
            instructions.addWidget(label(title, role="title"))
            instructions.addWidget(label(content, True))
        details = instructions
        for title, attribute in (
            ("Codex", "codex_details"),
            ("网页应用", "browser_details"),
            ("桌面客户端", "client_details"),
        ):
            details.addWidget(label(title, role="title"))
            content = label("尚未检查。", True)
            setattr(self, attribute, content)
            details.addWidget(content)
        details.addWidget(label("来源状态", role="title"))
        for source in SOURCES:
            content = label(source.name + " · 等待同步", True)
            self.source_details[source.id] = content
            details.addWidget(content)

    def preferences(self, layout):
        notifications = card(layout, "桌面系统通知")
        self.preference_toggle(notifications, "desktop_notifications", "任务通知", "完成、异常或等待输入时通知；独立于灯光亮度与连接。首次使用请允许系统通知。")
        light = card(layout, "提醒时机", "灯光亮度与动态效果请在「我的设备」中按设备设置。")
        row = QHBoxLayout()
        copy = QVBoxLayout()
        copy.addWidget(label("提示时长 / 活动后熄灭延时", role="title"))
        row.addLayout(copy, 1)
        self.preference_toggle(light, "hold_until_activity", "完成提醒常亮", "检测到任务完成后的首次键鼠活动，再等待下方秒数结束提醒。")
        self.duration = QSpinBox()
        self.duration.setRange(3, 60)
        self.duration.setSuffix(" 秒")
        self.duration.setAccessibleName("提示时长，3 至 60 秒")
        self.duration.setToolTip("完成或异常提醒结束后，回到当前任务状态。")
        self.duration.setFixedWidth(105)
        self.duration.editingFinished.connect(
            lambda: self.setting("duration", self.duration.value())
        )
        row.addWidget(self.duration)
        light.addLayout(row)
        quiet = card(layout, "灯光暂停与静默")
        self.preference_toggle(
            quiet,
            "paused",
            "暂停灯光提醒",
            "任务仍会记录，恢复后不补播。",
        )
        separator(quiet)
        self.preference_toggle(
            quiet, "quiet_enabled", "定时静默", ""
        )
        self.quiet_times_box = QWidget()
        quiet_times = QVBoxLayout(self.quiet_times_box)
        quiet_times.setContentsMargins(0, 0, 0, 0)
        quiet_times.setSpacing(8)
        row = QHBoxLayout()
        self.times = {}
        for key, title, initial in (
            ("quiet_start", "从", "22:00"),
            ("quiet_end", "到", "08:00"),
        ):
            row.addWidget(label(title, True))
            edit = QTimeEdit(QTime.fromString(initial, "HH:mm"))
            edit.setDisplayFormat("HH:mm")
            edit.setAccessibleName(
                "静默开始时间" if key == "quiet_start" else "静默结束时间"
            )
            edit.editingFinished.connect(
                lambda k=key, e=edit: self.setting(k, e.time().toString("HH:mm"))
            )
            self.times[key] = edit
            row.addWidget(edit)
        row.addStretch()
        quiet_times.addLayout(row)
        self.quiet_note = label("使用本机时间。", True)
        quiet_times.addWidget(self.quiet_note)
        quiet.addWidget(self.quiet_times_box)
        self.quiet_times_box.hide()

    def preference_toggle(self, layout, key, title, hint):
        row = QHBoxLayout()
        copy = QVBoxLayout()
        copy.setSpacing(5)
        copy.addWidget(label(title, role="title"))
        if hint:
            copy.addWidget(label(hint, True))
        row.addLayout(copy, 1)
        toggle = Toggle()
        toggle.setAccessibleName(title)
        toggle.toggled.connect(lambda value: self.setting(key, value))
        self.checks[key] = toggle
        row.addWidget(toggle)
        layout.addLayout(row)

    def activity(self, layout):
        activity = card(layout)
        row = QHBoxLayout()
        self.history_filter = QComboBox()
        self.history_filter.setAccessibleName("筛选事件类型")
        for title, key in (
            ("全部事件", "all"),
            ("真实任务", "real"),
            ("试亮与模拟", "test"),
        ):
            self.history_filter.addItem(title, key)
        self.history_filter.currentIndexChanged.connect(lambda _: self.render_history())
        row.addWidget(self.history_filter)
        self.history_search = QLineEdit()
        self.history_search.setPlaceholderText("搜索应用、状态或处理结果…")
        self.history_search.setAccessibleName("搜索活动记录")
        self.history_search.setClearButtonEnabled(True)
        self.history_search.textChanged.connect(lambda _: self.render_history())
        row.addWidget(self.history_search, 1)
        self.button(row, "导出当前记录", self.export_history)
        activity.addLayout(row)
        self.history = QTableWidget(0, 4)
        self.history.setAccessibleName("最近任务事件")
        self.history.setHorizontalHeaderLabels(
            ("时间", "应用 / 来源", "任务状态", "处理结果")
        )
        self.history.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.history.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.history.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.history.setAlternatingRowColors(True)
        self.history.setShowGrid(False)
        self.history.verticalHeader().hide()
        self.history.verticalHeader().setDefaultSectionSize(46)
        self.history.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self.history.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        self.history.setMinimumHeight(300)
        self.history.itemSelectionChanged.connect(self.show_event_detail)
        activity.addWidget(self.history)
        self.history_empty = label(
            "还没有记录。接入应用后，新任务会自动出现在这里。", True
        )
        activity.addWidget(self.history_empty)
        self.history_count = label("", True)
        activity.addWidget(self.history_count)
        details = card(layout, "记录详情")
        self.event_detail_box = details.parentWidget()
        self.event_detail = label("")
        self.event_detail.setTextFormat(Qt.TextFormat.RichText)
        self.event_detail.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        details.addWidget(self.event_detail)
        self.event_detail_box.hide()

    def testing(self, layout):
        appearance = card(layout, "界面语言 / Language")
        self.language_choice = QComboBox()
        self.language_choice.addItem("中文", "zh-CN")
        self.language_choice.addItem("English", "en")
        self.language_choice.setAccessibleName("界面语言 / Language")
        self.language_choice.currentIndexChanged.connect(
            lambda index: self.setting("language", self.language_choice.itemData(index))
        )
        appearance.addWidget(self.language_choice)
        help_box = card(layout, "常见问题")
        self.help_disclosures = []
        for title, content in (
            (
                "没有火焰灯也能使用吗？",
                "到「应用接入」选择实际使用的来源，完成接入后，在 AI 应用提交一条新任务，观察屏幕火焰或菜单栏从工作中到完成，并在「活动记录」查看任务状态。网页验证不能替代桌面客户端验证；演示和连接诊断不会被计为真实验证。",
            ),
            (
                "连接灯具需要重新接入吗？",
                "不需要。应用接入和偏好保存在本机，连接 Yeelight 灯具后直接沿用。到「我的设备」连接并试亮，再用新任务检查实体提醒。无硬件体验不验证局域网连接或实体发光效果。",
            ),
            (
                "灯没亮怎么办？",
                "先到「总览」检查设备是否在线，再到「我的设备」检查该设备亮度是否高于 0%，在「提醒偏好」检查是否暂停或处于静默时段。然后试亮当前设备。收到设备确认代表指令已被接受；实际发光仍需观察设备。没有硬件时直接查看屏幕提醒。",
            ),
            (
                "来源已开启，为什么没有提醒？",
                "开关只决定是否接收该来源。Codex 需要完成连接并打开新会话；AI 网页需要安装扩展、连接 Chrome 并刷新页面。旧对话不会补播，先提交一条新任务再查看活动记录。",
            ),
            (
                "关闭窗口会停止提醒吗？",
                "会。支持系统托盘时，关闭窗口仅隐藏界面，可从菜单栏 / 托盘重新打开。从应用菜单选择「退出 SeeEnd」才会停止本 App 的服务。电脑休眠、浏览器关闭或页面被丢弃时，无法保证继续采集网页任务。",
            ),
            (
                "Yeelight 连接失败怎么办？",
                "在 Yeelight APP 中开启「局域网控制」，确认电脑与灯具在同一局域网，再到「我的设备」搜索或手动输入 IP。",
            ),
        ):
            item = Disclosure(title, help_box)
            item.content.addWidget(label(content, True))
            self.help_disclosures.append(item)
        diagnostics = card(
            layout, "连接诊断", "模拟任务只检查连接，不代表真实应用接入成功。"
        )
        row = QHBoxLayout()
        diagnostics.addLayout(row)
        self.test_source = QComboBox()
        self.test_source.setAccessibleName("选择需要诊断的来源")
        for source in SOURCES:
            if source.id != "demo":
                self.test_source.addItem(source.name, source.id)
        row.addWidget(self.test_source, 1)
        self.button(
            row,
            "开始诊断",
            lambda: self.trigger(self.test_source.currentData()),
            self.trigger_buttons,
            "primary",
        )
        self.test_message = label("", True)
        diagnostics.addWidget(self.test_message)
        self.test_details_disclosure = Disclosure("诊断详情", diagnostics)
        self.test_details = label("尚未运行诊断。", True)
        self.test_details_disclosure.content.addWidget(self.test_details)
        manual = Disclosure("灯光状态测试", diagnostics)
        self.manual_disclosure = manual
        row = QHBoxLayout()
        manual.content.addLayout(row)
        for state, name in (
            ("idle", "当前无任务"),
            ("working", "任务进行中"),
            ("done", "任务已完成"),
            ("error", "需关注"),
        ):
            self.button(row, name, lambda s=state: self.manual(s), self.manual_buttons)
        self.manual_hint = label("", True)
        manual.content.addWidget(self.manual_hint)
        reset = card(layout, "重置 APP", "清除本机缓存、活动记录、设备配对、提醒偏好和应用接入配置，恢复初始状态。不会修改其他应用的文件或设备固件；应用接入需要重新配置。")
        self.reset_button = self.button(reset, "重置 APP", self.reset_app)
        information = card(layout)
        self.privacy_disclosure = Disclosure("隐私与后台运行", information)
        privacy = self.privacy_disclosure.content
        privacy.addWidget(label(
            "SeeEnd 不保存或转发提问与回答正文。任务状态、设备配对和提醒偏好保存在本机；活动记录显示最近 50 条事件，事件最多保留 7 天。", True
        ))
        privacy.addWidget(label(
            "关闭窗口后，应用继续提醒。从菜单栏、Dock 或托盘可重新打开；选择「退出 SeeEnd」才会停止本应用的后台服务。电脑休眠、浏览器关闭或页面被丢弃时，无法保证继续采集网页任务。", True
        ))
        self.about_disclosure = Disclosure("版本与支持信息", information)
        about = self.about_disclosure.content
        about.addWidget(label(f"SeeEnd  {__version__}", role="title"))
        about.addWidget(
            label(
                "支持 Yeelight YLTD003 和 YLDP005，可同时连接多台灯具，通过局域网连接。网页及桌面客户端接入的兼容性以各版本实测范围为准。",
                True,
            )
        )
        about.addWidget(
            label(
                "当前构建为本地预览版。正式发行的签名、公证和各平台兼容性验收尚未完成。",
                True,
            )
        )
