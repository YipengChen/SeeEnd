"""Device tokens in authenticated local ciphertext; legacy platform stores for migration."""
import ctypes
import hashlib
import base64
import json
import re
import sys
import os
import secrets
import uuid

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.exceptions import InvalidTag
from pathlib import Path

from aiflame.core.paths import runtime_dir, write_private


def valid_token(value):
    return isinstance(value, str) and bool(re.fullmatch(r"[0-9a-fA-F]{32}", value)) and value.lower() not in ("0" * 32, "f" * 32)


class TokenStore:
    def __init__(self, runtime=None, service=None):
        self.runtime = Path(runtime) if runtime is not None else runtime_dir()
        self.service = service or ("com.seeend.miio." + hashlib.sha256(str(self.runtime).encode()).hexdigest()[:16])

    def _path(self, account):
        name = hashlib.sha256((self.service + ":" + account).encode()).hexdigest() + ".json"
        return self.runtime / "device-tokens" / name

    def _dpapi(self, data, account, decrypt=False):
        from ctypes import wintypes

        class Blob(ctypes.Structure):
            _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]

        library = ctypes.WinDLL("crypt32", use_last_error=True)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        function = library.CryptUnprotectData if decrypt else library.CryptProtectData
        function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.POINTER(Blob),
                             ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
        function.restype = wintypes.BOOL
        kernel.LocalFree.argtypes, kernel.LocalFree.restype = [ctypes.c_void_p], ctypes.c_void_p
        clear = ctypes.create_string_buffer(data)
        entropy_bytes = (self.service + ":" + account).encode()
        entropy_buffer = ctypes.create_string_buffer(entropy_bytes)
        source = Blob(len(data), ctypes.cast(clear, ctypes.POINTER(ctypes.c_ubyte)))
        entropy = Blob(len(entropy_bytes), ctypes.cast(entropy_buffer, ctypes.POINTER(ctypes.c_ubyte)))
        output = Blob()
        try:
            # No LOCAL_MACHINE flag: bound to the current Windows user.
            if not function(ctypes.byref(source), None, ctypes.byref(entropy), None, None, 1, ctypes.byref(output)):
                raise RuntimeError("Windows 无法解密设备凭据，请使用原用户登录或重新扫码" if decrypt else
                                   "Windows 无法保护设备凭据，未保存 Token，请检查系统后重试")
            return ctypes.string_at(output.data, output.size)
        finally:
            ctypes.memset(clear, 0, len(data))
            if output.data:
                ctypes.memset(output.data, 0, output.size)
                kernel.LocalFree(output.data)

    def _windows(self, action, account, token=None):
        path = self._path(account)
        if action == "delete":
            path.unlink(missing_ok=True)
        elif action == "get":
            if not path.exists():
                return None
            try:
                saved = json.loads(path.read_text(encoding="utf-8"))
                if saved.get("version") != 1:
                    raise ValueError()
                return self._dpapi(base64.b64decode(saved["ciphertext"], validate=True), account, decrypt=True).decode()
            except (ValueError, KeyError, UnicodeError, TypeError, AttributeError):
                raise RuntimeError("插座凭据文件无效，请重新扫码") from None
        else:
            encrypted = self._dpapi(token.encode(), account)
            write_private(path, {"version": 1, "ciphertext": base64.b64encode(encrypted).decode()})

    def _call(self, action, account, token=None, *, noninteractive=False):
        if sys.platform == "win32":
            return self._windows(action, account, token)
        if sys.platform != "darwin":
            raise RuntimeError("米家凭据保存目前支持 macOS 和 Windows")
        cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        security = ctypes.CDLL("/System/Library/Frameworks/Security.framework/Security")
        ptr = ctypes.c_void_p
        cf.CFStringCreateWithCString.argtypes = [ptr, ctypes.c_char_p, ctypes.c_uint32]
        cf.CFStringCreateWithCString.restype = ptr
        cf.CFDataCreate.argtypes = [ptr, ptr, ctypes.c_long]
        cf.CFDataCreate.restype = ptr
        cf.CFDictionaryCreate.argtypes = [ptr, ptr, ptr, ctypes.c_long, ptr, ptr]
        cf.CFDictionaryCreate.restype = ptr
        cf.CFRelease.argtypes = [ptr]
        cf.CFDataGetLength.argtypes = [ptr]
        cf.CFDataGetLength.restype = ctypes.c_long
        cf.CFDataGetBytePtr.argtypes = [ptr]
        cf.CFDataGetBytePtr.restype = ptr
        for name, args in (("SecItemAdd", [ptr, ptr]), ("SecItemUpdate", [ptr, ptr]),
                           ("SecItemCopyMatching", [ptr, ptr]), ("SecItemDelete", [ptr])):
            function = getattr(security, name)
            function.argtypes, function.restype = args, ctypes.c_int32
        owned = []

        def constant(name):
            return ptr.in_dll(security, name).value

        def string(value):
            result = cf.CFStringCreateWithCString(None, value.encode(), 0x08000100)
            owned.append(result)
            return result

        def dictionary(values):
            keys = (ptr * len(values))(*values.keys())
            items = (ptr * len(values))(*values.values())
            result = cf.CFDictionaryCreate(None, keys, items, len(values),
                ctypes.addressof(ctypes.c_byte.in_dll(cf, "kCFTypeDictionaryKeyCallBacks")),
                ctypes.addressof(ctypes.c_byte.in_dll(cf, "kCFTypeDictionaryValueCallBacks")))
            owned.append(result)
            return result

        try:
            values = {constant("kSecClass"): constant("kSecClassGenericPassword"),
                      constant("kSecAttrService"): string(self.service),
                      constant("kSecAttrAccount"): string(account)}
            if noninteractive:
                values[constant("kSecUseAuthenticationUI")] = constant("kSecUseAuthenticationUIFail")
            if action == "get":
                values[constant("kSecReturnData")] = ptr.in_dll(cf, "kCFBooleanTrue").value
                result = ptr()
                status = security.SecItemCopyMatching(dictionary(values), ctypes.byref(result))
                if status == -25300:
                    return None
                if status == 0:
                    owned.append(result.value)
                    return ctypes.string_at(cf.CFDataGetBytePtr(result), cf.CFDataGetLength(result)).decode()
            elif action == "delete":
                status = security.SecItemDelete(dictionary(values))
                if status == -25300:
                    return None
            else:
                data = token.encode()
                buffer = ctypes.create_string_buffer(data)
                value = cf.CFDataCreate(None, buffer, len(data))
                owned.append(value)
                query = dictionary(values)
                values[constant("kSecValueData")] = value
                # Device-only, non-synchronizing generic password.
                values[constant("kSecAttrAccessible")] = constant("kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly")
                status = security.SecItemAdd(dictionary(values), None)
                if status == -25299:
                    status = security.SecItemUpdate(query, dictionary({constant("kSecValueData"): value}))
            if status != 0:
                raise RuntimeError("无法访问钥匙串，请允许 SeeEnd 访问设备凭据后重试")
        finally:
            for value in reversed(owned):
                if value:
                    cf.CFRelease(value)

    def _key(self, create=False):
        path = self.runtime / "device-tokens" / "key.json"
        if create and not path.exists():
            candidate = path.with_name("key-" + uuid.uuid4().hex + ".tmp")
            try:
                write_private(candidate, {"key": base64.b64encode(secrets.token_bytes(32)).decode()})
                try:
                    os.link(candidate, path)  # publish once, never replace another writer's key
                except FileExistsError:
                    pass
            finally:
                candidate.unlink(missing_ok=True)
        try:
            key = base64.b64decode(json.loads(path.read_text())["key"], validate=True)
            if len(key) != 32:
                raise ValueError()
            if os.name != "nt":
                path.chmod(0o600)
            return key
        except (OSError, ValueError, KeyError, TypeError):
            raise RuntimeError("设备凭据密钥丢失或损坏，请恢复完整备份，或重新扫码配置") from None

    def _save_local(self, device_id, token, legacy=False):
        nonce = secrets.token_bytes(12)
        aad = (self.service + ":" + device_id).encode()
        ciphertext = AESGCM(self._key(create=True)).encrypt(nonce, token.encode(), aad)
        write_private(self._path(device_id), {"version": 2, "nonce": base64.b64encode(nonce).decode(),
            "ciphertext": base64.b64encode(ciphertext).decode(), "legacy_cleanup": legacy})

    def get(self, device_id):
        if sys.platform == "win32":
            return self._call("get", device_id)
        path = self._path(device_id)
        if path.exists():
            try:
                saved = json.loads(path.read_text())
                if saved["version"] != 2:
                    raise ValueError()
                nonce = base64.b64decode(saved["nonce"], validate=True)
                cipher = base64.b64decode(saved["ciphertext"], validate=True)
                token = AESGCM(self._key()).decrypt(nonce, cipher, (self.service + ":" + device_id).encode()).decode()
                if not valid_token(token):
                    raise ValueError()
                return token
            except (InvalidTag, ValueError, KeyError, TypeError, AttributeError):
                raise RuntimeError("设备凭据文件损坏或不匹配，请重新扫码配置") from None
        if sys.platform != "darwin":
            return None
        try:
            token = self._call("get", device_id, noninteractive=True)
        except RuntimeError:
            raise RuntimeError("旧设备凭据无法自动迁移，请重新扫码；新凭据将加密保存在本机") from None
        if token is None:
            return None
        if not valid_token(token):
            raise RuntimeError("旧设备凭据无效，请重新扫码")
        self._save_local(device_id, token, legacy=True)
        # Only remove the old entry after durable ciphertext can be read back.
        self.get(device_id)
        try:
            self._call("delete", device_id, noninteractive=True)
        except RuntimeError:
            pass  # deletion/reset can retry without blocking normal device control
        else:
            self._save_local(device_id, token)
        return token

    def set(self, device_id, token):
        if not valid_token(token):
            raise ValueError("设备 Token 无效，请重新扫码获取")
        if sys.platform == "win32":
            self._call("set", device_id, token)
        else:
            legacy = False
            if self._path(device_id).exists():
                try:
                    legacy = bool(json.loads(self._path(device_id).read_text()).get("legacy_cleanup"))
                except (ValueError, AttributeError):
                    pass
            self._save_local(device_id, token, legacy=legacy)

    def delete(self, device_id):
        if sys.platform == "win32":
            return self._call("delete", device_id)
        path = self._path(device_id)
        # Legacy-only records and interrupted migrations still need cleanup.
        legacy = not path.exists()
        if path.exists():
            try:
                legacy = json.loads(path.read_text()).get("legacy_cleanup", False)
            except (ValueError, AttributeError):
                pass
        if legacy and sys.platform == "darwin":
            self._call("delete", device_id, noninteractive=True)
        path.unlink(missing_ok=True)
