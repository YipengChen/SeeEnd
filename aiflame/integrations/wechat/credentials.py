"""One profile's Weixin credentials; never included in API snapshots."""
import hashlib
import json
import sys
import threading
import uuid
from pathlib import Path

from aiflame.core.paths import write_private
from aiflame.devices.token_store import TokenStore
from .api import bounded_string, trusted_origin


class WechatCredentials(TokenStore):
    def __init__(self, runtime):
        super().__init__(runtime, service="com.seeend.wechat." + hashlib.sha256(str(Path(runtime).resolve()).encode()).hexdigest()[:16])
        self.reference = self.runtime / "wechat-credentials" / "profile.json"
        self.account_id = None
        self.commit_lock = threading.Lock()
        self.revoked = False

    def invalidate(self):
        with self.commit_lock:
            self.revoked = True

    def has_account(self):
        return self.reference.is_file()

    def _account_id(self):
        if self.revoked:
            return self.account_id
        if self.account_id is None and self.reference.is_file():
            saved = json.loads(self.reference.read_text(encoding="utf-8"))
            if not isinstance(saved, dict) or not isinstance(saved.get("account"), str):
                raise ValueError("微信凭据索引无效")
            value = saved["account"]
            self.account_id = str(uuid.UUID(value))
        return self.account_id

    def _path(self, account):
        name = hashlib.sha256(account.encode()).hexdigest() + ".json"
        return self.runtime / "wechat-credentials" / name

    def load(self):
        account_id = self._account_id()
        if not account_id:
            return {}
        if sys.platform in ("darwin", "win32"):
            value = self._call("get", account_id)
            saved = json.loads(value) if value else {}
        else:
            path = self._path(account_id)
            saved = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        if not saved:
            return {}
        if (not isinstance(saved, dict) or not bounded_string(saved.get("token"))
                or not bounded_string(saved.get("user_id"), 256) or not bounded_string(saved.get("bot_id"), 256)):
            raise ValueError("微信凭据无效，请重新扫码")
        saved["base_url"] = trusted_origin(saved.get("base_url"))
        for key in ("context", "cursor"):
            if saved.get(key) and not bounded_string(saved[key], 65536):
                raise ValueError("微信凭据无效，请重新扫码")
        return saved

    def save(self, account):
        with self.commit_lock:
            if self.revoked:
                return
        account_id = self._account_id() or str(uuid.uuid4())
        value = json.dumps(account, ensure_ascii=False)
        if sys.platform in ("darwin", "win32"):
            self._call("set", account_id, value)
        else:
            write_private(self._path(account_id), account)
        with self.commit_lock:
            revoked = self.revoked
            if not revoked:
                write_private(self.reference, {"account": account_id})
                self.account_id = account_id
        if revoked:
            # A late platform-store write may finish after reset. It can never
            # publish a reference or remove the fresh profile's credentials.
            try:
                current = json.loads(self.reference.read_text(encoding="utf-8")).get("account")
            except (ValueError, TypeError, AttributeError, OSError):
                current = None
            if current != account_id:
                if sys.platform in ("darwin", "win32"):
                    self._call("delete", account_id)
                else:
                    self._path(account_id).unlink(missing_ok=True)

    def clear(self):
        try:
            account_id = self._account_id()
        except (ValueError, TypeError, AttributeError, OSError):
            account_id = None
        # Remove the reference first: a denied Keychain deletion must not allow
        # a disconnected or reset profile to silently recover its old token.
        # A cancelled old login may finish after reset/rebind. Remove only its
        # own marker; malformed markers are always removable by disconnect.
        try:
            current = json.loads(self.reference.read_text(encoding="utf-8")).get("account")
        except (ValueError, TypeError, AttributeError, OSError):
            current = None
        if ((account_id is not None and current == account_id)
                or (not self.revoked and (current is None or account_id is None))):
            self.reference.unlink(missing_ok=True)
        self.account_id = None
        if sys.platform in ("darwin", "win32"):
            if account_id:
                self._call("delete", account_id)
        else:
            if account_id:
                self._path(account_id).unlink(missing_ok=True)
