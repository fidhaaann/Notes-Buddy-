"""
main.py
Entry point — starts the FastAPI server (for OAuth callback) and the
Telegram bot (polling) concurrently using asyncio.

Production deployment (Railway):
  Railway injects PORT as an env var. The app binds to 0.0.0.0:$PORT.
  Set OAUTH_REDIRECT_URI to https://<app>.up.railway.app/oauth/callback.
"""

import asyncio
import logging
import os

from dotenv import load_dotenv
load_dotenv()   # ← loads .env before anything reads env vars

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse

from telegram.ext import Application

from db.models import init_db, cleanup_expired_states
from bot.handlers import register_handlers
from drive.auth import exchange_code

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
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

# ── FastAPI app ───────────────────────────────────────────────────────────────
web_app = FastAPI(
    title="Drive Bot OAuth Server",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


@web_app.get("/oauth/callback")
async def oauth_callback(request: Request):
    """
    Google redirects here after the user authorizes access.
    After exchanging the code (with CSRF nonce verification):
      1. Sends a Telegram message to the user (login success + main menu).
      2. Redirects the browser straight back to the Telegram bot.
    """
    from html import escape

    params = dict(request.query_params)
    code   = params.get("code")
    state  = params.get("state")   # format: "telegram_id:nonce"

    if not code or not state:
        return HTMLResponse("<h2>❌ Missing parameters.</h2>", status_code=400)

    try:
        # exchange_code now parses state, verifies CSRF nonce, and returns telegram_id
        telegram_id = exchange_code(code, state)

        # ── Notify the user inside Telegram ───────────────────────────────────
        if _bot_app is not None:
            from bot.ui import main_menu_keyboard
            try:
                await _bot_app.bot.send_message(
                    chat_id=telegram_id,
                    text=(
                        "✅ Google Drive Connected!\n\n"
                        "Your account has been authorized successfully.\n"
                        "Use the menu below to start managing your files."
                    ),
                    reply_markup=main_menu_keyboard(),
                )
            except Exception:
                logger.warning("Could not send success message to user %s", telegram_id)

        # ── Redirect browser back to the Telegram bot ─────────────────────────
        safe_username = escape(_bot_username) if _bot_username else ""
        web_url = "https://t.me/" + safe_username if safe_username else "https://t.me"
        # tg:// protocol opens Telegram app directly on mobile
        app_url = "tg://resolve?domain=" + safe_username if safe_username else ""

        # Show a brief page first, then redirect — works on all platforms
        page = _SUCCESS_PAGE.replace("{{WEB_URL}}", web_url).replace("{{APP_URL}}", app_url)
        return HTMLResponse(page)

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
