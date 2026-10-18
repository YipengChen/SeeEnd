"""Offline behavior checks for capability discovery and task evidence.

Set SEEEND_APP_ROOT to an App checkout to check its actual declarative contract.
The optional check imports only the registry; it never starts a Hub or loads config.
"""

import copy
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
import urllib.error

import test_client
from test_client import CONTRACT, META, STATUS, client


class ContractTests(unittest.TestCase):
    def instance(self, outcomes, **options):
        return test_client.ClientTests.instance(self, outcomes, **options)

    def test_standalone_installation_preserves_the_repository_license(self):
        root = Path(__file__).resolve().parents[1]
        self.assertEqual((root / "seeend/LICENSE").read_bytes(), (root / "LICENSE").read_bytes())

    def test_capabilities_allow_new_native_source_without_client_list_changes(self):
        contract = copy.deepcopy(CONTRACT)
        contract["app_version"] = META["app_version"]
        contract["sources"].append({"id": "future-cli", "authenticated": True})
        contract["capabilities"]["native_sources"].append("future-cli")
        instance, opener = self.instance([META, contract, {"source_id": "future-cli", "files": []}], contract=False)
        result = instance.run("plan", "future-cli")
        self.assertEqual(result["plan"]["source_id"], "future-cli")
        self.assertEqual(opener.calls[-1][0].get_method(), "GET")

    def test_unsupported_operation_is_rejected_after_discovery_before_target_request(self):
        for command, source in (("plan", "local-script"), ("install", "unknown")):
            instance, opener = self.instance([META])
            with self.subTest(command=command), self.assertRaises(client.ClientError) as raised:
                instance.run(command, source, apply=True)
            self.assertEqual(raised.exception.category, "unsupported_source")
            self.assertEqual(len(opener.calls), 2)
            self.assertTrue(all(request.get_method() == "GET" for request, _ in opener.calls))

    def test_incompatible_contract_never_permits_writes(self):
        changes = [
            {"contract_version": 2}, {"contract_version": True}, {"schema_version": "2.0"},
            {"app_version": "wrong-version"}, {"required_fields": ["event_id"]},
            {"capabilities": {"native_sources": ["unregistered"], "verification_evidence": True, "deepseek_config_dir": False}},
        ]
        for change in changes:
            contract = copy.deepcopy(CONTRACT)
            contract["app_version"] = META["app_version"]
            contract.update(change)
            instance, opener = self.instance([META, contract], contract=False)
            with self.subTest(change=change), self.assertRaises(client.ClientError) as raised:
                instance.run("install", "codex", apply=True)
            self.assertEqual(raised.exception.category, "incompatible_contract")
            self.assertEqual(len(opener.calls), 2)
            self.assertTrue(all(request.get_method() == "GET" for request, _ in opener.calls))

    def test_current_contract_supports_read_only_discovery(self):
        for command, response in (("status", STATUS), ("plan", {"source_id": "codex", "files": []}),
                                  ("check", {"source_id": "codex", "checks": []})):
            instance, opener = self.instance([META, copy.deepcopy(CONTRACT), response], contract=False)
            with self.subTest(command=command):
                result = instance.run(command, "codex")
                self.assertTrue(result["ok"])
                self.assertTrue(all(request.get_method() == "GET" for request, _ in opener.calls))
        parsed = client.contract_summary(CONTRACT, META["app_version"])
        self.assertEqual(set(parsed["optional_fields"]), {"reason", "device_id", "return_target", "return_context"})
        self.assertNotIn("error", parsed["states"])

    def test_status_keeps_current_reminder_channel_switches_only(self):
        status = copy.deepcopy(STATUS)
        switches = {"desktop_flame_enabled": True, "desktop_notifications": False,
                    "sound_notifications": False, "sound_category": "chime",
                    "wechat_notifications": False, "hardware_notifications": False}
        status["settings"] = {**switches, "private_setting": "private-content"}
        instance, _ = self.instance([META, status])
        result = instance.run("status", "codex")
        self.assertEqual(result["outputs"]["settings"], switches)
        self.assertNotIn("private-content", json.dumps(result))

    def test_known_older_contract_remains_supported(self):
        meta = {"app_name": "SeeEnd", "app_version": "0.8.6"}
        contract = copy.deepcopy(CONTRACT)
        contract.update(app_version="0.8.6", optional_fields=["reason", "device_id"])
        contract["states"].append("error")
        for command, response in (("status", STATUS), ("install", {"message": "configured"})):
            instance, opener = self.instance([meta, contract, response], contract=False)
            with self.subTest(command=command):
                self.assertTrue(instance.run(command, "codex", apply=command == "install")["ok"])
                self.assertEqual(opener.calls[-1][0].get_method(), "POST" if command == "install" else "GET")
        evidence = {"source": "codex", "since": 0, "events": [
            {"event_id": "old-event", "task_id": "old-task", "seq": 2, "state": "error",
             "reason": "failed", "created": 1, "simulated": False, "status": "applied"}]}
        self.assertEqual(client.evidence_summary(evidence, "codex")["events"][0]["state"], "error")

    def test_unknown_or_missing_contract_fields_stop_before_write(self):
        changes = [
            {"states": CONTRACT["states"] + ["future-state"]},
            {"states": [state for state in CONTRACT["states"] if state != "working"]},
            {"optional_fields": CONTRACT["optional_fields"] + ["private-field"]},
            {"optional_fields": ["reason", "return_target", "return_context"]},
        ]
        for change in changes:
            contract = copy.deepcopy(CONTRACT)
            contract.update(change)
            instance, opener = self.instance([META, contract], contract=False)
            with self.subTest(change=change), self.assertRaises(client.ClientError) as raised:
                instance.run("install", "codex", apply=True)
            self.assertEqual(raised.exception.category, "incompatible_contract")
            self.assertEqual(len(opener.calls), 2)
            self.assertTrue(all(request.get_method() == "GET" for request, _ in opener.calls))

    def test_known_legacy_contract_missing_allows_read_only_but_blocks_writes(self):
        def missing():
            return urllib.error.HTTPError("http://127.0.0.1:8765/api/integration-contract", 404, "private", {}, io.BytesIO(b"{}"))

        legacy_meta = {"app_name": "SeeEnd", "app_version": "0.8.4"}
        instance, opener = self.instance([legacy_meta, missing(), STATUS], contract=False)
        result = instance.run("status", "codex")
        self.assertEqual(result["compatibility"], "legacy-read-only")
        self.assertEqual(len(opener.calls), 3)
        instance, opener = self.instance([legacy_meta, missing()], contract=False)
        with self.assertRaises(client.ClientError) as raised:
            instance.run("install", "codex", apply=True)
        self.assertEqual(raised.exception.category, "incompatible_contract")
        self.assertEqual(len(opener.calls), 2)
        instance, opener = self.instance([{"app_name": "SeeEnd", "app_version": "99.0"}, missing()], contract=False)
        with self.assertRaises(client.ClientError):
            instance.run("install", "codex", apply=True)
        self.assertEqual(len(opener.calls), 2)

    def test_deepseek_config_dir_is_never_silently_ignored(self):
        instance, opener = self.instance([])
        for command in ("plan", "check", "install", "uninstall"):
            with self.subTest(command=command), self.assertRaises(client.ClientError) as raised:
                instance.run(command, "deepseek-harness", "/tmp/another-root", "actual", apply=True)
            self.assertEqual(raised.exception.category, "invalid_argument")
            self.assertIn("DSH_HOME", raised.exception.message)
        self.assertFalse(opener.calls)

    def test_check_preserves_existing_profile_choices_without_unexpected_objects(self):
        value = {"source_id": "deepseek-harness", "checks": [], "profiles": ["work", {"private": "private"}],
                 "installed_profiles": ["work"], "recorded_profiles": ["work", "old"], "payload": "private"}
        instance, _ = self.instance([META, value])
        result = instance.run("check", "deepseek-harness")["check"]
        self.assertEqual(result["profiles"], ["work"])
        self.assertEqual(result["installed_profiles"], ["work"])
        self.assertEqual(result["recorded_profiles"], ["work", "old"])
        self.assertNotIn("private", json.dumps(result))

    def test_http_conflict_uses_only_allowlisted_error_code(self):
        for error_code in ("plugin_network", "config_invalid", "unknown-private-code"):
            error = urllib.error.HTTPError("http://127.0.0.1:8765/api/integrations/install", 409, "private reason",
                                           {"Set-Cookie": "private cookie"},
                                           io.BytesIO(json.dumps({"error_code": error_code, "error": "private response secret"}).encode()))
            instance, _ = self.instance([META, error])
            with self.subTest(error_code=error_code), self.assertRaises(client.ClientError) as raised:
                instance.run("install", "codex", apply=True)
            result = raised.exception.summary()
            self.assertNotIn("private", json.dumps(result))
            self.assertEqual(result["error"].get("code"), error_code if error_code in client.ERROR_MESSAGES else None)

    def test_evidence_keeps_same_task_metadata_and_excludes_nonqualifying_events(self):
        def event(identity, task="task-current", seq=1, **extra):
            return {"event_id": identity, "task_id": task, "seq": seq, "state": "working" if seq == 1 else "done",
                    "reason": None if seq == 1 else "completed", "created": 110 + seq, "simulated": False,
                    "status": "applied", "payload": "private transcript", **extra}

        value = {"source": "codex", "since": 100, "summary": {"source": "codex", "status": "verified"},
                 "events": [event("start"), event("end", seq=2), event("simulated", simulated=True),
                            event("ignored", status="unmatched"), event("old", created=99)], "tokens": {"codex": "private-token"}}
        instance, opener = self.instance([META, value])
        result = instance.run("evidence", "codex")["evidence"]
        self.assertEqual([event["event_id"] for event in result["events"]], ["start", "end"])
        self.assertEqual([event["task_id"] for event in result["events"]], ["task-current", "task-current"])
        self.assertEqual([event["seq"] for event in result["events"]], [1, 2])
        self.assertNotIn("private", json.dumps(result))
        self.assertTrue(all(request.get_method() == "GET" for request, _ in opener.calls))
        self.assertIn("source=codex", opener.calls[-1][0].full_url)

    def test_evidence_rejects_other_source_or_invalid_metadata(self):
        for value in ({"source": "claude-code", "events": []},
                      {"source": "codex", "events": [{"event_id": "invalid"}]}):
            instance, _ = self.instance([META, value])
            with self.subTest(value=value), self.assertRaises(client.ClientError) as raised:
                instance.run("evidence", "codex")
            self.assertEqual(raised.exception.category, "incompatible_response")

    def test_evidence_reports_truncation_and_retention_limits(self):
        value = {"source": "codex", "since": 100, "summary": {"source": "codex", "status": "verified"},
                 "events": [], "total": 1500, "limit": 1000, "truncated": True,
                 "retention": {"days": 7, "max_events": 10000, "payload": "private"}}
        instance, _ = self.instance([META, value])
        result = instance.run("evidence", "codex")["evidence"]
        self.assertTrue(result["truncated"])
        self.assertEqual(result["total"], 1500)
        self.assertEqual(result["retention"], {"days": 7, "max_events": 10000})
        self.assertIn("已截断", result["limitation"])
        self.assertNotIn("private", json.dumps(result))

    def test_contract_command_returns_only_public_declarative_metadata(self):
        contract = copy.deepcopy(CONTRACT)
        contract["app_version"] = META["app_version"]
        contract["token"] = "private-token"
        instance, opener = self.instance([META, contract], contract=False)
        result = instance.run("contract")
        self.assertEqual(result["contract"]["schema_version"], "1.0")
        self.assertNotIn("private-token", json.dumps(result))
        self.assertEqual(len(opener.calls), 2)


class AppContractTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get("SEEEND_APP_ROOT"), "Set SEEEND_APP_ROOT for optional cross-repository checks")
    def test_actual_app_contract_matches_client_and_test_snapshot(self):
        self.assertGreaterEqual(sys.version_info[:2], (3, 11), "Cross-repository checks require the App's supported Python (>=3.11)")
        root = Path(os.environ["SEEEND_APP_ROOT"]).expanduser().resolve()
        self.assertTrue((root / "aiflame/integrations/registry.py").is_file(), "SEEEND_APP_ROOT must point to the App repository root")
        environment = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": str(root)}
        result = subprocess.run([sys.executable, "-c", "import json; from aiflame.integrations.registry import integration_contract; print(json.dumps(integration_contract()))"],
                                cwd=str(root), env=environment, capture_output=True, text=True, timeout=15, check=True)
        actual = json.loads(result.stdout)
        parsed = client.contract_summary(actual, actual["app_version"])
        self.assertEqual(parsed["contract_version"], CONTRACT["contract_version"])
        self.assertEqual(set(parsed["required_fields"]), set(CONTRACT["required_fields"]))
        self.assertEqual(set(parsed["optional_fields"]), set(CONTRACT["optional_fields"]))
        self.assertEqual(set(parsed["states"]), set(CONTRACT["states"]))
        self.assertEqual(set(parsed["reasons"]), set(CONTRACT["reasons"]))
        self.assertEqual(set(parsed["capabilities"]["native_sources"]), set(CONTRACT["capabilities"]["native_sources"]))
        by_source = {source["id"]: source for source in parsed["sources"]}
        for expected in CONTRACT["sources"]:
            for field, value in expected.items():
                self.assertEqual(by_source[expected["id"]][field], value)


if __name__ == "__main__":
    unittest.main()
