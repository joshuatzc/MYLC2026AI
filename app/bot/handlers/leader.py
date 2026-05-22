"""
bot/handlers/leader.py – Become Leader flow and Admin Section (leader only).

Become Leader flow:
  1. User taps "Become Leader"
  2. Bot asks for the password (sets awaiting = "leader_password")
  3. User types password → bot checks → promotes or denies

Admin Section flow:
  1. Tap "Admin Section"  → eligible upgrades list
  2. Tap an upgrade       → confirm screen
  3. Tap "Yes, confirm"   → apply upgrade, show result
"""
from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message

from app.bot.keyboards import (
    admin_after_upgrade_keyboard,
    admin_confirm_keyboard,
    admin_eligible_keyboard,
    main_menu_keyboard,
)
from app.bot.utils import require_group
from app.database import AsyncSessionLocal
from app.models import Group, StationLevel
from app.services import auth, game_logic
from sqlalchemy import select
from sqlalchemy.orm import selectinload

router = Router()


async def _fetch_group(db, group_id: int) -> Group | None:
    result = await db.execute(select(Group).where(Group.id == group_id))
    return result.scalar_one_or_none()


async def _fetch_level(db, level_id: int) -> StationLevel | None:
    result = await db.execute(
        select(StationLevel)
        .options(selectinload(StationLevel.station))
        .where(StationLevel.id == level_id)
    )
    return result.scalar_one_or_none()


# ---------------------------------------------------------------------------
# Become Leader – trigger
# ---------------------------------------------------------------------------

@router.message(F.text == "Become Leader")
async def handle_become_leader(message: Message) -> None:
    chat_id = str(message.chat.id)
    group_id = await require_group(message, chat_id)
    if group_id is None:
        return

    async with AsyncSessionLocal() as db:
        await auth.set_awaiting(db, chat_id, "leader_password")

    await message.answer(
        "🔑 Please enter the leader password:",
    )


# ---------------------------------------------------------------------------
# Become Leader – password submission (catches any non-button text when awaiting)
# ---------------------------------------------------------------------------

@router.message(F.text)
async def handle_text_input(message: Message) -> None:
    """Catch-all for text that isn't a known reply button."""
    chat_id = str(message.chat.id)

    async with AsyncSessionLocal() as db:
        awaiting = await auth.get_awaiting(db, chat_id)

    if awaiting == "leader_password":
        await _process_leader_password(message, chat_id, message.text or "")
    else:
        # Unknown text – show main menu reminder
        async with AsyncSessionLocal() as db:
            role = await auth.get_role(db, chat_id)
        await message.answer(
            "Use the buttons below to navigate.",
            reply_markup=main_menu_keyboard(role),
        )


async def _process_leader_password(
    message: Message, chat_id: str, password: str
) -> None:
    async with AsyncSessionLocal() as db:
        success = await auth.become_leader(db, chat_id, password)
        role = await auth.get_role(db, chat_id)

    if success:
        await message.answer(
            "✅ *You are now a Leader!*\n"
            "You can now access the Admin Section.",
            parse_mode="Markdown",
            reply_markup=main_menu_keyboard("leader"),
        )
    else:
        await message.answer(
            "❌ Incorrect password. Please try again or tap another button.",
            reply_markup=main_menu_keyboard(role),
        )


# ---------------------------------------------------------------------------
# Admin Section – entry (leader only)
# ---------------------------------------------------------------------------

@router.message(F.text == "Admin Section")
async def handle_admin_section(message: Message) -> None:
    chat_id = str(message.chat.id)

    async with AsyncSessionLocal() as db:
        role = await auth.get_role(db, chat_id)

    if role != "leader":
        await message.answer("⛔ You need to be a leader to access this section.")
        return

    await _show_admin_eligible(message, chat_id, edit=False)


@router.callback_query(F.data == "admin_section")
async def cb_admin_section(callback: CallbackQuery) -> None:
    chat_id = str(callback.message.chat.id)
    await _show_admin_eligible(callback.message, chat_id, edit=True)
    await callback.answer()


async def _show_admin_eligible(
    message: Message, chat_id: str, *, edit: bool = False
) -> None:
    async with AsyncSessionLocal() as db:
        group_id = await auth.get_current_group_id(db, chat_id)
        if group_id is None:
            await message.answer("⚠️ No group selected.")
            return
        group = await _fetch_group(db, group_id)
        eligible = await game_logic.get_eligible_levels(db, group_id)

    group_name = group.name if group else "?"

    if not eligible:
        text = f"Your group *{group_name}* has no eligible upgrades right now."
        kb = admin_eligible_keyboard([])
    else:
        text = f"⬆️ *Eligible upgrades for {group_name}:*"
        kb = admin_eligible_keyboard(eligible)

    if edit:
        await message.edit_text(text, parse_mode="Markdown", reply_markup=kb)
    else:
        await message.answer(text, parse_mode="Markdown", reply_markup=kb)


# ---------------------------------------------------------------------------
# Admin upgrade detail – confirm screen
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("admin_upgrade_detail:"))
async def cb_admin_upgrade_detail(callback: CallbackQuery) -> None:
    level_id = int(callback.data.split(":")[1])
    chat_id = str(callback.message.chat.id)

    async with AsyncSessionLocal() as db:
        group_id = await auth.get_current_group_id(db, chat_id)
        group = await _fetch_group(db, group_id) if group_id else None
        level = await _fetch_level(db, level_id)

    if group is None or level is None:
        await callback.answer("Data not found.", show_alert=True)
        return

    new_pop = round(group.population * level.reward_multiplier, 2)

    await callback.message.edit_text(
        f"🏷 *{group.name}*\n"
        f"Upgrade: *{level.station.name} – Level {level.level_number}*\n\n"
        f"👥 Current population: *{group.population:,.1f}*\n"
        f"👥 New population after upgrade: *{new_pop:,.1f}*\n\n"
        f"💡 Hint: _{level.hint_text}_\n\n"
        f"Are you sure you want to confirm this upgrade?",
        parse_mode="Markdown",
        reply_markup=admin_confirm_keyboard(level_id),
    )
    await callback.answer()


# ---------------------------------------------------------------------------
# Admin upgrade – apply
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("admin_confirm:"))
async def cb_admin_confirm(callback: CallbackQuery) -> None:
    level_id = int(callback.data.split(":")[1])
    chat_id = str(callback.message.chat.id)

    async with AsyncSessionLocal() as db:
        group_id = await auth.get_current_group_id(db, chat_id)
        if group_id is None:
            await callback.answer("No group selected.", show_alert=True)
            return

        try:
            result = await game_logic.apply_level_upgrade(
                db, group_id, level_id, recorded_by=chat_id
            )
        except ValueError as exc:
            await callback.answer(str(exc), show_alert=True)
            return

    await callback.message.edit_text(
        f"✅ *Upgrade recorded!*\n\n"
        f"{result['station_name']} – Level {result['level_number']}\n"
        f"Population: *{result['old_population']:,.1f}* → *{result['new_population']:,.1f}* 🎉",
        parse_mode="Markdown",
        reply_markup=admin_after_upgrade_keyboard(),
    )
    await callback.answer("Upgrade applied!")
