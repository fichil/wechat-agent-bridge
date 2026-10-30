"""Single administrator, opaque persistent sessions, and bounded login attempts."""
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import hmac
import os
from pathlib import Path
import secrets
import sqlite3
import threading
import time
from urllib.parse import urlsplit

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, InvalidHashError
from pydantic import BaseModel, ConfigDict, Field, SecretStr

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_AUTH_PATH = ROOT / ".local/auth.sqlite3"
SESSION_SECONDS = 7 * 24 * 3600
HASHER = PasswordHasher(memory_cost=19456, time_cost=2, parallelism=1)


class AuthError(Exception):
    def __init__(self, code, message, status=401):
        self.code, self.message, self.status = code, message, status


@dataclass(frozen=True)
class AuthConfig:
    mode: str
    origin: str
    extra_dev_origins: tuple[str, ...] = ()

    def __post_init__(self):
        if any(ord(char) <= 32 or ord(char) == 127 for char in self.origin):
            raise ValueError("Invalid authentication origin configuration")
        value = urlsplit(self.origin)
        if self.mode not in ("production", "development") or value.username or value.password or value.query or value.fragment or value.path not in ("", "/"):
            raise ValueError("Invalid authentication origin configuration")
        if self.mode == "production":
            if value.scheme != "https" or not value.hostname or value.hostname in ("localhost", "127.0.0.1", "::1"):
                raise ValueError("Production requires BRIDGE_PUBLIC_ORIGIN=https://your-domain")
            if self.extra_dev_origins:
                raise ValueError("Development origins are forbidden in production")
        elif value.scheme != "http" or value.hostname not in ("127.0.0.1", "localhost"):
            raise ValueError("Development HTTP is restricted to loopback")
        for origin in self.extra_dev_origins:
            u = urlsplit(origin)
            if u.scheme != "http" or u.hostname not in ("127.0.0.1", "localhost") or u.path or u.query or u.fragment or u.username:
                raise ValueError("Invalid development origin")

    @classmethod
    def environment(cls):
        mode = os.environ.get("BRIDGE_ENV", "production")
        origin = os.environ.get("BRIDGE_PUBLIC_ORIGIN", "")
        if mode == "development":
            return cls(mode, origin or "http://127.0.0.1:5173", ("http://localhost:5173", "http://127.0.0.1:8765", "http://localhost:8765"))
        return cls(mode, origin)

    @property
    def secure(self):
        return self.mode == "production"

    @property
    def cookie(self):
        return "__Host-bridge_session" if self.secure else "bridge_dev_session"

    @property
    def hosts(self):
        allowed = {urlsplit(o).netloc.lower() for o in self.origins}
        for origin in self.origins:
            value = urlsplit(origin)
            default = 443 if value.scheme == 'https' else 80
            if value.port in (None, default):
                allowed.update((value.hostname, f'{value.hostname}:{default}'))
        return allowed

    def allows_host(self, authority, *, health=False):
        if authority.lower() in self.hosts:
            return True
        # The launcher probes only this information-free endpoint over loopback.
        if health:
            try:
                value = urlsplit('//'+authority)
            except ValueError:
                return False
            return value.hostname in ('127.0.0.1', 'localhost') and not value.username and not value.path
        return False

    @property
    def origins(self):
        return {self.origin.rstrip("/"), *self.extra_dev_origins}


class Login(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=1, max_length=64)
    password: SecretStr = Field(min_length=1, max_length=128)


class PasswordChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    current_password: SecretStr = Field(min_length=1, max_length=128)
    new_password: SecretStr = Field(min_length=12, max_length=128)


class AuthSession(BaseModel):
    authenticated: bool
    initialized: bool
    username: str | None = None
    expires_at: str | None = None
    csrf_token: str | None = None


class AuthOk(BaseModel):
    ok: bool = True


