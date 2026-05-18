"""
services/stepup_auth.py
Step-up verification via email OTP for sensitive actions.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta

from db import models
from services import email_service

OTP_LENGTH = 6
OTP_TTL_MINUTES = 10
VERIFIED_WINDOW_MINUTES = 5
RESEND_COOLDOWN_SECONDS = 60
MAX_ATTEMPTS = 5

_SECRET = os.environ.get("TOKEN_ENCRYPTION_KEY", "")


def _hash_code(code: str) -> str:
    base = f"{_SECRET}:{code}" if _SECRET else code
    return hashlib.sha256(base.encode("utf-8")).hexdigest()


def _mask_email(email: str) -> str:
    if "@" not in email:
        return email
    name, domain = email.split("@", 1)
    if len(name) <= 2:
        masked_name = name[0] + "*"
    else:
        masked_name = name[0] + "*" * (len(name) - 2) + name[-1]
    return f"{masked_name}@{domain}"


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _now_iso() -> str:
    return datetime.now().isoformat()


async def request_verification(telegram_id: int, action_label: str) -> dict:
    """
    Ensure a verification code is sent if needed.
    Returns a status dict for UI messaging.
    """
    now = datetime.now()
    if models.is_stepup_verified(telegram_id, now.isoformat()):
        return {"status": "verified"}

    email = models.get_user_email(telegram_id)
    if not email:
        return {"status": "no_email"}

    state = models.get_stepup_state(telegram_id)
    if state and state.get("code_hash") and state.get("expires_at"):
        exp = _parse_dt(state.get("expires_at"))
        if exp and exp > now:
            last_sent = _parse_dt(state.get("last_sent_at"))
            if last_sent:
                retry_after = RESEND_COOLDOWN_SECONDS - int((now - last_sent).total_seconds())
                if retry_after > 0:
                    return {
                        "status": "cooldown",
                        "retry_after": retry_after,
                        "email": _mask_email(email),
                    }

    code = f"{secrets.randbelow(10 ** OTP_LENGTH):0{OTP_LENGTH}d}"
    expires_at = (now + timedelta(minutes=OTP_TTL_MINUTES)).isoformat()
    models.set_stepup_code(telegram_id, _hash_code(code), expires_at, now.isoformat())

    subject = f"NotesBuddy Verification Code ({action_label})"
    body = (
        "Your verification code is:\n\n"
        f"  {code}\n\n"
        f"This code expires in {OTP_TTL_MINUTES} minutes.\n"
        "If you did not request this, you can ignore this email."
    )

    sent = await asyncio.to_thread(
        email_service.send_email,
        email,
        subject,
        body,
    )
    if not sent:
        models.clear_stepup_code(telegram_id)
        return {"status": "email_failed"}

    return {
        "status": "sent",
        "email": _mask_email(email),
        "ttl": OTP_TTL_MINUTES,
    }


def verify_code(telegram_id: int, code: str) -> dict:
    """Validate a user-supplied OTP code."""
    now = datetime.now()
    state = models.get_stepup_state(telegram_id)
    if not state:
        return {"status": "no_pending"}

    verified_until = _parse_dt(state.get("verified_until"))
    if verified_until and verified_until > now:
        remaining = int((verified_until - now).total_seconds() // 60) + 1
        return {"status": "already_verified", "remaining": remaining}

    if not state.get("code_hash") or not state.get("expires_at"):
        return {"status": "no_pending"}

    exp = _parse_dt(state.get("expires_at"))
    if not exp or exp <= now:
        models.clear_stepup_code(telegram_id)
        return {"status": "expired"}

    if state.get("attempts", 0) >= MAX_ATTEMPTS:
        models.clear_stepup_code(telegram_id)
        return {"status": "locked"}

    if hmac.compare_digest(state["code_hash"], _hash_code(code)):
        verified_until = (now + timedelta(minutes=VERIFIED_WINDOW_MINUTES)).isoformat()
        models.set_stepup_verified(telegram_id, verified_until)
        return {"status": "verified", "window": VERIFIED_WINDOW_MINUTES}

    attempts = models.increment_stepup_attempt(telegram_id)
    remaining = max(0, MAX_ATTEMPTS - attempts)
    if remaining <= 0:
        models.clear_stepup_code(telegram_id)
        return {"status": "locked"}
    return {"status": "invalid", "remaining": remaining}
