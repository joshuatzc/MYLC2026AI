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
    StealRecord,
)


# ---------------------------------------------------------------------------
# Church Upgrade Configuration & Helpers
# ---------------------------------------------------------------------------

CHURCH_TIERS = {
    0: {"name": "Home Church", "max_occupancy": 30, "bonus": 0.0, "min_population": 0, "steal_amount": 0},
    1: {"name": "Family Church", "max_occupancy": 500, "bonus": 0.10, "min_population": 15, "steal_amount": 15},
    2: {"name": "Mega Church", "max_occupancy": 10000, "bonus": 0.20, "min_population": 300, "steal_amount": 150},
    3: {"name": "Giga Church", "max_occupancy": 10000000, "bonus": 0.30, "min_population": 10000, "steal_amount": 2000},
}


def get_max_occupancy(level: int) -> int:
    return CHURCH_TIERS.get(level, CHURCH_TIERS[3])["max_occupancy"]


def get_church_bonus(level: int) -> float:
    return CHURCH_TIERS.get(level, CHURCH_TIERS[3])["bonus"]


def get_church_tier_name(level: int) -> str:
    return CHURCH_TIERS.get(level, CHURCH_TIERS[3])["name"]


def get_church_min_pop(level: int) -> int:
    return CHURCH_TIERS.get(level, CHURCH_TIERS[3])["min_population"]


