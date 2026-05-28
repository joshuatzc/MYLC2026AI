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
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.bot.keyboards import (
    admin_confirm_keyboard,
    admin_eligible_keyboard,
    main_menu_keyboard,
    admin_after_upgrade_keyboard,
    church_steal_targets_keyboard,
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

@router.message(F.text == "👑 Become Leader")
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
# Admin Section – entry (leader only)
# NOTE: must be registered BEFORE the catch-all F.text handler below,
# otherwise aiogram matches the catch-all first.
# ---------------------------------------------------------------------------

@router.message(F.text == "🔑 Admin Section")
async def handle_admin_section(message: Message) -> None:
    chat_id = str(message.chat.id)

    async with AsyncSessionLocal() as db:
        role = await auth.get_role(db, chat_id)

    if role != "leader":
        await message.answer("⛔ You need to be a leader to access this section.")
        return

    async with AsyncSessionLocal() as db:
        await auth.set_awaiting(db, chat_id, None)

    await _show_admin_eligible(message, chat_id, edit=False)


@router.callback_query(F.data == "admin_section")
async def cb_admin_section(callback: CallbackQuery) -> None:
    chat_id = str(callback.message.chat.id)
    async with AsyncSessionLocal() as db:
        await auth.set_awaiting(db, chat_id, None)
    await _show_admin_eligible(callback.message, chat_id, edit=True)
    await callback.answer()


@router.callback_query(F.data == "admin_cancel")
async def cb_admin_cancel(callback: CallbackQuery) -> None:
    chat_id = str(callback.message.chat.id)
    async with AsyncSessionLocal() as db:
        await auth.set_awaiting(db, chat_id, None)
    await callback.message.edit_text("Action cancelled.")
    await callback.answer()


@router.callback_query(F.data == "admin_rename_church_start")
async def cb_admin_rename_church_start(callback: CallbackQuery) -> None:
    chat_id = str(callback.message.chat.id)
    async with AsyncSessionLocal() as db:
        await auth.set_awaiting(db, chat_id, "rename_church")

    builder = InlineKeyboardBuilder()
    builder.button(text="❌ Cancel", callback_data="admin_section")

    await callback.message.edit_text(
        "✏️ *Rename Church*\n\nPlease enter the new name for your church:",
        parse_mode="Markdown",
        reply_markup=builder.as_markup(),
    )
    await callback.answer()


# ---------------------------------------------------------------------------
# Become Leader – password submission (catches any non-button text when awaiting)
# NOTE: this catch-all must stay AFTER all specific F.text == "..." handlers.
# ---------------------------------------------------------------------------

@router.message(F.text)
async def handle_text_input(message: Message) -> None:
    """Catch-all for text that isn't a known reply button."""
    chat_id = str(message.chat.id)

    async with AsyncSessionLocal() as db:
        awaiting = await auth.get_awaiting(db, chat_id)

    if awaiting == "leader_password":
        await _process_leader_password(message, chat_id, message.text or "")
    elif awaiting == "rename_church":
        await _process_rename_church(message, chat_id, message.text or "")
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


async def _process_rename_church(
    message: Message, chat_id: str, new_name: str
) -> None:
    new_name = new_name.strip()
    if not new_name:
        await message.answer(
            "⚠️ The church name cannot be empty. Please enter a valid name:"
        )
        return

    if len(new_name) > 100:
        await message.answer(
            "⚠️ The name is too long. Please keep it under 100 characters:"
        )
        return

    async with AsyncSessionLocal() as db:
        # Check if the name is already taken
        name_check = await db.execute(
            select(Group).where(Group.name == new_name)
        )
        existing_group = name_check.scalar_one_or_none()

        if existing_group:
            builder = InlineKeyboardBuilder()
            builder.button(text="❌ Cancel", callback_data="admin_section")
            await message.answer(
                f"⚠️ The name *{new_name}* is already taken by another church. Please choose a different name:",
                parse_mode="Markdown",
                reply_markup=builder.as_markup(),
            )
            return

        group_id = await auth.get_current_group_id(db, chat_id)
        if group_id is None:
            await auth.set_awaiting(db, chat_id, None)
            await message.answer("⚠️ No group selected.")
            return

        group = await _fetch_group(db, group_id)
        if group is None:
            await auth.set_awaiting(db, chat_id, None)
            await message.answer("⚠️ Selected group not found.")
            return

        old_name = group.name
        group.name = new_name

        # Clear awaiting state
        await auth.set_awaiting(db, chat_id, None)
        await db.commit()

    await message.answer(
        f"✅ *Church renamed successfully!*\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"Old Name: *{old_name}*\n"
        f"New Name: *{new_name}*\n",
        parse_mode="Markdown",
    )
    # Then show the admin upgrades list directly in a new message
    await _show_admin_eligible(message, chat_id, edit=False)


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

    if level.station.name == "Church Upgrade":
        new_level = level.level_number
        min_pop = game_logic.get_church_min_pop(new_level)
        steal_amt = game_logic.get_church_steal_amount(new_level)
        new_max = game_logic.get_max_occupancy(new_level)
        new_tier = game_logic.get_church_tier_name(new_level)
        current_tier = game_logic.get_church_tier_name(group.church_level)

        if group.population < min_pop:
            text = (
                f"🏛️ *Prerequisite Not Met!* 🏛️\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"Upgrade: *Church Upgrade – Level {new_level} ({new_tier})*\n\n"
                f"❌ *Blocked:* To upgrade your church to a **{new_tier}**, your group must have at least **{min_pop}** congregation members.\n\n"
                f"👥 Current Population: **{int(group.population):,}** members\n\n"
                f"💡 *Hint:* Complete other standard station upgrades first to grow your congregation!"
            )
            await callback.message.edit_text(
                text,
                parse_mode="Markdown",
                reply_markup=admin_eligible_keyboard([]),
            )
        else:
            text = (
                f"🏛️ *Confirm Church Upgrade* 🏛️\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"Upgrade: *Church Upgrade – Level {new_level} ({new_tier})*\n\n"
                f"👥 Current Population: **{int(group.population):,}** (Min required: {min_pop})\n"
                f"👥 New Max Occupancy: **{new_max:,}**\n"
                f"📈 Station Earning Bonus: **+{round(game_logic.get_church_bonus(new_level)*100)}%**\n"
                f"⚡ *Special Perk:* Steal up to **{steal_amt}** members from any group at **{current_tier}**!\n\n"
                f"💡 Hint: _{level.hint_text}_\n\n"
                f"Are you sure you want to upgrade your church?"
            )
            await callback.message.edit_text(
                text,
                parse_mode="Markdown",
                reply_markup=admin_confirm_keyboard(level_id),
            )
    else:
        new_pop = round(group.population * level.reward_multiplier)
        await callback.message.edit_text(
            f"🏷 *{group.name}*\n"
            f"Upgrade: *{level.station.name} – Level {level.level_number}*\n\n"
            f"👥 Current population: *{int(group.population):,}*\n"
            f"👥 New population after upgrade: *{new_pop:,}*\n\n"
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

        group = await _fetch_group(db, group_id)
        level = await _fetch_level(db, level_id)

    if group is None or level is None:
        await callback.answer("Data not found.", show_alert=True)
        return

    if level.station.name == "Church Upgrade":
        new_level = level.level_number
        steal_amt = game_logic.get_church_steal_amount(new_level)
        new_tier = game_logic.get_church_tier_name(new_level)
        current_tier = game_logic.get_church_tier_name(group.church_level)

        async with AsyncSessionLocal() as db:
            targets = await game_logic.get_eligible_steal_targets_for_upgrade(db, group_id, group.church_level)

        if not targets:
            text = (
                f"⚡ *Church Upgrade — No Theft Targets* ⚡\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"You are upgrading to a **{new_tier}**!\n"
                f"This upgrade permits stealing up to **{steal_amt}** members from another **{current_tier}** group.\n\n"
                f"⚠️ *Notice:* No other groups are currently at the **{current_tier}** tier! You will skip the theft and proceed with the upgrade only."
            )
            kb = church_steal_targets_keyboard(level_id, [])
        else:
            text = (
                f"⚡ *Church Upgrade — Select Theft Target* ⚡\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"You are upgrading to a **{new_tier}**!\n"
                f"This permits a one-time absolute theft of up to **{steal_amt}** members from any group at your current tier (**{current_tier}**).\n\n"
                f"Select a group to steal from, or skip the theft to upgrade immediately:"
            )
            kb = church_steal_targets_keyboard(level_id, targets)

        await callback.message.edit_text(
            text,
            parse_mode="Markdown",
            reply_markup=kb,
        )
        await callback.answer()
    else:
        # Standard station upgrade applied immediately
        async with AsyncSessionLocal() as db:
            try:
                result = await game_logic.apply_level_upgrade(
                    db, group_id, level_id, recorded_by=chat_id
                )
            except ValueError as exc:
                await callback.answer(str(exc), show_alert=True)
                return

        text = (
            f"✅ *Upgrade recorded!*\n\n"
            f"{result['station_name']} – Level {result['level_number']}\n"
            f"Population: *{result['old_population']:,.0f}* → *{result['new_population']:,.0f}* 🎉"
        )
        if result.get("capped"):
            text += "\n\n⚠️ *WARNING:* Population capped at max occupancy! Upgrade your church to continue growing."

        await callback.message.edit_text(
            text,
            parse_mode="Markdown",
            reply_markup=admin_after_upgrade_keyboard(),
        )
        await callback.answer("Upgrade applied!")


# ---------------------------------------------------------------------------
# Church Upgrade & Theft Execution Handler
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("admin_church_confirm:"))
async def cb_admin_church_confirm(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    level_id = int(parts[1])
    target_id_str = parts[2]
    chat_id = str(callback.message.chat.id)

    steal_target_id = None if target_id_str == "skip" else int(target_id_str)

    async with AsyncSessionLocal() as db:
        group_id = await auth.get_current_group_id(db, chat_id)
        if group_id is None:
            await callback.answer("No group selected.", show_alert=True)
            return

        try:
            result = await game_logic.apply_level_upgrade(
                db, group_id, level_id, recorded_by=chat_id, steal_target_group_id=steal_target_id
            )
        except ValueError as exc:
            await callback.answer(str(exc), show_alert=True)
            return

    if result["theft_applied"]:
        text = (
            f"🏛️ *Church Upgraded & Theft Successful!* 🏛️\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"Your church has expanded to a *{result['tier_name']}* (Level {result['level_number']})!\n"
            f"👥 Max Occupancy: **{result['max_occupancy']:,}**\n"
            f"📈 Station Bonus: **+{result['bonus_pct']}%**\n\n"
            f"⚡ *Theft Complete!*\n"
            f"Stole **{result['stolen_amount']:,}** members from **{result['target_name']}**! (Actual gained: **{result['actual_gained']:,}** due to occupancy cap)\n"
            f"👥 Target left with **{result['target_new_pop']:,.0f}** members due to safety net.\n\n"
            f"👥 Your Population: *{result['old_population']:,.0f}* → *{result['new_population']:,.0f}* 🎉"
        )
    else:
        text = (
            f"🏛️ *Church Upgraded Successfully!* 🏛️\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"Your church has expanded to a *{result['tier_name']}* (Level {result['level_number']})!\n"
            f"👥 Max Occupancy: **{result['max_occupancy']:,}**\n"
            f"📈 Station Bonus: **+{result['bonus_pct']}%**\n\n"
            f"*(No theft applied)*\n\n"
            f"👥 Your Population: *{result['old_population']:,.0f}* → *{result['new_population']:,.0f}* 🎉"
        )

    if result.get("capped"):
        text += "\n\n⚠️ *WARNING:* Population capped at max occupancy!"

    await callback.message.edit_text(
        text,
        parse_mode="Markdown",
        reply_markup=admin_after_upgrade_keyboard(),
    )
    await callback.answer("Church upgraded successfully!")
