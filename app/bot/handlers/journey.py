"""
bot/handlers/journey.py – My Journey handler.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from aiogram import F, Router
from aiogram.types import Message

from app.bot.utils import require_group
from app.database import AsyncSessionLocal
from app.models import Group
from app.services import game_logic
from sqlalchemy import select

router = Router()


@router.message(F.text == "🗺️ My Journey")
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
        f"👥 Population: `{int(group.population):,}` members",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━",
    ]

    if not history:
        lines.append("_No history recorded yet._")
    else:
        lines.append("📜 *TIMELINE OF YOUR JOURNEY*\n")
        for i, entry in enumerate(history, 1):
            # Parse completed_at and convert to GMT+8
            dt = datetime.strptime(entry["completed_at"], "%Y-%m-%d %H:%M UTC")
            gmt8_dt = dt + timedelta(hours=8)
            time_str = gmt8_dt.strftime("%H:%M")

            if i > 1:
                lines.append("  │")

            if entry["type"] == "upgrade":
                if entry["station_name"] == "Church Upgrade":
                    lines.append(
                        f"🏛️ *{i}. {entry['station_name']} – Level {entry['level_number']}*\n"
                        f"  ├ 🕒 `{time_str}`\n"
                        f"  └ 👥 Population: `{int(entry['population_after']):,}`"
                    )
                else:
                    lines.append(
                        f"🔹 *{i}. {entry['station_name']} – Level {entry['level_number']}*\n"
                        f"  ├ 🕒 `{time_str}`\n"
                        f"  └ 👥 Population: `{int(entry['population_after']):,}`"
                    )
            elif entry["type"] == "theft_committed":
                lines.append(
                    f"⚡ *{i}. Congregation Theft*\n"
                    f"  ├ 🕒 `{time_str}`\n"
                    f"  ├ 👥 Stole: `+{int(entry['amount']):,}` members\n"
                    f"  └ 🎯 Target: *{entry['target_name']}*"
                )
            elif entry["type"] == "theft_suffered":
                lines.append(
                    f"⚠️ *{i}. Theft Suffered*\n"
                    f"  ├ 🕒 `{time_str}`\n"
                    f"  ├ 👥 Lost: `-{int(entry['amount']):,}` members\n"
                    f"  └ 👤 Stealer: *{entry['stealer_name']}*"
                )

    await message.answer("\n".join(lines), parse_mode="Markdown")
