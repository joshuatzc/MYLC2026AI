"""
services/timed_events.py – Background timer tasks for live timed events.

Each function is designed to be run via asyncio.create_task() from within
a FastAPI endpoint. They sleep for the event duration, then apply effects
and fire an AI news broadcast automatically.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from sqlalchemy import select

from app.database import AsyncSessionLocal

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Super Pastor – auto-expire after duration
# ---------------------------------------------------------------------------

async def super_pastor_expire_timer(duration_minutes: int) -> None:
    """Auto-close the Super Pastor event if nobody claims it in time."""
    await asyncio.sleep(duration_minutes * 60)

    async with AsyncSessionLocal() as db:
        from app.models import GlobalState
        row = (await db.execute(
            select(GlobalState).where(GlobalState.key == "super_pastor_active")
        )).scalar_one_or_none()

        if not row or not row.value_bool:
            return  # Already claimed or manually stopped

        row.value_bool = False
        await db.commit()
        logger.info("Super Pastor event expired — no one claimed it.")

    from app.services.ai_news import trigger_event_broadcast
    await trigger_event_broadcast("super_pastor_expired", {})


# ---------------------------------------------------------------------------
# Infestation – auto-audit after duration
# ---------------------------------------------------------------------------

async def infestation_audit_timer(
    duration_minutes: int,
    cutoff: int,
    penalty: int,
) -> None:
    """
    Fire the infestation audit after duration_minutes.

    Each group's score = sum of completed station level_numbers + church_level.
    Groups with score < cutoff are penalised.
    """
    await asyncio.sleep(duration_minutes * 60)

    failed_groups: list[dict] = []
    passed_groups: list[dict] = []

    async with AsyncSessionLocal() as db:
        from app.models import GlobalState, Group, GroupStationProgress, StationLevel

        # Check if still active
        active_row = (await db.execute(
            select(GlobalState).where(GlobalState.key == "infestation_active")
        )).scalar_one_or_none()
        if not active_row or not active_row.value_bool:
            return

        active_row.value_bool = False

        # Audit every group
        all_groups = (await db.execute(select(Group))).scalars().all()
        for group in all_groups:
            # Sum level_numbers of all completed station levels
            completed_levels = (await db.execute(
                select(StationLevel.level_number)
                .join(GroupStationProgress, GroupStationProgress.station_level_id == StationLevel.id)
                .where(GroupStationProgress.group_id == group.id)
            )).scalars().all()

            score = (group.church_level or 1) + sum(completed_levels)

            if score >= cutoff:
                passed_groups.append({"name": group.name, "score": score})
            else:
                failed_groups.append({"name": group.name, "score": score})
                group.population = max(10.0, group.population - penalty)

        await db.commit()
        logger.info(
            "Infestation audit complete (cutoff=%d). Failed: %s. Passed: %s.",
            cutoff,
            [g["name"] for g in failed_groups],
            [g["name"] for g in passed_groups],
        )

    from app.services.ai_news import trigger_event_broadcast
    await trigger_event_broadcast("infestation_result", {
        "cutoff": cutoff,
        "penalty": penalty,
        "failed_groups": failed_groups,
        "passed_groups": passed_groups,
    })



# ---------------------------------------------------------------------------
# Corruption of Leaders – apply timer penalty after duration
# ---------------------------------------------------------------------------

async def corruption_expire_timer(duration_minutes: int, total_questions: int) -> None:
    """Apply penalty to groups that did not complete the quiz in time."""
    await asyncio.sleep(duration_minutes * 60)

    penalized_groups: list[dict] = []
    safe_groups: list[dict] = []

    async with AsyncSessionLocal() as db:
        from app.models import GlobalState, Group, GroupQuizState

        # Check if still active
        active_row = (await db.execute(
            select(GlobalState).where(GlobalState.key == "corruption_active")
        )).scalar_one_or_none()
        if not active_row or not active_row.value_bool:
            return

        active_row.value_bool = False

        all_groups = (await db.execute(select(Group))).scalars().all()

        for group in all_groups:
            quiz_state = (await db.execute(
                select(GroupQuizState).where(GroupQuizState.group_id == group.id)
            )).scalar_one_or_none()

            if quiz_state and quiz_state.completed:
                safe_groups.append({
                    "name": group.name,
                    "correct": quiz_state.correct_count,
                    "wrong": quiz_state.wrong_count,
                })
                continue

            # Questions not yet answered: apply -5% sequentially for each
            questions_answered = quiz_state.current_question_index if quiz_state else 0
            remaining = total_questions - questions_answered
            current_pop = group.population
            for _ in range(remaining):
                current_pop = max(10.0, current_pop * 0.95)
            group.population = round(current_pop)

            if quiz_state:
                quiz_state.wrong_count += remaining
                quiz_state.completed = True

            penalized_groups.append({
                "name": group.name,
                "questions_missed": remaining,
            })

        await db.commit()
        logger.info(
            "Corruption timer expired. Penalized: %s. Safe: %s.",
            [g["name"] for g in penalized_groups],
            [g["name"] for g in safe_groups],
        )

    from app.services.ai_news import trigger_event_broadcast
    await trigger_event_broadcast("corruption_result", {
        "penalized_groups": penalized_groups,
        "safe_groups": safe_groups,
    })
