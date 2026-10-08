"""Opt-in real-device verification against a running local Hub.

Sends clearly marked test tasks and restores the original prompt duration. Does
not install integrations or send a message to an AI service.
"""

import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from live_client import DashboardClient
from aiflame.integrations.chatgpt_web.host import WEB_SOURCES


def main():
    client = DashboardClient()
    call = client.call

    original = call("/api/status")
    assert original["device"]["online"], "Connect the ESP32 first"
    assert not original["settings"]["paused"] and not original["emergency"], (
        "Resume the Hub first"
    )
    report = []
    call("/api/settings", {"duration": 3})
    try:
        for source in ("manual", "local-script", "codex", *WEB_SOURCES):
            job = call("/api/trigger-test", {"source": source})
            deadline = time.monotonic() + 25
            while time.monotonic() < deadline:
                time.sleep(0.25)
                current = call("/api/trigger-test")["job"]
                assert current["id"] == job["id"], (
                    "Another test interrupted verification"
                )
                assert current["state"] != "failed", source + ": " + current["message"]
                if current["state"] == "succeeded" and any(
                    e["state"] == "done" and e["status"] == "applied"
                    for e in current["delivery"]
                ):
                    break
            else:
                raise AssertionError(source + ": missing device completion ACK")
            report.append(
                {
                    "source": source,
                    "adapter": "passed",
                    "completion_ack": "applied",
                    "delivery": current["delivery"],
                }
            )
            print(source + ": adapter + hardware completion ACK PASS", flush=True)
            time.sleep(3.3)
        download = client.download("/api/firmware/download")
        assert download.startswith(b"PK")
        print("Firmware download: ZIP PASS", flush=True)
    finally:
        call("/api/settings", {"duration": original["settings"]["duration"]})
    from aiflame.core.paths import ensure_runtime

    path = ensure_runtime() / "desktop-adapters-live.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
