"""
bot/handlers/church.py – My Church dashboard handler.
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


@router.message(F.text == "⛪ My Church")
async def handle_my_church(message: Message) -> None:
    chat_id = str(message.chat.id)
    group_id = await require_group(message, chat_id)
    if group_id is None:
        return

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Group).where(Group.id == group_id))
        group = result.scalar_one_or_none()
        if group is None:
            await message.answer("⚠️ Your selected group was not found.")
            return

        tier_name = game_logic.get_church_tier_name(group.church_level)
        max_occupancy = game_logic.get_max_occupancy(group.church_level)
        bonus_pct = round((await game_logic.get_group_church_bonus(db, group_id)) * 100)

    # Visual progress bar calculation
    percentage = min(1.0, group.population / max_occupancy)
    filled_blocks = round(percentage * 10)
    bar = f"[{'█' * filled_blocks}{'░' * (10 - filled_blocks)}]"

    # Dashboard formatting
    lines = [
        f"⛪ *{group.name} — Church Dashboard*",
        f"━━━━━━━━━━━━━━━━━━━",
        f"🏛️ *Church Tier:* {tier_name} (Level {group.church_level})",
        f"📈 *Station Earning Bonus:* `+{bonus_pct}%`",
        f"",
        f"👥 *Occupancy:* {bar} *{int(group.population):,}/{max_occupancy:,}*",
    ]

    if group.population >= max_occupancy:
        lines.append(f"⚠️ *WARNING:* Your church has reached *Max Occupancy*! Any further station upgrades will not increase your population. Upgrade your church at a physical station to expand!")
    elif group.population >= max_occupancy * 0.8:
        lines.append(f"⚠️ *ALERT:* Your church is near capacity! Upgrade soon to avoid hitting the cap.")

    # Next upgrade info
    next_level = group.church_level + 1
    if next_level <= 3:
        next_tier_name = game_logic.get_church_tier_name(next_level)
        min_pop = game_logic.get_church_min_pop(next_level)
        steal_amt = game_logic.get_church_steal_amount(next_level)
        
        status_symbol = "✅" if group.population >= min_pop else "❌"
        
        lines.append(
            f"\n💡 *Prerequisite for {next_tier_name} (Level {next_level}):*\n"
            f"  • {status_symbol} Required population: *{min_pop}* (Current: *{int(group.population)}*)\n"
            f"  • ⚡ Perk: Steal **{steal_amt}** members from a **{tier_name}** at upgrade time!"
        )
        
        # Display purchased hints for this next level
        async with AsyncSessionLocal() as db:
            from app.models import StationLevel, Station
            levels_res = await db.execute(
                select(StationLevel)
                .join(StationLevel.station)
                .where(Station.name == "Church Upgrade")
                .where(StationLevel.level_number == next_level)
            )
            next_level_obj = levels_res.scalar_one_or_none()
            
            if next_level_obj:
                purchased = await game_logic.get_purchased_hint_numbers(db, group.id, next_level_obj.id)
                if purchased:
                    lines.append(f"\n💡 *Unlocked hints for {next_tier_name}:*")
                    for h_num in sorted(purchased):
                        hint_text = game_logic.CHURCH_HINTS.get(next_level, {}).get(h_num, "No hint available.")
                        lines.append(f"  • *Hint {h_num}:* {hint_text}")
    else:
        lines.append(f"\n🎉 *Maximum Church Tier reached!* You have unlocked the legendary Giga Sanctuary.")

    await message.answer("\n".join(lines), parse_mode="Markdown")
