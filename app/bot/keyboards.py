"""
bot/keyboards.py – reusable keyboard builders for the Telegram bot.
"""
from __future__ import annotations

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder


# ---------------------------------------------------------------------------
# Reply keyboard (main menu)
# ---------------------------------------------------------------------------

def main_menu_keyboard(role: str = "normal") -> ReplyKeyboardMarkup:
    """Build the persistent reply keyboard based on the chat's current role."""
    rows = [
        [KeyboardButton(text="⛪ My Church"), KeyboardButton(text="🗺️ My Journey")],
        [KeyboardButton(text="📋 Check Prerequisites"), KeyboardButton(text="🏆 Leaderboard")],
        [KeyboardButton(text="🔄 Change Group")],
    ]
    if role == "leader":
        rows[-1].append(KeyboardButton(text="🔑 Admin Section"))
    else:
        rows[-1].append(KeyboardButton(text="👑 Become Leader"))

    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


# ---------------------------------------------------------------------------
# Inline keyboards – Change Group
# ---------------------------------------------------------------------------

def groups_inline_keyboard(groups: list[dict]) -> InlineKeyboardMarkup:
    """One button per group for the Change Group flow."""
    builder = InlineKeyboardBuilder()
    for g in groups:
        builder.button(
            text=g["name"],
            callback_data=f"select_group:{g['id']}",
        )
    builder.adjust(2)
    return builder.as_markup()


# ---------------------------------------------------------------------------
# Inline keyboards – Check Prerequisites / eligible
# ---------------------------------------------------------------------------

def eligible_levels_keyboard(
    eligible: list[dict],
    show_locked_btn: bool = True,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for lv in eligible:
        builder.button(
            text=f"{lv['station_name']} – Level {lv['level_number']}",
            callback_data=f"eligible_detail:{lv['id']}",
        )
    builder.adjust(1)
    if show_locked_btn:
        builder.button(text="🔒 Show locked options", callback_data="show_locked")
        builder.adjust(1)
    return builder.as_markup()


def eligible_detail_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="← Back to eligible list", callback_data="show_eligible")
    builder.button(text="🏠 Back to Main Menu", callback_data="main_menu")
    builder.adjust(1)
    return builder.as_markup()


def locked_levels_keyboard(locked: list[dict]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for lv in locked:
        builder.button(
            text=f"{lv['station_name']} – Level {lv['level_number']}",
            callback_data=f"locked_detail:{lv['id']}",
        )
    builder.adjust(1)
    builder.button(text="← Back to eligible list", callback_data="show_eligible")
    builder.adjust(1)
    return builder.as_markup()


def locked_detail_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="← Back to locked options", callback_data="show_locked")
    builder.button(text="← Back to eligible list", callback_data="show_eligible")
    builder.button(text="🏠 Back to Main Menu", callback_data="main_menu")
    builder.adjust(1)
    return builder.as_markup()


# ---------------------------------------------------------------------------
# Inline keyboards – Admin Section
# ---------------------------------------------------------------------------

def admin_eligible_keyboard(eligible: list[dict]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="✏️ Rename Church",
        callback_data="admin_rename_church_start",
    )
    for lv in eligible:
        builder.button(
            text=f"{lv['station_name']} – Level {lv['level_number']}",
            callback_data=f"admin_upgrade_detail:{lv['id']}",
        )
    builder.adjust(1)
    builder.button(text="❌ Cancel", callback_data="admin_cancel")
    builder.adjust(1)
    return builder.as_markup()


def admin_confirm_keyboard(level_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="✅ Yes, confirm",
        callback_data=f"admin_confirm:{level_id}",
    )
    builder.button(text="❌ Cancel", callback_data="admin_section")
    builder.adjust(1)
    return builder.as_markup()


def admin_after_upgrade_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="⬆️ Mark another upgrade", callback_data="admin_section")
    builder.button(text="🏠 Back to Main Menu", callback_data="main_menu")
    builder.adjust(1)
    return builder.as_markup()


def church_steal_targets_keyboard(level_id: int, targets: list[dict]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for t in targets:
        builder.button(
            text=f"⚡ Steal from {t['name']}",
            callback_data=f"admin_church_confirm:{level_id}:{t['id']}",
        )
    builder.adjust(1)
    builder.button(
        text="⏩ Skip Theft (Upgrade Only)",
        callback_data=f"admin_church_confirm:{level_id}:skip",
    )
    builder.adjust(1)
    return builder.as_markup()


def church_confirm_keyboard(level_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="✅ Yes, confirm",
        callback_data=f"admin_confirm:{level_id}",
    )
    builder.button(
        text="💡 Buy Hint",
        callback_data=f"admin_church_hint_menu:{level_id}",
    )
    builder.button(text="❌ Cancel", callback_data="admin_section")
    builder.adjust(1)
    return builder.as_markup()


def church_hint_menu_keyboard(
    level_id: int, purchased: list[int], current_pop: float, N: int
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    # Hint 1: 15% base, less 1% per previous completion
    if 1 not in purchased:
        cost_pct = max(0.01, 0.15 - (N * 0.01))
        cost_1 = round(current_pop * cost_pct)
        builder.button(
            text=f"🔓 Buy Hint 1 (-{cost_1} pop, {round(cost_pct*100)}%)",
            callback_data=f"admin_church_hint_buy:{level_id}:1",
        )

    # Hint 2: 20% base, less 1% per previous completion
    if 2 not in purchased:
        cost_pct = max(0.01, 0.20 - (N * 0.01))
        cost_2 = round(current_pop * cost_pct)
        builder.button(
            text=f"🔓 Buy Hint 2 (-{cost_2} pop, {round(cost_pct*100)}%)",
            callback_data=f"admin_church_hint_buy:{level_id}:2",
        )

    # Hint 3: 25% base, less 1% per previous completion
    if 3 not in purchased:
        cost_pct = max(0.01, 0.25 - (N * 0.01))
        cost_3 = round(current_pop * cost_pct)
        builder.button(
            text=f"🔓 Buy Hint 3 (-{cost_3} pop, {round(cost_pct*100)}%)",
            callback_data=f"admin_church_hint_buy:{level_id}:3",
        )

    builder.adjust(1)
    builder.button(
        text="← Back to Upgrade",
        callback_data=f"admin_upgrade_detail:{level_id}",
    )
    builder.adjust(1)
    return builder.as_markup()
