"""
main.py – FastAPI application entrypoint.

Starts the FastAPI app and, on startup, launches the Telegram bot
in the background using asyncio.
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import FastAPI

from app.database import init_db
from app.routers import admin

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Build the Biggest Church – Admin API",
    description="Backend API + admin interface for the church-building Telegram game.",
    version="1.0.0",
)

# Register routers
app.include_router(admin.router)


@app.on_event("startup")
async def on_startup() -> None:
    logger.info("Initialising database…")
    await init_db()

    logger.info("Seeding station data…")
    from scripts.seed import seed
    from app.database import AsyncSessionLocal
    async with AsyncSessionLocal() as db:
        await seed(db)

    # Launch bot polling in the background
    from app.bot_runner import create_bot, create_dispatcher

    bot = create_bot()
    dp = create_dispatcher()

    async def _polling():
        try:
            await dp.start_polling(bot, allowed_updates=["message", "callback_query"])
        except Exception as exc:
            logger.exception("Bot polling error: %s", exc)

    asyncio.create_task(_polling())
    logger.info("Telegram bot polling started.")

    # Launch AI News periodic broadcast in the background
    from app.services.ai_news import run_periodic_news_broadcast
    asyncio.create_task(run_periodic_news_broadcast(bot))
    logger.info("AI news background broadcast worker started.")



@app.get("/", tags=["health"])
async def health_check() -> dict:
    return {"status": "ok", "service": "church-game"}
