"""Credential isolation, native vault contracts and encrypted plug control."""
import base64
import ctypes
import hashlib
import json
from pathlib import Path
import socket
import struct
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.decrepit.ciphers.algorithms import ARC4
from cryptography.hazmat.primitives import padding
from aiflame.core.events import EventError
from aiflame.devices.mijia import PlugClient, HELLO, parse_hello, lan_host
from aiflame.devices.mijia_crypto import rc4, aes
from aiflame.devices.mijia_cloud import MijiaLogin, QRCloud, xiaomi_url
from aiflame.devices.token_store import TokenStore
from aiflame.devices.models import MIJIA_PLUG_MODEL
from aiflame.devices.light_settings import light_settings, validate_patch
from aiflame.transports.mijia import desired_power

TOKEN = "0123456789abcdef0123456789abcdef"


def reference_aes(data, key, iv, decrypt=False):
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    transform = cipher.decryptor() if decrypt else cipher.encryptor()
    if not decrypt:
        padder = padding.PKCS7(128).padder()
        data = padder.update(data) + padder.finalize()
    result = transform.update(data) + transform.finalize()
    if decrypt:
        padder = padding.PKCS7(128).unpadder()
        result = padder.update(result) + padder.finalize()
    return result


class WireSocket:
    def __init__(self):
        self.power = False
        self.frames, self.responses = [], []
        self.tamper = False
        self.identity = 123

    def connect(self, address):
        self.address = address

    def settimeout(self, value):
        pass

    def send(self, packet):
        if packet == HELLO:
            self.responses.append(struct.pack(">HHIII", 0x2131, 32, 0, self.identity, 100) + bytes(16))
            return
        token = bytes.fromhex(TOKEN)
        key = hashlib.md5(token).digest()
        iv = hashlib.md5(key + token).digest()
        assert packet[16:32] == hashlib.md5(packet[:16] + token + packet[32:]).digest()
        data = json.loads(reference_aes(packet[32:], key, iv, True))
        self.frames.append(data)
        if data["method"] == "set_properties":
            self.power = data["params"][0]["value"]
        rows = [{"did": "123", "siid": 2, "piid": 1, "code": 0}]
        if data["method"] == "get_properties":
            rows[0]["value"] = self.power
        payload = reference_aes(json.dumps({"id": data["id"], "result": rows}).encode(), key, iv)
        header = struct.pack(">HHIII", 0x2131, len(payload) + 32, 0, 123, 101)
        checksum = hashlib.md5(header + token + payload).digest()
        self.responses.append(header + (bytes(16) if self.tamper else checksum) + payload)

    def recv(self, size):
        return self.responses.pop(0)

    def close(self):
        pass


class FakeCloud:
    def __init__(self):
        self.closed = False

    def begin(self):
        return {"image": "", "login_url": "https://account.xiaomi.com/test", "poll_url": "https://account.xiaomi.com/poll", "timeout": 30}

    def poll(self, url, timeout=120):
        pass

    def authorize(self):
        pass

    def plugs(self, region, stopping):
        return {"123": {"did": "123", "name": "Desk lamp", "host": "192.168.1.2", "token": TOKEN},
                "456": {"did": "456", "name": "Other plug", "host": "192.168.1.3", "token": "abcdef0123456789abcdef0123456789"}}

    def close(self):
        self.closed = True


