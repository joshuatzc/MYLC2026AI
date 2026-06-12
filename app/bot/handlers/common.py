"""
bot/handlers/common.py – /start, main menu redraw, Change Group flow.
"""
from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, Message

from app.bot.keyboards import (
    groups_inline_keyboard,
    main_menu_keyboard,
    guides_keyboard,
)
from app.database import AsyncSessionLocal
from app.services import auth, game_logic

router = Router()


# ---------------------------------------------------------------------------
# /start
# ---------------------------------------------------------------------------

@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    chat_id = str(message.chat.id)
    async with AsyncSessionLocal() as db:
        group_id = await auth.get_current_group_id(db, chat_id)
        if group_id is None:
            # First time — ask them to pick a group before anything else
            groups = await game_logic.list_groups(db)
            await message.answer(
                "⛪ *Welcome to Build the Biggest Church!*\n\n"
                "Before we start, please choose which group you belong to:",
                parse_mode="Markdown",
                reply_markup=groups_inline_keyboard(groups),
            )
            return
        role = await auth.get_role(db, chat_id)

    await message.answer(
        "⛪ *Welcome back to Build the Biggest Church!*\n\n"
        "Use the buttons below to navigate.",
        parse_mode="Markdown",
        reply_markup=main_menu_keyboard(role),
    )


# ---------------------------------------------------------------------------
# Leaderboard
# ---------------------------------------------------------------------------

@router.message(F.text == "🏆 Leaderboard")
async def handle_leaderboard(message: Message) -> None:
    async with AsyncSessionLocal() as db:
        board = await game_logic.get_leaderboard(db)

    if not board:
        await message.answer("No groups found yet.")
        return

    lines = ["🏆 *Leaderboard*\n"]
    medals = ["🥇", "🥈", "🥉"]
    for entry in board:
        medal = medals[entry["rank"] - 1] if entry["rank"] <= 3 else f"{entry['rank']}."
        lines.append(f"{medal} *{entry['name']}* — {int(entry['population']):,} people")

    await message.answer("\n".join(lines), parse_mode="Markdown")


# ---------------------------------------------------------------------------
# Change Group – trigger
# ---------------------------------------------------------------------------

@router.message(F.text == "🔄 Change Group")
async def handle_change_group(message: Message) -> None:
    chat_id = str(message.chat.id)
    async with AsyncSessionLocal() as db:
        group_id = await auth.get_current_group_id(db, chat_id)
        if group_id is not None:
            # Already has a group — require admin password to change
            await auth.set_awaiting(db, chat_id, "change_group_password")
            await message.answer(
                "🔑 *Changing your group requires the admin password.*\n\n"
                "Please enter the password:",
                parse_mode="Markdown",
            )
            return

        groups = await game_logic.list_groups(db)

    if not groups:
        await message.answer("No groups available yet. Ask the admin to add some.")
        return

    await message.answer(
        "Select your group:",
        reply_markup=groups_inline_keyboard(groups),
    )


# ---------------------------------------------------------------------------
# Change Group – callback when user taps a group button
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("select_group:"))
async def cb_select_group(callback: CallbackQuery) -> None:
    chat_id = str(callback.message.chat.id)
    group_id = int(callback.data.split(":")[1])

    async with AsyncSessionLocal() as db:
        await auth.set_current_group(db, chat_id, group_id)
        # Fetch fresh group name
        groups = await game_logic.list_groups(db)
        role = await auth.get_role(db, chat_id)

    group_name = next((g["name"] for g in groups if g["id"] == group_id), "Unknown")

    await callback.message.edit_text(
        f"✅ You are now viewing *{group_name}*.\n"
        "Your role has been reset to Normal.",
        parse_mode="Markdown",
    )
    await callback.answer("Group updated!", show_alert=False)
    await callback.message.answer(
        "⛪ *Welcome to Build the Biggest Church!*\n\n"
        "Use the buttons below to navigate.",
        parse_mode="Markdown",
        reply_markup=main_menu_keyboard(role),
    )


# ---------------------------------------------------------------------------
# "Back to Main Menu" callback (used by many inline keyboards)
# ---------------------------------------------------------------------------

@router.callback_query(F.data == "main_menu")
async def cb_main_menu(callback: CallbackQuery) -> None:
    chat_id = str(callback.message.chat.id)
    async with AsyncSessionLocal() as db:
        role = await auth.get_role(db, chat_id)

    await callback.message.edit_text("Returning to main menu…")
    await callback.message.answer("Main Menu", reply_markup=main_menu_keyboard(role))
    await callback.answer()


# ---------------------------------------------------------------------------
# Help and Guides Handlers
# ---------------------------------------------------------------------------

@router.message(F.text == "📖 Help & Guides")
async def handle_help_guides(message: Message) -> None:
    await message.answer(
        "📖 *Help & Guides*\n\n"
        "Choose which guide you would like to view:",
        parse_mode="Markdown",
        reply_markup=guides_keyboard(),
    )


@router.message(Command("bothelp"))
async def handle_bothelp_command(message: Message) -> None:
    from app.config import settings
    from aiogram.types import WebAppInfo
    from aiogram.utils.keyboard import InlineKeyboardBuilder

    base_url = settings.BASE_URL.rstrip("/")
    if not base_url:
        await message.answer("⚠️ Bot Help is not configured by the administrator yet.")
        return

    builder = InlineKeyboardBuilder()
    is_https = base_url.startswith("https://")
    if is_https:
        builder.button(text="🤖 Open Bot Help", web_app=WebAppInfo(url=f"{base_url}/bothelp.html"))
    else:
        builder.button(text="🤖 Open Bot Help", url=f"{base_url}/bothelp.html")

    builder.adjust(1)
    await message.answer(
        "🤖 *Bot Help & Commands*\n\n"
        "Click the button below to open the Bot Help guide:",
        parse_mode="Markdown",
        reply_markup=builder.as_markup(),
    )


@router.message(Command("gamehelp"))
async def handle_gamehelp_command(message: Message) -> None:
    from app.config import settings
    from aiogram.types import WebAppInfo
    from aiogram.utils.keyboard import InlineKeyboardBuilder

    base_url = settings.BASE_URL.rstrip("/")
    if not base_url:
        await message.answer("⚠️ Game Guide is not configured by the administrator yet.")
        return

    builder = InlineKeyboardBuilder()
    is_https = base_url.startswith("https://")
    if is_https:
        builder.button(text="🎮 Open Game Help", web_app=WebAppInfo(url=f"{base_url}/gamehelp.html"))
    else:
        builder.button(text="🎮 Open Game Help", url=f"{base_url}/gamehelp.html")

    builder.adjust(1)
    await message.answer(
        "🎮 *Game Guide & Rules*\n\n"
        "Click the button below to open the Game Help guide:",
        parse_mode="Markdown",
        reply_markup=builder.as_markup(),
    )


@router.callback_query(F.data == "guide_not_configured")
async def cb_guide_not_configured(callback: CallbackQuery) -> None:
    await callback.answer(
        "⚠️ The bot administrator has not configured the BASE_URL setting in the .env file yet.",
        show_alert=True
    )
