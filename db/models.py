"""
db/models.py
SQLite schema creation and helper functions using Python's built-in sqlite3.
"""

import sqlite3
import os

DB_PATH = os.environ.get("DB_PATH", "bot_data.db")


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
            """
        )


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
            (telegram_id, token, refresh_token),
        )


def get_user(telegram_id: int) -> sqlite3.Row | None:
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)
        ).fetchone()


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
