"""
drive/auth.py
Google OAuth 2.0 helpers with PKCE support.
Each Telegram user has their own OAuth token stored in SQLite.

Supports two credential sources (checked in order):
  1. Environment variables: GOOGLE_CLIENT_ID + GOOGLE_CLIENT_SECRET
     (preferred for production / Railway deployment)
  2. File: credentials.json in the project root
     (convenient for local development)

Security:
  - OAuth state includes a CSRF nonce verified on callback.
  - PKCE (S256) for authorization code exchange protection.
  - Tokens never logged.
"""

import hashlib
import base64
from http.client import HTTPSConnection
from urllib.parse import urlencode
import json
import logging
import os
import secrets

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from google.auth.transport.requests import Request
from google.auth.exceptions import RefreshError

from db.models import get_user, upsert_user, store_oauth_state, verify_oauth_state

logger = logging.getLogger(__name__)

# V-NEW-01: The broad 'auth/drive' scope is REQUIRED because this bot is a
# full file manager — browsing, downloading, renaming, moving, and deleting
# ANY file in the user's Drive. Narrower scopes like 'drive.file' only see
# files created by the bot, breaking browse/search/download.
# Compensating controls:
#   1. TOKEN_ENCRYPTION_KEY mandatory in production (enforced in init_db)
#   2. Audit logging on all destructive operations (delete/rename/move)
#   3. Token revocation on logout (revoke_token in this module)
#   4. PKCE + CSRF protection on OAuth flow
SCOPES = ["https://www.googleapis.com/auth/drive"]
CREDENTIALS_FILE = os.environ.get("GOOGLE_CREDENTIALS_FILE", "credentials.json")
REDIRECT_URI = os.environ.get("OAUTH_REDIRECT_URI", "http://localhost:8000/oauth/callback")

# Google OAuth endpoints (standard, never change)
_GOOGLE_AUTH_URI  = "https://accounts.google.com/o/oauth2/auth"
_GOOGLE_TOKEN_URI = "https://oauth2.googleapis.com/token"


def _get_client_config() -> dict:
    """
    Build the OAuth client config dict.

    Priority:
      1. GOOGLE_CLIENT_ID + GOOGLE_CLIENT_SECRET env vars  → production
      2. credentials.json file                              → local dev
    """
    client_id     = os.environ.get("GOOGLE_CLIENT_ID")
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET")

    if client_id and client_secret:
        logger.debug("Using OAuth credentials from environment variables.")
        return {
            "web": {
                "client_id":     client_id,
                "client_secret": client_secret,
                "auth_uri":      _GOOGLE_AUTH_URI,
                "token_uri":     _GOOGLE_TOKEN_URI,
                "redirect_uris": [REDIRECT_URI],
            }
        }

    if os.path.isfile(CREDENTIALS_FILE):
        # V-NEW-06: Warn if credentials.json is present in production
        if os.environ.get("RAILWAY_PUBLIC_DOMAIN"):
            logger.warning(
                "credentials.json found in production — use env vars instead."
            )
        logger.debug("Using OAuth credentials from %s.", CREDENTIALS_FILE)
        with open(CREDENTIALS_FILE) as f:
            return json.load(f)

    raise FileNotFoundError(
        "No Google OAuth credentials found. "
        "Set GOOGLE_CLIENT_ID + GOOGLE_CLIENT_SECRET env vars, "
        f"or place a {CREDENTIALS_FILE} file in the project root."
    )


def _build_flow() -> Flow:
    """Create a configured OAuth Flow from the resolved client config."""
    config = _get_client_config()
    return Flow.from_client_config(
        config,
        scopes=SCOPES,
        redirect_uri=REDIRECT_URI,
    )


# ── PKCE helpers ──────────────────────────────────────────────────────────────

def _generate_pkce_pair() -> tuple[str, str]:
    """Generate a PKCE code_verifier and code_challenge (S256 method).

    Returns:
        (code_verifier, code_challenge)
    """
    # code_verifier: 43-128 characters of unreserved URI characters
    code_verifier = secrets.token_urlsafe(64)

    # code_challenge: BASE64URL(SHA256(code_verifier))
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")

    return code_verifier, code_challenge


