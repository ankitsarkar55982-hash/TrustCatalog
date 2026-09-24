"""Role-based authentication for TrustCatalog.

Uses SQLite for account storage and PBKDF2-HMAC password hashing from Python's
standard library. No passwords are stored in plaintext.
"""
import hashlib
import hmac
import os
import secrets
import sqlite3
from typing import Optional

import config

AUTH_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS auth_users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    full_name TEXT NOT NULL,
    username TEXT NOT NULL UNIQUE,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    password_salt TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('CUSTOMER','SELLER','ADMIN')),
    store_name TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""


def _connect():
    conn = sqlite3.connect(str(config.DB_PATH))
    conn.execute(AUTH_TABLE_SQL)
    conn.commit()
    return conn


def _hash_password(password: str, salt: bytes | None = None) -> tuple[str, str]:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 310_000)
    return salt.hex(), digest.hex()


def _verify_password(password: str, salt_hex: str, digest_hex: str) -> bool:
    salt = bytes.fromhex(salt_hex)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 310_000)
    return hmac.compare_digest(digest.hex(), digest_hex)


def init_auth():
    conn = _connect()
    conn.close()


def register_user(full_name: str, username: str, email: str, password: str,
                  role: str, store_name: str = "") -> tuple[bool, str]:
    role = role.upper().strip()
    if role not in {"CUSTOMER", "SELLER"}:
        return False, "Only Customer and Seller accounts can be registered."
    full_name, username, email = full_name.strip(), username.strip(), email.strip().lower()
    if not full_name or not username or not email or not password:
        return False, "Please fill in all required fields."
    if len(password) < 8:
        return False, "Password must contain at least 8 characters."
    if "@" not in email or "." not in email.rsplit("@", 1)[-1]:
        return False, "Please enter a valid email address."
    if role == "SELLER" and not store_name.strip():
        return False, "Store name is required for Seller registration."

    salt, digest = _hash_password(password)
    conn = _connect()
    try:
        conn.execute(
            """INSERT INTO auth_users
               (full_name, username, email, password_hash, password_salt, role, store_name)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (full_name, username, email, digest, salt, role, store_name.strip() or None),
        )
        conn.commit()
        return True, "Account created successfully. You can now log in."
    except sqlite3.IntegrityError:
        return False, "That username or email is already registered."
    finally:
        conn.close()


def authenticate(identifier: str, password: str, selected_role: str) -> Optional[dict]:
    selected_role = selected_role.upper().strip()
    identifier = identifier.strip().lower()
    conn = _connect()
    try:
        row = conn.execute(
            """SELECT id, full_name, username, email, password_hash, password_salt,
                      role, store_name
               FROM auth_users
               WHERE (lower(username) = ? OR lower(email) = ?) AND role = ?""",
            (identifier, identifier, selected_role),
        ).fetchone()
    finally:
        conn.close()

    if not row or not _verify_password(password, row[5], row[4]):
        return None
    return {
        "id": row[0], "full_name": row[1], "username": row[2],
        "email": row[3], "role": row[6], "store_name": row[7],
    }


def ensure_admin():
    """Create the private admin account from Streamlit secrets/env if supplied.

    Local fallback keeps the prototype usable; production deployments should
    set TRUSTCATALOG_ADMIN_PASSWORD as a Streamlit secret/environment variable.
    """
    username = os.getenv("TRUSTCATALOG_ADMIN_USERNAME", "demo_admin").strip()
    password = os.getenv("TRUSTCATALOG_ADMIN_PASSWORD", "DemoPass!2026")
    email = os.getenv("TRUSTCATALOG_ADMIN_EMAIL", "admin@trustcatalog.local").strip().lower()

    conn = _connect()
    try:
        exists = conn.execute(
            "SELECT 1 FROM auth_users WHERE role='ADMIN' LIMIT 1"
        ).fetchone()
        if exists:
            return
        salt, digest = _hash_password(password)
        conn.execute(
            """INSERT INTO auth_users
               (full_name, username, email, password_hash, password_salt, role)
               VALUES (?, ?, ?, ?, ?, 'ADMIN')""",
            ("TrustCatalog Administrator", username, email, digest, salt),
        )
        conn.commit()
    finally:
        conn.close()
