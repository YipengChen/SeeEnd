"""Opt-in hardware task-state checks; importing does not run them."""

from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from aiflame.core.paths import ensure_runtime
from live_client import DashboardClient, hardware_targets, hardware_applied, targets_connected, requested_devices


def wait_for_state(client, state, targets, seconds=8, event_id=None):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        _, s = client.request("/api/status")
        event_ok = not event_id or hardware_applied(s, targets, event_id)
        if (
            event_ok
            and targets_connected(s, targets)
            and s["display"]["device_confirmed"]
            and s["display"]["state"] == state
        ):
            return s
        time.sleep(0.1)
    raise AssertionError("No hardware ack for " + state)


def main():
    device_ids = requested_devices()
    client = DashboardClient()
    report = []
    assert client.request("/api/status", authenticated=False)[0] == 401
    assert (
        client.request("/api/stop", {}, headers={"Origin": "http://unrelated.example"})[
            0
        ]
        == 403
    )
    report.append("Authentication and Origin rejection: PASS")
    original = client.call("/api/status")
    targets = hardware_targets(original, device_ids)
    report.append("Physical targets: " + ", ".join(targets))
    assert client.request("/api/settings", {"duration": 3})[0] == 200
    tasks = []
    try:
        for mode in ("working", "done", "needs_input"):
            code, r = client.request("/api/test", {"state": mode})
            assert code == 202
            tasks.append(r["task_id"])
            wait_for_state(client, mode, targets, event_id=r["event_id"])
            report.append(mode + " hardware applied ACK: PASS")
            if mode in ("done", "needs_input"):
                time.sleep(3.3)
        for task in tasks:
            client.call("/api/test", {"state": "idle", "seq": 2, "task_id": task})
        wait_for_state(client, "idle", targets)
        report.append("Idle hardware ACK: PASS")
    finally:
        client.request("/api/settings", {"duration": original["settings"]["duration"]})
    (ensure_runtime() / "live-test.txt").write_text("\n".join(report) + "\n")
    print("\n".join(report))


if __name__ == "__main__":
    main()
