"""Silent hooks never change the host tool's permission or control flow."""

import json
import sys
import argparse
from aiflame.integrations.cli_tools.lifecycle import from_hooks, deliver


class SilentParser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(message)


def main():
    source = None
    try:
        parser = SilentParser(add_help=False)
        parser.add_argument("source")
        parser.add_argument("--port", type=int, default=8765)
        args = parser.parse_args()
        source = args.source
        for event in from_hooks(args.source, json.load(sys.stdin)):
            try:
                deliver(event, args.port)
            except Exception:
                pass
    except Exception:
        pass

    finally:
        if source == "gemini-cli":
            print("{}")
