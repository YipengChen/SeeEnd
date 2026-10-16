#!/usr/bin/env python3
"""Observe lifecycle metadata only; never alter Codex decisions or tool permissions."""

import json
import sys
from aiflame.integrations.codex.events import from_hook, deliver
from aiflame.integrations.cli_tools.hook import SilentParser
from aiflame.integrations.cli_tools.origin import prepare
from aiflame.core.events import EventError
from aiflame.core.task_origins import validate_context


def return_context(payload):
    try:
        return validate_context({key: payload.get(key) for key in ("session_id", "turn_id", "cwd")}, "codex")
    except EventError:
        return None


def main():
    try:
        parser = SilentParser(add_help=False)
        parser.add_argument("--port", type=int, default=8765)
        args = parser.parse_args()
        # Hook payloads can contain prompt/tool bodies; they are never persisted or forwarded.
        payload = json.load(sys.stdin)
        if isinstance(payload, dict):
            payload, origin = prepare(payload)
            event = from_hook(payload)
            context = return_context(payload)
            if event and context:
                event["return_context"] = context
            if event and origin:
                event["return_target"] = origin
            deliver(event, args.port)
    except Exception:
        pass
    # No stdout, no decision/block/allow: original Codex behavior is unchanged.


if __name__ == "__main__":
    main()
