"""
main.py
Entry point — starts the FastAPI server (for OAuth callback) and the
Telegram bot (polling) concurrently using asyncio.

Production deployment (Railway):
  Railway injects PORT as an env var. The app binds to 0.0.0.0:$PORT.
  Set OAUTH_REDIRECT_URI to https://<app>.up.railway.app/oauth/callback.

Security:
  - Security headers middleware on all HTTP responses
  - Sensitive data log filter
  - Username validation on OAuth redirect
"""

import asyncio
import json
import logging
import os
import re

from dotenv import load_dotenv
load_dotenv()   # ← loads .env before anything reads env vars

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from starlette.middleware.base import BaseHTTPMiddleware

from telegram.ext import Application

from db.models import init_db, cleanup_expired_states
from bot.handlers import register_handlers
from drive.auth import exchange_code

# ── Logging ───────────────────────────────────────────────────────────────────


class _SensitiveDataFilter(logging.Filter):
    """Redact potential tokens, keys, and credentials from log output."""
    _patterns = [
        # OAuth tokens (long base64-like strings)
        re.compile(r'ya29\.[A-Za-z0-9_-]{20,}'),
        # Generic long secrets (40+ chars)
        re.compile(r'(?<=["\s=:])[A-Za-z0-9_/+-]{40,}'),
        # Fernet tokens
        re.compile(r'gAAAAA[A-Za-z0-9_/+-]{20,}'),
    ]

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            for pattern in self._patterns:
                record.msg = pattern.sub('[REDACTED]', record.msg)
        return True


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
# Apply sensitive data filter to root logger
logging.getLogger().addFilter(_SensitiveDataFilter())
logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
HOST      = os.environ.get("HOST", "0.0.0.0")
PORT      = int(os.environ.get("PORT", "8000"))

# ── Globals set after bot starts ──────────────────────────────────────────────
# These let the OAuth callback reach the running bot instance.
_bot_app: Application | None = None
_bot_username: str | None    = None

# ── OAuth success page template ───────────────────────────────────────────────
# Loaded from external HTML file so linters don't try to parse it as Python.
_TEMPLATE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")
_SUCCESS_PAGE = open(os.path.join(_TEMPLATE_DIR, "success.html"), encoding="utf-8").read()

