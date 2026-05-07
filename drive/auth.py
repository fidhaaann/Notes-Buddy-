"""
drive/auth.py
Google OAuth 2.0 helpers.
Each Telegram user has their own OAuth token stored in SQLite.

Supports two credential sources (checked in order):
  1. Environment variables: GOOGLE_CLIENT_ID + GOOGLE_CLIENT_SECRET
     (preferred for production / Railway deployment)
  2. File: credentials.json in the project root
     (convenient for local development)
"""

import json
import logging
import os

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from google.auth.transport.requests import Request

from db.models import get_user, upsert_user

logger = logging.getLogger(__name__)

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

    Returns a dict in the format expected by
    google_auth_oauthlib.flow.Flow.from_client_config().
    """
    client_id     = os.environ.get("GOOGLE_CLIENT_ID")
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET")

    if client_id and client_secret:
        logger.info("Using OAuth credentials from environment variables.")
        return {
            "web": {
                "client_id":     client_id,
                "client_secret": client_secret,
                "auth_uri":      _GOOGLE_AUTH_URI,
                "token_uri":     _GOOGLE_TOKEN_URI,
                "redirect_uris": [REDIRECT_URI],
            }
        }

    # Fallback: read from file
    if os.path.isfile(CREDENTIALS_FILE):
        logger.info("Using OAuth credentials from %s.", CREDENTIALS_FILE)
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


def get_auth_url(telegram_id: int) -> str:
    """Return the OAuth consent URL for a user."""
    flow = _build_flow()
    # Pass telegram_id as 'state' so the callback knows which user to update
    auth_url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
        state=str(telegram_id),
    )
    return auth_url


def exchange_code(code: str, telegram_id: int) -> None:
    """Exchange the OAuth code for tokens and persist them."""
    flow = _build_flow()
    flow.fetch_token(code=code)
    creds = flow.credentials
    upsert_user(
        telegram_id=telegram_id,
        token=creds.token,
        refresh_token=creds.refresh_token,
    )
    logger.info("OAuth tokens stored for user %s.", telegram_id)


def get_credentials(telegram_id: int) -> Credentials | None:
    """Load and (if expired) refresh credentials for a user."""
    row = get_user(telegram_id)
    if not row or not row["token"]:
        return None

    config   = _get_client_config()
    installed = config.get("installed") or config.get("web")

    creds = Credentials(
        token=row["token"],
        refresh_token=row["refresh_token"],
        token_uri=_GOOGLE_TOKEN_URI,
        client_id=installed["client_id"],
        client_secret=installed["client_secret"],
        scopes=SCOPES,
    )

    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        upsert_user(
            telegram_id=telegram_id,
            token=creds.token,
            refresh_token=creds.refresh_token,
        )

    return creds
