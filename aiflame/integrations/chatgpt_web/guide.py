"""Shared four-stage installation sequence and real-task success criteria."""
import base64
from html import escape
from aiflame.core.paths import PACKAGE

IMAGES = PACKAGE / "assets/extension-guide"
STEPS = (
    ("准备安装文件", "SeeEnd 会准备 Chrome 要读取的文件夹。安装后请保留这个文件夹。", None),
    ("打开 Chrome 加载窗口", "在 Chrome 地址栏打开 chrome://extensions/，开启「开发者模式」，再点击「加载未打包的扩展程序」。", "chrome-extensions.jpg"),
    ("选择安装文件夹", "在刚打开的文件夹选择窗口中，粘贴下面的路径，进入 BrowserExtension 文件夹并点击「选择」。", "chrome-folder.jpg"),
    ("连接并验证网站", "先选择要验证的网站，允许扩展连接 SeeEnd，再开启该网站的提醒。最后在 Chrome 发一条新提问。", None),
)
CHECKS = (
    "完成标志：安装文件夹路径已显示。",
    "完成标志：Chrome 的文件夹选择窗口已打开。",
    "完成标志：Chrome 已显示 SeeEnd 卡片，卡片上的开关已开启。",
    "完成标志：SeeEnd 收到新任务的开始和回答完成，显示「网页接入已验证」。",
)
# Authentic source pixels; native and offline viewers use the same crop.
SHOTS = {
    1: (
        ("chrome-extensions.jpg", (2750, 10, 270, 110), "1 · 开启右上角的「开发者模式」"),
        ("chrome-extensions.jpg", (30, 120, 355, 95), "2 · 点击左上角的「加载未打包的扩展程序」"),
    ),
    2: (
        ("chrome-folder.jpg", (775, 70, 505, 100), "进入 BrowserExtension 文件夹"),
        ("chrome-folder.jpg", (1530, 790, 210, 85), "点击窗口右下角的「选择」"),
        ("chrome-extensions.jpg", (800, 405, 810, 420), "加载成功后，Chrome 会出现这张 SeeEnd 卡片"),
    ),
}
FOCUS = {}
CAPTIONS = ("", "Chrome 真实截图（中文界面）；按钮名称随 Chrome 语言变化。", "macOS 真实截图；Windows 使用「选择文件夹」。", "")
EXTRA_IMAGES = {}
PATH_HELP = {
    "darwin": "切换到文件夹选择窗口，按 ⌘⇧G，粘贴路径并回车，再点击「选择」。",
    "win32": "切换到文件夹选择窗口，点击顶部地址栏，粘贴路径并回车，再点击「选择文件夹」。",
    "linux": "切换到文件夹选择窗口，按 Ctrl+L，粘贴路径并回车，再选择该文件夹。",
}
UPGRADE = "升级 SeeEnd 后：重新准备安装文件，到 Chrome 的扩展管理页重新加载 SeeEnd，再在第 4 步重新允许连接并验证新任务。"
TROUBLESHOOTING = (
    ("找不到加载按钮", "确认地址是 chrome://extensions/，并开启「开发者模式」。部分版本叫「加载已解压的扩展程序」。若开关被公司策略锁定，请联系管理员。"),
    ("Chrome 无法读取安装文件", "返回第 1 步重新准备；在 Chrome 选择 BrowserExtension 文件夹本身，不要选择 SeeEnd.app 或单个文件。"),
    ("怎样确认扩展已安装", "准备文件不会自动安装扩展。Chrome 显示 SeeEnd 卡片且开关开启后，才算加载成功。"),
    ("网页还没有准备好", "在 Chrome 打开支持的 AI 对话网页并刷新。点击工具栏的扩展按钮，打开 SeeEnd，再点击「检查当前页面」。"),
    ("扩展无法连接 SeeEnd", "保持桌面 App 运行，在第 4 步重新允许连接，再刷新 AI 网页；仍失败时查看扩展的「详细诊断」。"),
    ("已连接，但没有完成提醒", "确认平台开关开启、提醒未暂停且不在静默时段。重新发一条普通文字提问，等待完整回答；已有回答、模拟任务、图片或研究模式不能用于验证。"),
)
HELP_FOR_STEP = ((1, 2), (0,), (1, 2), (3, 4, 5))


