"""Users, roles, sessions, device keys and the audit log."""
from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

from .db import DB

ROLES = {
    "admin": "School administrator",
    "management": "School management",
    "supervisor": "Floor supervisor",
    "security": "Security",
    "pe_teacher": "PE teacher",
    "bus_attendant": "Bus attendant",
    "counselor": "Counselor",
}

# who can do what
PERMISSIONS = {
    "incidents:view_all": {"admin", "management", "counselor"},
    "incidents:respond": {"admin", "management", "supervisor", "security", "pe_teacher", "bus_attendant"},
    "reports:view": {"admin", "management", "counselor"},
    "keywords:view": {"admin", "management"},
    "keywords:edit": {"admin"},
    "devices:manage": {"admin"},
    "settings:edit": {"admin"},
    "users:manage": {"admin"},
    "audit:view": {"admin", "management"},
}

SESSION_HOURS = 12


def now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def hash_password(pw: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, 200_000)
    return f"pbkdf2${salt.hex()}${dk.hex()}"


def verify_password(pw: str, stored: str) -> bool:
    _, salt, _ = stored.split("$")
    return hmac.compare_digest(hash_password(pw, bytes.fromhex(salt)), stored)


def sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def can(role: str, perm: str) -> bool:
    return role in PERMISSIONS[perm]


class Auth:
    def __init__(self, db: DB):
        self.db = db

    def create_user(self, school_id: str, username: str, password: str, role: str, display_name: str) -> int:
        if role not in ROLES:
            raise ValueError(f"unknown role {role}")
        return self.db.insert("users", school_id=school_id, username=username, role=role,
                              display_name=display_name, pw_hash=hash_password(password))

    def login(self, username: str, password: str) -> tuple[str, dict] | None:
        u = self.db.one("SELECT * FROM users WHERE username=? AND active=1", username)
        if not u or not verify_password(password, u["pw_hash"]):
            return None
        token = secrets.token_urlsafe(32)
        self.db.insert("sessions", token_hash=sha(token), user_id=u["id"],
                       expires_at=iso(now() + timedelta(hours=SESSION_HOURS)))
        return token, u

    def user_for_token(self, token: str) -> dict | None:
        s = self.db.one("SELECT * FROM sessions WHERE token_hash=?", sha(token))
        if not s or s["expires_at"] < iso(now()):
            return None
        return self.db.one("SELECT id, school_id, username, display_name, role FROM users WHERE id=? AND active=1",
                           s["user_id"])

    def logout(self, token: str) -> None:
        self.db.run("DELETE FROM sessions WHERE token_hash=?", sha(token))

    def register_device(self, school_id: str, device_id: str, location_id: str, name: str,
                        key: str | None = None) -> str:
        key = key or secrets.token_urlsafe(24)
        self.db.run("INSERT OR REPLACE INTO devices (id, school_id, location_id, name, key_hash, created_at) "
                    "VALUES (?,?,?,?,?,?)", device_id, school_id, location_id, name, sha(key), iso(now()))
        return key

    def device_for_key(self, key: str) -> dict | None:
        return self.db.one("SELECT * FROM devices WHERE key_hash=?", sha(key))

    def audit(self, actor: str, action: str, target: str = "", detail: str = "", school_id: str | None = None):
        self.db.insert("audit_log", ts=iso(now()), school_id=school_id, actor=actor, action=action,
                       target=target, detail=detail)
