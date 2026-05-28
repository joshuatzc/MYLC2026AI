"""
bot/utils.py – shared bot helpers.
"""
from __future__ import annotations

from aiogram.types import Message

from app.bot.keyboards import main_menu_keyboard
from app.database import AsyncSessionLocal
from app.services import auth


async def send_main_menu(message: Message, chat_id: str) -> None:
    """
    Send (or re-send) the persistent reply keyboard for this chat.
    Reads the current role from the DB and builds the appropriate keyboard.
    """
    async with AsyncSessionLocal() as db:
        role = await auth.get_role(db, chat_id)

    await message.answer(
        "Main Menu",
        reply_markup=main_menu_keyboard(role),
    )


async def require_group(message: Message, chat_id: str) -> int | None:
    """
    Ensure the user has a group selected.
    Returns group_id or None (sends an error message).
    """
    async with AsyncSessionLocal() as db:
        group_id = await auth.get_current_group_id(db, chat_id)

    if group_id is None:
        await message.answer(
            "⚠️ You haven't selected a group yet.\n"
            "Tap *🔄 Change Group* to choose one.",
            parse_mode="Markdown",
        )
    return group_id


async def require_leader(message: Message, chat_id: str) -> bool:
    """Returns True if the chat is in leader role."""
    async with AsyncSessionLocal() as db:
        role = await auth.get_role(db, chat_id)

    if role != "leader":
        await message.answer("⛔ This action requires leader access.")
        return False
    return True
