"""Native Qt Widgets shell and tray; no embedded browser process."""

import json
import sys
from aiflame.core.paths import PACKAGE, dashboard_token
from aiflame import __version__


def existing_hub():
    from aiflame.core.management_auth import SignedOpener
    opener = SignedOpener("http://127.0.0.1:8765", dashboard_token)
    try:
        with opener.open("http://127.0.0.1:8765/api/session", timeout=1):
            pass
        with opener.open("http://127.0.0.1:8765/api/status", timeout=1) as response:
            data = json.load(response)
        return data.get("app_version") == __version__ and data.get("app_name") == "SeeEnd"
    except (OSError, ValueError):
        return None


def main():
    from aiflame.desktop.localization import install_arrow_key_filter, t
    from PySide6.QtCore import QEvent
    from PySide6.QtWidgets import (
        QApplication,
        QMessageBox,
    )
    from aiflame.desktop.instance import DesktopInstance

    class FlameApplication(QApplication):
        hub = None
        prepare_quit = None

        def event(self, event):
            if event.type() == QEvent.Type.Quit:
                if self.hub and self.hub.devices.busy():
                    QMessageBox.information(
                        None, t("设备维护中"), t("设备连接操作正在进行，请等待完成后退出。")
                    )
                    event.ignore()
                    return True
                if self.prepare_quit:
                    self.prepare_quit()
            return super().event(event)

    app = FlameApplication(sys.argv)
    from aiflame.desktop.macos_accessibility import install_selection_guard
    install_selection_guard()
    install_arrow_key_filter()
    app.setApplicationName("SeeEnd")
    app.setOrganizationName("SeeEnd")
    instance = DesktopInstance(app)
    try:
        if not instance.start():
            return 0
        return run_primary(app, instance)
    except RuntimeError as error:
        QMessageBox.critical(None, t("无法启动 SeeEnd"), t(str(error)))
        return 1
    finally:
        instance.close()


def run_primary(app, instance):
    from contextlib import ExitStack
    # Always unwind partial startup; ExitStack also continues cleanup when one
    # resource raises. close() is idempotent after aboutToQuit has run it.
    with ExitStack() as resources:
        return _run_primary(app, instance, resources)


