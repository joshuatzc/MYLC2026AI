"""
bot/day_handlers/scoring.py – All Day Bot Telegram UI handlers.

This bot is used by the ice breaker points scorer on the day before night games.
It lets them register mini-games, enter ranked results, view standings, and
finalize (apply population bonuses so the night bot picks them up automatically).

Auth: one-time password challenge using the SECRET_KEY from .env.
      Authenticated chat IDs are held in memory — if the container restarts,
      the scorer just sends /start and enters the password again.
"""
from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    ReplyKeyboardMarkup,
    KeyboardButton,
)

from app.database import AsyncSessionLocal
from app.models import Group
from app.services import icebreaker

router = Router()

# ---------------------------------------------------------------------------
# In-memory auth (day-of tool; no persistence needed)
# ---------------------------------------------------------------------------

_authenticated: set[str] = set()


def _is_auth(chat_id: str) -> bool:
    return chat_id in _authenticated


# ---------------------------------------------------------------------------
# FSM state groups
# ---------------------------------------------------------------------------

class AuthState(StatesGroup):
    waiting_for_password = State()





class EnterResultsState(StatesGroup):
    waiting_for_game = State()
    waiting_for_placement = State()
    waiting_for_points_group_select = State()
    waiting_for_points_multiselect = State()
    waiting_for_points_value = State()
    waiting_for_confirm = State()


# ---------------------------------------------------------------------------
# Keyboard builders
# ---------------------------------------------------------------------------

ORDINALS = ["1st", "2nd", "3rd", "4th", "5th"]
PTS_MAP = {1: 500, 2: 400, 3: 300, 4: 200, 5: 100}


def _main_menu_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🎯 Enter Results"), KeyboardButton(text="🏆 Standings")],
            [KeyboardButton(text="✅ Finalize")],
        ],
        resize_keyboard=True
    )


def _back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="« Back to Menu", callback_data="ib:menu"),
    ]])


