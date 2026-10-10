"""Native page composition. Operations and state live in Dashboard."""

from PySide6.QtCore import Qt, QTime, QSize
from PySide6.QtGui import QColor, QIcon, QPixmap, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QHBoxLayout,
    QGridLayout,
    QHeaderView,
    QSizePolicy,
    QVBoxLayout,
)
from aiflame.desktop.localization import (
    QComboBox,
    QFrame,
    QLineEdit,
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
    def save_feedback_row(self, layout, key):
        frame = QWidget()
        row = QHBoxLayout(frame)
        row.setContentsMargins(0, 0, 0, 0)
        message = label("", True)
        row.addWidget(message, 1)
        retry = self.button(row, "重试", lambda: self.retry_setting(key), variant="quiet")
        self.save_feedback[key] = (frame, message, retry)
        frame.hide()
        layout.addWidget(frame)

    def source_section(self, layout, key):
        frame = QWidget()
        content = QVBoxLayout(frame)
        content.setContentsMargins(0, 8, 0, 0)
        content.setSpacing(8)
        layout.addWidget(frame)
        self.source_sections[key] = (frame, content)
        return content

    def source_block(self, layout, source):
        frame = QWidget()
        content = QVBoxLayout(frame)
        content.setContentsMargins(0, 0, 0, 10)
        content.setSpacing(8)
        layout.addWidget(frame)
        self.source_blocks[source] = (frame, layout)
        return content

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
        self.save_feedback = {}
        self.checks = {}
        self.source_rows = {}
        self.source_status = {}
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
        bar.setContentsMargins(30, 12, 30, 12)
        self.breadcrumb = label("总览", role="pageTitle", elide=True)
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
        self.tabs.widget(1).viewport().installEventFilter(self)
        self.nav_buttons[0].setChecked(True)


    def home(self, layout):
        mode_card = card(layout)
        mode_card.setContentsMargins(22, 20, 22, 20)
        mode_card.setSpacing(8)
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
        self.home_task_summary = label("", role="title")
        self.home_task_context = label("", True)
        self.home_task_context.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        scene.addWidget(self.home_task_summary)
        scene.addWidget(self.home_task_context)
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
                label("", True, elide=True),
                label("", role="recentSource", elide=True),
                label("", role="recentState", elide=True),
                label("", True, elide=True),
            )
            for item in (when, source, state, result):
                item.setWordWrap(False)
                item.setMinimumWidth(0)
                item.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
            result.setAlignment(Qt.AlignmentFlag.AlignRight)
            source.setProperty("literalText", True)
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
        self.device_instances.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.device_instances.setMinimumContentsLength(15)
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
        self.button(virtual, "切换为硬件提示并继续", lambda: self.choose_mode("hardware"), variant="primary")
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
        self.device_actions_layout = device_actions
        progress.addLayout(device_actions)
        self.yeelight_reconnect_button = self.button(device_actions, "连接此设备", lambda: self.device_action("connect", transport="yeelight", host=self.yeelight_host.text().strip()), self.maintenance_buttons)
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
        self.color_heading_layout = heading
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
            button.setMinimumHeight(36)
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
        self.save_feedback_row(copy, "source:" + source)
        row.addLayout(copy, 1)
        toggle = Toggle()
        toggle.setAccessibleName("启用" + policy.name)
        toggle.setToolTip(policy.description)
        toggle.toggled.connect(lambda value, s=source: self.source_changed(s, value))
        row.addWidget(toggle)
        self.source_checks[source] = toggle
        self.source_rows[source] = frame
        self.source_blocks.setdefault(source, (frame, layout))
        layout.addWidget(frame)

    def sources(self, layout):
        sources = card(layout)
        sources.setSpacing(8)
        outer = sources
        self.source_sections, self.source_blocks = {}, {}
        category = QHBoxLayout()
        category.setContentsMargins(0, 0, 0, 0)
        title = label("接入应用", role="title")
        title.setMargin(0)
        title.setIndent(0)
        category.addWidget(title)
        self.source_category = QComboBox()
        self.source_category.setAccessibleName("选择接入应用")
        for name, key in (("网页应用", "web"), ("编程工具", "cli"), ("桌面客户端", "desktop"), ("本地 API 与测试来源", "local")):
            self.source_category.addItem(name, key)
        self.source_category.currentIndexChanged.connect(self.source_category_changed)
        category.addWidget(self.source_category, 1)
        outer.addLayout(category)
        sources = self.source_section(outer, "web")
        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(12)
        self.button(actions, "安装浏览器扩展", self.open_browser_extension_guide, variant="primary")
        self.browser_install_hint = label("首次使用需先「安装浏览器扩展」。多个网页共用一个扩展，安装一次即可；平台开关只控制是否提醒。", True)
        actions.addWidget(self.browser_install_hint, 1)
        sources.addLayout(actions)
        self.browser_message = label("", True)
        sources.addWidget(self.browser_message)
        self.browser_message.hide()
        for source, name in WEB_SOURCES.items():
            self.source_row(sources, source, name + " 网页")

        cli = self.source_section(outer, "cli")
        sources = cli
        skill_actions = QHBoxLayout()
        skill_actions.setContentsMargins(0, 0, 0, 0)
        skill_actions.setSpacing(12)
        self.skill_copy_button = self.button(skill_actions, "复制安装指令", self.copy_coding_skill, variant="primary")
        self.button(skill_actions, "查看 Skill 仓库", self.open_coding_skill, variant="quiet")
        self.skill_install_hint = label("将指令粘贴到编程工具安装 Skill，接入与维护都通过 Skill 完成；下方开关只控制是否提醒。", True)
        skill_actions.addWidget(self.skill_install_hint, 1)
        sources.addLayout(skill_actions)
        self.skill_notice = label("", True)
        self.skill_notice.hide()
        sources.addWidget(self.skill_notice)
        for source, name in {"codex": "Codex", **CLI_SOURCES}.items():
            sources = self.source_block(cli, source)
            self.source_row(sources, source, name)

        sources = self.source_section(outer, "desktop")
        row = QHBoxLayout()
        self.permission_button = self.button(
            row,
            "授权接入",
            self.request_client_permissions,
            self.source_action_buttons,
        )
        self.button(row, "刷新状态", self.check_clients)
        row.addWidget(label("实验", role="badge"))
        row.addStretch()
        sources.addLayout(row)
        self.chatgpt_permission_hint = label("", True)
        sources.addWidget(self.chatgpt_permission_hint)
        self.chatgpt_permission_hint.hide()
        self.client_message = label("", True)
        sources.addWidget(self.client_message)
        self.client_message.hide()
        for source, name in DESKTOP_SOURCES.items():
            self.source_row(sources, source, name + " 桌面客户端")

        sources = self.source_section(outer, "local")
        for source in ("local-script", "manual", "demo"):
            self.source_row(sources, source)
        self.source_help(outer)
        self.source_category.setCurrentIndex(1)
        self.source_category_changed(self.source_category.currentIndex())

    def source_help(self, layout):
        self.integration_help_disclosure = Disclosure("接入说明", layout)
        instructions = self.integration_help_disclosure.content
        self.integration_help_panels = {}
        for category, content in (
            (
                "cli",
                "验证、诊断、修复、更新或移除接入，都在编程工具中使用 SeeEnd Skill。需要重载或信任 Hook 时，按 Skill 提示操作。",
            ),
            (
                "web",
                "所有网页共用一个 Chrome 扩展。点击「安装浏览器扩展」按引导安装，再发送一条新提问验证接入。",
            ),
            (
                "desktop",
                "仅支持 macOS 普通文字聊天。点击「授权接入」完成授权，打开客户端后点击「刷新状态」。此接入为实验功能。",
            ),
            (
                "local",
                "通过本地 API 上报任务，或使用手动与模拟来源测试提醒。模拟事件不计入真实接入验证。",
            ),
        ):
            panel = QWidget()
            body = QVBoxLayout(panel)
            body.setContentsMargins(0, 0, 0, 0)
            body.setSpacing(8)
            body.addWidget(label(content, True))
            if category in ("web", "desktop"):
                details = label("", True)
                setattr(self, "browser_details" if category == "web" else "client_details", details)
                details.hide()
                body.addWidget(details)
            self.integration_help_panels[category] = panel
            instructions.addWidget(panel)

    def preferences(self, layout):
        notifications = card(layout, "桌面系统通知")
        self.preference_toggle(notifications, "desktop_notifications", "任务通知", "完成、异常或等待输入时通知；独立于灯光亮度与连接。首次使用请允许系统通知。")
        self.notification_permission = label("正在读取系统通知状态…", True)
        notifications.addWidget(self.notification_permission)
        notification_actions = QHBoxLayout()
        notification_actions.setSpacing(12)
        self.notification_test_button = self.button(notification_actions, "测试通知", self.test_notification)
        self.button(notification_actions, "检查授权", self.request_notification_status, variant="quiet")
        notification_actions.addStretch()
        notifications.addLayout(notification_actions)
        settings_action = QHBoxLayout()
        self.button(settings_action, "打开系统通知设置", self.open_notification_settings, variant="quiet")
        settings_action.addStretch()
        notifications.addLayout(settings_action)
        self.notification_test_feedback = label("", True)
        notifications.addWidget(self.notification_test_feedback)
        light = card(layout, "提醒时机", "提醒颜色与各状态亮度请在「我的设备」中按设备设置。")
        row = QHBoxLayout()
        copy = QVBoxLayout()
        self.duration_title = label("提醒持续时间", role="title")
        copy.addWidget(self.duration_title)
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
        self.save_feedback_row(light, "duration")
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
        self.save_feedback_row(quiet_times, "quiet_start")
        self.save_feedback_row(quiet_times, "quiet_end")
        quiet.addWidget(self.quiet_times_box)
        self.quiet_times_box.hide()

    def preference_toggle(self, layout, key, title, hint):
        row = QHBoxLayout()
        copy = QVBoxLayout()
        copy.setSpacing(5)
        copy.addWidget(label(title, role="title"))
        if hint:
            copy.addWidget(label(hint, True))
        self.save_feedback_row(copy, key)
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
        self.history_filter.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.history_filter.setAccessibleName("筛选事件类型")
        for title, key in (
            ("全部事件", "all"),
            ("真实任务", "real"),
            ("试亮与模拟", "test"),
        ):
            self.history_filter.addItem(title, key)
        self.history_filter.currentIndexChanged.connect(lambda _: self.render_history())
        row.addWidget(self.history_filter)
        self.history_view = QComboBox()
        self.history_view.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.history_view.setAccessibleName("活动记录显示方式")
        self.history_view.addItem("按任务汇总", "tasks")
        self.history_view.addItem("原始事件", "events")
        self.history_view.currentIndexChanged.connect(lambda _: self.render_history())
        row.addWidget(self.history_view)
        row.addStretch()
        self.button(row, "导出当前记录", self.export_history)
        activity.addLayout(row)
        self.history_search = QLineEdit()
        self.history_search.setPlaceholderText("搜索应用、状态或处理结果…")
        self.history_search.setAccessibleName("搜索活动记录")
        self.history_search.setClearButtonEnabled(True)
        self.history_search.textChanged.connect(lambda _: self.render_history())
        activity.addWidget(self.history_search)
        self.history = QTableWidget(0, 5)
        self.history.setAccessibleName("最近任务事件")
        self.history.setHorizontalHeaderLabels(
            ("时间", "应用 / 来源", "任务状态", "处理结果", "任务标识")
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
        activity.addWidget(label("选中记录查看任务标识、投递结果与状态事件。", True))
        details = card(layout, "记录详情")
        self.event_detail_box = details.parentWidget()
        self.event_detail = label("")
        self.event_detail.setProperty("literalText", True)
        self.event_detail.setTextFormat(Qt.TextFormat.RichText)
        self.event_detail.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        details.addWidget(self.event_detail)
        self.button(details, "复制任务标识", self.copy_task_id, variant="quiet")
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
        self.save_feedback_row(appearance, "language")
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
