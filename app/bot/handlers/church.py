"""
bot/handlers/church.py – My Church dashboard handler.
"""
from __future__ import annotations

from aiogram import F, Router
from aiogram.types import Message, CallbackQuery
from app.bot.keyboards import my_church_dashboard_keyboard, hint_carousel_keyboard
from app.bot.utils import require_group
from app.database import AsyncSessionLocal
from app.models import Group
from app.services import game_logic
from sqlalchemy import select
from sqlalchemy.orm import selectinload

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
    reply_markup = None
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
        reply_markup = None
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
                    hint_numbers_str = ", ".join(f"Hint {h_num}" for h_num in sorted(purchased))
                    lines.append(f"  • Unlocked: *{hint_numbers_str}*")
                    lines.append("  • Tap the button below to view unlocked photo hints!")
                    reply_markup = my_church_dashboard_keyboard(next_level_obj.id)
    else:
        lines.append(f"\n🎉 *Maximum Church Tier reached!* You have unlocked the legendary Giga Sanctuary.")

    await message.answer("\n".join(lines), parse_mode="Markdown", reply_markup=reply_markup)


# ---------------------------------------------------------------------------
# Purchased Hints Interactive Photo Carousel Handlers
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("view_hints_nav:"))
async def cb_view_hints_nav(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    level_id = int(parts[1])
    current_idx = int(parts[2])
    chat_id = str(callback.message.chat.id)

    async with AsyncSessionLocal() as db:
        group_id = await require_group(callback.message, chat_id)
        if group_id is None:
            await callback.answer("No group selected.", show_alert=True)
            return

        from app.models import StationLevel
        level_res = await db.execute(
            select(StationLevel)
            .options(selectinload(StationLevel.station))
            .where(StationLevel.id == level_id)
        )
        level_obj = level_res.scalar_one_or_none()
        if not level_obj:
            await callback.answer("Level not found.", show_alert=True)
            return

        purchased = await game_logic.get_purchased_hint_numbers(db, group_id, level_id)
        if not purchased:
            await callback.answer("No hints purchased yet for this level.", show_alert=True)
            return

        # Sort hint numbers to maintain order (e.g. 1, 2, 3)
        purchased = sorted(purchased)
        total_hints = len(purchased)
        
        # Guard index bounds
        if current_idx < 0 or current_idx >= total_hints:
            current_idx = 0
            
        hint_number = purchased[current_idx]
        level_num = level_obj.level_number

        # Get hint info
        hint_data = game_logic.CHURCH_HINTS.get(level_num, {}).get(hint_number, {"text": "No hint available.", "photo": None})
        hint_text = hint_data.get("text", "No hint available.")
        photo_filename = hint_data.get("photo")

    # Resolve photo path – anchor to project root (handlers/ -> bot/ -> app/ -> project root)
    import os
    photo_path = None
    if photo_filename:
        _here = os.path.dirname(os.path.abspath(__file__))
        _project_root = os.path.abspath(os.path.join(_here, "..", "..", ".."))
        candidate = os.path.join(_project_root, "assets", "hints", photo_filename)
        if os.path.exists(candidate):
            photo_path = candidate

    # Build navigation keyboard
    keyboard = hint_carousel_keyboard(level_id, current_idx, total_hints)
    caption = (
        f"💡 *Hint {hint_number}* (of {total_hints} unlocked)\n"
        f"━━━━━━━━━━━━━━━━━━━\n\n"
        f"_{hint_text}_"
    )

    from aiogram.types import FSInputFile, InputMediaPhoto

    # Check if the message is already a photo/carousel message
    is_photo_message = callback.message.photo is not None

    if photo_path:
        photo_input = FSInputFile(photo_path)
        if is_photo_message:
            # Edit existing photo message in-place
            try:
                media = InputMediaPhoto(media=photo_input, caption=caption, parse_mode="Markdown")
                await callback.message.edit_media(media=media, reply_markup=keyboard)
                await callback.answer()
            except Exception:
                # Fallback: delete and send new
                await callback.message.delete()
                await callback.message.answer_photo(photo=photo_input, caption=caption, parse_mode="Markdown", reply_markup=keyboard)
                await callback.answer()
        else:
            # First load from text dashboard: send new photo message
            await callback.message.answer_photo(photo=photo_input, caption=caption, parse_mode="Markdown", reply_markup=keyboard)
            await callback.answer()
    else:
        # Text-only hint
        if is_photo_message:
            # Delete photo message and send text
            await callback.message.delete()
            await callback.message.answer(caption, parse_mode="Markdown", reply_markup=keyboard)
            await callback.answer()
        else:
            # Edit in place
            try:
                await callback.message.edit_text(caption, parse_mode="Markdown", reply_markup=keyboard)
                await callback.answer()
            except Exception:
                await callback.message.answer(caption, parse_mode="Markdown", reply_markup=keyboard)
                await callback.answer()


@router.callback_query(F.data == "hints_carousel_close")
async def cb_hints_carousel_close(callback: CallbackQuery) -> None:
    try:
        await callback.message.delete()
    except Exception:
        await callback.message.edit_text("Closed hint viewer.")
    await callback.answer()
