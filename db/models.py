"""
db/models.py
SQLite schema creation and helper functions using Python's built-in sqlite3.

Security features:
  - Optional Fernet encryption for OAuth tokens (set TOKEN_ENCRYPTION_KEY)
  - CSRF nonce storage for OAuth state verification
  - PKCE code_verifier storage for OAuth PKCE flow
  - WAL mode for safe concurrent access
  - Restrictive file permissions on DB file
"""

import base64
import hashlib
import logging
import os
import sqlite3
import stat
import sys
from datetime import datetime, timedelta

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


def _restrict_db_permissions() -> None:
    """Set restrictive file permissions on the database file (Unix only)."""
    if sys.platform == "win32":
        return  # Windows handles permissions differently
    try:
        if os.path.isfile(DB_PATH):
            os.chmod(DB_PATH, stat.S_IRUSR | stat.S_IWUSR)  # 0o600
    except OSError:
        logger.warning("Could not restrict DB file permissions for %s", DB_PATH)


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    # Enable WAL mode for better concurrent access
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
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
                telegram_id   INTEGER NOT NULL,
                nonce         TEXT NOT NULL,
                code_verifier TEXT,
                created_at    TEXT DEFAULT (datetime('now')),
                PRIMARY KEY (telegram_id, nonce)
            );

            CREATE TABLE IF NOT EXISTS audit_log (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER NOT NULL,
                action      TEXT NOT NULL,
                file_id     TEXT,
                detail      TEXT,
                created_at  TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS user_emails (
                telegram_id INTEGER PRIMARY KEY,
                email       TEXT NOT NULL UNIQUE,
                verified    BOOLEAN DEFAULT 0,
                created_at  TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS security_alerts (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER NOT NULL,
                alert_type  TEXT NOT NULL,
                description TEXT NOT NULL,
                action_taken TEXT,
                created_at  TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS anomaly_tracking (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER NOT NULL,
                action      TEXT NOT NULL,
                count       INTEGER DEFAULT 1,
                window_start TEXT DEFAULT (datetime('now')),
                updated_at  TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS stepup_auth (
                telegram_id   INTEGER PRIMARY KEY,
                code_hash     TEXT,
                expires_at    TEXT,
                verified_until TEXT,
                last_sent_at  TEXT,
                attempts      INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS task_jobs (
                id            TEXT PRIMARY KEY,
                telegram_id   INTEGER NOT NULL,
                job_type      TEXT NOT NULL,
                status        TEXT NOT NULL,
                progress      INTEGER DEFAULT 0,
                detail        TEXT,
                error_message TEXT,
                created_at    TEXT DEFAULT (datetime('now')),
                updated_at    TEXT DEFAULT (datetime('now'))
            );
            """
        )
        # ── Schema migrations for existing databases ──────────────────────────
        # Add code_verifier column if upgrading from pre-PKCE schema
        cols = [r[1] for r in conn.execute("PRAGMA table_info(oauth_states)").fetchall()]
        if "code_verifier" not in cols:
            conn.execute("ALTER TABLE oauth_states ADD COLUMN code_verifier TEXT")
            logger.info("Migrated oauth_states: added code_verifier column.")
        
        # Add user_emails table if missing (for alert notifications)
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        if "user_emails" not in tables:
            conn.execute(
                """
                CREATE TABLE user_emails (
                    telegram_id INTEGER PRIMARY KEY,
                    email       TEXT NOT NULL UNIQUE,
                    verified    BOOLEAN DEFAULT 0,
                    created_at  TEXT DEFAULT (datetime('now'))
                )
                """
            )
            logger.info("Created user_emails table for alert notifications.")
        
        if "security_alerts" not in tables:
            conn.execute(
                """
                CREATE TABLE security_alerts (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    telegram_id INTEGER NOT NULL,
                    alert_type  TEXT NOT NULL,
                    description TEXT NOT NULL,
                    action_taken TEXT,
                    created_at  TEXT DEFAULT (datetime('now'))
                )
                """
            )
            logger.info("Created security_alerts table.")
        
        if "anomaly_tracking" not in tables:
            conn.execute(
                """
                CREATE TABLE anomaly_tracking (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    telegram_id INTEGER NOT NULL,
                    action      TEXT NOT NULL,
                    count       INTEGER DEFAULT 1,
                    window_start TEXT DEFAULT (datetime('now')),
                    updated_at  TEXT DEFAULT (datetime('now'))
                )
                """
            )
            logger.info("Created anomaly_tracking table.")

        if "stepup_auth" not in tables:
            conn.execute(
                """
                CREATE TABLE stepup_auth (
                    telegram_id   INTEGER PRIMARY KEY,
                    code_hash     TEXT,
                    expires_at    TEXT,
                    verified_until TEXT,
                    last_sent_at  TEXT,
                    attempts      INTEGER DEFAULT 0
                )
                """
            )
            logger.info("Created stepup_auth table.")

        if "task_jobs" not in tables:
            conn.execute(
                """
                CREATE TABLE task_jobs (
                    id            TEXT PRIMARY KEY,
                    telegram_id   INTEGER NOT NULL,
                    job_type      TEXT NOT NULL,
                    status        TEXT NOT NULL,
                    progress      INTEGER DEFAULT 0,
                    detail        TEXT,
                    error_message TEXT,
                    created_at    TEXT DEFAULT (datetime('now')),
                    updated_at    TEXT DEFAULT (datetime('now'))
                )
                """
            )
            logger.info("Created task_jobs table.")
    _restrict_db_permissions()
    if _fernet:
        logger.info("Token encryption enabled.")
    else:
        # V-NEW-01 compensating control: require encryption in production
        if os.environ.get("RAILWAY_PUBLIC_DOMAIN"):
            logger.critical(
                "TOKEN_ENCRYPTION_KEY is REQUIRED in production. "
                "Generate with: python -c \"import secrets; print(secrets.token_urlsafe(32))\""
            )
            raise SystemExit(
                "TOKEN_ENCRYPTION_KEY must be set in production environments."
            )
        logger.warning("TOKEN_ENCRYPTION_KEY not set — tokens stored in plaintext.")


# ── User helpers ──────────────────────────────────────────────────────────────

def upsert_user(telegram_id: int, token: str | None = None, refresh_token: str | None = None) -> None:
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


def get_all_users() -> list[dict]:
    """Get all registered users (for emergency operations like revoke all)."""
    with get_connection() as conn:
        rows = conn.execute("SELECT telegram_id, token FROM users WHERE token IS NOT NULL").fetchall()
    return [
        {
            "telegram_id": row["telegram_id"],
            "token": _decrypt(row["token"]),
        }
        for row in rows
    ]


def delete_user(telegram_id: int) -> None:
    with get_connection() as conn:
        conn.execute("DELETE FROM users WHERE telegram_id = ?", (telegram_id,))
        # Also clean up favorites for this user (V-15)
        conn.execute("DELETE FROM favorites WHERE telegram_id = ?", (telegram_id,))


# ── File helpers ──────────────────────────────────────────────────────────────

def log_file(file_id: str, name: str, mime_type: str | None = None) -> None:
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


# ── OAuth state (CSRF + PKCE protection) ─────────────────────────────────────

def store_oauth_state(telegram_id: int, nonce: str, code_verifier: str | None = None) -> None:
    """Store a CSRF nonce and optional PKCE code_verifier for an OAuth flow."""
    with get_connection() as conn:
        conn.execute("DELETE FROM oauth_states WHERE telegram_id = ?", (telegram_id,))
        conn.execute(
            "INSERT INTO oauth_states (telegram_id, nonce, code_verifier) VALUES (?, ?, ?)",
            (telegram_id, nonce, code_verifier),
        )


def verify_oauth_state(telegram_id: int, nonce: str) -> tuple[bool, str | None]:
    """Verify and consume a CSRF nonce. Returns (valid, code_verifier).

    Single-use, expires after 10 minutes.
    """
    with get_connection() as conn:
        row = conn.execute(
            "SELECT code_verifier FROM oauth_states WHERE telegram_id = ? AND nonce = ? "
            "AND created_at > datetime('now', '-10 minutes')",
            (telegram_id, nonce),
        ).fetchone()
        if row:
            code_verifier = row["code_verifier"]
            conn.execute(
                "DELETE FROM oauth_states WHERE telegram_id = ? AND nonce = ?",
                (telegram_id, nonce),
            )
            return True, code_verifier
        return False, None


def cleanup_expired_states() -> None:
    """Remove expired OAuth states and prune old audit log entries."""
    with get_connection() as conn:
        conn.execute(
            "DELETE FROM oauth_states WHERE created_at < datetime('now', '-10 minutes')"
        )
        # F-02: Prune audit log entries older than 90 days to prevent unbounded growth
        conn.execute(
            "DELETE FROM audit_log WHERE created_at < datetime('now', '-90 days')"
        )
        # Clear expired step-up OTPs (verification windows are checked on read)
        conn.execute(
            "UPDATE stepup_auth SET code_hash = NULL, expires_at = NULL, attempts = 0 "
            "WHERE expires_at IS NOT NULL AND expires_at < datetime('now')"
        )


# ── Audit logging (V-NEW-01 compensating control) ────────────────────────────

def log_audit(telegram_id: int, action: str, file_id: str | None = None, detail: str | None = None) -> None:
    """Record a destructive operation for audit purposes.

    Because the bot uses the broad 'auth/drive' scope (required for full
    file-manager functionality), we log all destructive operations to provide
    accountability and incident-response capability.
    """
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO audit_log (telegram_id, action, file_id, detail) VALUES (?, ?, ?, ?)",
            (telegram_id, action, file_id, detail),
        )
    logger.info("AUDIT: user=%s action=%s file_id=%s detail=%s", telegram_id, action, file_id, detail)


# ── User email helpers (for security alerts) ──────────────────────────────────

def set_user_email(telegram_id: int, email: str) -> bool:
    """Store user's email for security alerts. Returns True if successful."""
    try:
        with get_connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO user_emails (telegram_id, email, verified) VALUES (?, ?, 1)",
                (telegram_id, email),
            )
        logger.info("Email set for user %s: %s", telegram_id, email)
        return True
    except Exception as e:
        logger.error("Failed to set email for user %s: %s", telegram_id, e)
        return False


def get_user_email(telegram_id: int) -> str | None:
    """Retrieve user's email address for alerts."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT email FROM user_emails WHERE telegram_id = ? AND verified = 1",
            (telegram_id,),
        ).fetchone()
    return row["email"] if row else None


# ── Step-up verification helpers (email OTP) ───────────────────────────────────

def get_stepup_state(telegram_id: int) -> dict | None:
    """Return step-up auth state for a user (OTP + verification window)."""
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT code_hash, expires_at, verified_until, last_sent_at, attempts
            FROM stepup_auth WHERE telegram_id = ?
            """,
            (telegram_id,),
        ).fetchone()
    if not row:
        return None
    return {
        "code_hash": row["code_hash"],
        "expires_at": row["expires_at"],
        "verified_until": row["verified_until"],
        "last_sent_at": row["last_sent_at"],
        "attempts": row["attempts"] or 0,
    }


def set_stepup_code(telegram_id: int, code_hash: str, expires_at: str, sent_at: str) -> None:
    """Store a new step-up OTP hash and expiry."""
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO stepup_auth (telegram_id, code_hash, expires_at, last_sent_at, attempts, verified_until)
            VALUES (?, ?, ?, ?, 0, NULL)
            ON CONFLICT(telegram_id) DO UPDATE SET
                code_hash     = excluded.code_hash,
                expires_at    = excluded.expires_at,
                last_sent_at  = excluded.last_sent_at,
                attempts      = 0,
                verified_until = NULL
            """,
            (telegram_id, code_hash, expires_at, sent_at),
        )


def set_stepup_verified(telegram_id: int, verified_until: str) -> None:
    """Mark user as verified for a short window and clear any pending OTP."""
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO stepup_auth (telegram_id, verified_until, attempts)
            VALUES (?, ?, 0)
            ON CONFLICT(telegram_id) DO UPDATE SET
                verified_until = excluded.verified_until,
                code_hash = NULL,
                expires_at = NULL,
                attempts = 0
            """,
            (telegram_id, verified_until),
        )


def clear_stepup_code(telegram_id: int) -> None:
    """Clear any pending OTP for the user."""
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE stepup_auth
            SET code_hash = NULL, expires_at = NULL, attempts = 0
            WHERE telegram_id = ?
            """,
            (telegram_id,),
        )


def increment_stepup_attempt(telegram_id: int) -> int:
    """Increment failed OTP attempt count and return the new count."""
    with get_connection() as conn:
        conn.execute(
            "UPDATE stepup_auth SET attempts = COALESCE(attempts, 0) + 1 WHERE telegram_id = ?",
            (telegram_id,),
        )
        row = conn.execute(
            "SELECT attempts FROM stepup_auth WHERE telegram_id = ?",
            (telegram_id,),
        ).fetchone()
    return int(row["attempts"]) if row else 0


def is_stepup_verified(telegram_id: int, now_iso: str) -> bool:
    """Check if user is currently in a verified window."""
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT 1 FROM stepup_auth
            WHERE telegram_id = ? AND verified_until IS NOT NULL AND verified_until > ?
            """,
            (telegram_id, now_iso),
        ).fetchone()
    return row is not None


# ── Background task helpers ───────────────────────────────────────────────────

def create_task_job(job_id: str, telegram_id: int, job_type: str, detail: str | None = None) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO task_jobs (id, telegram_id, job_type, status, detail)
            VALUES (?, ?, ?, 'queued', ?)
            """,
            (job_id, telegram_id, job_type, detail),
        )


def update_task_job(
    job_id: str,
    status: str | None = None,
    progress: int | None = None,
    detail: str | None = None,
    error_message: str | None = None,
) -> None:
    updates: list[str] = []
    params: list = []
    if status is not None:
        updates.append("status = ?")
        params.append(status)
    if progress is not None:
        updates.append("progress = ?")
        params.append(progress)
    if detail is not None:
        updates.append("detail = ?")
        params.append(detail)
    if error_message is not None:
        updates.append("error_message = ?")
        params.append(error_message)
    updates.append("updated_at = datetime('now')")
    params.append(job_id)
    if not updates:
        return
    with get_connection() as conn:
        conn.execute(
            f"UPDATE task_jobs SET {', '.join(updates)} WHERE id = ?",
            params,
        )


def cleanup_task_jobs(ttl_seconds: int) -> None:
    """Remove completed/failed task records older than ttl_seconds."""
    with get_connection() as conn:
        conn.execute(
            """
            DELETE FROM task_jobs
            WHERE status IN ('completed', 'failed')
              AND updated_at < datetime('now', ?)
            """,
            (f"-{ttl_seconds} seconds",),
        )


# ── Anomaly detection helpers ──────────────────────────────────────────────────

def track_action(telegram_id: int, action: str) -> int:
    """Track action count within 5-minute window. Returns current count."""
    now = datetime.now().isoformat()
    window_start = (datetime.now() - timedelta(minutes=5)).isoformat()
    
    with get_connection() as conn:
        # Check if tracking record exists and is within window
        row = conn.execute(
            """
            SELECT id, count FROM anomaly_tracking
            WHERE telegram_id = ? AND action = ? AND window_start > ?
            ORDER BY window_start DESC LIMIT 1
            """,
            (telegram_id, action, window_start),
        ).fetchone()
        
        if row:
            # Increment existing counter
            new_count = row["count"] + 1
            conn.execute(
                "UPDATE anomaly_tracking SET count = ?, updated_at = ? WHERE id = ?",
                (new_count, now, row["id"]),
            )
            return new_count
        else:
            # Create new tracking record
            conn.execute(
                "INSERT INTO anomaly_tracking (telegram_id, action, count, window_start) VALUES (?, ?, 1, ?)",
                (telegram_id, action, now),
            )
            return 1


def log_security_alert(telegram_id: int, alert_type: str, description: str, action_taken: str | None = None) -> None:
    """Log a security alert (e.g., anomaly detected, token revoked)."""
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO security_alerts (telegram_id, alert_type, description, action_taken)
            VALUES (?, ?, ?, ?)
            """,
            (telegram_id, alert_type, description, action_taken),
        )
    logger.warning("SECURITY ALERT: user=%s type=%s description=%s action=%s", 
                   telegram_id, alert_type, description, action_taken)


def cleanup_anomaly_tracking() -> None:
    """Clean up old anomaly tracking records (older than 24 hours)."""
    with get_connection() as conn:
        conn.execute(
            "DELETE FROM anomaly_tracking WHERE window_start < datetime('now', '-24 hours')"
        )
