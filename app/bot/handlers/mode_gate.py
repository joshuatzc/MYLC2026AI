"""
bot/handlers/mode_gate.py – Outer middleware that silently blocks all updates
when the game_mode is set to "icebreaker".

During the ice breaker day, only the admin (via SSH + bash script or the API)
needs to interact with the system.  Regular Telegram users see nothing.
"""
from __future__ import annotations

from typing import Any, Callable, Awaitable

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from app.database import AsyncSessionLocal
from app.services.icebreaker import get_game_mode


class GameModeGateMiddleware(BaseMiddleware):
    """
    Outer middleware attached to the Dispatcher.

    - If game_mode == "icebreaker"  →  drop the update silently (no response).
    - If game_mode == "nightgame"   →  pass through normally.
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        async with AsyncSessionLocal() as db:
            mode = await get_game_mode(db)

        if mode == "icebreaker":
            # Silently drop — no reply, no error
            return None

        return await handler(event, data)
