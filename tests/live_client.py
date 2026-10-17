"""Shared authenticated dashboard client for opt-in hardware verification."""

import argparse
import json
import urllib.error
import urllib.request
from aiflame.core.paths import dashboard_token as current_dashboard_token
from aiflame.core.management_auth import SignedOpener, NoRedirect


def requested_devices():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device-id", action="append", help="Physical target ID; repeat for multiple lamps. Defaults to all enabled lamps.")
    return parser.parse_args().device_id


def hardware_targets(snapshot, device_ids=None):
    """Freeze intended physical sessions before sending any manual test tasks."""
    assert snapshot["settings"]["hardware_notifications"] is True, "Enable Device alerts first"
    assert not snapshot["emergency"], "Reopen SeeEnd before hardware verification"
    assert snapshot.get("display", {}).get("key") != "muted", "Increase lamp brightness before hardware verification"
    rows = {row["device_id"]: row for row in snapshot.get("devices", [])}
    ids = device_ids if device_ids is not None else [
        key for key, row in rows.items()
        if not row["connection_paused"] and row["light_settings"]["brightness"] > 0
    ]
    assert ids, "Enable and connect at least one physical lamp"
    assert len(set(ids)) == len(ids), "Target IDs must be unique"
    targets = {}
    for key in ids:
        assert key in rows, "Unknown target: " + key
        row = rows[key]
        assert row.get("online") and not row["connection_paused"], "Connect target: " + key
        assert row["light_settings"]["brightness"] > 0, "Increase target brightness: " + key
        targets[key] = (row.get("session_id"), row.get("connected_at"))
    return targets


def targets_connected(snapshot, targets):
    rows = {row["device_id"]: row for row in snapshot.get("devices", [])}
    return snapshot["settings"]["hardware_notifications"] is True and all(
        key in rows and rows[key].get("online") and not rows[key]["connection_paused"]
        and rows[key]["light_settings"]["brightness"] > 0
        and (rows[key].get("session_id"), rows[key].get("connected_at")) == token
        for key, token in targets.items()
    )


def hardware_applied(snapshot, targets, event_id):
    applied = {row["output"] for row in snapshot.get("deliveries", [])
               if row["event"] == event_id and row["status"] == "applied"}
    return targets_connected(snapshot, targets) and all("flame:" + key in applied for key in targets)


class DashboardClient:
    def __init__(self, base="http://127.0.0.1:8765", dashboard_token=None):
        self.base = base
        self.raw_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        reader = (lambda: dashboard_token) if dashboard_token is not None else current_dashboard_token
        self.opener = SignedOpener(base, reader, opener=self.raw_opener)
        with self.opener.open(base + "/api/session", timeout=3):
            pass

    def request(self, path, body=None, headers=None, authenticated=True):
        request = urllib.request.Request(
            self.base + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Content-Type": "application/json", **(headers or {})},
        )
        opener = (
            self.opener
            if authenticated and not any(key.lower() in ("authorization", "cookie") for key in (headers or {}))
            else self.raw_opener
        )
        try:
            response = opener.open(request, timeout=5)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            return response.status, json.load(response)

    def call(self, path, body=None):
        status, result = self.request(path, body)
        if not 200 <= status < 300:
            raise RuntimeError(result.get("error", f"HTTP {status}"))
        return result

    def download(self, path):
        with self.opener.open(self.base + path, timeout=5) as response:
            return response.read()
