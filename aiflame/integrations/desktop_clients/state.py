"""Require a new reply toolbar, an observed generation and uninterrupted scope."""

from dataclasses import dataclass, field
import uuid

from aiflame.core.events import make_event


@dataclass(frozen=True)
class Frame:
    scope: tuple[int, int, int]  # PID, window identity, composer identity; local only.
    generating: bool = False
    send: bool = False
    copies: frozenset[int] = field(default_factory=frozenset)
    finals: frozenset[int] = field(default_factory=frozenset)
    errors: frozenset[int] = field(default_factory=frozenset)


class Tracker:
    def __init__(self, source, emit):
        self.source, self.emit = source, emit
        self.previous = None
        self.task = None
        self.seq = 0
        self.last_sent = 0
        self.candidate = None
        self.baseline = None

    def _send(self, state, now, reason=None):
        self.seq += 1
        self.emit(make_event(self.source, self.task, state, self.seq, reason))
        self.last_sent = now

    def reset(self, now):
        if self.task:
            self._send("idle", now, "cancelled")
        self.task = self.candidate = self.baseline = self.previous = None

    def observe(self, frame, now, interrupted=False):
        if (
            interrupted
            or frame is None
            or (self.previous and self.previous.scope != frame.scope)
            or (self.previous and now - self.last_observed > 10)
        ):
            self.reset(now)
        if frame is None:
            return
        # Establish a baseline on startup, permission changes and navigation.
        # A generation already in progress is never adopted as a fresh task.
        if self.previous is None:
            self.previous, self.last_observed = frame, now
            return
        if frame.generating and not self.previous.generating and not self.task:
            self.baseline = self.previous
            self.task, self.seq = str(uuid.uuid4()), 0
            self._send("working", now)
        if self.task:
            new_copy = frame.copies - self.baseline.copies
            new_final = frame.finals - self.baseline.finals
            new_error = len(frame.errors) > len(self.baseline.errors)
            if new_error and not frame.generating:
                self._send("error", now, "failed")
                self.task = self.candidate = None
            elif (
                not frame.generating
                and frame.send
                and new_copy
                and new_final
                and len(frame.copies) > len(self.baseline.copies)
                and len(frame.finals) > len(self.baseline.finals)
            ):
                key = (new_copy, new_final)
                if self.candidate and self.candidate[0] == key:
                    if now - self.candidate[1] >= 2:
                        self._send("done", now, "completed")
                        self.task = self.candidate = None
                else:
                    self.candidate = (key, now)
            else:
                self.candidate = None
            if self.task and now - self.last_sent >= 30:
                self._send("working", now)
        self.previous, self.last_observed = frame, now
