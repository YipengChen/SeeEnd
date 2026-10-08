"""Shared authenticated dashboard client for opt-in hardware verification."""

import http.cookiejar
import json
import urllib.error
import urllib.request


class DashboardClient:
    def __init__(self, base="http://127.0.0.1:8765"):
        self.base = base
        self.opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
        )
        with self.opener.open(base + "/", timeout=3):
            pass

    def request(self, path, body=None, headers=None, authenticated=True):
        request = urllib.request.Request(
            self.base + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Content-Type": "application/json", **(headers or {})},
        )
        opener = (
            self.opener
            if authenticated
            else urllib.request.build_opener(urllib.request.ProxyHandler({}))
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