class MijiaTests(unittest.TestCase):
    def test_cloud_rc4_matches_independent_drop_1024_cipher(self):
        key, data = bytes(range(16)), b"a cloud payload"
        encryptor = Cipher(ARC4(key), mode=None).encryptor()
        reference = encryptor.update(bytes(1024) + data)[1024:]
        self.assertEqual(rc4(data, key), reference)
        self.assertEqual(rc4(reference, key), data)

    def test_cloud_request_signature_and_encrypted_response(self):
        cloud = QRCloud()
        cloud.user_id, cloud.service_token = "42", "test-session"
        secret = bytes(range(32))
        cloud.security = base64.b64encode(secret).decode()
        path, data = "/v2/homeroom/gethome", {"fg": True, "limit": 300}
        nonce = bytes(range(8)) + (120).to_bytes(4, "big")
        key = hashlib.sha256(secret + nonce).digest()
        signed_nonce = base64.b64encode(key).decode()

        def reference_rc4(payload):
            cipher = Cipher(ARC4(key), mode=None).encryptor()
            return cipher.update(bytes(1024) + payload)[1024:]

        def fetch(url, fields, headers):
            self.assertEqual(url, "https://de.api.io.mi.com/app" + path)
            self.assertEqual(fields["_nonce"], base64.b64encode(nonce).decode())
            plaintext = reference_rc4(base64.b64decode(fields["data"])).decode()
            self.assertEqual(json.loads(plaintext), data)
            initial = "&".join(["POST", path, "data=" + plaintext, signed_nonce])
            inner = base64.b64encode(hashlib.sha1(initial.encode()).digest()).decode()
            self.assertEqual(reference_rc4(base64.b64decode(fields["rc4_hash__"])).decode(), inner)
            outer = "&".join(["POST", path, "data=" + fields["data"],
                              "rc4_hash__=" + fields["rc4_hash__"], signed_nonce])
            self.assertEqual(fields["signature"], base64.b64encode(hashlib.sha1(outer.encode()).digest()).decode())
            self.assertEqual(headers["MIOT-ENCRYPT-ALGORITHM"], "ENCRYPT-RC4")
            return base64.b64encode(reference_rc4(b'{"code":0,"result":{"homelist":[]}}'))

        with patch("aiflame.devices.mijia_cloud.os.urandom", return_value=bytes(range(8))), \
                patch("aiflame.devices.mijia_cloud.time.time", return_value=7200), patch.object(cloud, "fetch", side_effect=fetch):
            self.assertEqual(cloud.api("de", path, data), {"homelist": []})
        cloud.close()

    def test_cloud_filters_credentials_to_supported_owned_plugs(self):
        cloud = QRCloud()
        cloud.user_id = "42"
        devices = [{"did": "123", "model": "cuco.plug.v3", "name": "Lamp", "token": TOKEN},
                   {"did": "456", "model": "camera.other", "token": TOKEN},
                   {"did": "789", "model": "cuco.plug.v3", "token": "0" * 32}]
        with patch.object(cloud, "api", side_effect=[{"homelist": [{"id": 1}]}, {"device_info": devices}]):
            self.assertEqual(set(cloud.plugs("cn", threading.Event())), {"123"})
        cloud.close()

    def test_public_addresses_cannot_receive_plug_commands(self):
        self.assertEqual(lan_host("192.168.1.2"), "192.168.1.2")
        for host in ("8.8.8.8", "127.0.0.1", "255.255.255.255"):
            with self.assertRaises(ValueError):
                lan_host(host)

    def test_lan_readback_and_authenticated_packets(self):
        connection = WireSocket()
        with patch("aiflame.devices.mijia.socket.socket", return_value=connection):
            client = PlugClient("192.168.1.2", TOKEN, "123").open()
            self.assertFalse(client.power())
            client.set_power(True)
            self.assertTrue(client.power())
            self.assertEqual(connection.address, ("192.168.1.2", 54321))
            connection.tamper = True
            with self.assertRaisesRegex(ValueError, "校验失败"):
                client.power()
            client.close()

    def test_wrong_physical_identity_cannot_receive_power_commands(self):
        connection = WireSocket()
        connection.identity = 456
        with patch("aiflame.devices.mijia.socket.socket", return_value=connection):
            with self.assertRaisesRegex(ValueError, "另一台"):
                PlugClient("192.168.1.2", TOKEN, "123").open()
        self.assertEqual(connection.frames, [])

    def test_login_selection_hides_and_discards_other_device_tokens(self):
        cloud = FakeCloud()
        login = MijiaLogin(factory=lambda: cloud)
        session = login.start()
        end = time.monotonic() + 2
        while login.status()["state"] != "selecting" and time.monotonic() < end:
            time.sleep(.005)
        self.assertTrue(cloud.closed)
        public = json.dumps(login.status())
        self.assertNotIn(TOKEN, public)
        self.assertNotIn("token", public.lower())
        with self.assertRaises(EventError):
            login.take("wrong-session", "123")
        chosen = login.take(session["id"], "123")
        self.assertEqual(chosen["token"], TOKEN)
        self.assertEqual(login.devices, {})
        self.assertEqual(login.status()["devices"], [])
        with self.assertRaises(EventError):
            login.take(session["id"], "456")
        login.thread.join(1)

    def wait_for_login(self, login, state):
        end = time.monotonic() + 3
        while login.status()["state"] != state and time.monotonic() < end:
            time.sleep(.005)
        self.assertEqual(login.status()["state"], state)

    def test_confirmation_is_not_polled_again_after_callback_timeout(self):
        import http.cookiejar
        from urllib.error import URLError
        from unittest.mock import Mock
        cloud = QRCloud()
        cloud.begin = Mock(return_value=FakeCloud().begin())
        cloud.plugs = Mock(return_value=FakeCloud().plugs("cn", threading.Event()))
        confirmations, callbacks, timeouts = [], [], []
        callback_started = threading.Event()
        payload = json.dumps({"userId": "42", "ssecurity": base64.b64encode(bytes(32)).decode(),
                              "location": "https://sts.api.io.mi.com/sts"}).encode()
        def fetch(url, **kwargs):
            if url == "https://account.xiaomi.com/poll":
                confirmations.append(url)
                timeouts.append(kwargs["timeout"])
                if len(confirmations) > 1:
                    raise AssertionError("confirmation has already been consumed")
                return payload
            callbacks.append(url)
            callback_started.set()
            if len(callbacks) == 1:
                raise URLError(TimeoutError("temporary STS timeout"))
            cloud.cookies.set_cookie(http.cookiejar.Cookie(
                version=0, name="serviceToken", value="test-session", port=None, port_specified=False,
                domain="sts.api.io.mi.com", domain_specified=True, domain_initial_dot=False,
                path="/", path_specified=True, secure=True, expires=None, discard=True,
                comment=None, comment_url=None, rest={}))
            return b"ok"
        cloud.fetch = Mock(side_effect=fetch)
        login = MijiaLogin(factory=lambda: cloud)
        try:
            login.start()
            self.assertTrue(callback_started.wait(1))
            self.assertEqual(login.status()["state"], "authorizing")
            self.assertIn("手机已确认", login.status()["message"])
            self.assertEqual(login.status()["qr_image"], "")
            self.wait_for_login(login, "selecting")
            self.assertEqual(len(confirmations), 1)
            self.assertEqual(len(callbacks), 2)
            self.assertGreater(timeouts[0], 10)
            self.assertLessEqual(timeouts[0], 30)
            self.assertIsNone(cloud.location)
            self.assertIsNone(cloud.security)
            self.assertNotIn(TOKEN, json.dumps(login.status()))
        finally:
            login.cancel()
            login.thread.join(1)

    def test_query_timeout_retries_after_one_confirmation(self):
        from unittest.mock import Mock
        cloud = FakeCloud()
        cloud.poll = Mock()
        cloud.authorize = Mock()
        devices = cloud.plugs("cn", threading.Event())
        cloud.plugs = Mock(side_effect=[TimeoutError("temporary query timeout"), devices])
        login = MijiaLogin(factory=lambda: cloud)
        try:
            login.start()
            self.wait_for_login(login, "selecting")
            self.assertEqual(cloud.poll.call_count, 1)
            cloud.authorize.assert_called_once()
            self.assertEqual(cloud.plugs.call_count, 2)
        finally:
            login.cancel()
            login.thread.join(1)

    def test_failed_authorization_is_visible_after_confirmation(self):
        from unittest.mock import Mock
        cloud = FakeCloud()
        cloud.poll = Mock()
        cloud.authorize = Mock(side_effect=ValueError("missing callback credential"))
        cloud.plugs = Mock()
        login = MijiaLogin(factory=lambda: cloud)
        login.start()
        self.wait_for_login(login, "failed")
        login.thread.join(1)
        self.assertEqual(cloud.poll.call_count, 1)
        cloud.plugs.assert_not_called()
        self.assertIn("手机已确认登录", login.status()["message"])
        self.assertEqual(login.status()["qr_image"], "")
        self.assertTrue(cloud.closed)

    def test_authorization_retries_are_bounded_without_repolling_confirmation(self):
        from unittest.mock import Mock
        cloud = FakeCloud()
        cloud.poll = Mock()
        cloud.authorize = Mock(side_effect=TimeoutError("callback unavailable"))
        cloud.plugs = Mock()
        login = MijiaLogin(factory=lambda: cloud)
        login.start()
        self.wait_for_login(login, "failed")
        login.thread.join(1)
        self.assertEqual(cloud.poll.call_count, 1)
        self.assertEqual(cloud.authorize.call_count, 3)
        cloud.plugs.assert_not_called()
        self.assertEqual(login.devices, {})
        self.assertIn("手机已确认登录", login.status()["message"])
        self.assertTrue(cloud.closed)

    def test_cancel_during_authorization_prevents_device_query(self):
        from unittest.mock import Mock
        entered, release = threading.Event(), threading.Event()
        cloud = FakeCloud()
        def authorize():
            entered.set()
            if not release.wait(2):
                raise AssertionError("test did not release authorization")
        cloud.authorize = authorize
        cloud.plugs = Mock()
        login = MijiaLogin(factory=lambda: cloud)
        session = login.start()
        try:
            self.assertTrue(entered.wait(1))
            login.cancel(session["id"])
        finally:
            release.set()
            login.thread.join(1)
        self.assertFalse(login.thread.is_alive())
        self.assertEqual(login.status()["state"], "cancelled")
        self.assertEqual(login.devices, {})
        cloud.plugs.assert_not_called()
        self.assertTrue(cloud.closed)

    def test_cancel_and_stale_session_cannot_import(self):
        login = MijiaLogin(factory=FakeCloud)
        session = login.start()
        time.sleep(.03)
        login.cancel(session["id"])
        login.thread.join(1)
        self.assertEqual(login.devices, {})
        self.assertEqual(login.status()["state"], "cancelled")
        with self.assertRaises(EventError):
            login.take(session["id"], "123")

    def test_cloud_rejects_insecure_or_non_xiaomi_endpoints(self):
        for url in ("http://account.xiaomi.com/", "https://mi.com.evil.test/", "https://evilmi.com/",
                    "https://user:password@account.xiaomi.com/", "https://account.xiaomi.com:8080/"):
            with self.assertRaises(ValueError):
                xiaomi_url(url)
        self.assertEqual(xiaomi_url("https://cn.api.io.mi.com/app"), "https://cn.api.io.mi.com/app")

    def test_windows_storage_contains_ciphertext_only_and_no_fallback(self):
        with tempfile.TemporaryDirectory() as runtime:
            store = TokenStore(runtime)
            encoded = b"opaque protected bytes"
            with patch("aiflame.devices.token_store.sys.platform", "win32"), patch.object(store, "_dpapi") as native:
                native.return_value = encoded
                store.set("miio:123", TOKEN)
                saved = store._path("miio:123").read_text()
                self.assertNotIn(TOKEN, saved)
                self.assertNotIn("token", json.loads(saved))
                native.return_value = TOKEN.encode()
                self.assertEqual(store.get("miio:123"), TOKEN)
                native.assert_called_with(encoded, "miio:123", decrypt=True)
                native.side_effect = RuntimeError("DPAPI unavailable")
                with self.assertRaises(RuntimeError):
                    store.set("miio:456", TOKEN)
                self.assertFalse(store._path("miio:456").exists())
                store.delete("miio:123")
                self.assertFalse(store._path("miio:123").exists())

    def test_windows_dpapi_uses_current_user_context_and_frees_native_output(self):
        store = TokenStore("/tmp/test-vault", service="test-service")
        outputs, inputs, releases = [], [], []

        def protect(source, description, entropy, reserved, prompt, flags, result):
            self.assertEqual(flags, 1)  # UI_FORBIDDEN, without LOCAL_MACHINE.
            self.assertIsNone(prompt)
            inputs.append(source._obj)
            self.assertEqual(ctypes.string_at(entropy._obj.data, entropy._obj.size), b"test-service:miio:123")
            output = ctypes.create_string_buffer(b"native encrypted data")
            outputs.append(output)
            result._obj.size = len(b"native encrypted data")
            result._obj.data = ctypes.cast(output, ctypes.POINTER(ctypes.c_ubyte))
            return True

        def release(pointer):
            self.assertEqual(ctypes.string_at(pointer, len(b"native encrypted data")), bytes(len(b"native encrypted data")))
            releases.append(pointer)

        from unittest.mock import Mock
        crypto, kernel = Mock(), Mock()
        crypto.CryptProtectData = Mock(side_effect=protect)
        kernel.LocalFree = Mock(side_effect=release)
        with patch.object(ctypes, "WinDLL", side_effect=[crypto, kernel], create=True):
            self.assertEqual(store._dpapi(TOKEN.encode(), "miio:123"), b"native encrypted data")
        self.assertEqual(len(releases), 1)
        self.assertEqual(ctypes.string_at(inputs[0].data, inputs[0].size), bytes(len(TOKEN)))

    @unittest.skipUnless(sys.platform == "win32", "Requires native Windows DPAPI")
    def test_real_windows_dpapi_roundtrip_and_tamper(self):
        with tempfile.TemporaryDirectory() as runtime:
            store = TokenStore(runtime)
            store.set("miio:123", TOKEN)
            self.assertEqual(store.get("miio:123"), TOKEN)
            cipher = store._dpapi(TOKEN.encode(), "miio:123")
            with self.assertRaises(RuntimeError):
                store._dpapi(cipher, "miio:456", decrypt=True)
            store.delete("miio:123")

    def test_continuous_state_preview_respects_configured_power(self):
        for state in ('working', 'needs_input', 'done'):
            for power in (True, False):
                with self.subTest(state=state, power=power):
                    settings = {'enabled': True, 'state_power': {state: power}}
                    preview = {'state': state, 'device_test': True, 'continuous': True}
                    self.assertEqual(desired_power(preview, settings), power)
                    self.assertEqual(desired_power({'state': state}, settings), power)
                    self.assertEqual(desired_power({'state': state, 'device_test': True}, settings),
                                     state == 'working')

    def test_plug_alerts_are_enabled_even_with_legacy_disabled_settings(self):
        record = {"hardware_model": MIJIA_PLUG_MODEL}
        settings = light_settings(record)
        self.assertFalse(desired_power({"state": "working"}, settings))
        for state in ("done", "needs_input", "needs_input"):
            self.assertTrue(desired_power({"state": state}, settings))
        self.assertFalse(desired_power({"state": "idle"}, settings))
        validate_patch({"state_power": {"working": True}}, MIJIA_PLUG_MODEL)
        record["light_settings"] = {"enabled": False}
        self.assertEqual(light_settings(record)["brightness"], 100)
        self.assertTrue(desired_power({"state": "done"}, light_settings(record)))
        for patch_data in ({"enabled": False}, {"brightness": 0}, {"state_power": {"idle": True}}, {"state_power": {"done": 1}}):
            with self.assertRaises(EventError):
                validate_patch(patch_data, MIJIA_PLUG_MODEL)
