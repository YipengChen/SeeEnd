"""Mutual management authentication against real HTTP handlers, without sockets."""

from io import BytesIO
import json
import os
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import urllib.error
import urllib.parse
import urllib.request
from email.message import Message
from aiflame import __version__
from aiflame.core.paths import dashboard_token, new_config, write_private
from aiflame.core.management_auth import (
    AuthError,
    ManagementServer,
    SignedOpener,
    VerifiedResponse,
    _key,
    request_header,
    MAX_NONCES,
)
from aiflame.hub.server import Handler, Hub


class OfflineSocket:
    def __init__(self, request):
        self.input, self.output = BytesIO(request), bytearray()

    def makefile(self, *args):
        return self.input

    def settimeout(self, *args):
        pass

    def sendall(self, data):
        self.output.extend(data)


def auth_header(hub, method, path, body=None):
    challenge = hub.management.challenge(
        "0" * 32, hub.config["dashboard"], "SeeEnd", __version__
    )
    context = {
        "key": _key(hub.config["dashboard"]),
        "instance": challenge["instance"],
        "generation": challenge["generation"],
    }
    return request_header(context, method, path, body)


class OfflineTransport:
    def __init__(self, context):
        self.context, self.calls = context, []

    def open(self, request, timeout=5):
        request = (
            urllib.request.Request(request) if isinstance(request, str) else request
        )
        self.calls.append(request)
        parsed = urllib.parse.urlsplit(request.full_url)
        path = parsed.path + ("?" + parsed.query if parsed.query else "")
        headers = dict(request.header_items())
        headers.setdefault("Host", "127.0.0.1:54321")
        payload = request.data or b""
        if request.data is not None:
            headers.setdefault("Content-Length", str(len(payload)))
        raw = request.get_method() + " " + path + " HTTP/1.1\r\n"
        raw += (
            "".join(key + ": " + value + "\r\n" for key, value in headers.items())
            + "\r\n"
        )
        sock = OfflineSocket(raw.encode() + payload)
        Handler(sock, ("127.0.0.1", 12345), SimpleNamespace(context=self.context))
        header, body = bytes(sock.output).split(b"\r\n\r\n", 1)
        lines = header.decode().split("\r\n")
        status, result_headers = int(lines[0].split()[1]), Message()
        for line in lines[1:]:
            key, value = line.split(": ", 1)
            result_headers[key] = value
        if status >= 400:
            raise urllib.error.HTTPError(
                request.full_url,
                status,
                "offline HTTP error",
                result_headers,
                BytesIO(body),
            )
        return VerifiedResponse(body, status, result_headers)


