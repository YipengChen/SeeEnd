"""Offline tests: no socket, running SeeEnd instance, or user configuration."""

import contextlib
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import socket
import sys
import urllib.parse
import tempfile
import unittest
from unittest import mock
import urllib.error
import urllib.request


SCRIPT = Path(__file__).resolve().parents[1] / "seeend/scripts/seeend_client.py"
sys.path.insert(0, str(SCRIPT.parent))
auth = __import__("seeend_auth")
spec = importlib.util.spec_from_file_location("seeend_client", SCRIPT)
client = importlib.util.module_from_spec(spec)
spec.loader.exec_module(client)



class Response:
    def __init__(self, value, code=200, headers=None):
        self.data = json.dumps(value).encode() if not isinstance(value, bytes) else value
        self.code = code
        self.headers = headers or {}

    def getcode(self):
        return self.code

    def read(self, size):
        return self.data[:size]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class Opener:
    def __init__(self, outcomes):
        self.outcomes, self.calls, self.challenges = list(outcomes), [], []
        self.server = auth.ManagementServer()

    def open(self, request, timeout):
        parsed = urllib.parse.urlsplit(request.full_url)
        path = parsed.path + ('?' + parsed.query if parsed.query else '')
        if parsed.path == '/api/auth/challenge':
            self.challenges.append(request)
            nonce = urllib.parse.parse_qs(parsed.query)['nonce'][0]
            return Response(self.server.challenge(nonce, 'test-private-dashboard', 'SeeEnd', META['app_version']))
        self.calls.append((request, timeout))
        context = self.server.verify(request.get_header('Authorization'), request.get_method(), path,
                                     request.data or b'', 'test-private-dashboard')
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, urllib.error.HTTPError):
            raw = outcome.read()
            outcome.headers['X-SeeEnd-Proof'] = auth.response_proof(context, outcome.code, raw)
            error = urllib.error.HTTPError(outcome.url, outcome.code, outcome.msg, outcome.headers, io.BytesIO(raw))
            outcome.close()
            raise error
        if isinstance(outcome, BaseException):
            raise outcome
        response = Response(outcome)
        response.headers['X-SeeEnd-Proof'] = auth.response_proof(context, response.code, response.data)
        return response


META = {"app_name": "SeeEnd", "app_version": "0.8.69"}
CONTRACT = json.loads((Path(__file__).parent / "fixtures/integration-contract.json").read_text())
STATUS = {
    "sources": [{"id": "codex", "name": "Codex", "enabled": True},
                {"id": "claude-code", "name": "Claude", "enabled": False}],
    "verification": {"codex": {"source": "codex", "status": "verified", "complete_tasks": 2},
                     "claude-code": {"source": "claude-code", "status": "error"}},
    "display": {"state": "done", "task": "private task", "payload": "raw text"},
    "settings": {"mode": "virtual", "source_enabled": {"codex": True}, "environment": {"PRIVATE": "private env"}},
    "events": [{"body": "private transcript"}], "tasks": [{"task": "private task"}],
    "tokens": {"codex": "secret-token-value"}, "device": {"online": False, "ip": "private address"},
}


