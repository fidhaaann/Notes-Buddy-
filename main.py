"""
main.py
Entry point — starts the FastAPI server (for OAuth callback) and the
Telegram bot (polling) concurrently using asyncio.
"""

import asyncio
import logging
import os

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse

from telegram.ext import Application

from db.models import init_db
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
HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "8000"))

# ── FastAPI app ───────────────────────────────────────────────────────────────
web_app = FastAPI(title="Drive Bot OAuth Server")


@web_app.get("/oauth/callback", response_class=HTMLResponse)
async def oauth_callback(request: Request):
    """Google redirects here after the user authorizes access."""
    params = dict(request.query_params)
    code = params.get("code")
    state = params.get("state")  # telegram_id

    if not code or not state:
        return HTMLResponse("<h2>❌ Missing parameters.</h2>", status_code=400)

    try:
        telegram_id = int(state)
        exchange_code(code, telegram_id)
        return HTMLResponse(
            "<h2>✅ Authorization successful!</h2>"
            "<p>You can close this tab and return to Telegram.</p>"
        )
    except Exception as e:
        logger.exception("OAuth callback error")
        return HTMLResponse(f"<h2>❌ Error: {e}</h2>", status_code=500)


@web_app.get("/health")
async def health():
    return {"status": "ok"}


# ── Bot ───────────────────────────────────────────────────────────────────────
def build_bot() -> Application:
    if not BOT_TOKEN:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN environment variable is not set.\n"
            "Export it before running:  export TELEGRAM_BOT_TOKEN=<your-token>"
        )
    app = Application.builder().token(BOT_TOKEN).build()
    register_handlers(app)
    return app


# ── Main ──────────────────────────────────────────────────────────────────────
async def main() -> None:
    # Initialise DB
    init_db()
    logger.info("Database initialised.")

    # Build bot
    bot_app = build_bot()

    # Start polling (non-blocking)
    await bot_app.initialize()
    await bot_app.start()
    await bot_app.updater.start_polling(drop_pending_updates=True)
    logger.info("Telegram bot started (polling).")

    # Start FastAPI
    config = uvicorn.Config(web_app, host=HOST, port=PORT, log_level="info")
    server = uvicorn.Server(config)
    logger.info(f"OAuth server listening on http://{HOST}:{PORT}")
    await server.serve()

    # Graceful shutdown
    await bot_app.updater.stop()
    await bot_app.stop()
    await bot_app.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