class SessionAuthTests(unittest.TestCase):
    def setUp(self):
        self.context = SimpleNamespace(
            port=54321,
            config=new_config(),
            management=ManagementServer(),
            request_guard=threading.RLock(),
            lock=threading.RLock(),
            operation_lock=threading.RLock(),
            active_posts=0,
            resetting=False,
            stopping=threading.Event(),
            limits={},
        )
        self.transport = OfflineTransport(self.context)
        self.client = SignedOpener(
            "http://127.0.0.1:54321",
            lambda: self.context.config["dashboard"],
            opener=self.transport,
        )

    def raw(self, path, **headers):
        try:
            result = self.transport.open(
                urllib.request.Request(self.client.base + path, headers=headers)
            )
        except urllib.error.HTTPError as error:
            result = error
        with result:
            return result.status, result.headers, json.loads(result.read())

    def test_no_anonymous_root_bearer_cookie_or_source_management_fallback(self):
        cases = [
            ({}, 401),
            ({"Authorization": "Bearer " + self.context.config["dashboard"]}, 401),
            ({"Cookie": "aiflame=" + self.context.config["dashboard"]}, 401),
        ]
        cases += [
            ({"Authorization": "Bearer " + token}, 403)
            for token in self.context.config["tokens"].values()
        ]
        for headers, expected in cases:
            status, response_headers, _ = self.raw("/api/session", **headers)
            self.assertEqual(status, expected)
            self.assertNotIn("Set-Cookie", response_headers)

    def test_session_never_transmits_root_or_cookie(self):
        with self.client.open(self.client.base + "/api/session") as response:
            self.assertEqual(json.load(response)["app_name"], "SeeEnd")
            self.assertNotIn("Set-Cookie", response.headers)
        for request in self.transport.calls:
            self.assertNotIn(
                self.context.config["dashboard"], str(request.header_items())
            )
            self.assertIsNone(request.get_header("Cookie"))
        self.assertIsNone(self.transport.calls[0].get_header("Authorization"))
        self.assertTrue(
            self.transport.calls[1]
            .get_header("Authorization")
            .startswith("seeend-hmac-v1 ")
        )

    def test_body_method_path_replay_and_bounded_nonce_cache(self):
        header = auth_header(self.context, "POST", "/api/example", b"{}")
        for method, path, body in [
            ("GET", "/api/example", b"{}"),
            ("POST", "/api/other", b"{}"),
            ("POST", "/api/example", b"changed"),
        ]:
            with self.assertRaises(AuthError):
                self.context.management.verify(
                    header, method, path, body, self.context.config["dashboard"]
                )
        self.assertEqual(len(self.context.management.nonces), 0)
        self.context.management.verify(
            header, "POST", "/api/example", b"{}", self.context.config["dashboard"]
        )
        with self.assertRaises(AuthError):
            self.context.management.verify(
                header, "POST", "/api/example", b"{}", self.context.config["dashboard"]
            )
        self.context.management.nonces = {
            str(i): int(self.context.management.clock()) + 30 for i in range(MAX_NONCES)
        }
        with self.assertRaises(AuthError):
            self.context.management.verify(
                auth_header(self.context, "GET", "/api/session"),
                "GET",
                "/api/session",
                b"",
                self.context.config["dashboard"],
            )
        self.assertEqual(len(self.context.management.nonces), MAX_NONCES)

    def test_rotation_restart_and_expired_requests(self):
        header = auth_header(self.context, "GET", "/api/session")
        old = self.context.config["dashboard"]
        self.context.config = new_config()
        self.assertEqual(self.raw("/api/session", Authorization=header)[0], 401)
        self.context.config["dashboard"] = old
        self.context.management = ManagementServer()
        self.assertEqual(self.raw("/api/session", Authorization=header)[0], 401)
        header = auth_header(self.context, "GET", "/api/session")
        with patch.object(
            self.context.management,
            "clock",
            return_value=self.context.management.clock() + 31,
        ):
            self.assertEqual(self.raw("/api/session", Authorization=header)[0], 401)

    def test_fake_peer_receives_only_public_challenge_and_cannot_forge_success(self):
        requests = []

        def fake(request, timeout):
            requests.append(request)
            return VerifiedResponse(
                b'{"app_name":"SeeEnd","app_version":"0.8.66"}', 200, {}
            )

        client = SignedOpener(
            self.client.base,
            lambda: self.context.config["dashboard"],
            opener=SimpleNamespace(open=fake),
        )
        with self.assertRaises(AuthError):
            client.open(self.client.base + "/api/session")
        self.assertEqual(len(requests), 1)
        self.assertIsNone(requests[0].get_header("Authorization"))
        self.assertNotIn(
            self.context.config["dashboard"], str(requests[0].header_items())
        )
        with self.client.open(self.client.base + "/api/session"):
            pass
        self.client.opener = SimpleNamespace(open=fake)
        with self.assertRaises(AuthError):
            self.client.open(self.client.base + "/api/session")

    def test_challenge_nonce_metadata_proof(self):
        for field in ("nonce", "app_version", "proof"):

            def corrupt(request, timeout):
                with self.transport.open(request, timeout) as response:
                    payload = json.load(response)
                payload[field] = "tampered"
                return VerifiedResponse(json.dumps(payload).encode(), 200, {})

            client = SignedOpener(
                self.client.base,
                lambda: self.context.config["dashboard"],
                opener=SimpleNamespace(open=corrupt),
            )
            with self.assertRaises(AuthError):
                client.open(self.client.base + "/api/session")

    def test_reset_response_has_old_request_proof_and_no_new_secret(self):
        old = self.context.config

        def reset_profile(expected_config):
            self.assertIs(expected_config, old)
            self.context.config = new_config()
            return {"events": []}

        self.context.reset_app = reset_profile
        request = urllib.request.Request(
            self.client.base + "/api/reset",
            data=b'{"confirm":true}',
            headers={"Content-Type": "application/json"},
        )
        with self.client.open(request) as response:
            self.assertEqual(json.load(response), {"events": []})
            self.assertNotIn("Set-Cookie", response.headers)
            self.assertNotIn(self.context.config["dashboard"], str(response.headers))
        with self.client.open(self.client.base + "/api/session") as response:
            self.assertEqual(response.status, 200)

    def test_inflight_get_blocks_reset_and_cannot_receive_new_secret(self):
        entered, resume, errors = threading.Event(), threading.Event(), []
        reply, old_config = Handler.reply, self.context.config

        def delayed(handler, code, payload, *args, **kwargs):
            if handler.path == "/api/session" and code == 200:
                entered.set()
                if not resume.wait(2):
                    raise AssertionError("barrier timed out")
            return reply(handler, code, payload, *args, **kwargs)

        def perform():
            try:
                with self.client.open(self.client.base + "/api/session") as response:
                    self.assertNotIn("Set-Cookie", response.headers)
                    self.assertNotIn(
                        self.context.config["dashboard"], str(response.headers)
                    )
            except BaseException as error:
                errors.append(error)

        with patch.object(Handler, "reply", delayed):
            worker = threading.Thread(target=perform)
            worker.start()
            self.assertTrue(entered.wait(2))
            self.context.devices = SimpleNamespace(busy=lambda: False)
            self.context.tests = SimpleNamespace(busy=lambda: False)
            with self.assertRaisesRegex(Exception, "当前操作"):
                Hub.reset_app(self.context, expected_config=old_config)
            self.context.config = new_config()
            resume.set()
            worker.join(2)
        self.assertEqual(errors, [])
        with self.assertRaisesRegex(Exception, "管理会话已变化"):
            Hub.reset_app(self.context, expected_config=old_config)