class ClientTests(unittest.TestCase):
    def instance(self, outcomes, contract=True):
        outcomes = list(outcomes)
        if contract and outcomes and isinstance(outcomes[0], dict) and outcomes[0].get("app_name") == "SeeEnd":
            value = copy.deepcopy(CONTRACT)
            value["app_version"] = outcomes[0]["app_version"]
            outcomes.insert(1, value)
        opener = Opener(outcomes)
        with mock.patch.object(urllib.request, "build_opener", return_value=opener):
            instance = client.Client(credential_reader=lambda: "test-private-dashboard")
        return instance, opener

    def test_only_exact_http_loopback_authorities(self):
        for base in ("http://127.0.0.1:8765", "http://localhost:1234/", "http://localhost"):
            self.assertTrue(client.validate_base_url(base).startswith("http://"))
        for base in ("https://localhost", "http://localhost.evil", "http://127.0.0.2", "http://[::1]",
                     "http://user:password@localhost", "http://localhost?", "http://localhost#",
                     "http://localhost/x", "http://localhost:0", "http://localhost:65536",
                     "http://localhost:bad", "http://localhost\\@evil", "http://localhost%2eevil",
                     "http://localhost\n", " http://localhost"):
            with self.subTest(base=base), self.assertRaises(client.ClientError):
                client.validate_base_url(base)

    def test_opener_bypasses_proxy_has_no_cookie_processor_and_blocks_redirect(self):
        with mock.patch.object(urllib.request, "build_opener") as build:
            client.Client()
        handlers = build.call_args.args
        self.assertEqual(next(h for h in handlers if isinstance(h, urllib.request.ProxyHandler)).proxies, {})
        self.assertFalse(any(isinstance(h, urllib.request.HTTPCookieProcessor) for h in handlers))
        redirect = next(h for h in handlers if isinstance(h, client.NoRedirect))
        for destination in ("https://evil.example/", "http://127.0.0.1:9999/", "http://localhost:8765/other"):
            with self.assertRaises(urllib.error.HTTPError):
                redirect.redirect_request(urllib.request.Request("http://127.0.0.1:8765/api/session"), None,
                                          302, "Found", {}, destination)

    def test_session_identity_checked_before_other_requests(self):
        instance, opener = self.instance([{"app_name": "Other", "app_version": "0.8.4"}, STATUS])
        with self.assertRaises(client.ClientError) as raised:
            instance.run("status")
        self.assertEqual(raised.exception.category, "identity_mismatch")
        self.assertEqual(len(opener.calls), 1)

    def test_every_request_is_signed_and_root_secret_is_redacted(self):
        status = copy.deepcopy(STATUS)
        status["sources"][0]["name"] = "reflected test-private-dashboard"
        instance, opener = self.instance([META, status])
        result = instance.run("status", "codex")
        self.assertTrue(opener.calls[0][0].get_header("Authorization").startswith(auth.PROTOCOL + " "))
        self.assertTrue(all(request.get_header("Authorization").startswith(auth.PROTOCOL + " ") for request, _ in opener.calls))
        self.assertTrue(all("test-private-dashboard" not in str(request.header_items()) for request, _ in opener.calls))
        self.assertTrue(all(request.get_header("Authorization") is None for request in opener.challenges))
        self.assertNotIn("test-private-dashboard", json.dumps(result))

    def test_session_rejection_stops_without_anonymous_fallback(self):
        for code in (401, 403):
            error = urllib.error.HTTPError("http://127.0.0.1:8765/api/session", code,
                                           "rejected", {}, io.BytesIO(b"{}"))
            instance, opener = self.instance([error])
            with self.assertRaises(client.ClientError):
                instance.run("status")
            self.assertEqual(len(opener.calls), 1)
            self.assertIsNotNone(opener.calls[0][0].get_header("Authorization"))

    def test_status_filters_target_and_excludes_secrets_raw_environment_and_events(self):
        instance, opener = self.instance([META, STATUS])
        result = instance.run("status", "codex")
        text = json.dumps(result)
        self.assertEqual(result["source"]["id"], "codex")
        self.assertEqual(result["verification"]["status"], "verified")
        self.assertEqual(result["verification"]["evidence_scope"], "source-wide")
        self.assertIn("会话、任务和时间", result["verification"]["limitation"])
        for value in ("claude-code", "secret-token-value", "private transcript", "private env", "private task", "private address"):
            self.assertNotIn(value, text)
        self.assertNotIn("events", result)
        self.assertEqual([r.get_method() for r, _ in opener.calls], ["GET", "GET", "GET"])

    def test_large_status_is_verified_and_filtered_but_tampering_is_rejected(self):
        for tampered in (False, True):
            with self.subTest(tampered=tampered):
                status = copy.deepcopy(STATUS)
                status["diagnostic_padding"] = "x" * (3 * 1024 * 1024)
                instance, opener = self.instance([META, status])
                original_open = opener.open

                def open_response(request, timeout):
                    response = original_open(request, timeout)
                    if tampered and urllib.parse.urlsplit(request.full_url).path == "/api/status":
                        response.data += b" "
                    return response

                opener.open = open_response
                if tampered:
                    with self.assertRaises(client.ClientError) as raised:
                        instance.run("status", "codex")
                    self.assertEqual(raised.exception.category, "authentication_failed")
                else:
                    result = instance.run("status", "codex")
                    self.assertTrue(result["ok"])
                    self.assertNotIn("diagnostic_padding", result)
                    self.assertLess(len(json.dumps(result)), 10000)

    def test_plan_keeps_actual_files_snippet_reload_but_redacts_secret_text(self):
        plan = {
            "source_id": "codex", "files": ["/tmp/custom/hooks.json"],
            "changes": ["Add owned hooks only"], "snippet": '{"hooks": {}, "tokens": {"codex":"future-token"}}',
            "onboarding": {"reload_steps": ["Restart then trust"], "verification_prompt": "Reply only"},
            "cookie": "cookie-secret-987", "message": "ignored",
            "manual": "Bearer raw-bearer-token; Cookie=cookie-secret-987",
        }
        instance, opener = self.instance([META, plan])
        instance.redactor._learn_values("memory-cookie-123")
        plan["changes"].append("memory-cookie-123")
        result = instance.run("plan", "codex", "/tmp/custom folder")
        self.assertEqual(result["plan"]["files"], ["/tmp/custom/hooks.json"])
        self.assertEqual(result["plan"]["reload_steps"], ["Restart then trust"])
        self.assertIn("hooks", result["plan"]["snippet"])
        for secret in ("future-token", "raw-bearer-token", "cookie-secret-987", "memory-cookie-123"):
            self.assertNotIn(secret, json.dumps(result))
        self.assertIn("config_dir=%2Ftmp%2Fcustom+folder", opener.calls[2][0].full_url)

    def test_check_keeps_actionable_diagnostics_filters_extra_fields(self):
        report = {
            "config_file": "/tmp/hooks.json", "configuration_ready": False,
            "issues": ["Missing Stop hook"], "checks": [{"id": "Stop", "status": "error", "message": "Missing", "action": "Repair only owned hook", "payload": "private"}],
            "verification": {"source": "codex", "status": "waiting", "token": "secret"},
            "signals": {"complete": {"received": 0, "applied": 0, "events": ["private"]}},
            "environment": {"SECRET": "private"},
        }
        instance, _ = self.instance([META, report])
        result = instance.run("check", "codex")["check"]
        self.assertEqual(result["checks"][0]["action"], "Repair only owned hook")
        self.assertEqual(result["issues"], ["Missing Stop hook"])
        self.assertNotIn("private", json.dumps(result))
        self.assertNotIn("token", result["verification"])
        self.assertEqual(result["verification"]["evidence_scope"], "source-wide")

    def test_old_other_profile_verification_does_not_verify_selected_profile(self):
        # History is from an earlier Profile. The actual backend does not bind its
        # verification window to the profile passed in this new check request.
        historical = {
            "source": "deepseek-harness", "status": "verified", "complete_tasks": 1,
            "since": 100, "started_at": 110, "finished_at": 120,
            "profile": "previous-profile",
            "evidence_scope": "selected-profile", "limitation": "All profiles verified",
        }
        report = {
            "source_id": "deepseek-harness", "profile": "new-profile",
            "configuration_ready": True, "status": "connected",
            "checks": [], "verification": historical,
        }
        instance, opener = self.instance([META, report])
        result = instance.run("check", "deepseek-harness", profile="new-profile")["check"]
        self.assertEqual(result["profile"], "new-profile")
        self.assertIn("profile=new-profile", opener.calls[2][0].full_url)
        self.assertEqual(result["verification"]["status"], "verified")
        self.assertEqual(result["verification"]["evidence_scope"], "source-wide")
        self.assertIn("不证明当前配置目录或 Profile 已验收", result["verification"]["limitation"])
        self.assertIn("会话、任务和时间", result["verification"]["limitation"])
        self.assertNotIn("profile", result["verification"])
        self.assertNotIn("All profiles verified", json.dumps(result))

        instance, _ = self.instance([META, {
            "sources": [{"id": "deepseek-harness", "enabled": True}],
            "verification": {"deepseek-harness": historical},
        }])
        status = instance.run("status", "deepseek-harness")
        self.assertEqual(status["verification"]["evidence_scope"], "source-wide")
        self.assertIn("目标会话完成真实新任务验证", status["verification"]["limitation"])
        self.assertNotIn("previous-profile", json.dumps(status))

    def test_post_requires_apply_before_any_network(self):
        instance, opener = self.instance([])
        for command in ("install", "uninstall", "verify"):
            with self.subTest(command=command), self.assertRaises(client.ClientError) as raised:
                instance.run(command, "codex")
            self.assertEqual(raised.exception.category, "apply_required")
        self.assertFalse(opener.calls)

    def test_install_post_payload_bounded_long_timeout_and_no_verification_claim(self):
        instance, opener = self.instance([META, {"message": "Configured"}])
        result = instance.run("install", "claude-code", "/tmp/claude custom", apply=True)
        request, timeout = opener.calls[2]
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(timeout, 60)
        self.assertEqual(json.loads(request.data), {"source": "claude-code", "config_dir": "/tmp/claude custom"})
        self.assertIn("真实新任务验证", result["note"])
        self.assertNotIn("verification", result)

    def test_verify_local_script_changes_window_only(self):
        status = {"sources": [{"id": "local-script", "enabled": True}],
                  "verification": {"local-script": {"status": "waiting"}}}
        instance, opener = self.instance([META, status])
        result = instance.run("verify", "local-script", apply=True)
        self.assertEqual(opener.calls[2][0].full_url, "http://127.0.0.1:8765/api/verification")
        self.assertEqual(json.loads(opener.calls[2][0].data), {"source": "local-script"})
        self.assertEqual(result["verification"]["status"], "waiting")

    def test_unsupported_source_missing_source_and_wrong_scope_rejected(self):
        instance, opener = self.instance([])
        cases = [("plan", "invalid/source", None, None, False),
                 ("install", "Invalid", None, None, True),
                 ("check", None, None, None, False),
                 ("status", "codex", "/tmp", None, False),
                 ("check", "codex", None, "wrong", False),
                 ("install", "deepseek-harness", None, None, True),
                 ("uninstall", "deepseek-harness", None, None, True)]
        for args in cases:
            with self.subTest(args=args), self.assertRaises(client.ClientError):
                instance.run(*args)
        self.assertFalse(opener.calls)

    def test_http_errors_do_not_leak_response_reason_headers_or_body(self):
        for code in (302, 401, 403, 404, 409, 429, 500):
            error = urllib.error.HTTPError("http://127.0.0.1:8765/secret-url", code, "secret reason",
                                           {"Set-Cookie": "secret cookie"}, io.BytesIO(b'{"error":"secret body"}'))
            instance, _ = self.instance([error])
            with self.subTest(code=code), self.assertRaises(client.ClientError) as raised:
                instance.run("status")
            result = raised.exception.summary()
            self.assertFalse(result["ok"])
            self.assertEqual(result["error"]["http_status"], code)
            self.assertNotIn("secret", json.dumps(result))
            self.assertEqual(result["error"]["category"], "redirect_blocked" if code == 302 else "http_error")

    def test_timeout_after_post_warns_partial_completion_and_check_before_retry(self):
        for error in (socket.timeout("secret timeout"), urllib.error.URLError(socket.timeout("secret timeout"))):
            instance, _ = self.instance([META, error])
            with self.assertRaises(client.ClientError) as raised:
                instance.run("install", "codex", apply=True)
            self.assertEqual(raised.exception.category, "timeout")
            self.assertIn("可能已部分完成", raised.exception.message)
            self.assertIn("check", raised.exception.message)
            self.assertNotIn("secret", raised.exception.message)

    def test_network_and_malformed_json_are_failures_with_no_raw_text(self):
        for error, category in ((urllib.error.URLError("private connection data"), "network_error"),
                                (b"<html>private raw response</html>", "incompatible_response")):
            instance, _ = self.instance([error])
            with self.assertRaises(client.ClientError) as raised:
                instance.run("status")
            self.assertEqual(raised.exception.category, category)
            self.assertNotIn("private", json.dumps(raised.exception.summary()))

    def test_mismatched_plan_and_check_never_report_success(self):
        for command, report in (("plan", {"source_id": "opencode", "files": []}),
                                ("check", {"source_id": "opencode", "checks": []})):
            instance, _ = self.instance([META, report])
            with self.subTest(command=command), self.assertRaises(client.ClientError) as raised:
                instance.run(command, "codex")
            self.assertEqual(raised.exception.category, "incompatible_response")

    def test_mismatched_nested_verification_never_reports_other_source(self):
        instance, _ = self.instance([META, {"sources": [{"id": "codex"}],
            "verification": {"codex": {"source": "claude-code", "status": "verified"}}}])
        with self.assertRaises(client.ClientError) as raised:
            instance.run("status", "codex")
        self.assertEqual(raised.exception.category, "incompatible_response")

    def test_main_default_readonly_and_failure_exit_code(self):
        with mock.patch.object(client, "Client") as factory, contextlib.redirect_stdout(io.StringIO()) as output:
            factory.return_value.run.return_value = {"ok": True}
            self.assertEqual(client.main([]), 0)
            self.assertEqual(factory.return_value.run.call_args.args[0], "status")
        with mock.patch.object(client, "Client") as factory, contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(client.main(["install", "--source", "codex"]), 1)
            factory.assert_not_called()
            self.assertEqual(json.loads(output.getvalue())["error"]["category"], "apply_required")


