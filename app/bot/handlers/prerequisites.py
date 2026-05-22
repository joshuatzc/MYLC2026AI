"""
bot/handlers/prerequisites.py – Check Prerequisites flow.

Flow:
  Step 1 – show eligible levels (inline keyboard)
  Step 2 – eligible detail (hint)
  Step 3 – show locked levels
  Step 4 – locked detail (hint + missing prereqs)
"""
from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message

from app.bot.keyboards import (
    eligible_detail_keyboard,
    eligible_levels_keyboard,
    locked_detail_keyboard,
    locked_levels_keyboard,
)
from app.bot.utils import require_group
from app.database import AsyncSessionLocal
from app.models import Group, StationLevel
from app.services import auth, game_logic
from sqlalchemy import select
from sqlalchemy.orm import selectinload

router = Router()


async def _fetch_group_name(db, group_id: int) -> str:
    result = await db.execute(select(Group.name).where(Group.id == group_id))
    return result.scalar_one_or_none() or "?"


async def _fetch_level(db, level_id: int) -> StationLevel | None:
    result = await db.execute(
        select(StationLevel)
        .options(selectinload(StationLevel.station))
        .where(StationLevel.id == level_id)
    )
    return result.scalar_one_or_none()


# ---------------------------------------------------------------------------
# Step 1 – Eligible list (entry point from reply keyboard)
# ---------------------------------------------------------------------------

@router.message(F.text == "Check Prerequisites")
async def handle_check_prereqs(message: Message) -> None:
    chat_id = str(message.chat.id)
    group_id = await require_group(message, chat_id)
    if group_id is None:
        return

    async with AsyncSessionLocal() as db:
        group_name = await _fetch_group_name(db, group_id)
        eligible = await game_logic.get_eligible_levels(db, group_id)

    if not eligible:
        await message.answer(
            f"*{group_name}* has no eligible upgrades right now.\n"
            "Tap *Show locked options* to see what's still blocked.",
            parse_mode="Markdown",
            reply_markup=eligible_levels_keyboard([], show_locked_btn=True),
        )
        return

    await message.answer(
        f"For *{group_name}*, you are currently eligible to attempt:",
        parse_mode="Markdown",
        reply_markup=eligible_levels_keyboard(eligible),
    )


# ---------------------------------------------------------------------------
# Step 2 – Eligible detail
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("eligible_detail:"))
async def cb_eligible_detail(callback: CallbackQuery) -> None:
    level_id = int(callback.data.split(":")[1])

    async with AsyncSessionLocal() as db:
        level = await _fetch_level(db, level_id)

    if level is None:
        await callback.answer("Level not found.", show_alert=True)
        return

    await callback.message.edit_text(
        f"✅ *You are eligible to attempt:*\n"
        f"{level.station.name} – Level {level.level_number}\n\n"
        f"💡 *Hint:* _{level.hint_text}_",
        parse_mode="Markdown",
        reply_markup=eligible_detail_keyboard(),
    )
    await callback.answer()


# ---------------------------------------------------------------------------
# Step 2 back → Step 1  (re-render eligible list)
# ---------------------------------------------------------------------------

@router.callback_query(F.data == "show_eligible")
async def cb_show_eligible(callback: CallbackQuery) -> None:
    chat_id = str(callback.message.chat.id)

    async with AsyncSessionLocal() as db:
        group_id = await auth.get_current_group_id(db, chat_id)
        if group_id is None:
            await callback.answer("No group selected.", show_alert=True)
            return
        group_name = await _fetch_group_name(db, group_id)
        eligible = await game_logic.get_eligible_levels(db, group_id)

    await callback.message.edit_text(
        f"For *{group_name}*, you are currently eligible to attempt:",
        parse_mode="Markdown",
        reply_markup=eligible_levels_keyboard(eligible),
    )
    await callback.answer()


# ---------------------------------------------------------------------------
# Step 3 – Locked list
# ---------------------------------------------------------------------------

@router.callback_query(F.data == "show_locked")
async def cb_show_locked(callback: CallbackQuery) -> None:
    chat_id = str(callback.message.chat.id)

    async with AsyncSessionLocal() as db:
        group_id = await auth.get_current_group_id(db, chat_id)
        if group_id is None:
            await callback.answer("No group selected.", show_alert=True)
            return
        group_name = await _fetch_group_name(db, group_id)
        locked = await game_logic.get_locked_levels(db, group_id)

    if not locked:
        await callback.message.edit_text(
            f"🎉 No locked options for *{group_name}* – everything is accessible!",
            parse_mode="Markdown",
            reply_markup=eligible_levels_keyboard([], show_locked_btn=False),
        )
    else:
        await callback.message.edit_text(
            f"🔒 *Locked options for {group_name}* (you cannot attempt these yet):",
            parse_mode="Markdown",
            reply_markup=locked_levels_keyboard(locked),
        )
    await callback.answer()


# ---------------------------------------------------------------------------
# Step 4 – Locked detail
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("locked_detail:"))
async def cb_locked_detail(callback: CallbackQuery) -> None:
    level_id = int(callback.data.split(":")[1])
    chat_id = str(callback.message.chat.id)

    async with AsyncSessionLocal() as db:
        group_id = await auth.get_current_group_id(db, chat_id)
        level = await _fetch_level(db, level_id)
        group_name = await _fetch_group_name(db, group_id) if group_id else "?"
        missing = (
            await game_logic.get_missing_prereqs(db, group_id, level_id)
            if group_id
            else []
        )

    if level is None:
        await callback.answer("Level not found.", show_alert=True)
        return

    missing_lines = "\n".join(
        f"  • {m['station_name']} – Level {m['level_number']}" for m in missing
    ) or "  (none listed)"

    await callback.message.edit_text(
        f"🔒 *You cannot attempt yet:*\n"
        f"{level.station.name} – Level {level.level_number}\n\n"
        f"💡 *Hint:* _{level.hint_text}_\n\n"
        f"For *{group_name}*, you still need to complete:\n{missing_lines}",
        parse_mode="Markdown",
        reply_markup=locked_detail_keyboard(),
    )
    await callback.answer()
