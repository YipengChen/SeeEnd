"""Authenticated loopback API client shared by integration diagnostics."""

import json
import urllib.request


def send(event, token, port=8765, timeout=3):
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/v1/events",
        data=json.dumps(event).encode(),
        headers={
            "Authorization": "Bearer " + token,
            "Content-Type": "application/json",
        },
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=timeout) as response:
        return json.load(response)