class CredentialTests(unittest.TestCase):
    def test_paths_match_per_platform_and_isolated_override(self):
        home = Path("/test-home")
        filename = "credentials.json"
        self.assertEqual(client.credentials_path("darwin", home, {}), home / "Library/Application Support/AI Flame" / filename)
        self.assertEqual(client.credentials_path("win32", home, {}), home / "AppData/Local/AI Flame" / filename)
        self.assertEqual(client.credentials_path("win32", home, {"LOCALAPPDATA": "/local-app-data"}), Path("/local-app-data/AI Flame") / filename)
        self.assertEqual(client.credentials_path("linux", home, {}), home / ".local/share/ai-flame" / filename)
        self.assertEqual(client.credentials_path("linux", home, {"XDG_DATA_HOME": "relative"}), home / ".local/share/ai-flame" / filename)
        self.assertEqual(client.credentials_path("linux", home, {"XDG_DATA_HOME": "/xdg"}), Path("/xdg/ai-flame") / filename)
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(client.credentials_path("darwin", home, {"AIFLAME_DATA_DIR": directory}), Path(directory).resolve() / filename)

    def test_reads_only_dashboard_and_never_modifies_private_file(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(os.environ, {"AIFLAME_DATA_DIR": directory}):
            path = Path(directory) / "credentials.json"
            raw = json.dumps({"dashboard": "isolated-dashboard-token", "tokens": {"codex": "unused-source-secret"}}).encode()
            path.write_bytes(raw)
            before = path.stat()
            self.assertEqual(client.read_dashboard_token(), "isolated-dashboard-token")
            self.assertEqual(path.read_bytes(), raw)
            self.assertEqual(path.stat().st_mode, before.st_mode)
            self.assertEqual(path.stat().st_mtime_ns, before.st_mtime_ns)

    def test_missing_invalid_oversized_or_inaccessible_credentials_stop_before_network(self):
        malformed = [None, b"not-json-secret", b"\xff", b"[]", b"{}", b'{"dashboard":1}',
                     b'{"dashboard":""}', b'{"dashboard":"secret\\nheader"}',
                     b'{"dashboard":"' + b"a" * 257 + b'"}', b"x" * (client.MAX_CREDENTIALS + 1)]
        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(os.environ, {"AIFLAME_DATA_DIR": directory}):
            path = Path(directory) / "credentials.json"
            for raw in malformed:
                path.unlink(missing_ok=True)
                if raw is not None:
                    path.write_bytes(raw)
                opener = Opener([])
                with mock.patch.object(urllib.request, "build_opener", return_value=opener):
                    instance = client.Client()
                with self.subTest(raw_size=None if raw is None else len(raw)), self.assertRaises(client.ClientError) as raised:
                    instance.run("status")
                self.assertEqual(raised.exception.category, "credentials_unavailable")
                self.assertEqual(opener.calls, [])
                self.assertNotIn("secret", json.dumps(raised.exception.summary()))
                if raw is None:
                    self.assertFalse(path.exists())
                else:
                    self.assertEqual(path.read_bytes(), raw)
            with mock.patch.object(client.Path, "open", side_effect=PermissionError("private-secret-path")):
                with self.assertRaises(client.ClientError) as raised:
                    client.read_dashboard_token()
                self.assertNotIn("private-secret-path", json.dumps(raised.exception.summary()))


if __name__ == "__main__":
    unittest.main()