def _run_primary(app, instance, resources):
    from aiflame.desktop.localization import QAction, QSystemTrayIcon, set_language, t
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QMenu, QMessageBox
    from aiflame.desktop.widgets import Dashboard
    from aiflame.desktop.theme import TOOLTIP_STYLE
    from aiflame.desktop.tray import TrayIndicator
    from aiflame.desktop.window import DesktopWindow, WindowRestorer
    from aiflame.hub.server import Hub

    icon = QIcon(str(PACKAGE / "assets/icon.svg"))
    app.setWindowIcon(icon)
    app.setStyle("Fusion")
    app.setStyleSheet(TOOLTIP_STYLE)
    hub = None
    if not existing_hub():
        try:
            hub = Hub()
            resources.callback(hub.stop)
            hub.start()
        except OSError:
            QMessageBox.critical(
                None, t("无法启动"), t("本地端口 8765 被旧版或另一份 Hub 占用，请退出该 Hub后重新打开 SeeEnd。")
            )
            return 1

    app.hub = hub
    if hub:
        set_language(hub.engine.settings.get("language", "zh-CN"))

    window = DesktopWindow(hub)
    resources.callback(window.deleteLater)
    window.setWindowTitle("SeeEnd")
    window.resize(1240, 900)
    window.setMinimumSize(920, 680)
    dashboard = Dashboard(window)
    resources.callback(dashboard.shutdown)
    window.setCentralWidget(dashboard)
    activate = None
    if sys.platform == "darwin":
        from aiflame.desktop.macos_shell import MacTray, activate_application
        tray = MacTray(icon, app)
        resources.callback(tray.shutdown)
        activate = activate_application
    else:
        tray = QSystemTrayIcon(icon, app)
        resources.callback(tray.hide)
    from aiflame.desktop.notifications import NotificationConsumer, SystemNotifications
    notifications = NotificationConsumer(dashboard, SystemNotifications(tray), app)
    resources.callback(notifications.shutdown)
    from aiflame.desktop.sound_consumer import SoundConsumer
    sounds = SoundConsumer(dashboard, parent=app)
    resources.callback(sounds.shutdown)
    restorer = WindowRestorer(app, window, activate)
    if hasattr(dashboard, "return_failed"):
        dashboard.return_failed.connect(restorer.request)
    resources.callback(restorer.close)
    instance.activation_requested.connect(restorer.request)
    tray.setToolTip("SeeEnd · 任务提醒助手")
    menu = QMenu()
    status_action = QAction("灯光 · 正在连接", menu)
    status_action.setEnabled(False)
    indicator = TrayIndicator(tray, dashboard, status_action, app)
    resources.callback(indicator.shutdown)
    from aiflame.desktop.floating_flame import DesktopFlame
    desktop_flame = DesktopFlame()
    restorer.activation_guard = desktop_flame.pointer_interaction_active
    resources.callback(desktop_flame.shutdown)
    indicator.state_changed.connect(desktop_flame.update_status)
    dashboard.state_changed.connect(desktop_flame.update_status)
    desktop_flame.open_requested.connect(restorer.request)
    desktop_flame.return_requested.connect(dashboard.return_task)
    desktop_flame.menu_visible_changed.connect(restorer.set_activation_suppressed)
    desktop_flame.disable_requested.connect(
        lambda: dashboard.act("/api/settings", {"desktop_flame_enabled": False},
                              failed=restorer.request)
    )
    tray_available = tray.isSystemTrayAvailable()

    def sync_background(_visible=None):
        available = tray_available or desktop_flame.isVisible()
        window.background_available = available
        app.setQuitOnLastWindowClosed(not available)
        if not available and window.isHidden() and not window.quitting:
            restorer.request()

    desktop_flame.visibility_changed.connect(sync_background)

    def show():
        restorer.request()

    def prepare_quit():
        # Dock quit also reaches QEvent.Quit, before Qt closes any windows.
        # Stop background restoration and the flame's preference-only close path.
        window.quitting = True
        restorer.close()
        desktop_flame.shutdown()

    app.prepare_quit = prepare_quit
    resources.callback(setattr, app, "prepare_quit", None)

    def quit_app():
        if hub and hub.devices.busy():
            show()
            QMessageBox.information(window, t("设备维护中"), t("请等待设备连接操作结束后退出。"))
            return
        window.quitting = True
        app.quit()

    show_action = QAction("打开 SeeEnd", menu)
    show_action.triggered.connect(show)
    exit_action = QAction("退出 SeeEnd", menu)
    exit_action.setMenuRole(QAction.MenuRole.QuitRole)
    exit_action.triggered.connect(quit_app)
    help_action = QAction("设置与帮助", menu)

    def open_help():
        dashboard.navigate(5)
        show()

    help_action.triggered.connect(open_help)
    license_action = QAction("许可与第三方组件 / Licenses", menu)

    def open_licenses():
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        from aiflame.core.paths import PROJECT
        QMessageBox.information(window, "SeeEnd · Licenses",
            "SeeEnd © 2026 Yipeng Chen · 专有许可 / Proprietary license\n\n"
            "使用 Qt / PySide6 LGPL 动态库；允许按 LGPL 替换库和调试修改。\n"
            "Uses LGPL Qt/PySide libraries; library replacement and debugging are permitted.\n\n"
            "完整许可和替换说明位于软件资源目录。\n"
            "Full licenses and replacement instructions are in the app resources.")
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(PROJECT)))

    license_action.triggered.connect(open_licenses)

    menu.aboutToShow.connect(indicator.poll)
    menu.addAction(status_action)
    menu.addSeparator()
    menu.addAction(show_action)
    menu.addSeparator()
    menu.addAction(help_action)
    menu.addAction(license_action)
    menu.addAction(exit_action)
    tray.setContextMenu(menu)
    if sys.platform != "darwin":
        tray.activated.connect(
            lambda reason: (
                show() if reason == QSystemTrayIcon.ActivationReason.Trigger else None
            )
        )
    if tray_available:
        tray.show()
    sync_background()
    indicator.start()
    menu_bar = window.menuBar().addMenu("SeeEnd")
    menu_bar.addAction(show_action)
    menu_bar.addAction(help_action)
    menu_bar.addAction(license_action)
    menu_bar.addAction(exit_action)

    app.aboutToQuit.connect(resources.close)
    window.showMaximized()
    restorer.request()
    return app.exec()