def _group_kb(
    groups: list[dict],
    prefix: str,
    exclude_ids: list[int] | None = None,
    show_done: bool = False,
) -> InlineKeyboardMarkup:
    """Group picker — 2 buttons per row, sorted Group 1…14."""
    exclude = set(exclude_ids or [])
    buttons = [
        InlineKeyboardButton(text=g["name"], callback_data=f"{prefix}:{g['id']}")
        for g in groups
        if g["id"] not in exclude
    ]
    rows: list[list[InlineKeyboardButton]] = [
        buttons[i:i + 2] for i in range(0, len(buttons), 2)
    ]
    if show_done:
        rows.append([InlineKeyboardButton(
            text="✓ Done — no more placers", callback_data=f"{prefix}:done"
        )])
    rows.append([InlineKeyboardButton(text="✖ Cancel", callback_data="ib:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _games_kb(games: list[dict], prefix: str) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(
            text=f"{g['name']}  ({len(g['results'])} result{'s' if len(g['results']) != 1 else ''})",
            callback_data=f"{prefix}:{g['id']}",
        )]
        for g in games
    ]
    rows.append([InlineKeyboardButton(text="« Back", callback_data="ib:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)





def _points_group_kb(
    groups: list[dict],
    scores: dict[int, int],
) -> InlineKeyboardMarkup:
    """Group picker for points entry — shows each group name and their assigned points."""
    buttons = []
    for g in groups:
        pts = scores.get(g["id"], 0)
        label = f"{g['name']} ({pts})"
        buttons.append(InlineKeyboardButton(text=label, callback_data=f"ib:pts:grp:{g['id']}"))

    rows: list[list[InlineKeyboardButton]] = [
        buttons[i:i + 2] for i in range(0, len(buttons), 2)
    ]
    rows.append([
        InlineKeyboardButton(text="✓ Done & Save", callback_data="ib:pts:done"),
        InlineKeyboardButton(text="✖ Cancel", callback_data="ib:menu")
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _confirm_redo_kb(confirm_cb: str, redo_cb: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Confirm & Save", callback_data=confirm_cb),
        InlineKeyboardButton(text="✏️ Redo", callback_data=redo_cb),
    ]])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _load_groups() -> list[dict]:
    """Return groups sorted numerically by their trailing number (Group 1 < Group 2 …)."""
    async with AsyncSessionLocal() as db:
        from sqlalchemy import select as sa_select
        res = await db.execute(sa_select(Group))
        groups = res.scalars().all()

    def _sort_key(g: Group) -> int:
        parts = g.name.rsplit(" ", 1)
        return int(parts[-1]) if parts[-1].isdigit() else 999

    return [{"id": g.id, "name": g.name} for g in sorted(groups, key=_sort_key)]


async def _show_main_menu(target: Message | CallbackQuery, edit: bool = False) -> None:
    text = "🏆 <b>MYLC Ice Breaker Scorer</b>\n\nUse the buttons below to navigate:"
    kb = _main_menu_kb()
    if isinstance(target, CallbackQuery):
        await target.message.answer(text, reply_markup=kb)
    else:
        await target.answer(text, reply_markup=kb)


# ---------------------------------------------------------------------------
# /start  +  password auth
# ---------------------------------------------------------------------------

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    chat_id = str(message.chat.id)
    if _is_auth(chat_id):
        await state.clear()
        await _show_main_menu(message)
        return
    await state.set_state(AuthState.waiting_for_password)
    await message.answer(
        "🔐 <b>Ice Breaker Scorer Bot</b>\n\nEnter the scorer password to continue:"
    )


@router.message(AuthState.waiting_for_password)
async def handle_password(message: Message, state: FSMContext) -> None:
    from app.config import settings
    chat_id = str(message.chat.id)
    if message.text and message.text.strip() == settings.SECRET_KEY:
        _authenticated.add(chat_id)
        await state.clear()
        await message.answer("✅ Authenticated! Welcome, scorer.")
        await _show_main_menu(message)
    else:
        await message.answer("❌ Wrong password. Try again:")


# ---------------------------------------------------------------------------
# Main menu callback
# ---------------------------------------------------------------------------

@router.callback_query(F.data == "ib:menu")
async def cb_menu(callback: CallbackQuery, state: FSMContext) -> None:
    if not _is_auth(str(callback.message.chat.id)):
        await callback.answer("Not authenticated — send /start", show_alert=True)
        return
    await state.clear()
    try:
        await callback.message.delete()
    except Exception:
        await callback.message.edit_text("Menu closed.")
    await _show_main_menu(callback)
    await callback.answer()


# ---------------------------------------------------------------------------
# 🎯  Enter Results
# ---------------------------------------------------------------------------

@router.message(F.text == "🎯 Enter Results")
async def msg_results_start(message: Message, state: FSMContext) -> None:
    if not _is_auth(str(message.chat.id)):
        await message.answer("Not authenticated.")
        return

    async with AsyncSessionLocal() as db:
        games = await icebreaker.get_all_games(db)

    if not games:
        await message.answer(
            "❌ No games yet. Add a game first."
        )
        return

    await state.set_state(EnterResultsState.waiting_for_game)
    await message.answer(
        "🎯 <b>Enter Results</b>\n\nSelect a game:",
        reply_markup=_games_kb(games, "ib:results:game"),
    )


@router.callback_query(F.data.startswith("ib:results:game:"))
async def cb_results_pick_game(callback: CallbackQuery, state: FSMContext) -> None:
    game_id = int(callback.data.split(":")[-1])

    async with AsyncSessionLocal() as db:
        games = await icebreaker.get_all_games(db)

    game = next((g for g in games if g["id"] == game_id), None)
    if not game:
        await callback.answer("Game not found.", show_alert=True)
        return

    overwrite_note = ""
    if game["results"]:
        overwrite_note = (
            "\n\n⚠️ <i>This game already has results — "
            "entering new ones will add/edit them.</i>"
        )

    scoring_type = game.get("scoring_type", "ranking")
    game_name = game["name"]

    if scoring_type == "points":
        # Pre-populate scores from existing results
        scores = {r["group_id"]: r["points"] for r in game["results"]}

        if game_name in ("Chemistry Competition", "Scratch My Back"):
            await state.update_data(
                game_id=game_id,
                game_name=game_name,
                scoring_type=scoring_type,
                scores=scores,
                selected_group_ids=[],
            )
            await state.set_state(EnterResultsState.waiting_for_points_multiselect)
            await _show_points_multiselect(callback, state)
        elif game_name == "Blind Karaoke":
            await state.update_data(
                game_id=game_id,
                game_name=game_name,
                scoring_type=scoring_type,
                scores=scores,
                single_select_group_id=None,
            )
            await state.set_state(EnterResultsState.waiting_for_points_group_select)
            await _show_points_single_select(callback, state)
        else:
            await state.update_data(
                game_id=game_id,
                game_name=game_name,
                scoring_type=scoring_type,
                scores=scores,
                current_editing_group_id=None,
            )
            await state.set_state(EnterResultsState.waiting_for_points_group_select)
            await _show_points_group_select(callback, state)
    else:
        max_places = 1 if scoring_type == "single" else 5
        await state.update_data(
            game_id=game_id,
            game_name=game_name,
            scoring_type=scoring_type,
            max_places=max_places,
            placements=[],
            current_place=1,
        )
        await state.set_state(EnterResultsState.waiting_for_placement)

        groups = await _load_groups()
        place_label = "only winner" if scoring_type == "single" else "1st place"
        await callback.message.edit_text(
            f"🎯 <b>{game_name}</b>{overwrite_note}\n\n"
            f"Who came <b>{place_label}</b>?",
            reply_markup=_group_kb(groups, "ib:pick", show_done=False),
        )
    await callback.answer()


@router.callback_query(F.data == "ib:results")
async def cb_results_redo(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    game_id = data.get("game_id")
    if not game_id:
        await callback.answer("Session expired.", show_alert=True)
        return

    # Simulate game pick to restart entry
    callback.data = f"ib:results:game:{game_id}"
    await cb_results_pick_game(callback, state)


def _points_multiselect_kb(
    groups: list[dict],
    selected_ids: list[int],
) -> InlineKeyboardMarkup:
    """Group picker for Chemistry/Scratch My Back — toggles checks next to groups."""
    selected_set = set(selected_ids)
    buttons = []
    for g in groups:
        label = f"✅ {g['name']}" if g["id"] in selected_set else f"☐ {g['name']}"
        buttons.append(InlineKeyboardButton(text=label, callback_data=f"ib:pts:toggle:{g['id']}"))

    rows: list[list[InlineKeyboardButton]] = [
        buttons[i:i + 2] for i in range(0, len(buttons), 2)
    ]
    rows.append([
        InlineKeyboardButton(text="✓ Done & Add +100 pts", callback_data="ib:pts:multi_done"),
        InlineKeyboardButton(text="✖ Cancel", callback_data="ib:menu")
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _show_points_multiselect(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    data = await state.get_data()
    game_name = data["game_name"]
    selected_group_ids = data["selected_group_ids"]

    groups = await _load_groups()

    lines = [
        f"🎯 <b>{game_name}</b> (+100 pts per round win)",
        "Select all groups that won this round. Tapping a group toggles selection. Click <b>Done & Add +100 pts</b> when finished.\n",
        "<b>Selected Groups:</b>"
    ]
    selected_names = []
    selected_set = set(selected_group_ids)
    for g in groups:
        if g["id"] in selected_set:
            selected_names.append(g["name"])

    if selected_names:
        lines.append(", ".join(selected_names))
    else:
        lines.append("<i>None selected yet</i>")

    reply_markup = _points_multiselect_kb(groups, selected_group_ids)
    await callback.message.edit_text("\n".join(lines), reply_markup=reply_markup)


@router.callback_query(F.data.startswith("ib:pts:toggle:"))
async def cb_points_toggle_group(callback: CallbackQuery, state: FSMContext) -> None:
    group_id = int(callback.data.split(":")[-1])
    data = await state.get_data()
    selected_group_ids: list[int] = data["selected_group_ids"]

    if group_id in selected_group_ids:
        selected_group_ids.remove(group_id)
    else:
        selected_group_ids.append(group_id)

    await state.update_data(selected_group_ids=selected_group_ids)
    await _show_points_multiselect(callback, state)
    await callback.answer()


@router.callback_query(F.data == "ib:pts:multi_done")
async def cb_points_multi_done(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    selected_group_ids: list[int] = data["selected_group_ids"]
    game_name: str = data["game_name"]
    scores: dict[int, int] = data["scores"]

    groups = await _load_groups()
    lines = [
        f"📋 <b>Confirm additions for: {game_name}</b>\n",
        "The following groups will be added <b>+100 points</b>:\n"
    ]
    if selected_group_ids:
        for gid in selected_group_ids:
            gname = next((g["name"] for g in groups if g["id"] == gid), f"Group {gid}")
            current_total = scores.get(gid, 0)
            new_total = current_total + 100
            lines.append(f"  • {gname}: <b>+100 pts</b> (New Total: <b>{new_total} pts</b>)")
    else:
        lines.append("  <i>None (no points will be added)</i>")

    await state.set_state(EnterResultsState.waiting_for_confirm)
    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=_confirm_redo_kb("ib:results:confirm", "ib:results"),
    )
    await callback.answer()


def _points_single_select_kb(
    groups: list[dict],
) -> InlineKeyboardMarkup:
    """Group picker for Blind Karaoke — one tap selects and triggers confirmation."""
    buttons = [
        InlineKeyboardButton(text=g["name"], callback_data=f"ib:pts:single:{g['id']}")
        for g in groups
    ]
    rows: list[list[InlineKeyboardButton]] = [
        buttons[i:i + 2] for i in range(0, len(buttons), 2)
    ]
    rows.append([
        InlineKeyboardButton(text="✖ Cancel", callback_data="ib:menu")
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _show_points_single_select(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    data = await state.get_data()
    game_name = data["game_name"]
    groups = await _load_groups()

    lines = [
        f"🎯 <b>{game_name}</b> (+300 pts per round win)",
        "Select the group that won this round. Tapping a group will go straight to the confirmation screen."
    ]

    reply_markup = _points_single_select_kb(groups)
    await callback.message.edit_text("\n".join(lines), reply_markup=reply_markup)


@router.callback_query(F.data.startswith("ib:pts:single:"))
async def cb_points_single_pick(callback: CallbackQuery, state: FSMContext) -> None:
    group_id = int(callback.data.split(":")[-1])
    data = await state.get_data()
    game_name: str = data["game_name"]
    scores: dict[int, int] = data["scores"]

    groups = await _load_groups()
    gname = next((g["name"] for g in groups if g["id"] == group_id), f"Group {group_id}")
    current_total = scores.get(group_id, 0)
    new_total = current_total + 300

    await state.update_data(single_select_group_id=group_id)

    lines = [
        f"📋 <b>Confirm addition for: {game_name}</b>\n",
        f"The following group will be added <b>+300 points</b>:\n",
        f"  • {gname}: <b>+300 pts</b> (New Total: <b>{new_total} pts</b>)"
    ]

    await state.set_state(EnterResultsState.waiting_for_confirm)
    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=_confirm_redo_kb("ib:results:confirm", "ib:results"),
    )
    await callback.answer()


async def _show_points_group_select(
    target: Message | CallbackQuery,
    state: FSMContext,
) -> None:
    data = await state.get_data()
    game_name = data["game_name"]
    scores = data["scores"]

    groups = await _load_groups()

    lines = [
        f"🎯 <b>{game_name}</b> (Direct Points)",
        "Select a group to enter/update points. Click <b>Done & Save</b> when finished.\n",
        "<b>Current Scores:</b>"
    ]
    has_scores = False
    for g in groups:
        pts = scores.get(g["id"], 0)
        if pts > 0:
            lines.append(f"  • {g['name']}: <b>{pts} pts</b>")
            has_scores = True

    if not has_scores:
        lines.append("  <i>No scores entered yet (all 0)</i>")

    reply_markup = _points_group_kb(groups, scores)

    if isinstance(target, CallbackQuery):
        await target.message.edit_text("\n".join(lines), reply_markup=reply_markup)
    else:
        await target.answer("\n".join(lines), reply_markup=reply_markup)


@router.callback_query(F.data.startswith("ib:pts:grp:"))
async def cb_points_pick_group(callback: CallbackQuery, state: FSMContext) -> None:
    group_id = int(callback.data.split(":")[-1])
    data = await state.get_data()
    game_name = data["game_name"]
    scores = data["scores"]

    groups = await _load_groups()
    gname = next((g["name"] for g in groups if g["id"] == group_id), f"Group {group_id}")
    current_pts = scores.get(group_id, 0)

    await state.update_data(current_editing_group_id=group_id)
    await state.set_state(EnterResultsState.waiting_for_points_value)

    await callback.message.edit_text(
        f"🎯 <b>{game_name}</b>\n\n"
        f"Enter points for <b>{gname}</b> (current: {current_pts}):\n\n"
        f"💡 <i>Type an integer (e.g. 300).\n"
        f"Prefix with + or - to add/subtract (e.g. +100, -50) for multi-round games.</i>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✖ Cancel", callback_data="ib:pts:cancel_edit"),
        ]])
    )
    await callback.answer()


@router.callback_query(F.data == "ib:pts:cancel_edit")
async def cb_points_cancel_edit(callback: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(current_editing_group_id=None)
    await state.set_state(EnterResultsState.waiting_for_points_group_select)
    await _show_points_group_select(callback, state)
    await callback.answer()


@router.message(EnterResultsState.waiting_for_points_value)
async def handle_points_value(message: Message, state: FSMContext) -> None:
    text = message.text.strip() if message.text else ""
    is_additive = text.startswith("+") or text.startswith("-")

    try:
        val = int(text)
    except ValueError:
        await message.answer(
            "❌ Please enter a valid integer.\n"
            "Examples: 300, +100, -50"
        )
        return

    data = await state.get_data()
    group_id = data["current_editing_group_id"]
    scores: dict[int, int] = data["scores"]

    if is_additive:
        scores[group_id] = max(0, scores.get(group_id, 0) + val)
    else:
        scores[group_id] = max(0, val)

    await state.update_data(scores=scores, current_editing_group_id=None)
    await state.set_state(EnterResultsState.waiting_for_points_group_select)

    await _show_points_group_select(message, state)


@router.callback_query(F.data == "ib:pts:done")
async def cb_points_done(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    scores: dict[int, int] = data["scores"]
    game_name: str = data["game_name"]

    non_zero = {gid: pts for gid, pts in scores.items() if pts > 0}
    if not non_zero:
        await callback.answer("Please enter points for at least one group.", show_alert=True)
        return

    groups = await _load_groups()
    lines = [f"📋 <b>Confirm results for: {game_name}</b>\n"]
    for gid, pts in sorted(non_zero.items(), key=lambda x: -x[1]):
        gname = next((g["name"] for g in groups if g["id"] == gid), f"Group {gid}")
        lines.append(f"  • {gname}: <b>+{pts} pts</b>")

    await state.set_state(EnterResultsState.waiting_for_confirm)
    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=_confirm_redo_kb("ib:results:confirm", "ib:results"),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("ib:pick:"))
async def cb_pick_group(callback: CallbackQuery, state: FSMContext) -> None:
    token = callback.data.split(":")[-1]
    data = await state.get_data()
    placements: list[int] = data["placements"]
    current_place: int = data["current_place"]
    max_places: int = data["max_places"]
    game_name: str = data["game_name"]

    if token == "done":
        if not placements:
            await callback.answer("Enter at least 1 placement first.", show_alert=True)
            return
        await _show_confirm(callback, state, data)
        return

    group_id = int(token)
    placements.append(group_id)
    current_place += 1
    await state.update_data(placements=placements, current_place=current_place)

    groups = await _load_groups()

    if current_place > max_places:
        await _show_confirm(callback, state, await state.get_data())
        return

    placed_lines = "\n".join(
        f"  {ORDINALS[i]}: {next((g['name'] for g in groups if g['id'] == pid), '?')}"
        for i, pid in enumerate(placements)
    )
    ordinal = ORDINALS[current_place - 1] if current_place <= 5 else f"{current_place}th"

    await callback.message.edit_text(
        f"🎯 <b>{game_name}</b>\n\n"
        f"<b>Placed so far:</b>\n{placed_lines}\n\n"
        f"Who came <b>{ordinal} place</b>?",
        reply_markup=_group_kb(groups, "ib:pick", exclude_ids=placements, show_done=True),
    )
    await callback.answer()


async def _show_confirm(
    callback: CallbackQuery,
    state: FSMContext,
    data: dict,
) -> None:
    groups = await _load_groups()
    placements: list[int] = data["placements"]
    game_name: str = data["game_name"]

    lines = [f"📋 <b>Confirm results for: {game_name}</b>\n"]
    for i, gid in enumerate(placements):
        gname = next((g["name"] for g in groups if g["id"] == gid), "?")
        pts = PTS_MAP.get(i + 1, 0)
        lines.append(f"  {ORDINALS[i]}: <b>{gname}</b>  (+{pts} pts)")

    await state.set_state(EnterResultsState.waiting_for_confirm)
    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=_confirm_redo_kb("ib:results:confirm", "ib:results"),
    )
    await callback.answer()


@router.callback_query(F.data == "ib:results:confirm")
async def cb_results_confirm(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    game_id: int = data["game_id"]
    game_name: str = data["game_name"]
    scoring_type: str = data.get("scoring_type", "ranking")

    # Load groups for names
    groups = await _load_groups()

    summary_lines = [
        f"✅ <b>Results saved!</b>",
        f"🎯 Game: <b>{game_name}</b>",
    ]

    async with AsyncSessionLocal() as db:
        try:
            if scoring_type == "points":
                scores: dict[int, int] = data["scores"]

                if game_name in ("Chemistry Competition", "Scratch My Back"):
                    selected_group_ids: list[int] = data["selected_group_ids"]
                    for gid in selected_group_ids:
                        scores[gid] = scores.get(gid, 0) + 100
                    await icebreaker.record_results(db, game_id, scores=scores)

                    summary_lines.append("\n<b>Scores Log (Round Added):</b>")
                    if selected_group_ids:
                        for gid in selected_group_ids:
                            gname = next((g["name"] for g in groups if g["id"] == gid), f"Group {gid}")
                            summary_lines.append(f"  • {gname}: <b>+100 pts</b> (New Total: <b>{scores[gid]} pts</b>)")
                    else:
                        summary_lines.append("  <i>None (no points added this round)</i>")

                elif game_name == "Blind Karaoke":
                    gid = data["single_select_group_id"]
                    scores[gid] = scores.get(gid, 0) + 300
                    await icebreaker.record_results(db, game_id, scores=scores)

                    summary_lines.append("\n<b>Scores Log (Round Added):</b>")
                    gname = next((g["name"] for g in groups if g["id"] == gid), f"Group {gid}")
                    summary_lines.append(f"  • {gname}: <b>+300 pts</b> (New Total: <b>{scores[gid]} pts</b>)")

                else:
                    # General direct points (e.g. Taste So Good)
                    await icebreaker.record_results(db, game_id, scores=scores)
                    non_zero = {gid: pts for gid, pts in scores.items() if pts > 0}
                    if non_zero:
                        summary_lines.append("\n<b>Scores Log:</b>")
                        for gid, pts in sorted(non_zero.items(), key=lambda x: -x[1]):
                            gname = next((g["name"] for g in groups if g["id"] == gid), f"Group {gid}")
                            summary_lines.append(f"  • {gname}: <b>+{pts} pts</b>")
                    else:
                        summary_lines.append("\n<i>No points were recorded (all 0)</i>")
            else:
                placements: list[int] = data["placements"]
                await icebreaker.record_results(db, game_id, placements=placements)
                if placements:
                    summary_lines.append("\n<b>Placements Log:</b>")
                    for i, gid in enumerate(placements):
                        gname = next((g["name"] for g in groups if g["id"] == gid), f"Group {gid}")
                        pts = PTS_MAP.get(i + 1, 0)
                        summary_lines.append(f"  • {ORDINALS[i]}: <b>{gname}</b> (+{pts} pts)")
                else:
                    summary_lines.append("\n<i>No placements were recorded</i>")
        except ValueError as exc:
            await state.clear()
            await callback.message.edit_text(f"❌ Error: {exc}", reply_markup=_back_kb())
            await callback.answer()
            return

    await state.clear()
    await callback.message.edit_text(
        "\n".join(summary_lines),
        reply_markup=None,  # removes inline buttons completely so it stays as a clean log message
    )
    await callback.answer("Saved!", show_alert=False)


# ---------------------------------------------------------------------------
# 🏆  Standings
# ---------------------------------------------------------------------------

@router.message(F.text == "🏆 Standings")
async def msg_standings(message: Message) -> None:
    if not _is_auth(str(message.chat.id)):
        await message.answer("Not authenticated.")
        return

    async with AsyncSessionLocal() as db:
        standings = await icebreaker.get_standings(db)

    medals = {1: "🥇", 2: "🥈", 3: "🥉"}
    lines = ["🏆 <b>Current Standings</b>\n"]
    for s in standings:
        medal = medals.get(s["rank"], f"{s['rank']}.")
        bonus = f"+{s['starting_pop_bonus']}" if s["starting_pop_bonus"] > 0 else "no bonus"
        lines.append(
            f"{medal} <b>{s['group_name']}</b>  —  {s['total_points']} pts  "
            f"→ starts at <b>{s['final_starting_pop']}</b> ({bonus})"
        )

    await message.answer("\n".join(lines))


# ---------------------------------------------------------------------------
# ✅  Finalize → Open Night Games
# ---------------------------------------------------------------------------

@router.message(F.text == "✅ Finalize")
async def msg_finalize_preview(message: Message) -> None:
    if not _is_auth(str(message.chat.id)):
        await message.answer("Not authenticated.")
        return

    async with AsyncSessionLocal() as db:
        standings = await icebreaker.get_standings(db)

    medals = {1: "🥇", 2: "🥈", 3: "🥉"}
    lines = [
        "✅ <b>Finalize Ice Breaker</b>\n",
        "This will apply starting population bonuses and open the night games.\n",
        "<b>Starting Populations After Bonus:</b>",
    ]
    for s in standings:
        medal = medals.get(s["rank"], f"{s['rank']}.")
        bonus = f"+{s['starting_pop_bonus']}" if s["starting_pop_bonus"] > 0 else "no bonus"
        lines.append(
            f"  {medal} {s['group_name']}: <b>{s['final_starting_pop']}</b> people ({bonus})"
        )

    await message.answer(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="🚀 Yes, Open Night Games!", callback_data="ib:finalize:confirm"),
                InlineKeyboardButton(text="✖ Cancel", callback_data="ib:menu"),
            ],
        ]),
    )


@router.callback_query(F.data == "ib:finalize:confirm")
async def cb_finalize_confirm(callback: CallbackQuery) -> None:
    if not _is_auth(str(callback.message.chat.id)):
        await callback.answer("Not authenticated.", show_alert=True)
        return

    async with AsyncSessionLocal() as db:
        standings = await icebreaker.apply_bonuses(db)

    medals = {1: "🥇", 2: "🥈", 3: "🥉"}
    top_lines_parts = []
    for s in standings[:5]:
        medal = medals.get(s["rank"], f"{s['rank']}.")
        top_lines_parts.append(f"  {medal} {s['group_name']} → {s['final_starting_pop']} people")
    top_lines = "\n".join(top_lines_parts)

    await callback.message.edit_text(
        f"🚀 <b>Night games are now LIVE!</b>\n\n"
        f"Population bonuses applied. Top 5 starting populations:\n"
        f"{top_lines}\n\n"
        f"<i>Players can now use the night bot.</i>"
    )
    await callback.answer("Done!", show_alert=False)
