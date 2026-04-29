"""
drive/auth.py
Google OAuth 2.0 helpers.
Each Telegram user has their own OAuth token stored in SQLite.
"""

import json
import os
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from google.auth.transport.requests import Request

from db.models import get_user, upsert_user

SCOPES = ["https://www.googleapis.com/auth/drive"]
CREDENTIALS_FILE = os.environ.get("GOOGLE_CREDENTIALS_FILE", "credentials.json")
REDIRECT_URI = os.environ.get("OAUTH_REDIRECT_URI", "http://localhost:8000/oauth/callback")


def get_auth_url(telegram_id: int) -> str:
    """Return the OAuth consent URL for a user."""
    flow = Flow.from_client_secrets_file(
        CREDENTIALS_FILE,
        scopes=SCOPES,
        redirect_uri=REDIRECT_URI,
    )
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
    flow = Flow.from_client_secrets_file(
        CREDENTIALS_FILE,
        scopes=SCOPES,
        redirect_uri=REDIRECT_URI,
    )
    flow.fetch_token(code=code)
    creds = flow.credentials
    upsert_user(
        telegram_id=telegram_id,
        token=creds.token,
        refresh_token=creds.refresh_token,
    )


def get_credentials(telegram_id: int) -> Credentials | None:
    """Load and (if expired) refresh credentials for a user."""
    row = get_user(telegram_id)
    if not row or not row["token"]:
        return None

    with open(CREDENTIALS_FILE) as f:
        client_info = json.load(f)

    installed = client_info.get("installed") or client_info.get("web")
    creds = Credentials(
        token=row["token"],
        refresh_token=row["refresh_token"],
        token_uri="https://oauth2.googleapis.com/token",
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