def digest(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class AuthStore:
    def __init__(self, path=DEFAULT_AUTH_PATH, *, clock=time.time):
        self.clock = clock
        self.lock = threading.RLock()
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), timeout=5, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS administrator (
                singleton INTEGER PRIMARY KEY CHECK(singleton=1), username TEXT NOT NULL,
                password_hash TEXT NOT NULL, generation INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS sessions (
                token_hash TEXT PRIMARY KEY, csrf TEXT NOT NULL, expires REAL NOT NULL,
                generation INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS attempts (ip TEXT NOT NULL, at REAL NOT NULL);
            CREATE INDEX IF NOT EXISTS attempts_time ON attempts(at);
        """)
        # Same-cost validation for unknown users, without a reusable credential.
        self.dummy_hash = HASHER.hash(secrets.token_urlsafe(32))

    @contextmanager
    def transaction(self):
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                yield
                self.db.commit()
            except BaseException:
                self.db.rollback()
                raise

    def close(self):
        with self.lock:
            self.db.close()

    def initialized(self):
        with self.lock:
            return self.db.execute("SELECT 1 FROM administrator").fetchone() is not None

    def set_password(self, password, *, username="admin", reset=False):
        if not 12 <= len(password) <= 128 or not 1 <= len(username) <= 64 or username.strip() != username:
            raise ValueError("Username must be 1–64 characters; password must be 12–128 characters")
        encoded = HASHER.hash(password)
        with self.transaction():
            existing = self.db.execute("SELECT * FROM administrator").fetchone()
            if bool(existing) != reset:
                raise ValueError("Administrator already initialized" if existing else "Initialize the administrator first")
            if existing:
                self.db.execute("UPDATE administrator SET password_hash=?,generation=generation+1", (encoded,))
            else:
                self.db.execute("INSERT INTO administrator VALUES(1,?,?,1)", (username, encoded))
            self.db.execute("DELETE FROM sessions")
            self.db.execute("DELETE FROM attempts")

    def throttle(self, ip):
        now = self.clock()
        with self.transaction():
            self.db.execute("DELETE FROM attempts WHERE at<=?", (now-900,))
            local = self.db.execute("SELECT COUNT(*) FROM attempts WHERE ip=?", (ip,)).fetchone()[0]
            total = self.db.execute("SELECT COUNT(*) FROM attempts WHERE at>?", (now-60,)).fetchone()[0]
            if local >= 10 or total >= 30:
                raise AuthError("login_rate_limited", "尝试次数过多，请稍后再试。", 429)
            self.db.execute("INSERT INTO attempts VALUES(?,?)", (ip, now))

    @staticmethod
    def verify(encoded, password):
        try:
            return HASHER.verify(encoded, password)
        except (VerificationError, InvalidHashError):
            return False

    def login(self, username, password, ip):
        self.throttle(ip)
        with self.lock:
            admin = self.db.execute("SELECT * FROM administrator").fetchone()
        valid = self.verify(admin["password_hash"] if admin else self.dummy_hash, password)
        if not admin or not valid or not hmac.compare_digest(username.encode(), admin["username"].encode()):
            raise AuthError("invalid_credentials", "用户名或密码不正确。")
        token = secrets.token_urlsafe(32)
        csrf = secrets.token_urlsafe(32)
        with self.transaction():
            current = self.db.execute("SELECT generation FROM administrator").fetchone()
            if current[0] != admin["generation"]:
                raise AuthError("invalid_credentials", "用户名或密码不正确。")
            self.db.execute("DELETE FROM sessions WHERE expires<=?", (self.clock(),))
            self.db.execute("INSERT INTO sessions VALUES(?,?,?,?)", (digest(token), csrf, self.clock()+SESSION_SECONDS, admin["generation"]))
        return token, self.session(token)

    def session(self, token):
        if not token or len(token) > 128:
            return None
        with self.lock:
            row = self.db.execute("""SELECT s.*,a.username FROM sessions s JOIN administrator a
                ON a.generation=s.generation WHERE token_hash=? AND expires>?""", (digest(token), self.clock())).fetchone()
        return dict(row) if row else None

    def logout(self, token):
        with self.transaction():
            self.db.execute("DELETE FROM sessions WHERE token_hash=?", (digest(token),))

    def change_password(self, token, old, new, ip):
        self.throttle(ip)
        with self.lock:
            admin = self.db.execute("SELECT * FROM administrator").fetchone()
        if not admin or not self.session(token) or not self.verify(admin["password_hash"], old):
            raise AuthError("invalid_credentials", "当前密码不正确。")
        encoded = HASHER.hash(new)
        with self.transaction():
            if not self.session(token):
                raise AuthError("authentication_required", "登录已失效，请重新登录。")
            self.db.execute("UPDATE administrator SET password_hash=?,generation=generation+1", (encoded,))
            self.db.execute("DELETE FROM sessions")

    def view(self, session=None):
        return AuthSession(authenticated=bool(session), initialized=self.initialized(),
            username=session["username"] if session else None,
            expires_at=datetime.fromtimestamp(session["expires"], timezone.utc).isoformat() if session else None,
            csrf_token=session["csrf"] if session else None)
