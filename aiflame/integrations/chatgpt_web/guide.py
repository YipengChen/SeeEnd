"""Browser extension installation instructions and protocol diagnostic helpers."""
from html import escape

STORE_URL = "https://chromewebstore.google.com/detail/miokmdeomlchjpihkcnommpnheagoimc"
STEPS = (
    ("从 Chrome 商店安装", "点击「在 Chrome 安装扩展」打开商店，再点击「添加至 Chrome」并确认安装 SeeEnd。安装完成后回到 App 继续。", None),
    ("连接并开始使用", "选择常用的 AI 网站，允许扩展连接 SeeEnd，再开启该网站的提醒。刷新 AI 网页后，即可开始使用。", None),
)
CHECKS = (
    "完成标志：Chrome 已安装并启用 SeeEnd 扩展。仅打开商店页面不代表安装完成。",
    "允许连接并开启提醒后，点击「完成设置」。新任务的进度会自动显示在 SeeEnd 中。",
)
UPGRADE = "商店版扩展由 Chrome 自动更新。升级或移动 SeeEnd App 后，在第 2 步重新允许连接，再刷新 AI 网页。"
TROUBLESHOOTING = (
    ("无法打开商店或安装扩展", "复制商店链接，在 Chrome 地址栏粘贴打开。请使用 Chrome；若安装被公司策略阻止，请联系管理员。"),
    ("怎样确认扩展已安装", "商店页面显示「从 Chrome 中移除」，或 Chrome 的扩展菜单中出现已启用的 SeeEnd，表示安装完成。仅打开商店页面不会安装扩展。"),
    ("网页还没有准备好", "在 Chrome 打开支持的 AI 对话网页并刷新。点击工具栏的扩展按钮，打开 SeeEnd，再点击「检查当前页面」。"),
    ("扩展无法连接 SeeEnd", "保持桌面 App 运行，在第 2 步重新允许连接，再刷新 AI 网页；仍失败时查看扩展的「详细诊断」。"),
    ("已连接，但没有完成提醒", "确认对应网站的提醒开关已开启，再发送一条普通文字提问并等待完整回答。已有对话中的新提问也可追踪；已完成的历史任务不会补发提醒。图片、语音或研究模式可能暂不支持。"),
)
HELP_FOR_STEP = ((0, 1), (2, 3, 4))


def offline_html():
    parts = ['<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>SeeEnd 浏览器扩展安装</title><style>body{font:17px/1.8 system-ui,sans-serif;max-width:800px;margin:40px auto;padding:0 24px;color:#292824;background:#f6f5f1}h1,h2{line-height:1.4}section{margin:36px 0}.instructions{white-space:pre-line}.check{font-weight:600}details{margin:18px 0}a{color:#46633b;overflow-wrap:anywhere}</style><h1>SeeEnd 浏览器扩展安装</h1><p>两个阶段：从 Chrome 商店安装 → 连接并开始使用。</p>']
    for index, (title, text, _) in enumerate(STEPS):
        parts.append('<section><h2>'+str(index+1)+'. '+escape(title)+'</h2><p class="instructions">'+escape(text)+'</p>')
        if index == 0:
            parts.append('<p><a href="'+escape(STORE_URL, quote=True)+'">在 Chrome 商店安装 SeeEnd</a></p><p>在 SeeEnd 点击「安装浏览器扩展」打开引导，再点击第 1 步的「在 Chrome 安装扩展」打开商店。安装后回到引导，勾选「我已在 Chrome 安装并启用 SeeEnd 扩展」，再点击「继续」。已安装的用户可直接确认；无需灯具。</p>')
        else:
            parts.append('<p>在 SeeEnd 选择网站，允许连接并开启提醒，然后点击「完成设置」。在该网站登录并刷新网页，即可接收新任务的提醒。也可点击「在 Chrome 打开」直接前往网站；无需灯具。</p>')
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
        if item.get("status") in ("cancelled", "timeout", "unknown", "incomplete", "needs_input"):
            return "failed"
    return "waiting"


def verification_text(snapshot, baseline, selected_source=None):
    from aiflame.integrations.chatgpt_web.host import WEB_SOURCES
    choices = [selected_source] if selected_source else list(WEB_SOURCES)
    states = {source: verification_state(snapshot, baseline, source) for source in choices}
    for source in choices:
        state = states[source]
        if state == "verified":
            text = WEB_SOURCES[source] + "：网页接入已验证。已收到这条新任务的开始和回答完成。"
            return text
        if state == "working":
            return WEB_SOURCES[source] + "：已收到任务开始，正在等待回答完成。"
        if state == "failed":
            return WEB_SOURCES[source] + "：这条任务没有正常完成，尚未验证。请重新发送一条普通文字提问并等待完整回答。"
    if all(state == "disabled" for state in states.values()):
        return ((WEB_SOURCES[selected_source] + " 的提醒开关已关闭。") if selected_source else "所有网页平台的提醒开关都已关闭。") + "请回到「应用接入」开启它，再发送新提问。"
    return "尚未收到这次验证的新任务。请刷新 AI 网页、检查扩展，再发一条普通文字提问。"