def get_auth_url(telegram_id: int) -> str:
    """Return the OAuth consent URL. Includes CSRF nonce + PKCE in state."""
    nonce = secrets.token_urlsafe(32)
    code_verifier, code_challenge = _generate_pkce_pair()

    # Store nonce + verifier for later exchange
    store_oauth_state(telegram_id, nonce, code_verifier)

    flow = _build_flow()
    auth_url, _ = flow.authorization_url(
        access_type="offline",
        prompt="consent",
        state=f"{telegram_id}:{nonce}",
        code_challenge=code_challenge,
        code_challenge_method="S256",
    )
    return auth_url


def exchange_code(code: str, state: str) -> int:
    """Exchange the OAuth code for tokens and persist them.

    Args:
        code:  The authorization code from Google.
        state: The state parameter (format: "telegram_id:nonce").

    Returns:
        The Telegram user ID extracted from the verified state.

    Raises:
        ValueError: If state verification fails (CSRF protection).
    """
    parts = state.split(":", 1)
    if len(parts) != 2:
        raise ValueError("Invalid OAuth state format.")

    try:
        telegram_id = int(parts[0])
    except (ValueError, TypeError):
        raise ValueError("Invalid OAuth state: bad user ID.")

    nonce = parts[1]
    valid, code_verifier = verify_oauth_state(telegram_id, nonce)
    if not valid:
        raise ValueError("OAuth state verification failed — possible CSRF. Try /login again.")

    flow = _build_flow()
    # Include PKCE code_verifier in token exchange if available
    if code_verifier:
        flow.fetch_token(code=code, code_verifier=code_verifier)
    else:
        flow.fetch_token(code=code)

    creds = flow.credentials
    upsert_user(
        telegram_id=telegram_id,
        token=creds.token or "",
        refresh_token=creds.refresh_token or "",
    )
    # Log success without exposing any token data
    logger.info("OAuth flow completed for user %s.", telegram_id)
    return telegram_id


def revoke_token(telegram_id: int) -> bool:
    """Revoke the user's OAuth token at Google before deleting locally.

    Uses stdlib http.client to avoid adding a requests dependency.
    Returns True if revocation succeeded, False otherwise.
    """
    from db.models import get_user
    row = get_user(telegram_id)
    if not row or not row["token"]:
        return False
    try:
        # Use the refresh_token if available (it has longer lifetime),
        # otherwise fall back to the access token.
        token_to_revoke = row.get("refresh_token") or row["token"]
        conn = HTTPSConnection("oauth2.googleapis.com", timeout=5)
        params = urlencode({"token": token_to_revoke})
        conn.request(
            "POST",
            "/revoke",
            body=params,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        resp = conn.getresponse()
        success = resp.status == 200
        conn.close()
        if success:
            logger.info("OAuth token revoked for user %s.", telegram_id)
        else:
            logger.warning(
                "Token revocation returned %s for user %s.", resp.status, telegram_id
            )
        return success
    except Exception:
        logger.warning("Token revocation failed for user %s.", telegram_id)
        return False


def get_credentials(telegram_id: int) -> Credentials | None:
    """Load and (if expired) refresh credentials for a user."""
    row = get_user(telegram_id)
    if not row or not row["token"]:
        return None

    config    = _get_client_config()
    installed = config.get("installed") or config.get("web")
    if not installed:
        return None

    creds = Credentials(
        token=row["token"],
        refresh_token=row["refresh_token"],
        token_uri=_GOOGLE_TOKEN_URI,
        client_id=installed["client_id"],
        client_secret=installed["client_secret"],
        scopes=SCOPES,
    )

    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except RefreshError as e:
            logger.warning("Token refresh failed for user %s: %s", telegram_id, str(e)[:200])
            return None
        upsert_user(
            telegram_id=telegram_id,
            token=creds.token or "",
            refresh_token=creds.refresh_token or "",
        )

    return creds
