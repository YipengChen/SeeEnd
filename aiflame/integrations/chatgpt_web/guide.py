"""Browser extension installation instructions and protocol diagnostic helpers."""
from html import escape

STORE_URL = "https://chromewebstore.google.com/detail/miokmdeomlchjpihkcnommpnheagoimc"
STEPS = (
    ("安装扩展", "在 Chrome 应用商店安装 SeeEnd 扩展，完成后回到这里。", None),
    ("允许连接", "允许浏览器扩展与 SeeEnd 通信。", None),
)


def offline_html():
    parts = ['<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>SeeEnd 浏览器扩展安装</title><style>body{font:17px/1.8 system-ui,sans-serif;max-width:680px;margin:40px auto;padding:0 24px;color:#292824;background:#f6f5f1}h1,h2{line-height:1.4}section{margin:36px 0}a{color:#46633b;overflow-wrap:anywhere}</style><h1>SeeEnd 浏览器扩展安装</h1>']
    for index, (title, text, _) in enumerate(STEPS):
        parts.append('<section><h2>'+str(index+1)+'. '+escape(title)+'</h2><p>'+escape(text)+'</p>')
        if index == 0:
            parts.append('<p><a href="'+escape(STORE_URL, quote=True)+'">前往应用商店</a></p>')
        else:
            parts.append('<p>在 SeeEnd 的「安装浏览器扩展」窗口点击「允许连接」。</p>')
        parts.append('</section>')
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
