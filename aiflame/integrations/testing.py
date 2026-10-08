"""Adapter self-tests, clearly distinguished from a real AI task."""

import json
import io
import struct
import threading
import time
import uuid
from aiflame.core.events import EventError, make_event
from aiflame.core.process import run
from aiflame.integrations.local_api.client import send
from aiflame.integrations.chatgpt_web.host import (
    WEB_SOURCES,
    sanitize,
    read_message,
    extension_id,
)
from aiflame.integrations.codex.events import from_hook
from aiflame.integrations.registry import SOURCES
from aiflame.integrations.cli_tools.providers import CLI_SOURCES
from aiflame.integrations.cli_tools.lifecycle import from_hook as from_cli_hook

LABELS = {source.id: source.name for source in SOURCES if source.id != "demo"}


class TriggerTests:
    def __init__(self, context):
        self.context = context
        self.job = None
        self.thread = None
        self.guard = threading.Lock()

    def start(self, source):
        if not isinstance(source, str) or source not in LABELS:
            raise EventError("测试来源不受支持")
        c = self.context
        if c.devices.busy():
            raise EventError("请等待设备维护完成", 409)
        with c.lock:
            if c.engine.muted():
                raise EventError("请先恢复触发并关闭静默", 409)
            if not c.engine.settings["source_enabled"][source]:
                raise EventError("请先启用该来源", 409)
        with self.guard:
            if self.job and self.job["state"] == "running":
                raise EventError("请等待当前测试结束", 409)
            self.job = {
                "id": str(uuid.uuid4()),
                "source": source,
                "label": LABELS[source],
                "state": "running",
                "events": [],
                "message": "正在发送测试开始事件",
                "simulated": True,
            }
            self.thread = threading.Thread(
                target=self._run, args=(source,), daemon=True
            )
            self.thread.start()
            return dict(self.job)

    def _run(self, source):
        c = self.context
        task = str(uuid.uuid4())
        try:
            for seq, state in ((1, "working"), (2, "done")):
                if source == "codex":
                    payload = {
                        "hook_event_name": "UserPromptSubmit" if seq == 1 else "Stop",
                        "session_id": "aiflame-self-test",
                        "turn_id": task,
                    }
                    event = from_hook(payload)
                    self._mark(event)
                    response = run(
                        "--codex-hook", timeout=10, input=json.dumps(payload)
                    )
                    if response.returncode:
                        raise RuntimeError("Hook 进程未正常退出")
                elif source in CLI_SOURCES:
                    payload = {
                        "hook_event_name": "UserPromptSubmit" if seq == 1 else "Stop",
                        "session_id": "aiflame-self-test:" + task,
                        "turn_id": task,
                    }
                    event = from_cli_hook(source, payload)
                    self._mark(event)
                    response = run(
                        "--cli-hook", source, "--port", str(c.port),
                        timeout=10, input=json.dumps(payload),
                    )
                    if response.returncode:
                        raise RuntimeError("CLI Hook 进程未正常退出")
                else:
                    event = make_event(
                        source, task, state, seq, "completed" if seq == 2 else None
                    )
                    self._mark(event)
                    if source == "manual":
                        with c.lock:
                            c.engine.accept(event, source)
                    else:
                        if source in WEB_SOURCES:
                            encoded = json.dumps(sanitize(event)).encode()
                            response = run(
                                "--browser-host",
                                "chrome-extension://" + extension_id() + "/",
                                timeout=10,
                                binary=True,
                                input=struct.pack("=I", len(encoded)) + encoded,
                            )
                            result = read_message(io.BytesIO(response.stdout))
                            if (
                                response.returncode
                                or not result
                                or not result.get("ok")
                            ):
                                raise RuntimeError("网页桥接进程失败")
                        else:
                            send(event, c.config["tokens"][source], c.port)
                with c.lock:
                    row = c.engine.db.execute(
                        "SELECT status FROM events WHERE id=?", (event["event_id"],)
                    ).fetchone()
                if not row:
                    raise RuntimeError("Hub 未收到适配器事件，请检查接入与运行目录")
                with self.guard:
                    self.job["events"].append(event["event_id"])
                    self.job["message"] = (
                        "开始事件已接收，正在等待完成阶段"
                        if seq == 1
                        else "完成事件已接收"
                    )
                if seq == 1 and c.stopping.wait(2):
                    with self.guard:
                        self.job.update(
                            state="cancelled", message="App 已退出，测试停止"
                        )
                    return
            with self.guard:
                self.job.update(
                    state="succeeded",
                    message="模拟通路测试完成；APP 呈现与设备回执见下方。此结果不计入真实验证，请在 AI 应用发起新任务。",
                )
        except Exception:
            with self.guard:
                self.job.update(
                    state="failed", message="适配器测试失败，请检查 Hub 与来源配置"
                )

    def _mark(self, event):
        with self.context.lock:
            db = self.context.engine.db
            db.execute(
                "DELETE FROM self_tests WHERE created < ?", (time.time() - 604800,)
            )
            db.execute(
                "INSERT OR REPLACE INTO self_tests VALUES (?,?)",
                (event["event_id"], time.time()),
            )
            db.commit()

    def busy(self):
        with self.guard:
            return bool(self.job and self.job["state"] == "running")

    def status(self):
        with self.guard:
            job = {**self.job, "events": list(self.job["events"])} if self.job else None
        if job:
            with self.context.lock:
                job["delivery"] = [
                    dict(row)
                    for event in job["events"]
                    if (
                        row := self.context.engine.db.execute(
                            "SELECT id,state,status FROM events WHERE id=?", (event,)
                        ).fetchone()
                    )
                ]
        return {"job": job}