def get_church_steal_amount(level: int) -> int:
    return CHURCH_TIERS.get(level, CHURCH_TIERS[3])["steal_amount"]


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
    steal_target_group_id: int | None = None,
) -> dict[str, Any]:
    """
    Apply a level upgrade for a group.

    Raises ValueError if:
    - The level is already completed.
    - Prerequisites are not satisfied.
    - Church Upgrade: group's current population is below minimum requirements.
    - Church Upgrade: selected theft target's level is not exactly equal to stealer's old level.

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

    if target.station.name == "Church Upgrade":
        # Upgrades the group's church level to the level number of this physical station upgrade
        new_church_level = target.level_number
        if group.church_level >= new_church_level:
            raise ValueError(f"Church already at level {group.church_level} ({get_church_tier_name(group.church_level)}).")
        
        # Sequentially upgrade
        if new_church_level != group.church_level + 1:
            raise ValueError(f"Cannot upgrade directly to Level {new_church_level}. You must upgrade to Level {group.church_level + 1} first.")

        # Prerequisite minimum population check
        min_pop = get_church_min_pop(new_church_level)
        if old_population < min_pop:
            raise ValueError(
                f"❌ Your group needs at least {min_pop} congregation members to upgrade to a {get_church_tier_name(new_church_level)}! "
                f"Current population: {int(old_population):,}."
            )

        stolen_amount = 0
        actual_gained = 0
        target_name = None
        target_old_pop = 0
        target_new_pop = 0
        theft_applied = False

        # Apply absolute steal with safety net at upgrade time
        if steal_target_group_id is not None:
            target_res = await db.execute(select(Group).where(Group.id == steal_target_group_id))
            target_group = target_res.scalar_one_or_none()
            if target_group is None:
                raise ValueError("Selected theft target group not found.")

            # Victim must be at the stealer's CURRENT level (which is strictly 1 level below the new upgraded level!)
            if target_group.church_level != group.church_level:
                raise ValueError(
                    f"You can only steal from groups currently at your tier ({get_church_tier_name(group.church_level)})! "
                    f"Target is at {get_church_tier_name(target_group.church_level)}."
                )

            # Absolute steal amount for this upgrade level
            absolute_steal = get_church_steal_amount(new_church_level)

            # Victim cannot go below 10 members safety net
            max_stolen = max(0.0, target_group.population - 10.0)
            stolen_amount = round(min(float(absolute_steal), max_stolen))

            if stolen_amount > 0:
                target_name = target_group.name
                target_old_pop = target_group.population
                
                # Deduct from target
                target_group.population = max(10.0, target_group.population - stolen_amount)
                target_new_pop = target_group.population

                # Stealer temporary population (will be capped below by new occupancy limit)
                # Group current population remains old_population for calculation of actual gained members.
                old_population_post_steal = old_population + stolen_amount
                
                record = StealRecord(
                    stealer_group_id=group_id,
                    target_group_id=steal_target_group_id,
                    amount=stolen_amount,
                    created_at=datetime.utcnow(),
                    recorded_by=recorded_by,
                )
                db.add(record)
                theft_applied = True
            else:
                old_population_post_steal = old_population
        else:
            old_population_post_steal = old_population

        group.church_level = new_church_level

        # Max occupancy changes, cap their current population
        new_max_occ = get_max_occupancy(group.church_level)
        new_population = min(new_max_occ, old_population_post_steal)
        
        if theft_applied:
            # Actual gained members after occupancy capping is applied
            actual_gained = max(0, new_population - old_population)

        group.population = new_population

        progress = GroupStationProgress(
            group_id=group_id,
            station_level_id=station_level_id,
            completed_at=datetime.utcnow(),
            population_after=new_population,
            recorded_by=recorded_by,
        )
        db.add(progress)
        await db.commit()

        return {
            "station_name": target.station.name,
            "level_number": target.level_number,
            "old_population": old_population,
            "new_population": new_population,
            "multiplier": 1.0,
            "church_upgraded": True,
            "new_church_level": group.church_level,
            "tier_name": get_church_tier_name(group.church_level),
            "max_occupancy": new_max_occ,
            "bonus_pct": round(get_church_bonus(group.church_level) * 100),
            "theft_applied": theft_applied,
            "stolen_amount": stolen_amount,
            "actual_gained": actual_gained,
            "target_name": target_name,
            "target_old_pop": target_old_pop,
            "target_new_pop": target_new_pop,
            "min_required": min_pop,
            "capped": theft_applied and (old_population_post_steal > new_max_occ),
        }
    else:
        # Standard station upgrade with earning bonus and max occupancy cap
        earned = old_population * (target.reward_multiplier - 1.0)
        bonus_pct = get_church_bonus(group.church_level)
        total_earned = earned * (1.0 + bonus_pct)
        new_population = round(old_population + total_earned)

        # Cap based on max occupancy of current church level
        max_occ = get_max_occupancy(group.church_level)
        capped = new_population > max_occ
        new_population = min(max_occ, new_population)

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
            "church_upgraded": False,
            "earned_bonus": total_earned - earned,
            "capped": capped,
        }


async def get_group_population(db: AsyncSession, group_id: int) -> int:
    result = await db.execute(select(Group.population).where(Group.id == group_id))
    row = result.scalar_one_or_none()
    if row is None:
        raise ValueError(f"Group id={group_id} not found.")
    return int(row)


async def get_eligible_steal_targets_for_upgrade(
    db: AsyncSession,
    group_id: int,
    current_church_level: int,
) -> list[dict[str, Any]]:
    """Return all other groups whose church level is exactly equal to current_church_level."""
    res = await db.execute(
        select(Group)
        .where(Group.id != group_id)
        .where(Group.church_level == current_church_level)
        .order_by(Group.name)
    )
    targets = res.scalars().all()
    return [
        {
            "id": t.id,
            "name": t.name,
            "population": int(t.population),
            "church_level": t.church_level,
            "tier_name": get_church_tier_name(t.church_level),
        }
        for t in targets
    ]


async def get_journey(db: AsyncSession, group_id: int) -> list[dict[str, Any]]:
    """
    Return ordered progress entries and steal entries for the group, compiled into a single timeline.
    """
    # Fetch station progress
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

    # Fetch steals committed
    stealer_res = await db.execute(
        select(StealRecord)
        .options(selectinload(StealRecord.target_group))
        .where(StealRecord.stealer_group_id == group_id)
    )
    steals_committed = stealer_res.scalars().all()

    # Fetch steals suffered
    target_res = await db.execute(
        select(StealRecord)
        .options(selectinload(StealRecord.stealer_group))
        .where(StealRecord.target_group_id == group_id)
    )
    steals_suffered = target_res.scalars().all()

    events = []

    # Add station completions
    for row in rows:
        events.append({
            "timestamp": row.completed_at,
            "type": "upgrade",
            "completed_at": row.completed_at.strftime("%Y-%m-%d %H:%M UTC"),
            "station_name": row.station_level.station.name,
            "level_number": row.station_level.level_number,
            "population_after": row.population_after,
        })

    # Add steals committed
    for row in steals_committed:
        events.append({
            "timestamp": row.created_at,
            "type": "theft_committed",
            "completed_at": row.created_at.strftime("%Y-%m-%d %H:%M UTC"),
            "target_name": row.target_group.name,
            "amount": row.amount,
        })

    # Add steals suffered
    for row in steals_suffered:
        events.append({
            "timestamp": row.created_at,
            "type": "theft_suffered",
            "completed_at": row.created_at.strftime("%Y-%m-%d %H:%M UTC"),
            "stealer_name": row.stealer_group.name,
            "amount": row.amount,
        })

    # Sort chronologically
    events.sort(key=lambda e: e["timestamp"])

    # Remove the timestamp object before returning
    for e in events:
        e.pop("timestamp", None)

    return events


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
