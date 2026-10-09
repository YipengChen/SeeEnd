"""Single entry point for the desktop app and bundled command-line helpers."""

import sys


def main():
    args = sys.argv[1:]
    from pathlib import Path

    if Path(sys.executable).stem == "SeeEnd Bridge":
        args.insert(0, "--browser-host")
    mode = args.pop(0) if args and args[0].startswith("--") else "--desktop"
    sys.argv = [sys.argv[0]] + args
    if mode == "--codex-hook":
        from aiflame.integrations.codex.hook import main as run
    elif mode == "--cli-hook":
        from aiflame.integrations.cli_tools.hook import main as run
    elif mode == "--browser-host":
        from aiflame.integrations.chatgpt_web.host import main as run
    elif mode == "--hub":
        from aiflame.hub.server import main as run
    elif mode == "--desktop":
        import faulthandler
        import os
        import traceback
        from datetime import datetime
        from aiflame import __version__
        from aiflame.core.paths import ensure_runtime
        log = open(ensure_runtime() / "desktop.log", "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stderr = log
        if os.name != "nt":
            os.dup2(log.fileno(), 1)
            os.dup2(log.fileno(), 2)
        faulthandler.enable(file=log)
        print(f"{datetime.now().astimezone().isoformat()} desktop start version={__version__} pid={os.getpid()}")
        try:
            from aiflame.desktop.app import main as run
            result = run()
            print(f"{datetime.now().astimezone().isoformat()} desktop exit pid={os.getpid()} code={result}")
            return result
        except BaseException:
            traceback.print_exc(file=log)
            raise
    else:
        raise SystemExit("Unknown SeeEnd mode")
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
