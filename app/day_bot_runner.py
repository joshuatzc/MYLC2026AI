"""
app/day_bot_runner.py – Day Bot (Ice Breaker Scorer) entry point.

Run as:
    python -m app.day_bot_runner          (local dev)
    docker compose up mylc-day-bot        (production)

This bot uses a *separate* Telegram bot token (DAY_BOT_TOKEN) so it's
completely independent of the night bot, but reads/writes the same database.
"""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from app.bot.day_handlers import scoring
from app.config import settings
from app.database import init_db

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def main() -> None:
    if not settings.DAY_BOT_TOKEN or settings.DAY_BOT_TOKEN == "YOUR_DAY_BOT_TOKEN_HERE":
        raise RuntimeError(
            "DAY_BOT_TOKEN is not set. Add it to your .env file.\n"
            "Create a new bot via @BotFather and paste the token as DAY_BOT_TOKEN."
        )

    logger.info("Initialising database…")
    await init_db()

    # Seed DB if needed (safe to run even if night bot already seeded it)
    from scripts.seed import seed
    from app.database import AsyncSessionLocal
    async with AsyncSessionLocal() as db:
        await seed(db)

    bot = Bot(
        token=settings.DAY_BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(scoring.router)

    logger.info("Day Bot (Ice Breaker Scorer) starting polling…")
    await dp.start_polling(bot, allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    asyncio.run(main())
