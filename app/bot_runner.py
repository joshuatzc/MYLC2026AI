"""
bot.py – aiogram Dispatcher setup and router registration.

Run standalone with:  python -m app.bot_runner
Or launched from main.py on startup.
"""
from __future__ import annotations

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from app.bot.handlers import common, journey, leader, prerequisites, church
from app.config import settings


def create_bot() -> Bot:
    return Bot(
        token=settings.TELEGRAM_BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )


def create_dispatcher() -> Dispatcher:
    dp = Dispatcher()

    # Register routers – ORDER MATTERS for overlapping filters.
    # common must come before leader so the catch-all text handler in
    # leader.py only fires when no named button matches.
    dp.include_router(common.router)
    dp.include_router(church.router)
    dp.include_router(journey.router)
    dp.include_router(prerequisites.router)
    dp.include_router(leader.router)

    return dp


async def start_polling() -> None:
    """Start the bot in long-polling mode (for development)."""
    from app.database import init_db

    await init_db()

    bot = create_bot()
    dp = create_dispatcher()

    await dp.start_polling(bot, allowed_updates=["message", "callback_query"])

