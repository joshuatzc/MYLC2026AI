"""
bot/handlers/journey.py – My Journey handler.
"""
from __future__ import annotations

from aiogram import F, Router
from aiogram.types import Message

from app.bot.utils import require_group
from app.database import AsyncSessionLocal
from app.models import Group
from app.services import game_logic
from sqlalchemy import select

router = Router()


@router.message(F.text == "My Journey")
async def handle_my_journey(message: Message) -> None:
    chat_id = str(message.chat.id)
    group_id = await require_group(message, chat_id)
    if group_id is None:
        return

    async with AsyncSessionLocal() as db:
        # Fetch group name and population
        result = await db.execute(select(Group).where(Group.id == group_id))
        group = result.scalar_one_or_none()
        if group is None:
            await message.answer("⚠️ Your selected group was not found.")
            return

        history = await game_logic.get_journey(db, group_id)

    lines = [
        f"⛪ *{group.name}*",
        f"👥 Current population: *{int(group.population):,}*\n",
    ]

    if not history:
        lines.append("_No upgrades completed yet._")
    else:
        lines.append("📜 *Upgrade History:*")
        for i, entry in enumerate(history, 1):
            lines.append(
                f"{i}. {entry['station_name']} – Level {entry['level_number']}\n"
                f"   ✅ {entry['completed_at']}\n"
                f"   👥 Population after: {int(entry['population_after']):,}"
            )

    await message.answer("\n".join(lines), parse_mode="Markdown")
