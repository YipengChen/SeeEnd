"""Select an existing macOS terminal tab/pane without typing or running commands."""

import subprocess
import sys

from aiflame.core.events import EventError
from aiflame.integrations.cli_tools.origin import processes

TERMINAL_SCRIPT = '''on run argv
    set targetTTY to item 1 of argv
    set mustMatchTitle to (count of argv) > 1
    if mustMatchTitle then
        set expectedTitle to item 2 of argv
        set expectedWindow to item 3 of argv
    end if
    set matchedTabs to {}
    set matchedWindows to {}
    tell application id "com.apple.Terminal"
        repeat with w in windows
            try
                set tabList to tabs of w
            on error errorText number errorNumber
                if errorNumber is -1728 then
                    set tabList to {}
                else
                    return "unreadable"
                end if
            end try
            repeat with t in tabList
                if tty of t is targetTTY then
                    set titleMatches to true
                    if mustMatchTitle then
                        considering case
                            set titleMatches to (custom title of t is expectedTitle) and (id of w as text is expectedWindow)
                        end considering
                    end if
                    if titleMatches then
                        set end of matchedTabs to t
                        set end of matchedWindows to w
                    end if
                end if
            end repeat
        end repeat
        if (count of matchedTabs) > 1 then return "ambiguous"
        if (count of matchedTabs) is 0 then
            if mustMatchTitle then return "changed"
            return "missing"
        end if
        set targetTab to item 1 of matchedTabs
        set targetWindow to item 1 of matchedWindows
        set selected of targetTab to true
        set miniaturized of targetWindow to false
        set index of targetWindow to 1
        activate
        return "opened"
    end tell
end run'''

ITERM_SCRIPT = '''on run argv
    set targetGUID to item 1 of argv
    set targetTTY to item 2 of argv
    set mustMatchTitle to (count of argv) > 2
    if mustMatchTitle then
        set expectedTitle to item 3 of argv
        set expectedWindow to item 4 of argv
    end if
    set matchedSessions to {}
    set matchedTabs to {}
    set matchedWindows to {}
    tell application id "com.googlecode.iterm2"
        repeat with w in windows
            repeat with t in tabs of w
                repeat with s in sessions of t
                    if (unique ID of s is targetGUID) and (tty of s is targetTTY) then
                        set titleMatches to true
                        if mustMatchTitle then
                            considering case
                                set rawTitle to variable s named "terminalWindowName"
                                set titleMatches to (rawTitle is expectedTitle) and (id of w as text is expectedWindow)
                            end considering
                        end if
                        if titleMatches then
                            set end of matchedSessions to s
                            set end of matchedTabs to t
                            set end of matchedWindows to w
                        end if
                    end if
                end repeat
            end repeat
        end repeat
        if (count of matchedSessions) > 1 then return "ambiguous"
        if (count of matchedSessions) is 0 then
            if mustMatchTitle then return "changed"
            return "missing"
        end if
        select (item 1 of matchedSessions)
        select (item 1 of matchedTabs)
        set targetWindow to item 1 of matchedWindows
        set miniaturized of targetWindow to false
        select targetWindow
        activate
        return "opened"
    end tell
end run'''


def activate(destination, is_current=None, *, expected_title=None, window_id=None):
    if sys.platform != "darwin":
        raise EventError("返回 Terminal 和 iTerm2 目前仅支持 macOS。", 409)
    def check_process():
        item = processes().get(destination["pid"])
        if not item or item["started"] != destination["started"] or "/dev/" + item["tty"] != destination["tty"]:
            raise EventError("原终端任务已结束或位置已失效，请在编程工具中恢复会话。", 409)

    def execute(script, args=(), timeout=60):
        result = subprocess.run(["/usr/bin/osascript", "-e", script, *args],
                                capture_output=True, text=True, timeout=timeout)
        if result.returncode:
            if "-1743" in result.stderr:
                raise EventError("请在系统设置 → 隐私与安全性 → 自动化中，允许 SeeEnd 控制此终端，然后重试。", 409)
            raise EventError("无法切换原终端。请确认终端仍在运行并允许 SeeEnd 自动化控制。", 409)
        return result

    try:
        check_process()
        app = "com.apple.Terminal" if destination["app"] == "Terminal" else "com.googlecode.iterm2"
        # Request automation permission without changing focus. The user may
        # keep the dialog open while a task exits or changes conversation.
        execute('tell application id "' + app + '" to get count of windows')
        check_process()
        if is_current is not None and not is_current():
            if expected_title is not None:
                raise EventError("原终端会话或标题已变化，请重新定位。", 409)
            raise EventError("原终端已切换到其他会话，请在编程工具中恢复原会话。", 409)
        script = TERMINAL_SCRIPT if destination["app"] == "Terminal" else ITERM_SCRIPT
        args = [destination["tty"]] if destination["app"] == "Terminal" else [destination["session_guid"], destination["tty"]]
        if expected_title is not None:
            args.extend([expected_title, str(window_id)])
        result = execute(script, args, timeout=10)
    except subprocess.TimeoutExpired as error:
        raise EventError("返回终端超时。请处理系统授权弹窗后重试。", 409) from error
    except (OSError, subprocess.SubprocessError) as error:
        raise EventError("无法读取原终端位置，请重新打开 SeeEnd 后重试。", 409) from error
    outcome = result.stdout.strip()
    if outcome == "ambiguous":
        raise EventError("发现多个对应终端位置，无法唯一定位。", 409)
    if outcome == "changed":
        raise EventError("原终端会话或标题已变化，请重新定位。", 409)
    if outcome == "unreadable":
        raise EventError("终端信息读取不完整，无法确认唯一位置。", 409)
    if outcome != "opened":
        raise EventError("原终端标签页或分屏已关闭，请在编程工具中恢复会话。", 409)
    return "opened"
