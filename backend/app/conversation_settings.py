"""Atomic local preferences only; no account names, messages, images, or keys."""
import hashlib
import json
import os
from pathlib import Path
import re
import threading
from uuid import uuid4

from .conversation_models import Exclusions

from bridge_paths import DATA
DEFAULT_PATH = DATA / "conversation-settings.json"


class SettingsError(Exception):
    pass


class ConversationSettingsStore:
    def __init__(self, path=DEFAULT_PATH):
        self.path = Path(path)
        self.lock = threading.RLock()

    @staticmethod
    def key(wxid):
        return hashlib.sha256(("wechat-conversations-v1:" + wxid).encode()).hexdigest()

    def _load(self):
        if not self.path.exists():
            return {}
        try:
            if self.path.stat().st_size > 1024 * 1024:
                raise ValueError
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if data.get("schema_version") != 1 or not isinstance(data.get("accounts"), dict):
                raise ValueError
            for key, value in data["accounts"].items():
                if not re.fullmatch(r"[a-f0-9]{64}", key) or not isinstance(value, dict) or set(value) != set(Exclusions.model_fields):
                    raise ValueError
            return {key: Exclusions.model_validate(value).model_dump() for key, value in data["accounts"].items()}
        except Exception:
            raise SettingsError("settings_read_failed") from None

    def get(self, wxid):
        with self.lock:
            return Exclusions.model_validate(self._load().get(self.key(wxid), {}))

    def save(self, wxid, exclusions):
        with self.lock:
            data = self._load()
            data[self.key(wxid)] = exclusions.model_dump()
            temporary = self.path.with_name(self.path.name + "." + uuid4().hex + ".tmp")
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with temporary.open("x", encoding="utf-8") as file:
                    json.dump(dict(schema_version=1, accounts=data), file, indent=2)
                    file.write("\n")
                    file.flush()
                    os.fsync(file.fileno())
                os.replace(temporary, self.path)
            except OSError:
                raise SettingsError("settings_write_failed") from None
            finally:
                temporary.unlink(missing_ok=True)
            return exclusions.model_copy(deep=True)
