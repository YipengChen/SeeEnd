"""Client identities and exact control labels; no conversation text matching."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Client:
    id: str
    name: str
    bundle_ids: tuple[str, ...]
    app_names: tuple[str, ...]


CLIENTS = (
    Client("doubao-desktop", "豆包", ("com.bot.pc.doubao",), ("Doubao", "豆包")),
    Client(
        "qianwen-desktop",
        "千问",
        ("cn.qwenwork.desktop.mac",),
        ("QwenWorkCN", "Qwen", "千问", "千问办公"),
    ),
    Client("yuanbao-desktop", "元宝", (), ("Yuanbao", "yuanbao", "元宝", "腾讯元宝")),
    # Current ChatGPT.app shares a bundle ID with Codex. The native reader
    # additionally requires the ordinary Chat composer's accessible label.
    Client("chatgpt-desktop", "ChatGPT", ("com.openai.chat", "com.openai.codex"), ()),
)
DESKTOP_SOURCES = {client.id: client.name for client in CLIENTS}
# Exact static labels checked in ChatGPT.app 26.928.20755. Never match the
# Work or Codex composer, or read editor values to infer the mode.
CHATGPT_COMPOSERS = frozenset(
    ("Ask ChatGPT", "询问 ChatGPT", "問 ChatGPT", "問問 ChatGPT")
)
CHATGPT_SEND = frozenset(("Send", "发送", "傳送"))
CHATGPT_STOP = frozenset(("Stop", "停止"))
CHATGPT_FINAL = frozenset(
    (
        "Good response",
        "Bad response",
        "Regenerate response",
        "回复优秀",
        "回复不佳",
        "重新生成回复",
        "回覆良好",
        "回覆欠佳",
        "回應良好",
        "回應欠佳",
        "重新產生回覆",
    )
)
STOP = frozenset(
    (
        "停止",
        "停止生成",
        "停止回答",
        "停止回复",
        "停止执行",
        "Stop",
        "Stop generating",
        "Stop streaming",
    )
)
SEND = frozenset(
    ("发送", "发送消息", "发送提示", "Send", "Send message", "Send prompt")
)
COPY = frozenset(
    (
        "复制",
        "复制回复",
        "复制文本",
        "复制 Markdown 格式",
        "Copy",
        "Copy response",
        "Copy as plain text",
        "Copy as markdown",
    )
)
FINAL = frozenset(
    (
        "赞",
        "踩",
        "点赞",
        "点踩",
        "喜欢",
        "不喜欢",
        "回复不错",
        "回复不好",
        "重新生成",
        "重新生成回复",
        "重新回答",
        "Like",
        "Dislike",
        "Good response",
        "Bad response",
        "Regenerate",
        "Regenerate response",
    )
)
ERROR = frozenset(("重试", "Retry", "Try again"))
