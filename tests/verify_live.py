"""Opt-in hardware state and emergency-off checks; importing does not run them."""

from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from aiflame.core.paths import ensure_runtime
from live_client import DashboardClient


def wait_for_state(client, state, seconds=8, event_id=None, min_seq=None):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        _, s = client.request("/api/status")
        event_ok = (
            not event_id
            or client.request("/v1/events/" + event_id)[1]["status"] == "applied"
        )
        seq_ok = min_seq is None or (s["last_ack"] and s["last_ack"]["seq"] > min_seq)
        if (
            event_ok
            and seq_ok
            and s["device"]["online"]
            and s["last_ack"]
            and s["last_ack"]["state"] == state
        ):
            return s
        time.sleep(0.1)
    raise AssertionError("No hardware ack for " + state)


def main():
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
    assert (
        not original["settings"]["paused"]
    ), "Resume reminders first"
    assert client.request("/api/settings", {"duration": 3})[0] == 200
    try:
        for mode in ("working", "done", "error"):
            code, r = client.request("/api/test", {"state": mode})
            assert code == 202
            wait_for_state(client, mode, event_id=r["event_id"])
            report.append(mode + " hardware applied ACK: PASS")
            if mode in ("done", "error"):
                time.sleep(3.3)
        # Pausing turns the physical device off.
        before = client.request("/api/status")[1]["last_ack"]["seq"]
        client.request("/api/settings", {"paused": True})
        wait_for_state(client, "idle", min_seq=before)
        report.append("Pause hardware ACK: PASS")
    finally:
        client.request("/api/settings", {"paused": False, "duration": original["settings"]["duration"]})
    (ensure_runtime() / "live-test.txt").write_text("\n".join(report) + "\n")
    print("\n".join(report))


if __name__ == "__main__":
    main()
