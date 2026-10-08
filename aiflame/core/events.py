"""Source-independent event types and construction."""

import datetime as dt
from dataclasses import dataclass
import uuid


class EventError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class SourcePolicy:
    id: str
    name: str
    description: str
    authenticated: bool = False
    requires_start: bool = False
    ignore_unregistered: bool = False
    allow_late_cancel: bool = False
    default_enabled: bool = True
    # Hooks with explicit terminal events need not emit periodic heartbeats.
    working_lease_seconds: float | None = 120


def make_event(source, task, state, seq=1, reason=None):
    value = {
        "schema_version": "1.0",
        "event_id": str(uuid.uuid4()),
        "source_id": source,
        "task_id": task,
        "seq": seq,
        "state": state,
        "occurred_at": dt.datetime.now(dt.timezone.utc)
        .isoformat()
        .replace("+00:00", "Z"),
        "ttl_ms": 60000,
    }
    if reason:
        value["reason"] = reason
    return value
