"""
db/models.py
SQLite schema creation and helper functions using Python's built-in sqlite3.

Security features:
  - Optional Fernet encryption for OAuth tokens (set TOKEN_ENCRYPTION_KEY)
  - CSRF nonce storage for OAuth state verification
"""

import base64
import hashlib
import logging
import os
import sqlite3

logger = logging.getLogger(__name__)

DB_PATH = os.environ.get("DB_PATH", "bot_data.db")

# ── Optional token encryption ────────────────────────────────────────────────
# Set TOKEN_ENCRYPTION_KEY env var to enable. If not set, tokens stored plaintext.
_ENCRYPTION_KEY = os.environ.get("TOKEN_ENCRYPTION_KEY", "")
_fernet = None

if _ENCRYPTION_KEY:
    try:
        from cryptography.fernet import Fernet
        key = base64.urlsafe_b64encode(
            hashlib.sha256(_ENCRYPTION_KEY.encode()).digest()
        )
        _fernet = Fernet(key)
    except ImportError:
        logger.warning("cryptography not installed — tokens stored in plaintext.")


def _encrypt(value: str | None) -> str | None:
    if not value or _fernet is None:
        return value
    return _fernet.encrypt(value.encode()).decode()


def _decrypt(value: str | None) -> str | None:
    if not value or _fernet is None:
        return value
    try:
        return _fernet.decrypt(value.encode()).decode()
    except Exception:
        return value  # fallback: plaintext from before encryption was enabled


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Create tables if they don't already exist."""
    with get_connection() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id       INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id   INTEGER UNIQUE NOT NULL,
                token         TEXT,
                refresh_token TEXT
            );

            CREATE TABLE IF NOT EXISTS files (
                file_id     TEXT PRIMARY KEY,
                name        TEXT NOT NULL,
                type        TEXT,
                uploaded_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS favorites (
                telegram_id INTEGER,
                file_id     TEXT,
                PRIMARY KEY (telegram_id, file_id)
            );

            CREATE TABLE IF NOT EXISTS oauth_states (
                telegram_id INTEGER NOT NULL,
                nonce       TEXT NOT NULL,
                created_at  TEXT DEFAULT (datetime('now')),
                PRIMARY KEY (telegram_id, nonce)
            );
            """
        )
    if _fernet:
        logger.info("Token encryption enabled.")
    else:
        logger.warning("TOKEN_ENCRYPTION_KEY not set — tokens stored in plaintext.")


# ── User helpers ──────────────────────────────────────────────────────────────

def upsert_user(telegram_id: int, token: str = None, refresh_token: str = None) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO users (telegram_id, token, refresh_token)
            VALUES (?, ?, ?)
            ON CONFLICT(telegram_id) DO UPDATE SET
                token         = excluded.token,
                refresh_token = excluded.refresh_token
            """,
            (telegram_id, _encrypt(token), _encrypt(refresh_token)),
        )


def get_user(telegram_id: int) -> dict | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)
        ).fetchone()
    if row is None:
        return None
    return {
        "user_id": row["user_id"],
        "telegram_id": row["telegram_id"],
        "token": _decrypt(row["token"]),
        "refresh_token": _decrypt(row["refresh_token"]),
    }


def delete_user(telegram_id: int) -> None:
    with get_connection() as conn:
        conn.execute("DELETE FROM users WHERE telegram_id = ?", (telegram_id,))


# ── File helpers ──────────────────────────────────────────────────────────────

def log_file(file_id: str, name: str, mime_type: str = None) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO files (file_id, name, type)
            VALUES (?, ?, ?)
            """,
            (file_id, name, mime_type),
        )


# ── Favorite helpers ──────────────────────────────────────────────────────────

def add_favorite(telegram_id: int, file_id: str) -> None:
    with get_connection() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO favorites (telegram_id, file_id) VALUES (?, ?)",
            (telegram_id, file_id),
        )

def remove_favorite(telegram_id: int, file_id: str) -> None:
    with get_connection() as conn:
        conn.execute(
            "DELETE FROM favorites WHERE telegram_id = ? AND file_id = ?",
            (telegram_id, file_id),
        )

def is_favorite(telegram_id: int, file_id: str) -> bool:
    with get_connection() as conn:
        res = conn.execute(
            "SELECT 1 FROM favorites WHERE telegram_id = ? AND file_id = ?",
            (telegram_id, file_id),
        ).fetchone()
        return res is not None

def get_favorites(telegram_id: int) -> list[str]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT file_id FROM favorites WHERE telegram_id = ?",
            (telegram_id,),
        ).fetchall()
        return [row["file_id"] for row in rows]


# ── OAuth state (CSRF protection) ────────────────────────────────────────────

def store_oauth_state(telegram_id: int, nonce: str) -> None:
    """Store a CSRF nonce for an OAuth flow."""
    with get_connection() as conn:
        conn.execute("DELETE FROM oauth_states WHERE telegram_id = ?", (telegram_id,))
        conn.execute(
            "INSERT INTO oauth_states (telegram_id, nonce) VALUES (?, ?)",
            (telegram_id, nonce),
        )


def verify_oauth_state(telegram_id: int, nonce: str) -> bool:
    """Verify and consume a CSRF nonce. Single-use, expires after 10 minutes."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT 1 FROM oauth_states WHERE telegram_id = ? AND nonce = ? "
            "AND created_at > datetime('now', '-10 minutes')",
            (telegram_id, nonce),
        ).fetchone()
        if row:
            conn.execute(
                "DELETE FROM oauth_states WHERE telegram_id = ? AND nonce = ?",
                (telegram_id, nonce),
            )
            return True
        return False


def cleanup_expired_states() -> None:
    """Remove expired OAuth states (older than 10 minutes)."""
    with get_connection() as conn:
        conn.execute(
            "DELETE FROM oauth_states WHERE created_at < datetime('now', '-10 minutes')"
        )
