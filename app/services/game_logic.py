"""
services/game_logic.py – core game logic.

All functions accept an AsyncSession so they can be called from both
the bot (which creates its own sessions) and FastAPI route handlers.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import (
    Group,
    GroupStationProgress,
    StationLevel,
    StationLevelPrerequisite,
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

async def _completed_level_ids(db: AsyncSession, group_id: int) -> set[int]:
    """Return the set of station_level_ids completed by the group."""
    result = await db.execute(
        select(GroupStationProgress.station_level_id).where(
            GroupStationProgress.group_id == group_id
        )
    )
    return {row[0] for row in result.all()}


async def _all_levels(db: AsyncSession) -> list[StationLevel]:
    """Return all StationLevel rows, eagerly loading station + prerequisites."""
    result = await db.execute(
        select(StationLevel)
        .options(
            selectinload(StationLevel.station),
            selectinload(StationLevel.prerequisites).selectinload(
                StationLevelPrerequisite.required_station_level
            ),
        )
        .order_by(StationLevel.station_id, StationLevel.level_number)
    )
    return list(result.scalars().all())


def _prereq_ids_for_level(level: StationLevel) -> list[int]:
    """
    Return the list of required station_level_ids for this level.

    MVP fallback rule:
        If no explicit prerequisites are defined (prerequisites table is
        empty for this level), automatically treat level_number > 1 as
        requiring level_number - 1 of the same station.
    """
    if level.prerequisites:
        return [p.required_station_level_id for p in level.prerequisites]

    # --- MVP implicit rule ---
    if level.level_number == 1:
        return []

    # Find sibling level (level_number - 1, same station)
    # We rely on the caller having loaded all levels to avoid extra queries.
    # Return a sentinel that callers must resolve; see get_eligible_levels.
    return []  # resolved properly in get_eligible_levels / get_locked_levels


async def _resolve_implicit_prereqs(
    level: StationLevel,
    all_levels: list[StationLevel],
) -> list[int]:
    """
    Return the effective list of prerequisite station_level_ids,
    applying the MVP implicit rule when no explicit prereqs exist.
    """
    if level.prerequisites:
        return [p.required_station_level_id for p in level.prerequisites]

    if level.level_number == 1:
        return []

    # Find previous level of same station
    for lv in all_levels:
        if (
            lv.station_id == level.station_id
            and lv.level_number == level.level_number - 1
        ):
            return [lv.id]

    return []  # no previous level found → treat as unblocked


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def get_eligible_levels(
    db: AsyncSession, group_id: int
) -> list[dict[str, Any]]:
    """
    Return station levels that:
    - are NOT yet completed by this group
    - have ALL prerequisites satisfied
    """
    completed = await _completed_level_ids(db, group_id)
    all_levels = await _all_levels(db)

    eligible = []
    for level in all_levels:
        if level.id in completed:
            continue
        prereqs = await _resolve_implicit_prereqs(level, all_levels)
        if all(pid in completed for pid in prereqs):
            eligible.append(_level_to_dict(level))

    return eligible


async def get_locked_levels(
    db: AsyncSession, group_id: int
) -> list[dict[str, Any]]:
    """
    Return station levels that:
    - are NOT completed
    - have at least one prerequisite NOT yet satisfied
    """
    completed = await _completed_level_ids(db, group_id)
    all_levels = await _all_levels(db)

    locked = []
    for level in all_levels:
        if level.id in completed:
            continue
        prereqs = await _resolve_implicit_prereqs(level, all_levels)
        if prereqs and not all(pid in completed for pid in prereqs):
            locked.append(_level_to_dict(level))

    return locked


async def get_missing_prereqs(
    db: AsyncSession, group_id: int, station_level_id: int
) -> list[dict[str, Any]]:
    """
    Return the prerequisite levels that this group has NOT yet completed
    for the given station_level_id.
    """
    completed = await _completed_level_ids(db, group_id)
    all_levels = await _all_levels(db)

    target = next((lv for lv in all_levels if lv.id == station_level_id), None)
    if target is None:
        return []

    prereq_ids = await _resolve_implicit_prereqs(target, all_levels)
    missing = []
    for pid in prereq_ids:
        if pid not in completed:
            prereq_level = next((lv for lv in all_levels if lv.id == pid), None)
            if prereq_level:
                missing.append(_level_to_dict(prereq_level))

    return missing


async def apply_level_upgrade(
    db: AsyncSession,
    group_id: int,
    station_level_id: int,
    recorded_by: str | None = None,
) -> dict[str, Any]:
    """
    Apply a level upgrade for a group.

    Raises ValueError if:
    - The level is already completed.
    - Prerequisites are not satisfied.

    Returns a dict with upgrade details.
    """
    completed = await _completed_level_ids(db, group_id)
    if station_level_id in completed:
        raise ValueError("Level already completed.")

    all_levels = await _all_levels(db)
    target = next((lv for lv in all_levels if lv.id == station_level_id), None)
    if target is None:
        raise ValueError(f"StationLevel id={station_level_id} not found.")

    prereqs = await _resolve_implicit_prereqs(target, all_levels)
    missing = [pid for pid in prereqs if pid not in completed]
    if missing:
        raise ValueError(
            f"Cannot apply upgrade: missing prerequisites {missing}."
        )

    # Fetch group (fresh)
    group_result = await db.execute(select(Group).where(Group.id == group_id))
    group = group_result.scalar_one_or_none()
    if group is None:
        raise ValueError(f"Group id={group_id} not found.")

    old_population = group.population
    new_population = round(old_population * target.reward_multiplier)

    progress = GroupStationProgress(
        group_id=group_id,
        station_level_id=station_level_id,
        completed_at=datetime.utcnow(),
        population_after=new_population,
        recorded_by=recorded_by,
    )
    db.add(progress)
    group.population = new_population
    await db.commit()

    return {
        "station_name": target.station.name,
        "level_number": target.level_number,
        "old_population": old_population,
        "new_population": new_population,
        "multiplier": target.reward_multiplier,
    }


async def get_group_population(db: AsyncSession, group_id: int) -> int:
    result = await db.execute(select(Group.population).where(Group.id == group_id))
    row = result.scalar_one_or_none()
    if row is None:
        raise ValueError(f"Group id={group_id} not found.")
    return int(row)


async def get_journey(db: AsyncSession, group_id: int) -> list[dict[str, Any]]:
    """
    Return ordered progress entries for the group, joined with station/level data.
    """
    result = await db.execute(
        select(GroupStationProgress)
        .options(
            selectinload(GroupStationProgress.station_level).selectinload(
                StationLevel.station
            )
        )
        .where(GroupStationProgress.group_id == group_id)
        .order_by(GroupStationProgress.completed_at)
    )
    rows = result.scalars().all()
    return [
        {
            "station_name": row.station_level.station.name,
            "level_number": row.station_level.level_number,
            "completed_at": row.completed_at.strftime("%Y-%m-%d %H:%M UTC"),
            "population_after": row.population_after,
        }
        for row in rows
    ]


async def get_leaderboard(db: AsyncSession) -> list[dict[str, Any]]:
    """Return all groups sorted by population descending."""
    result = await db.execute(
        select(Group).order_by(Group.population.desc())
    )
    groups = result.scalars().all()
    return [
        {"rank": i + 1, "name": g.name, "population": g.population}
        for i, g in enumerate(groups)
    ]


async def list_groups(db: AsyncSession) -> list[dict[str, Any]]:
    result = await db.execute(select(Group).order_by(Group.name))
    groups = result.scalars().all()
    return [{"id": g.id, "name": g.name, "population": g.population} for g in groups]


# ---------------------------------------------------------------------------
# Private formatting helper
# ---------------------------------------------------------------------------

def _level_to_dict(level: StationLevel) -> dict[str, Any]:
    return {
        "id": level.id,
        "station_id": level.station_id,
        "station_name": level.station.name if level.station else "?",
        "level_number": level.level_number,
        "hint_text": level.hint_text,
        "reward_multiplier": level.reward_multiplier,
    }