# ── Security Headers Middleware ───────────────────────────────────────────────

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add security headers to all HTTP responses."""
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "style-src 'self' 'unsafe-inline'; "
            "script-src 'self' 'unsafe-inline'; "
            "frame-ancestors 'none';"
        )
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response


# ── FastAPI app ───────────────────────────────────────────────────────────────
web_app = FastAPI(
    title="Drive Bot OAuth Server",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
web_app.add_middleware(SecurityHeadersMiddleware)


# ── Username validation ───────────────────────────────────────────────────────
_USERNAME_PATTERN = re.compile(r'^[A-Za-z0-9_]{3,32}$')

def _safe_username(username: str | None) -> str:
    """Validate and return a safe bot username for redirect URLs."""
    if not username:
        return ""
    if not _USERNAME_PATTERN.match(username):
        logger.warning("Bot username failed validation: %s", username[:32])
        return ""
    return username


@web_app.get("/oauth/callback")
async def oauth_callback(request: Request):
    """
    Google redirects here after the user authorizes access.
    After exchanging the code (with CSRF nonce + PKCE verification):
      1. Sends a Telegram message to the user (login success + main menu).
      2. Redirects the browser straight back to the Telegram bot.
    """
    from html import escape

    params = dict(request.query_params)
    code   = params.get("code")
    state  = params.get("state")   # format: "telegram_id:nonce"

    if not code or not state:
        return HTMLResponse("<h2>❌ Missing parameters.</h2>", status_code=400)

    # Validate state format before processing
    if len(state) > 200 or not re.match(r'^\d+:[A-Za-z0-9_-]+$', state):
        return HTMLResponse("<h2>❌ Invalid request.</h2>", status_code=400)

    try:
        # exchange_code now parses state, verifies CSRF nonce + PKCE, and returns telegram_id
        telegram_id = exchange_code(code, state)

        # ── Notify the user inside Telegram ───────────────────────────────────
        if _bot_app is not None:
            from bot.formatter import login_successful, email_setup_prompt
            from bot.ui import post_login_keyboard, stepup_email_entry_keyboard
            from db import models
            try:
                await _bot_app.bot.send_message(
                    chat_id=telegram_id,
                    text=login_successful(),
                    reply_markup=post_login_keyboard(),
                )
                if not models.get_user_email(telegram_id):
                    await _bot_app.bot.send_message(
                        chat_id=telegram_id,
                        text=email_setup_prompt(),
                        reply_markup=stepup_email_entry_keyboard(),
                    )
            except Exception:
                logger.warning("Could not send success message to user %s", telegram_id)

        # ── Redirect browser back to the Telegram bot ─────────────────────────
        safe_name = _safe_username(_bot_username)
        safe_name_escaped = escape(safe_name) if safe_name else ""
        web_url = f"https://t.me/{safe_name_escaped}" if safe_name_escaped else "https://t.me"
        app_url = f"tg://resolve?domain={safe_name_escaped}" if safe_name_escaped else ""

        # Show a brief page first, then redirect — works on all platforms
        # V-NEW-07: Use JSON serialization for JS-safe URL injection
        page = _SUCCESS_PAGE.replace(
            "{{WEB_URL}}", json.dumps(web_url)[1:-1]  # strip outer quotes
        ).replace(
            "{{APP_URL}}", json.dumps(app_url)[1:-1]
        )
        return HTMLResponse(page)

    except ValueError as e:
        # CSRF/PKCE verification failure — expected error, don't expose details
        logger.warning("OAuth state verification failed: %s", str(e)[:100])
        return HTMLResponse(
            "<h2>❌ Authorization failed</h2>"
            "<p>The login link may have expired. Please try again.</p>",
            status_code=400,
        )
    except Exception as e:
        logger.exception("OAuth callback error")
        return HTMLResponse(
            "<h2>❌ Authorization failed</h2>"
            "<p>Something went wrong. Please try again or contact support.</p>",
            status_code=500,
        )


@web_app.get("/health")
async def health():
    return {"status": "ok"}


# ── Bot ───────────────────────────────────────────────────────────────────────
def build_bot() -> Application:
    if not BOT_TOKEN:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN environment variable is not set.\n"
            "  • Railway: Add it in the Variables tab of your service dashboard.\n"
            "  • Local:   Add it to your .env file and restart."
        )
    from telegram.ext import AIORateLimiter
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .rate_limiter(AIORateLimiter(max_retries=3))
        .build()
    )
    register_handlers(app)
    return app


# ── Main ──────────────────────────────────────────────────────────────────────
async def main() -> None:
    global _bot_app, _bot_username

    # ── Startup banner ────────────────────────────────────────────────────────
    railway_domain = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "")
    env_label      = "Railway" if railway_domain else "Local"
    logger.info("="*60)
    logger.info("  Notes-Buddy — Telegram Google Drive Bot")
    logger.info("  Environment : %s", env_label)
    if railway_domain:
        logger.info("  Public URL  : https://%s", railway_domain)
    logger.info("  OAuth URI   : %s", os.environ.get("OAUTH_REDIRECT_URI", "(default localhost)"))
    logger.info("  Server      : %s:%s", HOST, PORT)
    logger.info("="*60)

    # Initialise DB
    init_db()
    cleanup_expired_states()
    logger.info("Database initialised.")

    # Build & start bot
    _bot_app = build_bot()
    await _bot_app.initialize()
    await _bot_app.start()
    assert _bot_app.updater is not None, "Bot updater failed to initialise"
    await _bot_app.updater.start_polling(drop_pending_updates=True)

    # Store the bot's username so the OAuth callback can build the redirect URL
    _bot_username = _bot_app.bot.username
    logger.info("Telegram bot started (polling) as @%s.", _bot_username)

    # V-NEW-03: Periodic cleanup of expired OAuth states
    async def _periodic_cleanup():
        """Clean expired OAuth states every 30 minutes."""
        while True:
            await asyncio.sleep(1800)  # 30 minutes
            try:
                cleanup_expired_states()
                logger.debug("Periodic OAuth state cleanup completed.")
            except Exception:
                logger.warning("Periodic state cleanup failed.")

    asyncio.create_task(_periodic_cleanup())

    # Start FastAPI (OAuth server)
    config = uvicorn.Config(web_app, host=HOST, port=PORT, log_level="info")
    server = uvicorn.Server(config)
    logger.info("OAuth server listening on http://%s:%s", HOST, PORT)
    await server.serve()

    # Graceful shutdown
    logger.info("Shutting down...")
    assert _bot_app.updater is not None
    await _bot_app.updater.stop()
    await _bot_app.stop()
    await _bot_app.shutdown()
    logger.info("Shutdown complete.")


if __name__ == "__main__":
    asyncio.run(main())
