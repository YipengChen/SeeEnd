"""Manual light tests and explicitly simulated demonstration tasks."""

import uuid
import threading
from aiflame.core.events import EventError, make_event


def test(engine, body):
    state = body.get("state")
    if state not in ("idle", "working", "done", "error"):
        raise EventError("测试状态不受支持")
    task = body.get("task_id") or str(uuid.uuid4())
    event = make_event("manual", task, state, body.get("seq", 1), body.get("reason"))
    response, code = engine.accept(event, "manual")
    return {**response, "task_id": task}, code


class DemoTasks:
    """Explicit simulated tasks cancelled by emergency stop or Hub shutdown."""

    def __init__(self, context):
        self.context = context
        self.generation = 0

    def cancel(self):
        self.generation += 1

    def start(self, body):
        c = self.context
        outcome = body.get("outcome", "done")
        if outcome not in ("done", "error"):
            raise EventError("演示结果不受支持")
        if c.engine.muted():
            raise EventError("请先恢复触发", 409)
        task = str(uuid.uuid4())
        c.engine.accept(make_event("demo", task, "working"), "demo")
        threading.Thread(
            target=self._finish,
            args=(task, outcome, self.generation),
            daemon=True,
        ).start()
        return {"task_id": task, "simulated": True, "seconds": 6}

    def _finish(self, task, outcome, generation):
        c = self.context
        if c.stopping.wait(6):
            return
        with c.lock:
            if (
                c.stopping.is_set()
                or generation != self.generation
                or c.engine.emergency
            ):
                return
            try:
                c.engine.accept(
                    make_event(
                        "demo",
                        task,
                        outcome,
                        2,
                        "completed" if outcome == "done" else "failed",
                    ),
                    "demo",
                )
            except EventError:
                pass
