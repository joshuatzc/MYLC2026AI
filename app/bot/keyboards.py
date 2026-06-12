"""
bot/keyboards.py – reusable keyboard builders for the Telegram bot.
"""
from __future__ import annotations

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    WebAppInfo,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.config import settings


# ---------------------------------------------------------------------------
# Reply keyboard (main menu)
# ---------------------------------------------------------------------------

def main_menu_keyboard(role: str = "normal") -> ReplyKeyboardMarkup:
    """Build the persistent reply keyboard based on the chat's current role."""
    rows = [
        [KeyboardButton(text="⛪ My Church"), KeyboardButton(text="🗺️ My Journey")],
        [KeyboardButton(text="📋 Check Prerequisites"), KeyboardButton(text="🏆 Leaderboard")],
        [KeyboardButton(text="📖 Help & Guides"), KeyboardButton(text="🔄 Change Group")],
    ]
    if role == "leader":
        rows[-1].append(KeyboardButton(text="🔑 Admin Section"))
    else:
        rows[-1].append(KeyboardButton(text="👑 Become Leader"))

    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def guides_keyboard() -> InlineKeyboardMarkup:
    """Build the inline keyboard to open Game Help and Bot Help."""
    builder = InlineKeyboardBuilder()
    base_url = settings.BASE_URL.rstrip("/")
    
    if base_url:
        is_https = base_url.startswith("https://")
        
        if is_https:
            # Premium WebApp experience
            builder.button(
                text="🤖 Bot Help",
                web_app=WebAppInfo(url=f"{base_url}/bothelp.html")
            )
            builder.button(
                text="🎮 Game Help",
                web_app=WebAppInfo(url=f"{base_url}/gamehelp.html")
            )
        else:
            # Fallback to standard URL links if not HTTPS (localhost / testing)
            builder.button(
                text="🤖 Bot Help",
                url=f"{base_url}/bothelp.html"
            )
            builder.button(
                text="🎮 Game Help",
                url=f"{base_url}/gamehelp.html"
            )
    else:
        # Configuration warning
        builder.button(
            text="⚠️ Help URLs not configured",
            callback_data="guide_not_configured"
        )
        
    builder.adjust(1)
    return builder.as_markup()


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

def admin_eligible_keyboard(
    eligible: list[dict],
    super_pastor_active: bool = False,
    corruption_quiz_available: bool = False,
    rename_available: bool = True,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if rename_available:
        builder.button(
            text="✏️ Rename Church",
            callback_data="admin_rename_church_start",
        )
    if super_pastor_active:
        builder.button(
            text="🌟 Claim Super Pastor",
            callback_data="claim_super_pastor",
        )
    if corruption_quiz_available:
        builder.button(
            text="📜 Take Corruption Quiz",
            callback_data="corruption_start_quiz",
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


def church_confirm_keyboard(
    level_id: int, next_hint_num: int | None = None, cost_pct: int = 0
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="✅ Yes, confirm",
        callback_data=f"admin_confirm:{level_id}",
    )
    if next_hint_num is not None:
        builder.button(
            text=f"💡 Buy Hint {next_hint_num} (-{cost_pct}% population)",
            callback_data=f"admin_church_hint_buy:{level_id}:{next_hint_num}",
        )
    builder.button(text="❌ Cancel", callback_data="admin_section")
    builder.adjust(1)
    return builder.as_markup()


def super_pastor_confirm_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="✅ Yes, confirm claim",
        callback_data="super_pastor_confirm",
    )
    builder.button(text="❌ Cancel", callback_data="admin_section")
    builder.adjust(1)
    return builder.as_markup()


def my_church_dashboard_keyboard(level_id: int) -> InlineKeyboardMarkup:
    """Build the button to view unlocked hints for the next level upgrade."""
    builder = InlineKeyboardBuilder()
    builder.button(
        text="🖼️ View Unlocked Hints",
        callback_data=f"view_hints_nav:{level_id}:0",
    )
    return builder.as_markup()


def hint_carousel_keyboard(level_id: int, current_idx: int, total_hints: int) -> InlineKeyboardMarkup:
    """Build navigation keyboard for browsing purchased photo hints."""
    builder = InlineKeyboardBuilder()
    
    if total_hints > 1:
        prev_idx = (current_idx - 1) % total_hints
        next_idx = (current_idx + 1) % total_hints
        builder.button(text="◀️ Prev", callback_data=f"view_hints_nav:{level_id}:{prev_idx}")
        builder.button(text="❌ Close", callback_data="hints_carousel_close")
        builder.button(text="Next ▶️", callback_data=f"view_hints_nav:{level_id}:{next_idx}")
        builder.adjust(3)
    else:
        builder.button(text="❌ Close", callback_data="hints_carousel_close")
        builder.adjust(1)
        
    return builder.as_markup()
