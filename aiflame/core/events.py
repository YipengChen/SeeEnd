"""Source-independent event types and construction."""

import datetime as dt
from dataclasses import dataclass
import uuid

REQUIRED_FIELDS = ("schema_version", "event_id", "source_id", "task_id", "seq", "state", "occurred_at", "ttl_ms")
OPTIONAL_FIELDS = ("reason", "device_id")
STATES = ("idle", "working", "needs_input", "done", "error", "cancelled", "timeout", "unknown")
REASONS = ("completed", "failed", "cancelled", "timeout")


class EventError(Exception):
    def __init__(self, message, status=400, error_code="request_invalid"):
        super().__init__(message)
        self.status = status
        self.error_code = error_code


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