def offline_html():
    parts = ['<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>SeeEnd 浏览器扩展安装</title><style>body{font:17px/1.8 system-ui,sans-serif;max-width:800px;margin:40px auto;padding:0 24px;color:#292824;background:#f6f5f1}h1,h2{line-height:1.4}section{margin:36px 0}figure{margin:18px 0}.crop{position:relative;overflow:hidden;background:white}.crop img{position:absolute;max-width:none;height:auto}figcaption{font-size:14px;color:#59564e}.instructions{white-space:pre-line}.check{font-weight:600}details{margin:18px 0}</style><h1>SeeEnd 浏览器扩展安装</h1><p>四个阶段：准备文件 → 打开加载窗口 → 选择文件夹 → 连接并验证网站。</p>']
    dimensions = {"chrome-extensions.jpg": (3024, 1654), "chrome-folder.jpg": (1760, 896)}
    for index, (title, text, _) in enumerate(STEPS):
        parts.append('<section><h2>'+str(index+1)+'. '+escape(title)+'</h2><p class="instructions">'+escape(text)+'</p>')
        if index == 0:
            parts.append('<p>在 SeeEnd 点击「准备安装文件」，等待安装文件夹路径显示。无需另行下载或解压。</p>')
        if index == 2:
            parts.append('<p>先在 SeeEnd 复制完整路径。macOS：按 ⌘⇧G 后粘贴并回车。Windows：点击文件夹窗口地址栏，粘贴并回车。</p>')
        if index == 3:
            parts.append('<p>在 SeeEnd 选择网站，依次完成允许连接、开启提醒和「在 Chrome 打开」。在该网站登录并刷新网页，打开 SeeEnd 扩展，点击「检查当前页面」。检查通过后发送新提问，等回答完成，再回 SeeEnd 查看验证结果。</p><p>页面检查通过只代表能连接。真实任务验证需要一条新的普通文字提问；无需灯具。</p>')
        for filename, crop, caption in SHOTS.get(index, ()):
            data = base64.b64encode((IMAGES / filename).read_bytes()).decode()
            x, y, w, h = crop
            full_w, _ = dimensions[filename]
            parts.append(f'<figure><div class="crop" style="width:min(100%,{w/2:g}px);aspect-ratio:{w}/{h}"><img alt="{escape(caption)}：真实截图局部" style="width:{full_w/w*100:g}%;left:{-x/w*100:g}%;top:{-y/h*100:g}%" src="data:image/jpeg;base64,{data}"></div><figcaption>{escape(caption)}</figcaption></figure>')
        parts.append('<p class="check">'+escape(CHECKS[index])+'</p></section>')
    parts.append('<h2>遇到问题</h2>')
    for question, answer in TROUBLESHOOTING + (("常规升级", UPGRADE),):
        parts.append('<details><summary>'+escape(question)+'</summary><p>'+escape(answer)+'</p></details>')
    return ''.join(parts)+'</html>'


def verification_state(snapshot, baseline, selected_source):
    """A switch/configuration alone cannot prove a new real task."""
    sources = {row["id"]: row for row in snapshot.get("sources", [])}
    if not sources.get(selected_source, {}).get("enabled"):
        return "disabled"
    item = snapshot.get("verification", {}).get(selected_source, {})
    if item.get("started_at") and item["started_at"] > baseline.get(selected_source, 0):
        if item.get("status") in ("verified", "working"):
            return item["status"]
        if item.get("status") in ("cancelled", "timeout", "unknown", "error", "incomplete", "needs_input"):
            return "failed"
    if snapshot.get("settings", {}).get("paused") or snapshot.get("display", {}).get("key") == "muted":
        return "muted"
    return "waiting"


def verification_text(snapshot, baseline, selected_source=None):
    from aiflame.integrations.chatgpt_web.host import WEB_SOURCES
    choices = [selected_source] if selected_source else list(WEB_SOURCES)
    states = {source: verification_state(snapshot, baseline, source) for source in choices}
    for source in choices:
        state = states[source]
        if state == "verified":
            text = WEB_SOURCES[source] + "：网页接入已验证。已收到这条新任务的开始和回答完成。"
            if snapshot.get("settings", {}).get("paused") or snapshot.get("display", {}).get("key") == "muted":
                text += " 当前提醒已暂停或静默；恢复后再发一条新任务查看提醒。"
            return text
        if state == "working":
            return WEB_SOURCES[source] + "：已收到任务开始，正在等待回答完成。"
        if state == "failed":
            return WEB_SOURCES[source] + "：这条任务没有正常完成，尚未验证。请重新发送一条普通文字提问并等待完整回答。"
    if all(state == "disabled" for state in states.values()):
        return ((WEB_SOURCES[selected_source] + " 的提醒开关已关闭。") if selected_source else "所有网页平台的提醒开关都已关闭。") + "请回到「应用接入」开启它，再发送新提问。"
    if "muted" in states.values():
        return "提醒当前已暂停或静默。请先恢复提醒，再发送一条新提问。"
    return "尚未收到这次验证的新任务。请刷新 AI 网页、检查扩展，再发一条普通文字提问。"
