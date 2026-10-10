#!/usr/bin/env python3
"""Observe lifecycle metadata only; never alter Codex decisions or tool permissions."""

import json
import sys
from aiflame.integrations.codex.events import from_hook, deliver
from aiflame.integrations.cli_tools.hook import SilentParser


def main():
    try:
        parser = SilentParser(add_help=False)
        parser.add_argument("--port", type=int, default=8765)
        args = parser.parse_args()
        # Hook payloads can contain prompt/tool bodies; they are never persisted or forwarded.
        payload = json.load(sys.stdin)
        if isinstance(payload, dict):
            event = from_hook(payload)
            deliver(event, args.port)
    except Exception:
        pass
    # No stdout, no decision/block/allow: original Codex behavior is unchanged.


if __name__ == "__main__":
    main()
