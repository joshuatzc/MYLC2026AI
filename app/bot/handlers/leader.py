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
    church_steal_targets_keyboard,
    church_confirm_keyboard,
)
from app.bot.utils import require_group
from app.database import AsyncSessionLocal
from app.models import Group, StationLevel, GroupStationProgress
from app.services import auth, game_logic
from sqlalchemy import select, func
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
    from app.bot.handlers.corruption import cancel_briefing_timer
    cancel_briefing_timer(chat_id)
    async with AsyncSessionLocal() as db:
        await auth.set_awaiting(db, chat_id, None)
    await _show_admin_eligible(callback.message, chat_id, edit=True)
    await callback.answer()


@router.callback_query(F.data == "admin_cancel")
async def cb_admin_cancel(callback: CallbackQuery) -> None:
    chat_id = str(callback.message.chat.id)
    from app.bot.handlers.corruption import cancel_briefing_timer
    cancel_briefing_timer(chat_id)
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
    elif awaiting == "change_group_password":
        await _process_change_group_password(message, chat_id, message.text or "")
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


async def _process_change_group_password(
    message: Message, chat_id: str, password: str
) -> None:
    from app.config import settings
    from app.bot.keyboards import groups_inline_keyboard

    if password != settings.LEADER_PASSWORD:
        async with AsyncSessionLocal() as db:
            role = await auth.get_role(db, chat_id)
        await message.answer(
            "❌ Incorrect password. Please try again or tap another button.",
            reply_markup=main_menu_keyboard(role),
        )
        return

    async with AsyncSessionLocal() as db:
        await auth.set_awaiting(db, chat_id, None)
        groups = await game_logic.list_groups(db)

    if not groups:
        await message.answer("No groups available yet. Ask the admin to add some.")
        return

    await message.answer(
        "Select your group:",
        reply_markup=groups_inline_keyboard(groups),
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

    # Trigger AI news
    from app.services.ai_news import trigger_event_broadcast
    import asyncio
    asyncio.create_task(trigger_event_broadcast("rename", {"old_name": old_name, "new_name": new_name}))


    await message.answer(
        f"✅ *Church renamed successfully!*\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"Old Name: *{old_name}*\n"
        f"New Name: *{new_name}*\n",
        parse_mode="Markdown",
    )


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

        # Check if Super Pastor is active
        from app.models import GlobalState, GroupQuizState
        sp_active_res = await db.execute(select(GlobalState).where(GlobalState.key == "super_pastor_active"))
        sp_active_row = sp_active_res.scalar_one_or_none()
        sp_active = sp_active_row.value_bool if sp_active_row else False

        # Check if Corruption quiz is active and group hasn't completed it
        corr_active_res = await db.execute(select(GlobalState).where(GlobalState.key == "corruption_active"))
        corr_active_row = corr_active_res.scalar_one_or_none()
        corruption_active = corr_active_row.value_bool if corr_active_row else False

        corruption_quiz_available = False
        if corruption_active:
            quiz_state = (await db.execute(
                select(GroupQuizState).where(GroupQuizState.group_id == group_id)
            )).scalar_one_or_none()
            corruption_quiz_available = not (quiz_state and quiz_state.completed)

    group_name = group.name if group else "?"

    if not eligible:
        text = f"Your group *{group_name}* has no eligible upgrades right now."
        if sp_active:
            text = f"Your group *{group_name}* has no standard eligible upgrades right now, but a 🌟 *Super Pastor* is active!"
        kb = admin_eligible_keyboard([], super_pastor_active=sp_active, corruption_quiz_available=corruption_quiz_available)
    else:
        text = f"⬆️ *Eligible upgrades for {group_name}:*"
        if sp_active:
            text = f"🌟 *SPECIAL EVENT ACTIVE: THE SUPER PASTOR HAS ARRIVED!* 🏃‍♂️💨\n\nBring the items to him IRL first to claim! Or proceed with standard upgrades below:\n\n{text}"
        if corruption_quiz_available:
            text = f"📜 *CORRUPTION INVESTIGATION ACTIVE!* Prove your legitimacy in the Admin Section above.\n\n{text}"
        kb = admin_eligible_keyboard(eligible, super_pastor_active=sp_active, corruption_quiz_available=corruption_quiz_available)

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
                # Calculate rank-based next_boost
                completions_count = await db.execute(
                    select(func.count(GroupStationProgress.id))
                    .where(GroupStationProgress.station_level_id == level_id)
                )
                N = completions_count.scalar() or 0
                
                next_boost = 15 - N
                if N == 13:
                    next_boost = 1
                elif next_boost < 1:
                    next_boost = 1
                    
                current_bonus_pct = round((await game_logic.get_group_church_bonus(db, group_id)) * 100)

                # Get already purchased hints for this level
                purchased = await game_logic.get_purchased_hint_numbers(db, group_id, level_id)
                
                # Find next hint number sequentially (must buy 1, then 2, then 3)
                next_hint_num = None
                for h in (1, 2, 3):
                    if h not in purchased:
                        next_hint_num = h
                        break
                
                cost_pct = 0
                if next_hint_num is not None:
                    base_percentage = 0.10 + (next_hint_num * 0.05)  # 15%, 20%, 25%
                    cost_pct = round(max(0.01, base_percentage - (N * 0.01)) * 100)
                
                text = (
                    f"🏛️ *Confirm Church Upgrade* 🏛️\n"
                    f"━━━━━━━━━━━━━━━━━━━\n"
                    f"Upgrade: *Church Upgrade – Level {new_level} ({new_tier})*\n\n"
                    f"⚠️ *WARNING:* (CONFIRM Only if u passed the STATION)\n\n"
                    f"👥 Current Population: **{int(group.population):,}** (Min required: {min_pop})\n"
                    f"👥 New Max Occupancy: **{new_max:,}**\n"
                    f"📈 Station Earning Bonus: **+{current_bonus_pct}%** (`+{next_boost}%` if upgraded now! 🏆)\n"
                    f"⚡ *Special Perk:* Steal **10%** of congregation members from any group, regardless of church level!\n\n"
                    f"💡 Hint: _{level.hint_text}_\n\n"
                    f"Are you sure you want to upgrade your church?"
                )
                await callback.message.edit_text(
                    text,
                    parse_mode="Markdown",
                    reply_markup=church_confirm_keyboard(level_id, next_hint_num, cost_pct),
                )
        else:
            # Calculate correct estimated population taking church passive bonus and max occupancy cap into account
            earned = group.population * (level.reward_multiplier - 1.0)
            bonus_pct = await game_logic.get_group_church_bonus(db, group_id)
            total_earned = earned * (1.0 + bonus_pct)
            new_pop = round(group.population + total_earned)

            # Cap based on max occupancy of current church level
            max_occ = game_logic.get_max_occupancy(group.church_level)
            capped = new_pop > max_occ
            new_pop = min(max_occ, new_pop)

            bonus_pct_display = round(bonus_pct * 100)

            # Detail the passive bonus earning boost
            buffs_info = ""
            if bonus_pct_display > 0:
                buffs_info += f"📈 *Church Passive Bonus:* `+{bonus_pct_display}%` additional members earned\n"
            
            if capped:
                buffs_info += f"⚠️ *WARNING:* Your population will be *capped* at your current church limit of **{max_occ:,}** members! Upgrade your church to expand your capacity.\n"

            text = (
                f"🏷 *{group.name}*\n"
                f"Upgrade: *{level.station.name} – Level {level.level_number}*\n\n"
                f"👥 Current population: *{int(group.population):,}*\n"
                f"👥 New population after upgrade: *{new_pop:,}*\n"
                f"{buffs_info}\n"
                f"💡 Hint: _{level.hint_text}_\n\n"
                f"Are you sure you want to confirm this upgrade?"
            )
            await callback.message.edit_text(
                text,
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
            # No eligible steal targets exist: bypass target selection and apply the upgrade immediately
            async with AsyncSessionLocal() as db:
                try:
                    result = await game_logic.apply_level_upgrade(
                        db, group_id, level_id, recorded_by=chat_id, steal_target_group_id=None
                    )
                except ValueError as exc:
                    await callback.answer(str(exc), show_alert=True)
                    return

            # Trigger AI news event broadcast
            from app.services.ai_news import trigger_event_broadcast
            import asyncio
            asyncio.create_task(trigger_event_broadcast("church_upgrade", {
                "group_name": group.name if group else "Unknown Group",
                "station_name": result["station_name"],
                "level_number": result["level_number"],
                "old_population": result["old_population"],
                "new_population": result["new_population"],
                "tier_name": result.get("tier_name"),
                "theft_applied": False,
                "stolen_amount": 0,
                "target_name": None,
            }))

            text = (
                f"🏛️ *Church Upgraded Successfully!* 🏛️\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"Your church has expanded to a *{result['tier_name']}* (Level {result['level_number']})!\n"
                f"👥 Max Occupancy: **{result['max_occupancy']:,}**\n"
                f"📈 Station Bonus: **+{result['bonus_pct']}%**\n\n"
                f"*(No theft applied — no groups currently at your tier)*\n\n"
                f"👥 Your Population: *{result['old_population']:,.0f}* → *{result['new_population']:,.0f}* 🎉"
            )
            if result.get("capped"):
                text += "\n\n⚠️ *WARNING:* Population capped at max occupancy!"

            await callback.message.edit_text(
                text,
                parse_mode="Markdown",
                reply_markup=None,
            )
            await callback.answer("Church upgraded successfully!")
        else:
            text = (
                f"⚡ *Church Upgrade — Select Theft Target* ⚡\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"You are upgrading to a **{new_tier}**!\n"
                f"This permits a one-time theft of **10%** of their congregation members from any group regardless of their church level.\n\n"
                f"⚠️ *Safety Net:* You can only steal from a group until they have **10 members** remaining. They will never go below this 10-member safety net.\n\n"
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

        # Trigger AI news
        from app.services.ai_news import trigger_event_broadcast
        import asyncio
        asyncio.create_task(trigger_event_broadcast("upgrade", {
            "group_name": group.name if group else "Unknown Group",
            "station_name": result["station_name"],
            "level_number": result["level_number"],
            "old_population": result["old_population"],
            "new_population": result["new_population"],
        }))

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
            reply_markup=None,
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

        group = await _fetch_group(db, group_id)
        group_name = group.name if group else "Unknown Group"

        try:
            result = await game_logic.apply_level_upgrade(
                db, group_id, level_id, recorded_by=chat_id, steal_target_group_id=steal_target_id
            )
        except ValueError as exc:
            await callback.answer(str(exc), show_alert=True)
            return

    # Trigger AI news
    from app.services.ai_news import trigger_event_broadcast
    import asyncio
    asyncio.create_task(trigger_event_broadcast("church_upgrade", {
        "group_name": group_name,
        "station_name": result["station_name"],
        "level_number": result["level_number"],
        "old_population": result["old_population"],
        "new_population": result["new_population"],
        "tier_name": result.get("tier_name"),
        "theft_applied": result.get("theft_applied"),
        "stolen_amount": result.get("stolen_amount"),
        "target_name": result.get("target_name"),
    }))


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
        reply_markup=None,
    )
    await callback.answer("Church upgraded successfully!")


# ---------------------------------------------------------------------------
# Church Upgrade Hint Handlers
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("admin_church_hint_buy:"))
async def cb_admin_church_hint_buy(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    level_id = int(parts[1])
    hint_number = int(parts[2])
    chat_id = str(callback.message.chat.id)

    async with AsyncSessionLocal() as db:
        group_id = await auth.get_current_group_id(db, chat_id)
        if group_id is None:
            await callback.answer("No group selected.", show_alert=True)
            return

        try:
            result = await game_logic.buy_church_hint(db, group_id, level_id, hint_number)
        except ValueError as exc:
            await callback.answer(str(exc), show_alert=True)
            return

    # Silent toast message
    await callback.answer(f"🎉 Hint {hint_number} purchased successfully! (-{result['cost']} pop)")

    # Edit the message to say hint bought, check My Church, and remove all inline buttons
    text = (
        f"✅ *Hint Purchased!* 💡\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"You have successfully purchased **Hint {hint_number}** for this Church Upgrade (-{result['cost']} congregation members).\n\n"
        f"👥 New Congregation: **{int(result['new_population']):,}** members\n\n"
        f"📖 *Check 'My Church':* The hint is now permanently visible to all your group members in the **My Church** panel on your main keyboards!"
    )
    await callback.message.edit_text(
        text,
        parse_mode="Markdown",
        reply_markup=None,
    )

    # Deliver the purchased hint immediately (with photo support if exists)
    import os
    from aiogram.types import FSInputFile

    photo_filename = result.get("hint_photo")
    photo_path = None
    if photo_filename:
        _here = os.path.dirname(os.path.abspath(__file__))
        _project_root = os.path.abspath(os.path.join(_here, "..", "..", ".."))
        candidate = os.path.join(_project_root, "assets", "hints", photo_filename)
        if os.path.exists(candidate):
            photo_path = candidate

    if photo_path:
        photo = FSInputFile(photo_path)
        await callback.message.answer_photo(
            photo=photo,
            caption=f"💡 *Hint {hint_number} unlocked!*\n\n{result['hint_text']}",
            parse_mode="Markdown",
        )
    else:
        await callback.message.answer(
            f"💡 *Hint {hint_number} unlocked!*\n\n{result['hint_text']}",
            parse_mode="Markdown",
        )


# ---------------------------------------------------------------------------
# Claim Super Pastor Handlers (Confirm & Execute)
# ---------------------------------------------------------------------------

@router.callback_query(F.data == "claim_super_pastor")
async def cb_claim_super_pastor(callback: CallbackQuery) -> None:
    chat_id = str(callback.message.chat.id)
    
    async with AsyncSessionLocal() as db:
        group_id = await auth.get_current_group_id(db, chat_id)
        if group_id is None:
            await callback.answer("No group selected.", show_alert=True)
            return

        # Check if active (race condition safety)
        from app.models import GlobalState, Group
        sp_active_res = await db.execute(select(GlobalState).where(GlobalState.key == "super_pastor_active"))
        sp_active_row = sp_active_res.scalar_one_or_none()
        sp_active = sp_active_row.value_bool if sp_active_row else False

        if not sp_active:
            await callback.answer("⚠️ This event has already ended or is no longer active!", show_alert=True)
            # Refresh admin eligible screen to remove button
            await _show_admin_eligible(callback.message, chat_id, edit=True)
            return

        # Validate duplicate claims
        claims_res = await db.execute(select(GlobalState).where(GlobalState.key == "super_pastor_claims"))
        claims_row = claims_res.scalar_one_or_none()
        claims_str = claims_row.value_str if claims_row else ""
        claims_list = [int(x) for x in claims_str.split(",") if x.strip()]

        if group_id in claims_list:
            await callback.answer("⚠️ Your group has already claimed this event! Allow other groups a chance!", show_alert=True)
            return

        # Check claim count limit
        claim_count_res = await db.execute(select(GlobalState).where(GlobalState.key == "super_pastor_claim_count"))
        claim_count_row = claim_count_res.scalar_one_or_none()
        claim_count = claim_count_row.value_int if claim_count_row else 0

        if claim_count >= 3:
            await callback.answer("⚠️ The Super Pastor event has already closed (all 3 spots claimed)!", show_alert=True)
            await _show_admin_eligible(callback.message, chat_id, edit=True)
            return

        # Fetch reward
        sp_reward_res = await db.execute(select(GlobalState).where(GlobalState.key == "super_pastor_reward"))
        sp_reward_row = sp_reward_res.scalar_one_or_none()
        reward_amount = sp_reward_row.value_int if sp_reward_row else 1000

        # Fetch group
        group_res = await db.execute(select(Group).where(Group.id == group_id))
        group = group_res.scalar_one_or_none()
        if not group:
            await callback.answer("Group not found.", show_alert=True)
            return

        old_pop = group.population
        max_occ = game_logic.get_max_occupancy(group.church_level)
        new_pop = min(max_occ, old_pop + reward_amount)
        capped = (old_pop + reward_amount) > max_occ

    from app.bot.keyboards import super_pastor_confirm_keyboard

    cap_warning = ""
    if capped:
        cap_warning = f"\n⚠️ *WARNING:* Your population will be *capped* at your current church limit of **{max_occ:,}** members! Upgrade your church to continue growing.\n"

    text = (
        f"🌟 *Confirm Super Pastor Claim* 🌟\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"Event: *Super Pastor – Spot Claim*\n\n"
        f"⚠️ *WARNING:* Make sure you have presented the required items (stopwatch at 3s 16ms, power bank, and toilet paper) to *Rev Bernard* IRL and he has verified them first!\n\n"
        f"👥 Current Population: **{int(old_pop):,}**\n"
        f"📈 Population Reward: **+{reward_amount:,}** members\n"
        f"👥 New population after claim: **{int(new_pop):,}**\n"
        f"{cap_warning}\n"
        f"Are you sure you want to confirm this claim?"
    )

    await callback.message.edit_text(
        text,
        parse_mode="Markdown",
        reply_markup=super_pastor_confirm_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "super_pastor_confirm")
async def cb_super_pastor_confirm(callback: CallbackQuery) -> None:
    chat_id = str(callback.message.chat.id)
    
    async with AsyncSessionLocal() as db:
        group_id = await auth.get_current_group_id(db, chat_id)
        if group_id is None:
            await callback.answer("No group selected.", show_alert=True)
            return

        # Check if active (race condition safety)
        from app.models import GlobalState, Group
        sp_active_res = await db.execute(select(GlobalState).where(GlobalState.key == "super_pastor_active"))
        sp_active_row = sp_active_res.scalar_one_or_none()
        sp_active = sp_active_row.value_bool if sp_active_row else False

        if not sp_active:
            await callback.answer("⚠️ This event has already ended or is no longer active!", show_alert=True)
            # Refresh admin eligible screen to remove button
            await _show_admin_eligible(callback.message, chat_id, edit=True)
            return

        # Validate duplicate claims
        claims_res = await db.execute(select(GlobalState).where(GlobalState.key == "super_pastor_claims"))
        claims_row = claims_res.scalar_one_or_none()
        claims_str = claims_row.value_str if claims_row else ""
        claims_list = [int(x) for x in claims_str.split(",") if x.strip()]

        if group_id in claims_list:
            await callback.answer("⚠️ Your group has already claimed this event! Allow other groups a chance!", show_alert=True)
            return

        # Check claim count limit
        claim_count_res = await db.execute(select(GlobalState).where(GlobalState.key == "super_pastor_claim_count"))
        claim_count_row = claim_count_res.scalar_one_or_none()
        claim_count = claim_count_row.value_int if claim_count_row else 0

        if claim_count >= 3:
            # Mark inactive just in case it wasn't already
            sp_active_row.value_bool = False
            await db.commit()
            await callback.answer("⚠️ The Super Pastor event has already closed (all 3 spots claimed)!", show_alert=True)
            await _show_admin_eligible(callback.message, chat_id, edit=True)
            return

        # Increment claim count
        new_claim_count = claim_count + 1
        if not claim_count_row:
            claim_count_row = GlobalState(key="super_pastor_claim_count", value_int=new_claim_count)
            db.add(claim_count_row)
        else:
            claim_count_row.value_int = new_claim_count

        # Append group ID to claims
        if claims_list:
            claims_list.append(group_id)
            new_claims_str = ",".join(str(x) for x in claims_list)
        else:
            new_claims_str = str(group_id)

        if not claims_row:
            claims_row = GlobalState(key="super_pastor_claims", value_str=new_claims_str)
            db.add(claims_row)
        else:
            claims_row.value_str = new_claims_str

        # Update last claimed_by for backward compatibility
        claimed_by_res = await db.execute(select(GlobalState).where(GlobalState.key == "super_pastor_claimed_by"))
        claimed_by_row = claimed_by_res.scalar_one_or_none()
        if not claimed_by_row:
            claimed_by_row = GlobalState(key="super_pastor_claimed_by", value_int=group_id)
            db.add(claimed_by_row)
        else:
            claimed_by_row.value_int = group_id

        # Close the event if we hit 3 claims
        if new_claim_count >= 3:
            sp_active_row.value_bool = False

        # Fetch reward
        sp_reward_res = await db.execute(select(GlobalState).where(GlobalState.key == "super_pastor_reward"))
        sp_reward_row = sp_reward_res.scalar_one_or_none()
        reward_amount = sp_reward_row.value_int if sp_reward_row else 1000

        # Give reward to group
        group_res = await db.execute(select(Group).where(Group.id == group_id))
        group = group_res.scalar_one_or_none()
        if not group:
            await callback.answer("Group not found.", show_alert=True)
            return

        old_pop = group.population
        
        # Cap based on max occupancy of current church level
        max_occ = game_logic.get_max_occupancy(group.church_level)
        new_pop = min(max_occ, old_pop + reward_amount)
        group.population = new_pop
        
        await db.commit()

        # Trigger eligibility check
        game_logic.trigger_eligibility_check(group_id, old_pop, new_pop)

    # Trigger AI news announcement
    from app.services.ai_news import trigger_event_broadcast
    import asyncio
    asyncio.create_task(trigger_event_broadcast("super_pastor_claim", {
        "group_name": group.name,
        "reward_amount": reward_amount,
        "old_population": old_pop,
        "new_population": new_pop,
        "spot_number": new_claim_count,
        "remaining_spots": 3 - new_claim_count,
    }))

    text = (
        f"🎉 *Super Pastor Claimed Successfully!* 🏆\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"Congratulations! Your group *{group.name}* claimed spot #{new_claim_count}! 🏃‍♂️💨\n\n"
        f"👥 Population: *{int(old_pop):,}* → *{int(new_pop):,}* (+{reward_amount} members)!\n"
    )
    if 3 - new_claim_count > 0:
        text += f"📢 Only *{3 - new_claim_count}* spot(s) remaining!"
    else:
        text += f"🚫 All 3 spots have been swooped! The event is now officially closed."
        
    if new_pop == max_occ and old_pop + reward_amount > max_occ:
        text += "\n\n⚠️ *WARNING:* Population capped at max occupancy!"

    await callback.message.edit_text(
        text,
        parse_mode="Markdown",
        reply_markup=None,
    )
    await callback.answer("Super Pastor claimed successfully!")


