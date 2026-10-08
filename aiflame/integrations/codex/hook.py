#!/usr/bin/env python3
"""Observe lifecycle metadata only; never alter Codex decisions or tool permissions."""

import json, sys
from aiflame.integrations.codex.events import from_hook, deliver


def main():
    try:
        # Hook payloads can contain prompt/tool bodies; they are never persisted or forwarded.
        payload = json.load(sys.stdin)
        if isinstance(payload, dict):
            event = from_hook(payload)
            deliver(event)
    except Exception:
        pass
    # No stdout, no decision/block/allow: original Codex behavior is unchanged.


if __name__ == "__main__":
    main()