class ClaimValidationRouteTests(unittest.TestCase):
    setUp = SessionAuthTests.setUp
    def test_real_claim_to_validation_routes_return_boolean_and_revoke_stale_claims(self):
        from aiflame.core.engine import Engine
        from aiflame.core.events import make_event
        from aiflame.integrations.registry import SOURCES
        from aiflame.outputs.desktop import DesktopOutput
        from aiflame.outputs.sound import SoundOutput
        if not all(hasattr(output, "validate_claim") for output in (DesktopOutput, SoundOutput)):
            self.skipTest("Claim validation output methods arrive with the parallel notification branch")
        engine = self.context.engine = Engine(":memory:", sources=SOURCES)
        self.addCleanup(engine.db.close)
        engine.update_settings({"desktop_notifications": True, "sound_notifications": True})
        engine.accept(make_event("codex", "claim-route", "working"), "codex")
        engine.accept(make_event("codex", "claim-route", "needs_input", 2), "codex")
        claims = []
        for channel, key in (("notifications", "notifications"), ("sounds", "sounds")):
            request = urllib.request.Request(self.client.base + "/api/" + channel + "/claim",
                data=b"{}", headers={"Content-Type": "application/json"})
            with self.client.open(request) as response:
                item = json.load(response)[key][0]
            body = {field: item[field] for field in ("event_id", "claim", "generation")}
            claims.append((channel, body))
            request = urllib.request.Request(self.client.base + "/api/" + channel + "/validate",
                data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
            with self.client.open(request) as response:
                self.assertEqual(json.load(response), {"valid": True})
        engine.accept(make_event("codex", "claim-route", "working", 3), "codex")
        for channel, body in claims:
            request = urllib.request.Request(self.client.base + "/api/" + channel + "/validate",
                data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
            with self.client.open(request) as response:
                self.assertEqual(json.load(response), {"valid": False})


class DesktopCredentialTests(unittest.TestCase):
    def test_read_only_bootstrap_does_not_create_missing_profile(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict(os.environ, {"AIFLAME_DATA_DIR": directory}),
        ):
            with self.assertRaises(FileNotFoundError):
                dashboard_token()
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_local_api_signs_every_call_and_reloads_rotated_private_token(self):
        from aiflame.desktop.network import LocalAPI

        fixture = SessionAuthTests()
        fixture.setUp()
        client = SimpleNamespace(base=fixture.client.base)
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict(os.environ, {"AIFLAME_DATA_DIR": directory}),
        ):
            for token in ("first-private-token", "rotated-private-token"):
                fixture.context.config["dashboard"] = token
                write_private(
                    Path(directory) / "credentials.json", {"dashboard": token}
                )
                self.assertIsNone(
                    LocalAPI._perform(
                        client,
                        fixture.transport,
                        (1, "/api/session", None, False, None),
                    )[2]
                )
            self.assertIsNone(
                LocalAPI._perform(
                    client,
                    fixture.transport,
                    (2, "/api/integration-contract", None, False, None),
                )[2]
            )
            self.assertTrue(
                all(
                    request.get_header("Authorization").startswith("seeend-hmac-v1 ")
                    for request in fixture.transport.calls
                    if "/auth/challenge" not in request.full_url
                )
            )


if __name__ == "__main__":
    unittest.main()
