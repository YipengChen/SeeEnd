"""Native page composition. Operations and state live in Dashboard."""

from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QColor, QIcon, QPixmap, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QHBoxLayout,
    QGridLayout,
    QHeaderView,
    QLayout,
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
    QSpinBox,
    QDoubleSpinBox,
    QStackedWidget,
    QTableWidget,
    QWidget,
)

from aiflame import __version__
from aiflame.devices.models import MODELS, DEFAULT_MODEL
from aiflame.devices.light_settings import STATE_BRIGHTNESS_DEFAULTS, FRONT_FLASH_DEFAULTS
from aiflame.integrations.registry import SOURCES
from aiflame.integrations.cli_tools.providers import CLI_SOURCES, REPOSITORY_URLS
from aiflame.integrations.chatgpt_web.host import WEB_SOURCES, WEB_URLS
from .components import CategoryTabs, DeviceModelCard, Disclosure, FlameVisual, HelpButton, HomeSourceButton, TemperatureSwatch, Toggle, ValueSlider, brand_icon, card, icon, label, separator

from .light_controls import LightModePicker, LightPreviewPanel

PAGES = (
    ("总览", "home"),
    ("我的设备", "device"),
    ("应用接入", "apps"),
    ("提醒偏好", "settings"),
    ("活动记录", "history"),
    ("设置与帮助", "help"),
)
RECENT_ROW_HEIGHT = 58
RECENT_ROW_GAP = 8


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
        self.manual_buttons = []
        self.nav_buttons = []
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
        self.main_surface = main
        column = QVBoxLayout(main)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        topbar = QWidget()
        topbar.setObjectName("topbar")
        topbar.setMaximumWidth(1488)
        topbar_host = QWidget()
        topbar_row = QHBoxLayout(topbar_host)
        topbar_row.setContentsMargins(0, 0, 0, 0)
        topbar_row.setSpacing(0)
        topbar_row.addWidget(topbar)
        bar = QHBoxLayout(topbar)
        # The one-pixel optical offset aligns the heading's ink with SeeEnd.
        bar.setContentsMargins(24, 29, 24, 4)
        self.breadcrumb = label("总览", role="moduleTitle", elide=True)
        self.breadcrumb.setWordWrap(False)
        bar.addWidget(self.breadcrumb, alignment=Qt.AlignmentFlag.AlignVCenter)
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
        column.addWidget(topbar_host)
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
            host = QHBoxLayout(widget)
            host.setContentsMargins(24, 24, 24, 24)
            content = QWidget()
            content.setObjectName("pageContent")
            content.setMaximumWidth(1440)
            host.addWidget(content, 1)
            layout = QVBoxLayout(content)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(16)
            builder(layout)
            if builder not in (self.home, self.activity):
                layout.addStretch()
            scroll.setWidget(widget)
            self.tabs.addWidget(scroll)
        self.statusbar = QFrame(main)
        self.statusbar.setObjectName("toast")
        bottom = QHBoxLayout(self.statusbar)
        bottom.setContentsMargins(16, 12, 12, 12)
        self.message = label("", True)
        bottom.addWidget(self.message, 1)
        self.retry_button = self.button(
            bottom, "重新连接", self.retry_message, variant="quiet"
        )
        self.retry_button.hide()
        self.message_close = self.button(bottom, "", self.statusbar.hide, variant="quiet", glyph="close")
        self.message_close.setFixedSize(32, 32)
        self.message_close.setAccessibleName("关闭提示")
        self.statusbar.hide()
        root.addWidget(main, 1)
        self.tabs.widget(0).viewport().installEventFilter(self)
        self.tabs.widget(1).viewport().installEventFilter(self)
        self.nav_buttons[0].setChecked(True)


    def home(self, layout):
        channels = card(layout, name="homeChannels")
        channels.setContentsMargins(20, 12, 16, 12)
        channels.setSpacing(0)
        self.home_channels_frame = channels.parentWidget()
        self.home_channels_layout = channels
        self.home_channels_heading = QGridLayout()
        self.home_channels_heading.setHorizontalSpacing(52)
        self.home_channels_heading.setVerticalSpacing(8)
        self.home_channel_heading = QHBoxLayout()
        self.home_channel_heading.setContentsMargins(0, 0, 0, 0)
        self.home_channel_heading.setSpacing(8)
        self.home_channel_title = label("提醒方式", role="homeChannelTitle")
        self.home_channel_title.setWordWrap(False)
        self.home_channel_title.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.home_channel_heading.addWidget(self.home_channel_title, 0, Qt.AlignmentFlag.AlignVCenter)
        self.home_channels_heading.addLayout(self.home_channel_heading, 0, 0)
        self.home_channels_heading.setColumnStretch(1, 1)
        self.home_channel_settings = self.button(self.home_channel_heading, "", lambda: self.navigate(3), variant="quiet", glyph="settings")
        self.home_channel_settings.setObjectName("homeChannelSettings")
        self.home_channel_settings.setFixedSize(32, 32)
        self.home_channel_settings.setIconSize(QSize(18, 18))
        self.home_channel_heading.setAlignment(self.home_channel_settings, Qt.AlignmentFlag.AlignVCenter)
        self.home_channel_settings.setAccessibleName("设置提醒方式")
        self.home_channel_settings.setToolTip("设置提醒方式")
        self.home_channels_grid = QGridLayout()
        self.home_channels_grid.setHorizontalSpacing(16)
        self.home_channels_grid.setVerticalSpacing(4)
        channels.addLayout(self.home_channels_heading)
        self.home_channel_checks = {}
        self.home_channel_items = []
        self.home_channels_inline = None
        for key, title, glyph in (
            ("desktop_notifications", "系统通知", "bell"),
            ("wechat_notifications", "微信通知", "message"),
            ("desktop_flame_enabled", "桌面火焰", "flame"),
            ("hardware_notifications", "设备提醒", "device"),
        ):
            item = QWidget()
            item.setFixedHeight(40)
            row = QHBoxLayout(item)
            row.setContentsMargins(16, 0, 0, 0)
            row.setSpacing(8)
            mark = label()
            mark.setFixedSize(18, 18)
            mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
            mark.setPixmap(icon(glyph, size=18).pixmap(18, 18))
            row.addWidget(mark, 0, Qt.AlignmentFlag.AlignVCenter)
            name = label(title, role="preferenceLabel")
            name.setWordWrap(False)
            name.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            name.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
            row.addWidget(name, 0, Qt.AlignmentFlag.AlignVCenter)
            toggle = Toggle()
            toggle.setFixedSize(48, 32)
            toggle.setAccessibleName(title)
            toggle.toggled.connect(lambda value, k=key: self.setting(k, value))
            self.home_channel_checks[key] = toggle
            row.addWidget(toggle, 0, Qt.AlignmentFlag.AlignVCenter)
            row.addStretch()
            self.home_channel_items.append(item)
        self.home_channels_heading.addLayout(self.home_channels_grid, 0, 1)
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
        top_frame = QWidget()
        self.home_body_frame = top_frame
        top = QVBoxLayout(top_frame)
        top.setContentsMargins(0, 0, 0, 0)
        top.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        top.setSpacing(18)
        layout.addWidget(top_frame, 1)
        layout.addStretch(0)
        scene = card(top, name="scene")
        scene.setSpacing(10)
        self.home_top = top
        self.scene_frame = scene.parentWidget()
        self.scene_frame.setMinimumHeight(380)
        scene.parentWidget().setMinimumWidth(300)
        top.setStretch(0, 6)
        scene_header = QHBoxLayout()
        self.scene_title = label("任务状态", role="title")
        scene_header.addWidget(self.scene_title)
        scene_header.addStretch()
        scene.addLayout(scene_header)
        self.home_task_summary = label("", True)
        self.home_task_context = label("", True)
        self.home_task_context.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        scene.addWidget(self.home_task_summary)
        scene.addWidget(self.home_task_context)
        self.flame = FlameVisual()
        self.flame.return_requested.connect(self.return_displayed_task)
        self.flame.setToolTip("双击返回当前任务。多条任务同时进行时，返回最近更新的一条。")
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
        aside.setSpacing(16)
        top.addLayout(aside, 5)
        quick = card(aside)
        self.home_sources_frame = quick.parentWidget()
        quick.setContentsMargins(22, 20, 22, 20)
        quick.setSpacing(4)
        quick.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.home_sources_button = self.button(
            quick, "应用接入", lambda: self.navigate(2),
            variant="sectionTitle",
        )
        self.home_sources_button.setAccessibleName("应用接入")
        self.home_sources_row = QWidget()
        source_groups = QVBoxLayout(self.home_sources_row)
        source_groups.setContentsMargins(0, 20, 0, 0)
        source_groups.setSpacing(14)
        source_groups.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.home_source_icons = {}
        self.home_source_groups = {}
        self.home_source_layouts = {}
        self.home_source_grid_state = {}
        self.home_sources_row.installEventFilter(self)
        categories = (
            ("网页应用", WEB_SOURCES),
            ("编程工具", {"codex": "Codex", **CLI_SOURCES}),
        )
        for title, sources in categories:
            group = QWidget()
            group.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            group_layout = QVBoxLayout(group)
            group_layout.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
            group_layout.setContentsMargins(0, 0, 0, 0)
            group_layout.setSpacing(4)
            group_layout.addWidget(label(title, role="homeSourceGroup"))
            source_icons = QGridLayout()
            source_icons.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
            source_icons.setHorizontalSpacing(8)
            source_icons.setVerticalSpacing(4)
            for number, (source, name) in enumerate(sources.items()):
                button = HomeSourceButton(source, name, WEB_URLS.get(source) or REPOSITORY_URLS[source])
                button.setCursor(Qt.CursorShape.PointingHandCursor)
                button.clicked.connect(lambda checked=False, b=button: self.open_app_link(b.link))
                source_icons.addWidget(button, number // 6, number % 6)
                button.hide()
                self.home_source_icons[source] = button
            group_layout.addLayout(source_icons)
            self.home_source_layouts[group] = source_icons
            group.installEventFilter(self)
            source_groups.addWidget(group)
            self.home_source_groups[title] = (group, tuple(sources))
        quick.addWidget(self.home_sources_row)
        self.home_sources_empty = label("尚未开启应用接入", True)
        quick.addWidget(self.home_sources_empty)
        self.home_sources_row.hide()
        self.home_sources_empty.hide()
        recent = card(aside)
        aside.setStretch(0, 4)
        aside.setStretch(1, 6)
        self.recent_frame = recent.parentWidget()
        recent.setSpacing(8)
        heading = QHBoxLayout()
        heading.addWidget(label("最近动态", role="title"))
        heading.addStretch()
        self.recent_more = self.button(
            heading, "更多", lambda: self.navigate(4), variant="quiet",
        )
        self.recent_more.setAccessibleName("更多活动记录")
        recent.addLayout(heading)
        self.recent_rows = []
        self.recent_return_buttons = []
        self.recent_row_frames = []
        self.recent_list_body = QWidget()
        # Row count follows this allocated space, rather than making the card
        # grow to accommodate the records in the model.
        self.recent_list_body.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Ignored)
        self.recent_list_body.setMinimumHeight(RECENT_ROW_HEIGHT)
        self.recent_list = QVBoxLayout(self.recent_list_body)
        self.recent_list.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        self.recent_list.setContentsMargins(0, 0, 0, 0)
        self.recent_list.setSpacing(0)
        self.recent_empty = label("暂无记录", True)
        self.recent_list.addWidget(self.recent_empty)
        self.recent_gaps = []
        recent.addWidget(self.recent_list_body, 1)
        self.recent_list.addStretch()
        # Reserve the heading, padding and one complete row in compact layouts.
        self.recent_frame.setMinimumHeight(144)
        self.recent_list_body.installEventFilter(self)

    def add_recent_row(self):
        if self.recent_rows:
            gap_index = self.recent_list.count() - 1
            self.recent_list.insertSpacing(gap_index, RECENT_ROW_GAP)
            self.recent_gaps.append((gap_index, self.recent_list.itemAt(gap_index).spacerItem()))
        frame = QFrame()
        frame.setObjectName("recentEvent")
        frame.setFixedHeight(RECENT_ROW_HEIGHT)
        row = QVBoxLayout(frame)
        row.setContentsMargins(0, 6, 0, 8)
        row.setSpacing(4)
        summary = QHBoxLayout()
        outcome = QHBoxLayout()
        outcome.setSpacing(12)
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
        when.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        result.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        source.setProperty("literalText", True)
        summary.addWidget(source, 1)
        summary.addWidget(when)
        outcome.addWidget(state)
        outcome.addStretch()
        return_button = self.button(outcome, "返回任务", lambda: self.return_task(return_button.property("returnEventId")), variant="quiet")
        return_button.setObjectName("returnTaskAction")
        return_button.setFixedHeight(20)
        policy = return_button.sizePolicy()
        policy.setRetainSizeWhenHidden(True)
        return_button.setSizePolicy(policy)
        return_button.hide()
        self.recent_return_buttons.append(return_button)
        outcome.addWidget(result)
        row.addLayout(summary)
        row.addLayout(outcome)
        self.recent_list.insertWidget(self.recent_list.count() - 1, frame)
        self.recent_row_frames.append(frame)
        self.recent_rows.append((when, source, state, result))

    def devices(self, layout):
        self.device_view_epoch = 0
        self.selected_device_id = None
        self.port_restore_pending = False
        self.device_operation_id = None
        self.rendered_yeelight_discovery = None
        self.device_selection_loading = False
        self.selected_device_state = {"online": False}
        self.selected_device_model = DEFAULT_MODEL
        self.adding_device = False
        self.device_rows = {}
        choices = card(layout)
        self.device_selector = choices.parentWidget()
        self.device_list_panel = QWidget()
        saved = QVBoxLayout(self.device_list_panel)
        saved.setContentsMargins(0, 0, 0, 0)
        saved.setSpacing(12)
        heading = QHBoxLayout()
        heading.addWidget(label("已添加设备", role="title"))
        heading.addStretch()
        self.device_add_button = self.button(heading, "添加设备", self.show_device_catalog, variant="primary", glyph="plus")
        saved.addLayout(heading)
        self.device_list_layout = QVBoxLayout()
        self.device_list_layout.setSpacing(12)
        saved.addLayout(self.device_list_layout)
        choices.addWidget(self.device_list_panel)
        self.device_list_panel.hide()
        self.device_catalog_panel = QWidget()
        catalog = QVBoxLayout(self.device_catalog_panel)
        catalog.setContentsMargins(0, 0, 0, 0)
        catalog.setSpacing(12)
        self.device_catalog_back = self.button(catalog, "返回设备列表", self.show_device_selector, variant="quiet", glyph="back")
        self.device_catalog_back.hide()
        catalog.addWidget(label("选择设备型号", role="title"))
        catalog.addWidget(label("选择型号后搜索或连接设备。", True))
        choices.addWidget(self.device_catalog_panel)
        self.device_cards_layout = QGridLayout()
        self.device_card_columns = 2
        self.device_cards_layout.setSpacing(16)
        catalog.addLayout(self.device_cards_layout)
        self.device_model_buttons = {}
        for index, model in enumerate(MODELS):
            button = DeviceModelCard(model)
            button.clicked.connect(lambda checked=False, model_id=model["id"]: self.choose_device_model(model_id))
            self.device_cards_layout.addWidget(button, index // 2, index % 2)
            self.device_model_buttons[model["id"]] = button
        self.device_details = QWidget()
        details = QVBoxLayout(self.device_details)
        self.device_details_layout = details
        details.setContentsMargins(0, 0, 0, 0)
        details.setSpacing(16)
        device_heading = card(details)
        heading = QHBoxLayout()
        self.device_back_button = self.button(heading, "返回设备列表", self.show_device_selector, variant="quiet", glyph="back")
        heading.addStretch()
        device_heading.addLayout(heading)
        model_heading = QVBoxLayout()
        model_heading.setSpacing(4)
        self.device_model_title = label("Yeelight 屏幕挂灯 Pro", role="pageTitle")
        self.device_model_title.setWordWrap(True)
        model_heading.addWidget(self.device_model_title)
        self.device_model_description = label("YLTD003 · 背部彩灯 / 前部白灯", True)
        self.device_model_description.setWordWrap(True)
        model_heading.addWidget(self.device_model_description)
        device_heading.addLayout(model_heading)
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

    def build_mijia_connection(self, parent_layout):
        self.mijia_login_epoch = 0
        self.mijia_ignored_sessions = set()
        self.mijia_login_state = None
        self.rendered_mijia_login = None
        self.mijia_step = 0
        self.mijia_discovered = []
        self.mijia_panel = QWidget()
        flow = QVBoxLayout(self.mijia_panel)
        flow.setContentsMargins(0, 8, 0, 0)
        flow.setSpacing(16)
        rail = QHBoxLayout()
        self.mijia_step_labels = []
        for index, title in enumerate(("1  确认设备", "2  扫码登录", "3  核对连接")):
            marker = label(title, role="title" if index == 0 else "muted")
            rail.addWidget(marker, 1)
            self.mijia_step_labels.append(marker)
        flow.addLayout(rail)
        self.mijia_target_summary = label("", True)
        self.mijia_target_summary.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.mijia_target_summary.hide()
        flow.addWidget(self.mijia_target_summary)
        self.mijia_steps = []

        def step(title):
            panel = QWidget()
            body = QVBoxLayout(panel)
            body.setContentsMargins(0, 0, 0, 0)
            body.setSpacing(12)
            body.addWidget(label(title, role="title"))
            flow.addWidget(panel)
            self.mijia_steps.append(panel)
            panel.setVisible(len(self.mijia_steps) == 1)
            return body

        target = step("先确认你要控制的插座")
        lookup = Disclosure("在哪里查看 IP 和设备 ID？", target)
        self.mijia_app_hint = label("米家 App：打开目标插座 → 右上角「…」/ 设置 → 更多设置 → 网络信息，查看 IP；在网络信息或设备信息中核对设备 ID（DID）。", True)
        lookup.content.addWidget(self.mijia_app_hint)
        lookup.content.addWidget(label("入口随米家版本不同。也可在米家给插座改一个易识别的名字，扫码后按名称核对。", True))
        fields = QHBoxLayout()
        fields.setSpacing(16)
        self.mijia_target_ip = QLineEdit()
        self.mijia_target_did = QLineEdit()
        for control, title, placeholder in ((self.mijia_target_ip, "目标 IP（选填）", "例如 192.168.1.100"),
                                             (self.mijia_target_did, "设备 ID / DID（选填）", "米家显示的纯数字 ID")):
            column = QVBoxLayout()
            column.setSpacing(6)
            column.addWidget(label(title))
            control.setPlaceholderText(placeholder)
            control.setAccessibleName(title)
            control.setMinimumWidth(0)
            column.addWidget(control)
            fields.addLayout(column, 1)
        target.addLayout(fields)
        self.mijia_target_note = label("有多台插座时，建议填写其中一项；留空则在扫码后选择。", True)
        target.addWidget(self.mijia_target_note)
        self.mijia_scan_button = self.button(target, "扫描局域网设备", self.scan_mijia_devices, self.maintenance_buttons)
        self.mijia_scan_choices = QComboBox()
        self.mijia_scan_choices.setAccessibleName("按 IP 和设备 ID 选择扫描到的设备")
        self.mijia_scan_choices.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.mijia_scan_choices.setMinimumContentsLength(15)
        self.mijia_scan_choices.addItem("选择扫描到的设备…", None)
        self.mijia_scan_choices.currentIndexChanged.connect(self.choose_mijia_discovery)
        self.mijia_scan_choices.hide()
        target.addWidget(self.mijia_scan_choices)
        self.mijia_scan_status = label("扫描只读取 IP 和设备 ID；型号将在扫码后核对。", True)
        target.addWidget(self.mijia_scan_status)
        self.mijia_next_button = self.button(target, "下一步：扫码登录", self.continue_mijia_login, self.maintenance_buttons, "primary")
        self.mijia_target_ip.textChanged.connect(self.mijia_target_changed)
        self.mijia_target_did.textChanged.connect(self.mijia_target_changed)

        login = step("登录插座所属的小米账号")
        login.addWidget(label("选择与米家一致的地区，再获取二维码。用米家 App 的扫一扫登录，也可在浏览器打开小米登录页。", True))
        row = QHBoxLayout()
        row.addWidget(label("账号地区"))
        self.mijia_region = QComboBox()
        self.mijia_region.setAccessibleName("米家账号地区")
        for name, region in (("中国大陆", "cn"), ("欧洲", "de"), ("美国", "us"), ("台湾", "tw"),
                             ("新加坡", "sg"), ("俄罗斯", "ru"), ("印度", "in"), ("印度（i2）", "i2")):
            self.mijia_region.addItem(name, region)
        row.addWidget(self.mijia_region)
        row.addStretch()
        login.addLayout(row)
        login.addWidget(label("扫码期间会读取米家账号下的设备凭据。SeeEnd 只保存选中插座的 Token；完成或取消后结束本地云会话。", True))
        self.mijia_login_status = label("", True)
        login.addWidget(self.mijia_login_status)
        self.mijia_qr = label()
        self.mijia_qr.setFixedSize(200, 200)
        self.mijia_qr.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.mijia_qr.setAccessibleName("小米账号登录二维码")
        self.mijia_qr.hide()
        login.addWidget(self.mijia_qr, 0, Qt.AlignmentFlag.AlignHCenter)
        self.mijia_login_button = self.button(login, "获取登录二维码", self.start_mijia_login, self.maintenance_buttons, "primary")
        login_actions = QHBoxLayout()
        login.addLayout(login_actions)
        self.mijia_login_link_button = self.button(login_actions, "浏览器登录", self.open_mijia_login, variant="quiet")
        self.mijia_login_link_button.setToolTip("在浏览器打开小米登录页")
        self.mijia_login_link_button.hide()
        self.mijia_cancel_button = self.button(login_actions, "取消并返回", self.return_mijia_target, variant="quiet")
        self.mijia_cancel_button.setToolTip("取消登录，返回确认设备")

        connect = step("核对名称、IP 和设备 ID 后连接")
        self.mijia_selection_status = label("", True)
        connect.addWidget(self.mijia_selection_status)
        self.mijia_choices = QComboBox()
        self.mijia_choices.setAccessibleName("选择已登录账号中的米家智能插座 3 / 4")
        self.mijia_choices.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.mijia_choices.setMinimumContentsLength(15)
        self.mijia_choices.addItem("请选择要连接的插座…", None)
        self.mijia_choices.currentIndexChanged.connect(self.choose_mijia_plug)
        connect.addWidget(self.mijia_choices)
        self.mijia_selected_info = label("", True)
        self.mijia_selected_info.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        connect.addWidget(self.mijia_selected_info)
        connect.addWidget(label("连接 IP（可按米家当前信息修改；留空自动查找）"))
        self.mijia_connect_ip = QLineEdit()
        self.mijia_connect_ip.setPlaceholderText("插座当前局域网 IP，可留空")
        self.mijia_connect_ip.setAccessibleName("用于连接目标插座的 IP 地址")
        self.mijia_connect_ip.textChanged.connect(lambda _: self.update_busy())
        connect.addWidget(self.mijia_connect_ip)
        self.mijia_match_hint = label("请选择插座后核对设备信息。", True)
        connect.addWidget(self.mijia_match_hint)
        connect.addWidget(label("连接前请核对名称、IP 和设备 ID。身份与型号验证成功后，才保存所选插座凭据并控制通断电。", True))
        self.mijia_import_button = self.button(connect, "连接这只插座", self.import_mijia_plug, self.maintenance_buttons, "primary")
        self.mijia_retry_button = self.button(connect, "重新扫码登录", self.retry_mijia_login, self.maintenance_buttons, "primary")
        self.mijia_retry_button.hide()
        self.mijia_back_button = self.button(connect, "返回修改目标设备", self.return_mijia_target, variant="quiet")
        parent_layout.addWidget(self.mijia_panel)
        self.mijia_panel.hide()

    def build_device_details(self, layout):
        details = layout
        disabled = card(layout, "设备提醒已关闭", "已有设备配置仍保留。开启设备提醒后，可连接设备并接收任务状态。")
        self.device_disabled_card = disabled.parentWidget()
        self.button(disabled, "设置提醒方式", lambda: self.navigate(3), variant="quiet")
        self.hardware_controls = QWidget()
        layout.addWidget(self.hardware_controls)
        layout = QVBoxLayout(self.hardware_controls)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)
        top = QVBoxLayout()
        top.setSpacing(16)
        self.device_connections_layout = top
        layout.addLayout(top)
        yeelight = card(top)
        self.connection_prepare_disclosure = Disclosure("连接准备", yeelight)
        self.connection_title = self.connection_prepare_disclosure.button
        self.connection_prepare_hint = label("在 Yeelight APP 中配置灯具，接入2.4 GHz WiFi，开启「局域网控制」。此电脑需与灯具在同一局域网内。", True)
        self.connection_prepare_disclosure.content.addWidget(self.connection_prepare_hint)
        self.yeelight_card = yeelight.parentWidget()
        yeelight.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.yeelight_history_layout = QVBoxLayout()
        self.yeelight_history_layout.setSpacing(12)
        yeelight.addLayout(self.yeelight_history_layout)
        self.yeelight_history_heading = QWidget()
        history_heading = QHBoxLayout(self.yeelight_history_heading)
        history_heading.setContentsMargins(0, 0, 0, 0)
        history_heading.addWidget(label("当前设备", role="title"))
        history_heading.addStretch()
        self.yeelight_history_layout.addWidget(self.yeelight_history_heading)
        add = card(yeelight, name="inlinePanel")
        add.setContentsMargins(0, 8, 0, 0)
        self.discovery_hint = label("先搜索局域网中的灯具；未找到时，可以手动输入 IP 地址。", True)
        add.addWidget(self.discovery_hint)
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
        self.build_mijia_connection(yeelight)
        self.yeelight_card.hide()
        progress = card(top, name="inlinePanel")
        progress.setContentsMargins(0, 8, 0, 0)
        self.device_status_layout = progress
        self.device_status_card = progress.parentWidget()
        top.removeWidget(progress.parentWidget())
        top.insertWidget(0, progress.parentWidget())
        self.device_summary = label("正在检查设备", role="lightStateTitle")
        summary = QHBoxLayout()
        summary.addWidget(self.device_summary)
        self.device_summary_icon = label()
        self.device_summary_icon.setFixedSize(18, 18)
        self.device_summary_icon.hide()
        summary.addWidget(self.device_summary_icon)
        summary.setSpacing(8)
        progress.addLayout(summary)
        self.device_connection_hint = label("", True)
        progress.addWidget(self.device_connection_hint)
        self.device_connection_hint.hide()
        self.device_information = Disclosure("设备信息", progress)
        self.device_information.content.setSpacing(0)
        self.device_information.content.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.device_information.layout().removeWidget(self.device_information.button)
        summary.addWidget(self.device_information.button)
        summary.addStretch()
        self.device_information.button.toggled.connect(self.device_information.setVisible)
        self.device_information.hide()
        self.device_version = label("连接后读取固件版本", True)
        self.device_information.content.addWidget(self.device_version)
        self.device_identity = label("", True)
        self.device_identity.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.device_information.content.addWidget(self.device_identity)
        self.mijia_connected_identity = label("", True)
        self.mijia_connected_identity.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.mijia_connected_identity.hide()
        self.device_information.content.addWidget(self.mijia_connected_identity)
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
        self.yeelight_reconnect_button = self.button(device_actions, "连接此设备", self.reconnect_current_device, self.maintenance_buttons, "primary")
        self.yeelight_reconnect_button.setMinimumHeight(40)
        self.yeelight_reconnect_button.hide()
        self.try_light_button = self.button(
            device_actions,
            "试亮此设备",
            self.test_current_device,
            self.manual_buttons,
            "primary",
        )
        device_actions.setSpacing(10)
        self.disconnect_button = self.button(device_actions, "断开此设备", lambda: self.device_action("disconnect"), self.maintenance_buttons)
        self.yeelight_delete_button = self.button(device_actions, "删除此设备", self.confirm_delete_current_device, self.maintenance_buttons)
        for button, tone in ((self.try_light_button, "preview"), (self.disconnect_button, "disconnect"),
                             (self.yeelight_delete_button, "delete")):
            button.setProperty("deviceAction", tone)
            button.setMinimumHeight(40)
        self.yeelight_delete_button.hide()
        self.device_test_caption = label("", True)
        progress.addWidget(self.device_test_caption)
        self.device_test_caption.hide()
        device_actions.addStretch()
        self.connection_paused = False
        self.yeelight_history_layout.addWidget(self.device_instance_panel)
        self.yeelight_history_layout.addWidget(self.device_status_card)
        self.yeelight_card.layout().addWidget(self.device_job_feedback)
        layout = details
        light = card(layout)
        self.device_light_card = light.parentWidget()
        self.plug_settings_panel = QWidget()
        plug = QVBoxLayout(self.plug_settings_panel)
        plug.setContentsMargins(0, 0, 0, 0)
        plug.setSpacing(14)
        plug.addWidget(label("任务提醒亮灭", role="title"))
        plug.addWidget(label("灯具自身开关保持打开，并支持通电自动亮起。", True))
        self.plug_previews = LightPreviewPanel()
        plug.addWidget(self.plug_previews)
        self.plug_state_sections = {}
        self.plug_state_resets = {}
        self.plug_power_choices = {}
        for state, title in (("working", "任务进行中"), ("done", "任务已完成"), ("needs_input", "等待输入")):
            state_panel = QWidget()
            section = QVBoxLayout(state_panel)
            section.setContentsMargins(0, 8, 0, 0)
            heading = QHBoxLayout()
            heading.addWidget(label(title, role="lightStateTitle"), 1)
            reset = self.button(heading, "恢复默认", lambda key=state: self.reset_light_state(key))
            reset.setObjectName("yeelightReset")
            reset.setAccessibleName(title + "恢复默认")
            self.plug_state_resets[state] = reset
            section.addLayout(heading)
            row = QHBoxLayout()
            row.addWidget(label("亮灭", True), 1)
            section.addLayout(row)
            choice = LightModePicker(columns=2)
            choice.setMinimumWidth(180)
            choice.setMaximumWidth(240)
            choice.setAccessibleName(title + "插座亮灭")
            choice.addItem("熄灭", False)
            choice.addItem("亮起", True)
            choice.currentIndexChanged.connect(lambda _, key=state, control=choice: self.device_light_setting("state_power", {key: control.currentData()}))
            row.addWidget(choice)
            plug.addWidget(state_panel)
            state_panel.hide()
            self.plug_state_sections[state] = state_panel
            self.plug_power_choices[state] = choice
        light.addWidget(self.plug_settings_panel)
        self.plug_settings_panel.hide()
        self.device_light_hint = label("先连接并添加设备，即可设置此设备的灯光表现。", True)
        light.addWidget(self.device_light_hint)
        self.yeelight_colors_panel = QWidget()
        colors = QVBoxLayout(self.yeelight_colors_panel)
        colors.setContentsMargins(0, 0, 0, 0)
        colors.setSpacing(18)
        heading = QHBoxLayout()
        heading.setSpacing(16)
        self.color_heading_layout = heading
        self.yeelight_light_title = label("灯效设置", role="title")
        heading.addWidget(self.yeelight_light_title, 0, Qt.AlignmentFlag.AlignVCenter)
        heading.addStretch()
        header = QVBoxLayout()
        header.setSpacing(4)
        header.addLayout(heading)
        self.yeelight_light_description = label("按任务状态设置颜色与亮度，修改自动保存。", True)
        header.addWidget(self.yeelight_light_description)
        header.addSpacing(6)
        colors.addLayout(header)
        self.yeelight_mode_panel = QWidget()
        mode_row = QVBoxLayout(self.yeelight_mode_panel)
        mode_row.setContentsMargins(0, 0, 0, 0)
        mode_row.setSpacing(14)
        mode_row.addWidget(label("选择灯效", role="lightStateTitle"))
        self.yeelight_reminder_mode = LightModePicker(columns=2)
        self.yeelight_reminder_mode.setAccessibleName("YLTD003 提醒灯模式")
        self.yeelight_reminder_mode.addItem("背部彩灯", "back")
        self.yeelight_reminder_mode.addItem("前部白灯", "front")
        self.yeelight_reminder_mode.currentIndexChanged.connect(lambda _: self.yeelight_mode_changed())
        mode_row.addWidget(self.yeelight_reminder_mode, 1)
        colors.addWidget(self.yeelight_mode_panel)
        self.cube_effect_hint = label("", True)
        self.cube_effect_hint.hide()
        colors.addWidget(self.cube_effect_hint)
        self.light_previews = LightPreviewPanel()
        colors.addWidget(self.light_previews)
        self.yeelight_state_sections = {}
        self.yeelight_state_resets = {}
        self.yeelight_color_choices = {}
        self.yeelight_color_buttons = {}
        self.yeelight_color_labels = {}
        self.yeelight_temperature_controls = {}
        self.yeelight_temperature_fields = {}
        self.yeelight_temperature_swatches = {}
        self.yeelight_temperature_labels = {}
        self.yeelight_temperature_titles = {}
        self.yeelight_state_titles = {}
        self.yeelight_brightness_controls = {}
        self.yeelight_brightness_labels = {}
        palette = (("天空蓝", "#4A90E2"), ("薄荷绿", "#52C788"), ("琥珀橙", "#FF9F43"),
                   ("珊瑚红", "#FF6B6B"), ("玫瑰粉", "#FF72B6"), ("薰衣草紫", "#A78BFA"),
                   ("靛蓝", "#6366F1"), ("湖水青", "#22D3EE"), ("柠檬黄", "#FACC15"), ("暖白", "#FFE7C2"))
        for number, (state, title) in enumerate((("working", "任务进行中"), ("done", "任务已完成"), ("needs_input", "等待输入"))):
            state_panel = QWidget()
            section = QVBoxLayout(state_panel)
            section.setContentsMargins(0, 8, 0, 0)
            section.setSpacing(10)
            state_title = label(title, role="lightStateTitle")
            self.yeelight_state_titles[state] = state_title
            state_heading = QHBoxLayout()
            state_heading.addWidget(state_title, 1)
            reset = self.button(state_heading, "恢复默认", lambda key=state: self.reset_light_state(key))
            reset.setObjectName("yeelightReset")
            reset.setAccessibleName(title + "恢复默认")
            self.yeelight_state_resets[state] = reset
            section.addLayout(state_heading)
            fields = QGridLayout()
            fields.setHorizontalSpacing(14)
            fields.setVerticalSpacing(8)
            fields.setColumnMinimumWidth(0, 40)
            fields.setColumnStretch(1, 1)
            fields.setColumnMinimumWidth(2, 96)
            self.yeelight_color_labels[state] = label("颜色", True)
            fields.addWidget(self.yeelight_color_labels[state], 0, 0)
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
            temperature_title = label("色温", True)
            temperature = ValueSlider(Qt.Orientation.Horizontal)
            temperature.setRange(2700, 6500)
            temperature.setSingleStep(100)
            temperature.setPageStep(100)
            temperature.setAccessibleName(title + "色温")
            temperature.setToolTip("2700K 暖光 · 6500K 冷光")
            temperature_value = QSpinBox()
            temperature_value.setRange(2700, 6500)
            temperature_value.setSingleStep(100)
            temperature_value.setSuffix(" K")
            temperature_value.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
            temperature_value.setKeyboardTracking(False)
            temperature_value.setFixedWidth(100)
            temperature_value.setAccessibleName(title + "色温数值")
            temperature_value.valueChanged.connect(temperature.setValue)
            temperature_field = QWidget()
            temperature_row = QHBoxLayout(temperature_field)
            temperature_row.setContentsMargins(0, 0, 0, 0)
            temperature_row.setSpacing(10)
            swatch = TemperatureSwatch()
            swatch.setAccessibleName(title + "色温示意")
            temperature_row.addWidget(swatch)
            temperature_row.addWidget(temperature, 1)
            temperature.valueChanged.connect(lambda value, key=state: self.yeelight_temperature_changed(key, value))
            fields.addWidget(temperature_title, 0, 0)
            fields.addWidget(temperature_field, 0, 1)
            fields.addWidget(temperature_value, 0, 2)
            self.yeelight_temperature_titles[state] = temperature_title
            self.yeelight_temperature_controls[state] = temperature
            self.yeelight_temperature_fields[state] = temperature_field
            self.yeelight_temperature_swatches[state] = swatch
            self.yeelight_temperature_labels[state] = temperature_value
            for item in (self.yeelight_color_labels[state], choice, button,
                         temperature_title, temperature_field, temperature_value):
                policy = item.sizePolicy()
                policy.setRetainSizeWhenHidden(True)
                item.setSizePolicy(policy)
            for item in (temperature_title, temperature_field, temperature_value):
                item.hide()
            fields.addWidget(label("亮度", True), 1, 0)
            control = ValueSlider(Qt.Orientation.Horizontal)
            control.setRange(0, 100)
            control.setValue(STATE_BRIGHTNESS_DEFAULTS[state])
            control.setAccessibleName(title + "亮度")
            value_label = QSpinBox()
            value_label.setRange(0, 100)
            value_label.setSuffix("%")
            value_label.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
            value_label.setKeyboardTracking(False)
            value_label.setFixedWidth(100)
            value_label.setValue(control.value())
            value_label.setAccessibleName(title + "亮度数值")
            value_label.valueChanged.connect(control.setValue)
            control.valueChanged.connect(lambda value, key=state: self.yeelight_brightness_changed(key, value))
            fields.addWidget(control, 1, 1)
            fields.addWidget(value_label, 1, 2, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            self.yeelight_brightness_controls[state] = control
            self.yeelight_brightness_labels[state] = value_label
            section.addLayout(fields)
            colors.addWidget(state_panel)
            self.yeelight_state_sections[state] = state_panel
            state_panel.hide()
        self.yeelight_flash_panel = QWidget()
        flash = QGridLayout(self.yeelight_flash_panel)
        flash.setContentsMargins(0, 0, 0, 0)
        flash.setHorizontalSpacing(14)
        flash.setVerticalSpacing(8)
        self.yeelight_flash_enabled = Toggle("闪烁提醒")
        self.yeelight_flash_enabled.setChecked(True)
        self.yeelight_flash_enabled.toggled.connect(lambda value: self.yeelight_flash_changed("enabled", value))
        flash.addWidget(self.yeelight_flash_enabled, 0, 0, 1, 3)
        self.yeelight_flash_controls = {}
        self.yeelight_flash_labels = {}
        for row, (key, title, minimum, maximum) in enumerate((
            ("on_ms", "亮灯时长", 100, 10000), ("off_ms", "熄灭时长", 100, 10000)), 1):
            title_label = label(title, True)
            title_label.setWordWrap(False)
            flash.addWidget(title_label, row, 0)
            control = ValueSlider(Qt.Orientation.Horizontal)
            control.setRange(minimum, maximum)
            control.setSingleStep(100)
            control.setPageStep(100)
            control.setValue(FRONT_FLASH_DEFAULTS[key])
            control.setAccessibleName("等待输入" + title)
            control.valueChanged.connect(lambda value, field=key: self.yeelight_flash_changed(field, value))
            flash.addWidget(control, row, 1)
            self.yeelight_flash_controls[key] = control
            value_label = QDoubleSpinBox()
            value_label.setRange(minimum / 1000, maximum / 1000)
            value_label.setDecimals(1)
            value_label.setSingleStep(0.1)
            value_label.setSuffix(" 秒")
            value_label.setButtonSymbols(QDoubleSpinBox.ButtonSymbols.NoButtons)
            value_label.setKeyboardTracking(False)
            value_label.setFixedWidth(124)
            value_label.setValue(FRONT_FLASH_DEFAULTS[key] / 1000)
            value_label.setAccessibleName("等待输入" + title + "数值")
            value_label.valueChanged.connect(lambda value, slider=control: slider.setValue(round(value * 1000)))
            flash.addWidget(value_label, row, 2)
            self.yeelight_flash_labels[key] = value_label
        flash.setColumnStretch(1, 1)
        flash.setColumnMinimumWidth(2, 96)
        hint = label("等待输入时重复闪烁；关闭闪烁则常亮。", True)
        flash.addWidget(hint, 3, 0, 1, 3)
        self.yeelight_flash_cycle_label = label("当前总周期：1.2 秒", True)
        flash.addWidget(self.yeelight_flash_cycle_label, 4, 0, 1, 3)
        flash_help = QVBoxLayout()
        limits = Disclosure("闪烁时长限制", flash_help)
        limits.content.addWidget(label("每次循环至少 1.2 秒。设置过短时，会自动延长当前时长，避免频繁控制灯具。", True))
        flash.addLayout(flash_help, 5, 0, 1, 3)
        colors.addWidget(self.yeelight_flash_panel)
        self.yeelight_flash_panel.hide()
        self.light_previews.stateSelected.connect(self.show_light_state_parameters)
        self.plug_previews.stateSelected.connect(self.show_light_state_parameters)
        light.addWidget(self.yeelight_colors_panel)
        self.yeelight_colors_panel.hide()
    def source_row(self, layout, source, short_name=None):
        policy = next(item for item in SOURCES if item.id == source)
        frame = QFrame()
        frame.setObjectName("sourceRow")
        row = QHBoxLayout(frame)
        row.setContentsMargins(0, 6, 0, 6)
        row.setSpacing(12)
        name = short_name or policy.name.replace("（实验）", "").replace(
            " 任务状态", ""
        )
        mark = label()
        mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        mark.setPixmap(brand_icon(source).pixmap(28, 28))
        mark.setAccessibleName(name + " 图标")
        row.addWidget(mark)
        mark.setFixedSize(36, 36)
        copy = QVBoxLayout()
        copy.setSpacing(3)
        heading = QHBoxLayout()
        heading.setSpacing(6)
        title = label(name, role="preferenceLabel")
        title.setWordWrap(False)
        heading.addWidget(title)
        if source in WEB_URLS:
            link = self.button(heading, "", lambda s=source: self.open_web_app(s), variant="quiet", glyph="external")
            link.setObjectName("sourceLink")
            link.setFixedSize(32, 32)
            link.setIconSize(QSize(16, 16))
            link.setAccessibleName(name + " · 打开网页")
            link.setToolTip(name + " · 打开网页")
        heading.addStretch()
        copy.addLayout(heading)
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
        heading = QHBoxLayout()
        heading.setSpacing(6)
        title = label("接入应用", role="title")
        title.setMargin(0)
        title.setIndent(0)
        title.setWordWrap(False)
        heading.addWidget(title)
        self.source_help(heading)
        category.addLayout(heading)
        category.addStretch()
        self.source_category = CategoryTabs()
        self.source_category.setAccessibleName("选择接入应用")
        for name, key in (("网页应用", "web"), ("编程工具", "cli")):
            self.source_category.addItem(name, key)
        self.source_category.currentChanged.connect(self.source_category_changed)
        category.addWidget(self.source_category)
        outer.addLayout(category)
        sources = self.source_section(outer, "web")
        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(12)
        self.browser_install_button = self.button(actions, "安装浏览器扩展", self.open_browser_extension_guide, variant="primary")
        self.browser_install_hint = label("完成扩展安装与连接，再刷新 AI 网页即可接收新任务状态。", True)
        actions.addWidget(self.browser_install_hint, 1)
        sources.addLayout(actions)
        self.browser_message = label("", True)
        sources.addWidget(self.browser_message)
        self.browser_message.hide()
        for source, name in WEB_SOURCES.items():
            self.source_row(sources, source, name)

        cli = self.source_section(outer, "cli")
        sources = cli
        skill_actions = QHBoxLayout()
        skill_actions.setContentsMargins(0, 0, 0, 0)
        skill_actions.setSpacing(12)
        self.skill_copy_button = self.button(skill_actions, "复制安装指令", self.copy_coding_skill, variant="primary")
        self.skill_install_hint = label("将指令粘贴到编程工具安装 Skill，接入与维护都通过 Skill 完成；下方开关只控制是否提醒。", True)
        skill_actions.addWidget(self.skill_install_hint, 1)
        sources.addLayout(skill_actions)
        self.skill_notice = label("", True)
        self.skill_notice.hide()
        sources.addWidget(self.skill_notice)
        for source, name in {"codex": "Codex", **CLI_SOURCES}.items():
            sources = self.source_block(cli, source)
            self.source_row(sources, source, name)

        self.source_category.setCurrentIndex(self.source_category.findData("web"))
        self.source_category_changed(self.source_category.currentIndex())

    def source_help(self, layout):
        self.integration_help_texts = {
            "cli": "安装、诊断、修复、更新或移除接入，都在编程工具中使用 SeeEnd Skill。需要重载或信任 Hook 时，按 Skill 提示操作。",
            "web": "所有网页共用一个 Chrome 扩展。点击「安装浏览器扩展」完成安装与连接，然后刷新 AI 网页，即可接收新任务的提醒。",
        }
        self.integration_help = HelpButton(self.integration_help_texts["web"], "接入")
        layout.addWidget(self.integration_help)

    def preferences(self, layout):
        self.preference_help = {}
        self.preference_help_texts = {}
        channels = card(layout)
        channels.addWidget(label("提醒方式", role="sectionTitle"))
        self.notification_permission_text = "读取授权中…"
        notification_actions = self.preference_toggle(
            channels, "desktop_notifications", "系统通知",
            help_text="任务完成或等待输入时通知，独立于灯具连接与亮度。先允许系统通知，再开启开关；通知是否显示还取决于专注模式和系统设置。macOS 请使用打包应用。"
        )
        self.update_preference_help("desktop_notifications", self.notification_permission_text)
        self.notification_test_button = self.button(notification_actions, "测试通知", self.notification_action)
        self.notification_test_feedback = label("", role="preferenceHint")
        self.notification_test_feedback.hide()
        channels.addWidget(self.notification_test_feedback)
        separator(channels)
        self.build_wechat(channels)
        separator(channels)
        self.preference_toggle(channels, "desktop_flame_enabled", "桌面火焰",
                               help_text="独立显示任务状态，可与其他提醒同时开启。关闭主窗口后仍显示；双击返回最新任务，拖动调整位置，右键打开 SeeEnd。")
        self.checks["desktop_flame_enabled"].setToolTip("关闭主窗口后，仍在桌面显示任务状态。")
        separator(channels)
        device_actions = self.preference_toggle(
            channels, "hardware_notifications", "设备提醒",
            help_text="通过已连接的灯具或插座提醒，设备可在「我的设备」中添加和配置。\n\n开启后恢复已有设备连接，各设备按自己的设置显示任务状态。关闭后停止设备提醒并释放连接，设备配置保留；其他提醒继续工作。"
        )
        self.button(device_actions, "管理设备", lambda: self.navigate(1))

        light = card(layout)
        light.addWidget(label("灯光提醒时长", role="sectionTitle"))
        self.preference_toggle(
            light, "hold_until_activity", "完成后保持亮灯",
            help_text="开启后，完成提醒保持亮灯，检测到首次键鼠活动后再开始倒计时。等待输入提醒立即计时。"
        )
        separator(light)
        duration_section = QVBoxLayout()
        duration_section.setSpacing(5)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(12)
        title_group = QHBoxLayout()
        title_group.setContentsMargins(0, 0, 0, 0)
        title_group.setSpacing(6)
        self.duration_title = label("提醒持续时间", role="preferenceLabel")
        self.duration_title.setWordWrap(False)
        self.duration_title.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        title_group.addWidget(self.duration_title, 0, Qt.AlignmentFlag.AlignVCenter)
        self.preference_help_icon(title_group, "duration", "")
        row.addLayout(title_group)
        row.addStretch()
        self.duration_control = QFrame()
        self.duration_control.setObjectName("durationControl")
        self.duration_control.setFixedSize(166, 36)
        duration_row = QHBoxLayout(self.duration_control)
        duration_row.setContentsMargins(3, 3, 3, 3)
        duration_row.setSpacing(0)
        self.duration_decrease = self.button(duration_row, "", lambda: self.adjust_duration(-1), glyph="minus")
        self.duration_decrease.setObjectName("durationStep")
        self.duration_decrease.setFixedSize(30, 30)
        self.duration_decrease.setAccessibleName("减少提醒时长")
        self.duration_decrease.setToolTip("减少 1 秒")
        self.duration = QSpinBox()
        self.duration.setObjectName("durationValue")
        self.duration.setRange(3, 60)
        self.duration.setSuffix(" 秒")
        self.duration.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        self.duration.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.duration.setKeyboardTracking(False)
        self.duration.setAccessibleName("提示时长，3 至 60 秒")
        self.duration.setToolTip("完成或等待输入提醒结束后，回到当前任务状态。")
        duration_row.addWidget(self.duration, 1)
        self.duration_increase = self.button(duration_row, "", lambda: self.adjust_duration(1), glyph="plus")
        self.duration_increase.setObjectName("durationStep")
        self.duration_increase.setFixedSize(30, 30)
        self.duration_increase.setAccessibleName("增加提醒时长")
        self.duration_increase.setToolTip("增加 1 秒")
        self.duration.valueChanged.connect(self.update_duration_steps)
        self.duration.editingFinished.connect(
            lambda: self.setting("duration", self.duration.value())
        )
        row.addWidget(self.duration_control, 0, Qt.AlignmentFlag.AlignVCenter)
        duration_section.addLayout(row)
        self.save_feedback_row(duration_section, "duration")
        light.addLayout(duration_section)

    def preference_help_icon(self, layout, key, text):
        titles = {"desktop_notifications": "系统通知", "desktop_flame_enabled": "桌面火焰",
                  "wechat_notifications": "微信通知", "hardware_notifications": "设备提醒",
                  "hold_until_activity": "完成后保持亮灯", "duration": "提醒持续时间"}
        control = HelpButton(text, titles.get(key, "设置"))
        self.preference_help[key] = control
        self.preference_help_texts[key] = text
        layout.addWidget(control, 0, Qt.AlignmentFlag.AlignVCenter)

    def update_preference_help(self, key, *details):
        self.preference_help[key].set_help("\n\n".join(text for text in
            (self.channel_save_feedback(key), *details, self.preference_help_texts[key]) if text))

    def preference_toggle(self, layout, key, title, *, help_text=""):
        section = QVBoxLayout()
        section.setSpacing(5)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(12)
        title_label = label(title, role="preferenceLabel")
        title_label.setWordWrap(True)
        title_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        title_group = QHBoxLayout()
        title_group.setContentsMargins(0, 0, 0, 0)
        title_group.setSpacing(6)
        title_group.addWidget(title_label, 0, Qt.AlignmentFlag.AlignVCenter)
        if help_text:
            self.preference_help_icon(title_group, key, help_text)
        row.addLayout(title_group)
        actions_box = QWidget()
        actions_box.setObjectName("preferenceActions")
        actions_box.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        actions = QHBoxLayout(actions_box)
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(4)
        actions.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(actions_box, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addStretch()
        toggle = Toggle()
        toggle.setAccessibleName(title)
        toggle.toggled.connect(lambda value: self.setting(key, value))
        self.checks[key] = toggle
        row.addWidget(toggle, 0, Qt.AlignmentFlag.AlignVCenter)
        section.addLayout(row)
        if key not in self.home_channel_checks:
            self.save_feedback_row(section, key)
        layout.addLayout(section)
        return actions

    def activity(self, layout):
        activity = card(layout)
        layout.setStretch(0, 1)
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
        self.history_search.setPlaceholderText("搜索应用、任务状态或灯光结果…")
        self.history_search.setAccessibleName("搜索活动记录")
        self.history_search.setClearButtonEnabled(True)
        self.history_search.textChanged.connect(lambda _: self.render_history())
        activity.addWidget(self.history_search)
        self.history = QTableWidget(0, 6)
        self.history.setAccessibleName("最近任务事件")
        self.history.setHorizontalHeaderLabels(
            ("时间", "应用 / 来源", "任务状态", "灯光结果", "任务标识", "详情")
        )
        self.history.horizontalHeaderItem(3).setToolTip("此列显示灯光处理结果。系统和微信通知请查看详情中的「通知回执」。")
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
        self.history.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.history.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)
        self.history.setColumnWidth(5, 88)
        self.history.setColumnHidden(4, True)
        self.history.setMinimumHeight(240)
        self.history.itemSelectionChanged.connect(self.show_event_detail)
        self.history_stack = QStackedWidget()
        self.history_stack.addWidget(self.history)
        empty = QWidget()
        empty_layout = QVBoxLayout(empty)
        empty_layout.addStretch()
        self.history_empty = label(
            "还没有记录。接入应用后，新任务会自动出现在这里。", role="title"
        )
        self.history_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(self.history_empty)
        self.history_clear_button = self.button(empty_layout, "清除筛选", self.clear_history_filters, variant="quiet")
        empty_layout.setAlignment(self.history_clear_button, Qt.AlignmentFlag.AlignHCenter)
        empty_layout.addStretch()
        self.history_stack.addWidget(empty)
        self.history_stack.setMinimumHeight(300)
        activity.addWidget(self.history_stack, 1)
        self.history_count = label("", True)
        activity.addWidget(self.history_count)
        self.history_selection_hint = label("点击「查看」，了解灯光结果、通知回执与任务标识。", True)
        activity.addWidget(self.history_selection_hint)
        details = card(layout)
        heading = QHBoxLayout()
        heading.addWidget(label("记录详情", role="title"))
        heading.addStretch()
        self.return_task_button = self.button(heading, "返回任务", self.return_selected_task)
        self.copy_task_button = self.button(heading, "复制任务标识", self.copy_task_id, variant="quiet")
        self.button(heading, "关闭详情", self.close_event_detail, variant="quiet", glyph="close")
        details.addLayout(heading)
        self.event_detail_box = details.parentWidget()
        self.event_detail = label("")
        self.event_detail.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.event_detail.setProperty("literalText", True)
        self.event_detail.setTextFormat(Qt.TextFormat.RichText)
        self.event_detail.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        details.addWidget(self.event_detail)
        self.event_detail_box.hide()

    def testing(self, layout):
        language_box = card(layout, "界面语言 / Language")
        self.language_choice = QComboBox()
        self.language_choice.addItem("中文", "zh-CN")
        self.language_choice.addItem("English", "en")
        self.language_choice.setMaximumWidth(280)
        self.language_choice.setAccessibleName("界面语言 / Language")
        self.language_choice.currentIndexChanged.connect(lambda index: self.setting("language", self.language_choice.itemData(index)))
        language_box.addWidget(self.language_choice)
        self.save_feedback_row(language_box, "language")

        help_box = card(layout, "常见问题")
        self.help_disclosures = []

        def help_disclosure(title):
            item = Disclosure(title, help_box)
            item.button.setObjectName("faqQuestion")
            item.content.setContentsMargins(0, 4, 0, 4)
            self.help_disclosures.append(item)
            return item

        for title, content, action in (
            (
                "不连接设备也能使用吗？",
                "可以。无需灯具，也能通过屏幕火焰、菜单栏或系统通知查看任务状态。\n到「应用接入」完成接入后，新任务会自动触发提醒。灯效演示可预览不同任务状态的表现。",
                ("前往应用接入", 2),
            ),
            (
                "连接灯具需要重新接入吗？",
                "不需要。先在「总览」开启「设备提醒」，再到「我的设备」连接并试亮，已有的应用接入和偏好会继续使用。",
                ("前往我的设备", 1),
            ),
            (
                "灯没亮怎么办？",
                "先在「总览」开启「设备提醒」，再到「我的设备」检查设备是否在线、亮度是否高于 0%，然后试亮。\n设备确认表示指令已被接受，实际发光仍需观察灯具。",
                ("前往我的设备", 1),
            ),
            (
                "应用提醒已开启，为什么没有提醒？",
                "网页请确认扩展已连接，并刷新页面；编程工具请按 SeeEnd Skill 提示完成加载与授权。\n再发送一条新任务，到「活动记录」查看接收情况。已完成的历史任务不会补发提醒。",
                ("前往应用接入", 2),
            ),
            (
                "关闭窗口会停止提醒吗？",
                "支持后台运行时，关闭窗口仅隐藏界面，提醒会继续运行；从菜单栏、Dock 或托盘可重新打开。选择「退出 SeeEnd」才会停止本应用的服务。\n电脑休眠、浏览器关闭或页面被丢弃时，网页任务可能无法继续采集。",
                None,
            ),
            (
                "Yeelight 连接失败怎么办？",
                "在 Yeelight APP 中开启「局域网控制」，确认电脑与灯具在同一局域网，再到「我的设备」搜索或手动输入 IP。",
                ("前往我的设备", 1),
            ),
        ):
            item = help_disclosure(title)
            item.content.addWidget(label(content, readable=True))
            if action:
                button = self.button(item.content, action[0], lambda i=action[1]: self.navigate(i))
                item.content.setAlignment(button, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.privacy_disclosure = help_disclosure("隐私与数据保存")
        privacy = self.privacy_disclosure.content
        privacy.addWidget(label(
            "SeeEnd 不保存或转发提问与回答正文。任务状态、设备配对和提醒偏好保存在本机；开启微信通知后，仅向绑定微信发送来源、任务状态和时间。活动记录按任务汇总显示最近 50 项任务，原始事件显示最近 50 条事件；事件最多保留 7 天。", readable=True
        ))
        self.about_disclosure = help_disclosure("版本与支持信息")
        about = self.about_disclosure.content
        about.addWidget(label(f"SeeEnd  {__version__}", readable=True))
        about.addWidget(
            label(
                "支持 Yeelight 屏幕挂灯 Pro（YLTD003）、W3 彩色灯泡（YLDP005）、Beam 塔灯（YLFWD-0021）、Cube Lite 和米家智能插座 3 / 4（ZNCZ301KK / ZNCZ401KK），可同时连接多台设备。网页接入的兼容性以各版本实测范围为准。",
                readable=True,
            )
        )
        about.addWidget(
            label(
                "当前构建为本地预览版。正式发行的签名、公证和各平台兼容性验收尚未完成。",
                readable=True,
            )
        )
        reset = card(layout)
        actions = QHBoxLayout()
        actions.setSpacing(16)
        self.reset_button = self.button(actions, "重置 APP", self.reset_app, variant="danger")
        self.reset_button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        actions.setAlignment(self.reset_button, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.reset_hint = label("清除 SeeEnd 本机数据和偏好，需重新配置；已安装的外部 Hook、插件和浏览器扩展保留。", True)
        actions.addWidget(self.reset_hint, 1, Qt.AlignmentFlag.AlignVCenter)
        reset.addLayout(actions)
