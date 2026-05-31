"""
bot/handlers/corruption.py – Corruption of Leaders quiz flow.

Scoring:
  - Right answer:    +10% population (sequential, capped at church max)
  - Wrong answer:    -5%  population (sequential, floor at 10)
  - Timeout (20s):   -5%  population (same as wrong, auto-advances)

Questions are loaded from app/config/corruption_quiz.json.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime

from aiogram import Bot, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import CallbackQuery, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select

from app.config import settings
from app.database import AsyncSessionLocal
from app.models import ChatState, GlobalState, Group, GroupQuizState
from app.services import game_logic

logger = logging.getLogger(__name__)
router = Router()

QUESTION_TIME_LIMIT = 20  # seconds per question

# ---------------------------------------------------------------------------
# Quiz questions – loaded once at import
# ---------------------------------------------------------------------------

def _load_questions() -> list[dict]:
    candidates = [
        os.path.join("app", "config", "corruption_quiz.json"),
        os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..", "config", "corruption_quiz.json")
        ),
    ]
    for path in candidates:
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                return json.load(f)
    logger.error("corruption_quiz.json not found! Checked: %s", candidates)
    return []


QUESTIONS: list[dict] = _load_questions()
TOTAL_QUESTIONS: int = len(QUESTIONS)

# ---------------------------------------------------------------------------
# In-memory per-question and briefing timer state (keyed by group_id/chat_id)
# ---------------------------------------------------------------------------

_question_timers: dict[int, asyncio.Task] = {}   # running timeout tasks
_question_sent_at: dict[int, float] = {}          # unix timestamps of question send
_briefing_timers: dict[str, asyncio.Task] = {}   # chat_id -> briefing countdown task


def cancel_briefing_timer(chat_id: str) -> None:
    """Helper to cancel a running briefing timer for a chat session."""
    task = _briefing_timers.pop(chat_id, None)
    if task and not task.done():
        task.cancel()


# ---------------------------------------------------------------------------
# Keyboard / formatting helpers
# ---------------------------------------------------------------------------

def _question_keyboard(question_index: int) -> InlineKeyboardMarkup:
    q = QUESTIONS[question_index]
    builder = InlineKeyboardBuilder()
    for letter, text in q["options"].items():
        builder.button(
            text=f"{letter}:  {text}",
            callback_data=f"corruption_answer:{question_index}:{letter}",
        )
    builder.adjust(1)
    return builder.as_markup()


def _format_question(
    question_index: int, correct: int, wrong: int, current_pop: int, remaining_seconds: int = QUESTION_TIME_LIMIT
) -> str:
    q = QUESTIONS[question_index]
    filled_blocks = round((remaining_seconds / QUESTION_TIME_LIMIT) * 10)
    filled_blocks = max(0, min(10, filled_blocks))
    bar = f"[{'█' * filled_blocks}{'░' * (10 - filled_blocks)}]"

    return (
        f"📜 <b>Corruption of Leaders Quiz</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"Question <b>{question_index + 1}</b> of <b>{TOTAL_QUESTIONS}</b>  "
        f"│  ✅ {correct}  ❌ {wrong}  │  👥 {current_pop:,}\n"
        f"⏱️ <b>Time Remaining:</b> <code>{bar}</code> <b>{remaining_seconds}s</b>\n\n"
        f"<b>{q['question']}</b>"
    )

# ---------------------------------------------------------------------------
# Per-question timer management
# ---------------------------------------------------------------------------

def _start_question_timer(group_id: int, question_index: int, message_id: int, chat_id: str) -> None:
    """Cancel any running timer for this group and start a fresh 20-second countdown."""
    existing = _question_timers.get(group_id)
    if existing and not existing.done():
        existing.cancel()
    _question_timers[group_id] = asyncio.create_task(
        _question_timeout_task(group_id, question_index, message_id, chat_id)
    )
    _question_sent_at[group_id] = time.time()


def _cancel_question_timer(group_id: int) -> None:
    """Cancel the running timer (called when user answers in time)."""
    task = _question_timers.pop(group_id, None)
    if task and not task.done():
        task.cancel()
    _question_sent_at.pop(group_id, None)


async def _question_timeout_task(group_id: int, question_index: int, message_id: int, chat_id: str) -> None:
    """
    Fires after QUESTION_TIME_LIMIT seconds.
    Updates the visual progress bar in the question message every 2 seconds.
    If the group is still on this question when time runs out, auto-advance with a -5% penalty
    and send the next question (or final summary) to all the group's chat sessions.
    """
    bot = Bot(
        token=settings.TELEGRAM_BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    try:
        remaining = float(QUESTION_TIME_LIMIT)
        while remaining > 0:
            sleep_step = min(2.0, remaining)
            await asyncio.sleep(sleep_step)
            remaining -= sleep_step

            async with AsyncSessionLocal() as db:
                quiz_state = (await db.execute(
                    select(GroupQuizState).where(GroupQuizState.group_id == group_id)
                )).scalar_one_or_none()

                # Guard: already answered or event stopped
                if (
                    not quiz_state
                    or quiz_state.completed
                    or quiz_state.current_question_index != question_index
                ):
                    return

                group = (await db.execute(
                    select(Group).where(Group.id == group_id)
                )).scalar_one_or_none()
                if not group:
                    return

                correct_count = quiz_state.correct_count
                wrong_count = quiz_state.wrong_count
                current_pop = int(group.population)

            # Edit in-place with updated progress bar
            text = _format_question(question_index, correct_count, wrong_count, current_pop, remaining_seconds=int(remaining))
            kb = _question_keyboard(question_index)
            try:
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=text,
                    reply_markup=kb,
                )
            except Exception as e:
                if "message to edit not found" in str(e):
                    break
                if "message is not modified" not in str(e):
                    logger.warning("Failed to edit question countdown for group %d: %s", group_id, e)

        # ---- Apply timeout penalty in DB -----------------------------------------
        chat_ids: list[str] = []
        old_pop: int = 0
        new_pop: int = 0
        is_last: bool = False
        correct_count: int = 0
        wrong_count: int = 0
        group_name: str = ""
        next_q_idx: int = question_index + 1

        async with AsyncSessionLocal() as db:
            quiz_state = (await db.execute(
                select(GroupQuizState).where(GroupQuizState.group_id == group_id)
            )).scalar_one_or_none()

            # Final guard check
            if (
                not quiz_state
                or quiz_state.completed
                or quiz_state.current_question_index != question_index
            ):
                return

            group = (await db.execute(
                select(Group).where(Group.id == group_id)
            )).scalar_one_or_none()
            if not group:
                return

            old_pop = int(group.population)
            new_pop = max(10, round(group.population * 0.95))   # -5% timeout penalty
            group.population = new_pop

            quiz_state.wrong_count += 1
            quiz_state.current_question_index += 1
            is_last = quiz_state.current_question_index >= TOTAL_QUESTIONS
            if is_last:
                quiz_state.completed = True

            correct_count = quiz_state.correct_count
            wrong_count = quiz_state.wrong_count
            group_name = group.name
            next_q_idx = quiz_state.current_question_index

            # Find all chat sessions linked to this group
            cs_rows = (await db.execute(
                select(ChatState).where(ChatState.group_id == group_id)
            )).scalars().all()
            chat_ids = [cs.chat_id for cs in cs_rows]

            await db.commit()

        if not chat_ids:
            return

        # ---- Build and send messages ---------------------------------------------
        correct_ans = QUESTIONS[question_index]["answer"]
        correct_text = QUESTIONS[question_index]["options"][correct_ans]
        delta = new_pop - old_pop
        delta_str = f"{delta:,}"

        timeout_msg = (
            f"⏰ <b>Time's up!</b> (-5%)\n"
            f"Correct answer: <b>{correct_ans}: {correct_text}</b>\n"
            f"👥 Population: <b>{old_pop:,}</b> → <b>{new_pop:,}</b> ({delta_str})"
        )

        active_msg_id = None
        active_chat_id = None

        for chat_id_item in chat_ids:
            try:
                await bot.send_message(chat_id_item, timeout_msg)
                if is_last:
                    await bot.send_message(
                        chat_id_item,
                        f"📜 <b>Quiz Complete!</b>\n"
                        f"━━━━━━━━━━━━━━━━━━━\n"
                        f"<b>{group_name}</b> has finished the quiz!\n\n"
                        f"✅ Correct: <b>{correct_count}</b>  "
                        f"│  ❌ Wrong / Timed out: <b>{wrong_count}</b>\n\n"
                        f"👥 Final Population: <b>{new_pop:,}</b> members\n\n"
                        f"Your church's legitimacy has been verified! ⛪",
                    )
                else:
                    sent_msg = await bot.send_message(
                        chat_id_item,
                        _format_question(next_q_idx, correct_count, wrong_count, new_pop),
                        reply_markup=_question_keyboard(next_q_idx),
                    )
                    active_msg_id = sent_msg.message_id
                    active_chat_id = chat_id_item
            except Exception as exc:  # noqa: BLE001
                logger.warning("Timeout send failed for chat %s: %s", chat_id_item, exc)

        if not is_last and active_msg_id and active_chat_id:
            _start_question_timer(group_id, next_q_idx, active_msg_id, active_chat_id)

    finally:
        _question_timers.pop(group_id, None)
        _question_sent_at.pop(group_id, None)
        await bot.session.close()


# ---------------------------------------------------------------------------
# Background task – 20-minute global event countdown briefing loop
# ---------------------------------------------------------------------------

async def _briefing_countdown_task(chat_id: str, message_id: int) -> None:
    """
    Background task to update the 20-minute global event countdown on the briefing message.
    Updates every 5 seconds.
    """
    bot = Bot(
        token=settings.TELEGRAM_BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    try:
        while True:
            async with AsyncSessionLocal() as db:
                from app.models import GlobalState
                active = (await db.execute(
                    select(GlobalState).where(GlobalState.key == "corruption_active")
                )).scalar_one_or_none()

                # Stop if event is no longer active
                if not active or not active.value_bool:
                    break

                started_row = (await db.execute(
                    select(GlobalState).where(GlobalState.key == "corruption_started_at")
                )).scalar_one_or_none()
                duration_row = (await db.execute(
                    select(GlobalState).where(GlobalState.key == "corruption_duration")
                )).scalar_one_or_none()

            if not started_row or not duration_row:
                break

            started_at = datetime.fromisoformat(started_row.value_str)
            duration_mins = duration_row.value_int or 20
            elapsed = (datetime.utcnow() - started_at).total_seconds()
            remaining = (duration_mins * 60) - elapsed

            if remaining <= 0:
                break

            mins = int(remaining // 60)
            secs = int(remaining % 60)
            percentage = max(0.0, min(1.0, remaining / (duration_mins * 60)))
            filled = round(percentage * 10)
            bar = f"[{'█' * filled}{'░' * (10 - filled)}]"

            text = (
                f"📜 <b>Corruption of Leaders Quiz</b>\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"The CAC Legitimacy Investigation is active! Prove your knowledge to secure your church's standing.\n\n"
                f"⚠️ <b>ANTI-CHEAT WARNING:</b>\n"
                f"• You only have <b>20 seconds</b> to answer each question!\n"
                f"• The timer starts as soon as you press the button below.\n\n"
                f"📈 <b>CONSEQUENCES:</b>\n"
                f"• ✅ <b>Correct Answer:</b> <b>+10%</b> congregation members\n"
                f"• ❌ <b>Wrong Answer / Timeout:</b> <b>-5%</b> congregation members\n"
                f"• ⏰ <b>Not Completing:</b> <b>-5%</b> per unanswered question when the event ends!\n\n"
                f"⏱️ <b>Global Event Time Remaining:</b>\n"
                f"<code>{bar}</code> <b>{mins}m {secs}s remaining</b>\n\n"
                f"Do not close this menu or navigate away once you start. Are you ready?"
            )

            builder = InlineKeyboardBuilder()
            builder.button(text="🚀 Start Quiz Now", callback_data="corruption_confirm_start")
            builder.button(text="❌ Cancel", callback_data="admin_cancel")
            builder.adjust(1)

            try:
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=text,
                    reply_markup=builder.as_markup(),
                )
            except Exception as e:
                if "message to edit not found" in str(e):
                    break
                if "message is not modified" not in str(e):
                    break

            await asyncio.sleep(5)
    finally:
        _briefing_timers.pop(chat_id, None)
        await bot.session.close()


# ---------------------------------------------------------------------------
# Callback: leader taps "📜 Take Corruption Quiz"
# ---------------------------------------------------------------------------

@router.callback_query(F.data == "corruption_start_quiz")
async def cb_start_corruption_quiz(callback: CallbackQuery) -> None:
    chat_id = str(callback.message.chat.id)

    async with AsyncSessionLocal() as db:
        cs = (await db.execute(
            select(ChatState).where(ChatState.chat_id == chat_id)
        )).scalar_one_or_none()
        if not cs or not cs.group_id:
            await callback.answer("⚠️ No group linked to this chat.", show_alert=True)
            return
        group_id: int = cs.group_id

        active_row = (await db.execute(
            select(GlobalState).where(GlobalState.key == "corruption_active")
        )).scalar_one_or_none()
        if not active_row or not active_row.value_bool:
            await callback.answer("⚠️ The Corruption quiz event is no longer active!", show_alert=True)
            return

        quiz_state = (await db.execute(
            select(GroupQuizState).where(GroupQuizState.group_id == group_id)
        )).scalar_one_or_none()

        if quiz_state and quiz_state.completed:
            await callback.answer("✅ Your group has already completed the quiz!", show_alert=True)
            return

        started_row = (await db.execute(
            select(GlobalState).where(GlobalState.key == "corruption_started_at")
        )).scalar_one_or_none()
        duration_row = (await db.execute(
            select(GlobalState).where(GlobalState.key == "corruption_duration")
        )).scalar_one_or_none()

    if started_row and duration_row:
        started_at = datetime.fromisoformat(started_row.value_str)
        duration_mins = duration_row.value_int or 20
        elapsed = (datetime.utcnow() - started_at).total_seconds()
        remaining = (duration_mins * 60) - elapsed
    else:
        remaining = 20 * 60

    mins = int(max(0, remaining // 60))
    secs = int(max(0, remaining % 60))
    percentage = max(0.0, min(1.0, remaining / (20 * 60)))
    filled = round(percentage * 10)
    bar = f"[{'█' * filled}{'░' * (10 - filled)}]"

    # Show briefing page if not started
    if not quiz_state:
        text = (
            f"📜 <b>Corruption of Leaders Quiz</b>\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"The CAC Legitimacy Investigation is active! Prove your knowledge to secure your church's standing.\n\n"
            f"⚠️ <b>ANTI-CHEAT WARNING:</b>\n"
            f"• You only have <b>20 seconds</b> to answer each question!\n"
            f"• The timer starts as soon as you press the button below.\n\n"
            f"📈 <b>CONSEQUENCES:</b>\n"
            f"• ✅ <b>Correct Answer:</b> <b>+10%</b> congregation members\n"
            f"• ❌ <b>Wrong Answer / Timeout:</b> <b>-5%</b> congregation members\n"
            f"• ⏰ <b>Not Completing:</b> <b>-5%</b> per unanswered question when the event ends!\n\n"
            f"⏱️ <b>Global Event Time Remaining:</b>\n"
            f"<code>{bar}</code> <b>{mins}m {secs}s remaining</b>\n\n"
            f"Do not close this menu or navigate away once you start. Are you ready?"
        )

        builder = InlineKeyboardBuilder()
        builder.button(text="🚀 Start Quiz Now", callback_data="corruption_confirm_start")
        builder.button(text="❌ Cancel", callback_data="admin_cancel")
        builder.adjust(1)

        await callback.message.edit_text(text, reply_markup=builder.as_markup())
        await callback.answer()

        # Cancel existing briefing timer task
        existing = _briefing_timers.get(chat_id)
        if existing and not existing.done():
            existing.cancel()
        _briefing_timers[chat_id] = asyncio.create_task(
            _briefing_countdown_task(chat_id, callback.message.message_id)
        )
        return

    q_idx = quiz_state.current_question_index
    await callback.answer(f"Resuming from question {q_idx + 1}…")

    async with AsyncSessionLocal() as db:
        group = (await db.execute(select(Group).where(Group.id == group_id))).scalar_one_or_none()
        current_pop = int(group.population) if group else 0
        correct = quiz_state.correct_count
        wrong = quiz_state.wrong_count

    await callback.message.edit_text(
        _format_question(q_idx, correct, wrong, current_pop),
        reply_markup=_question_keyboard(q_idx),
    )
    _start_question_timer(group_id, q_idx, callback.message.message_id, chat_id)


@router.callback_query(F.data == "corruption_confirm_start")
async def cb_confirm_start_quiz(callback: CallbackQuery) -> None:
    chat_id = str(callback.message.chat.id)

    # Cancel briefing timer for this chat
    cancel_briefing_timer(chat_id)

    async with AsyncSessionLocal() as db:
        cs = (await db.execute(
            select(ChatState).where(ChatState.chat_id == chat_id)
        )).scalar_one_or_none()
        if not cs or not cs.group_id:
            await callback.answer("⚠️ No group linked to this chat.", show_alert=True)
            return
        group_id: int = cs.group_id

        active_row = (await db.execute(
            select(GlobalState).where(GlobalState.key == "corruption_active")
        )).scalar_one_or_none()
        if not active_row or not active_row.value_bool:
            await callback.answer("⚠️ The Corruption quiz event is no longer active!", show_alert=True)
            return

        quiz_state = (await db.execute(
            select(GroupQuizState).where(GroupQuizState.group_id == group_id)
        )).scalar_one_or_none()

        if quiz_state and quiz_state.completed:
            await callback.answer("✅ Your group has already completed the quiz!", show_alert=True)
            return

        group = (await db.execute(select(Group).where(Group.id == group_id))).scalar_one_or_none()
        if not group:
            await callback.answer("Group not found.", show_alert=True)
            return

        if not quiz_state:
            quiz_state = GroupQuizState(
                group_id=group_id,
                current_question_index=0,
                correct_count=0,
                wrong_count=0,
                completed=False,
                started_at=datetime.utcnow(),
            )
            db.add(quiz_state)
            await db.commit()
            await callback.answer("📜 Quiz started! 20 seconds per question. Go!")
            q_idx = 0
        else:
            q_idx = quiz_state.current_question_index

        current_pop = int(group.population)
        correct = quiz_state.correct_count
        wrong = quiz_state.wrong_count

    if not QUESTIONS:
        await callback.message.edit_text("❌ Quiz questions failed to load. Contact the admin.")
        return

    await callback.message.edit_text(
        _format_question(q_idx, correct, wrong, current_pop),
        reply_markup=_question_keyboard(q_idx),
    )
    _start_question_timer(group_id, q_idx, callback.message.message_id, chat_id)


# ---------------------------------------------------------------------------
# Callback: leader taps an answer button
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("corruption_answer:"))
async def cb_corruption_answer(callback: CallbackQuery) -> None:
    _, q_idx_str, selected = callback.data.split(":")
    question_index = int(q_idx_str)
    chat_id = str(callback.message.chat.id)

    async with AsyncSessionLocal() as db:
        cs = (await db.execute(
            select(ChatState).where(ChatState.chat_id == chat_id)
        )).scalar_one_or_none()
        if not cs or not cs.group_id:
            await callback.answer("No group linked.", show_alert=True)
            return
        group_id: int = cs.group_id

        # Timing check
        sent_at = _question_sent_at.get(group_id, time.time())
        if time.time() - sent_at > QUESTION_TIME_LIMIT:
            await callback.answer("⏰ Too late! The timer already expired for that question.", show_alert=True)
            return

        quiz_state = (await db.execute(
            select(GroupQuizState).where(GroupQuizState.group_id == group_id)
        )).scalar_one_or_none()

        if not quiz_state or quiz_state.completed:
            await callback.answer("Quiz already completed or not started.", show_alert=True)
            return

        if quiz_state.current_question_index != question_index:
            await callback.answer("Please answer the current question.", show_alert=True)
            return

        group = (await db.execute(select(Group).where(Group.id == group_id))).scalar_one_or_none()
        if not group:
            await callback.answer("Group not found.", show_alert=True)
            return

        _cancel_question_timer(group_id)

        correct_answer: str = QUESTIONS[question_index]["answer"]
        is_correct = selected == correct_answer
        old_pop = group.population

        if is_correct:
            new_pop = min(
                float(game_logic.get_max_occupancy(group.church_level)),
                old_pop * 1.1,
            )
            quiz_state.correct_count += 1
        else:
            new_pop = max(10.0, old_pop * 0.95)
            quiz_state.wrong_count += 1

        new_pop = round(new_pop)
        group.population = new_pop
        quiz_state.current_question_index += 1

        is_last = quiz_state.current_question_index >= TOTAL_QUESTIONS
        if is_last:
            quiz_state.completed = True

        correct_count = quiz_state.correct_count
        wrong_count = quiz_state.wrong_count
        next_q_idx = quiz_state.current_question_index
        group_name = group.name

        await db.commit()

    # ---- Edit question message with result -----------------------------------
    result_icon = "✅" if is_correct else "❌"
    correct_option = QUESTIONS[question_index]["options"][correct_answer]
    delta = new_pop - int(old_pop)
    delta_str = f"+{delta:,}" if delta >= 0 else f"{delta:,}"

    await callback.message.edit_text(
        f"{result_icon} <b>{'Correct!' if is_correct else 'Wrong!'}</b>\n"
        f"Correct answer: <b>{correct_answer}: {correct_option}</b>\n"
        f"👥 Population: <b>{int(old_pop):,}</b> → <b>{new_pop:,}</b> ({delta_str})",
        reply_markup=None,
    )

    if is_last:
        await callback.message.answer(
            f"📜 <b>Quiz Complete!</b>\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"<b>{group_name}</b> has finished the Corruption of Leaders quiz!\n\n"
            f"✅ Correct: <b>{correct_count}</b>  │  ❌ Wrong / Timed out: <b>{wrong_count}</b>\n\n"
            f"👥 Final Population: <b>{new_pop:,}</b> members\n\n"
            f"Your church's legitimacy has been verified! ⛪"
        )
    else:
        sent_msg = await callback.message.answer(
            _format_question(next_q_idx, correct_count, wrong_count, new_pop),
            reply_markup=_question_keyboard(next_q_idx),
        )
        _start_question_timer(group_id, next_q_idx, sent_msg.message_id, str(sent_msg.chat.id))

    await callback.answer()
