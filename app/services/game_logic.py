"""
services/game_logic.py – core game logic.

All functions accept an AsyncSession so they can be called from both
the bot (which creates its own sessions) and FastAPI route handlers.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import (
    Group,
    GroupStationProgress,
    StationLevel,
    StationLevelPrerequisite,
    StealRecord,
    GroupHintPurchase,
    Station,
)


# ---------------------------------------------------------------------------
# Church Upgrade Configuration & Helpers
# ---------------------------------------------------------------------------

CHURCH_TIERS = {
    0: {"name": "Home Church", "max_occupancy": 50, "bonus": 0.0, "min_population": 0, "steal_amount": 0},
    1: {"name": "Family Church", "max_occupancy": 500, "bonus": 0.10, "min_population": 14, "steal_amount": 0},
    2: {"name": "Mega Church", "max_occupancy": 10000, "bonus": 0.20, "min_population": 150, "steal_amount": 0},
    3: {"name": "Giga Church", "max_occupancy": 10000000, "bonus": 0.30, "min_population": 1000, "steal_amount": 0},
}

CHURCH_HINTS = {
    1: {
        1: {
            "text": "The puzzle can be solved from 4 of these 7 pieces.",
            "photo": "l1_h1.png"
        },
        2: {
            "text": "These are the only 4 pieces you need to solve the puzzle.",
            "photo": "l1_h2.png"
        },
        3: {
            "text": "Here is how one of the pieces fits into the puzzle.",
            "photo": "l1_h3.png"
        }
    },
    2: {
        1: {
            "text": "This shows how the first 2 pieces fit in the puzzle.",
            "photo": "l2_h1.png"
        },
        2: {
            "text": "This shows how the first 4 pieces fit in the puzzle.",
            "photo": "l2_h2.png"
        },
        3: {
            "text": "This shows how the first 5 pieces fit in the puzzle.",
            "photo": "l2_h3.png"
        }
    },
    3: {
        1: {
            "text": "This shows how the first piece fits in the puzzle.",
            "photo": "l3_h1.png"
        },
        2: {
            "text": "This shows how the first 4 pieces fit in the puzzle.",
            "photo": "l3_h2.png"
        },
        3: {
            "text": "This shows how the first 9 pieces fit in the puzzle.",
            "photo": "l3_h3.png"
        }
    }
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
    - for Church Upgrade, also meet the population requirement
    """
    completed = await _completed_level_ids(db, group_id)
    all_levels = await _all_levels(db)

    # Fetch group to check population (needed for Church Upgrade population requirements)
    group_result = await db.execute(select(Group).where(Group.id == group_id))
    group = group_result.scalar_one_or_none()
    group_pop = group.population if group else 0.0

    eligible = []
    for level in all_levels:
        if level.id in completed:
            continue
        prereqs = await _resolve_implicit_prereqs(level, all_levels)
        if all(pid in completed for pid in prereqs):
            # Enforce population check on Church Upgrade eligibility!
            if level.station.name == "Church Upgrade":
                min_pop = get_church_min_pop(level.level_number)
                if group_pop < min_pop:
                    continue
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

        if steal_target_group_id is not None:
            target_res = await db.execute(select(Group).where(Group.id == steal_target_group_id))
            target_group = target_res.scalar_one_or_none()
            if target_group is None:
                raise ValueError("Selected theft target group not found.")

            # Calculate 10% of target group's population
            calculated_steal = target_group.population * 0.10

            # Victim cannot go below 10 members safety net
            max_stolen = max(0.0, target_group.population - 10.0)
            stolen_amount = round(min(float(calculated_steal), max_stolen))

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

        # Trigger eligibility check in case they are now eligible for the NEXT level
        trigger_eligibility_check(group_id, old_population, new_population)

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
            "bonus_pct": round((await get_group_church_bonus(db, group_id)) * 100),
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
        bonus_pct = await get_group_church_bonus(db, group_id)
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

        # Trigger eligibility check
        trigger_eligibility_check(group_id, old_population, new_population)

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
    """Return all other groups regardless of their church level."""
    res = await db.execute(
        select(Group)
        .where(Group.id != group_id)
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


async def get_purchased_hint_numbers(
    db: AsyncSession, group_id: int, station_level_id: int
) -> list[int]:
    """Return the list of hint numbers (1, 2, or 3) purchased by this group for this level."""
    res = await db.execute(
        select(GroupHintPurchase.hint_number)
        .where(GroupHintPurchase.group_id == group_id)
        .where(GroupHintPurchase.station_level_id == station_level_id)
    )
    return [row[0] for row in res.all()]


async def buy_church_hint(
    db: AsyncSession, group_id: int, station_level_id: int, hint_number: int
) -> dict[str, Any]:
    """
    Attempt to purchase a hint for a church upgrade level.
    Base hint costs are Hint 1: 15%, Hint 2: 20%, Hint 3: 25%.
    The cost drops by 1% for each group that completed the upgrade before you.
    Enforces the 10-member safety net.
    """
    if hint_number not in (1, 2, 3):
        raise ValueError("Invalid hint number. Must be 1, 2, or 3.")

    # 1. Fetch group
    group_res = await db.execute(select(Group).where(Group.id == group_id))
    group = group_res.scalar_one_or_none()
    if group is None:
        raise ValueError("Group not found.")

    # 2. Fetch level to verify it's a Church Upgrade
    level_res = await db.execute(
        select(StationLevel)
        .options(selectinload(StationLevel.station))
        .where(StationLevel.id == station_level_id)
    )
    level = level_res.scalar_one_or_none()
    if level is None or level.station.name != "Church Upgrade":
        raise ValueError("Hints can only be purchased for Church Upgrades.")

    # 3. Check if already purchased
    existing_res = await db.execute(
        select(GroupHintPurchase)
        .where(GroupHintPurchase.group_id == group_id)
        .where(GroupHintPurchase.station_level_id == station_level_id)
        .where(GroupHintPurchase.hint_number == hint_number)
    )
    if existing_res.scalar_one_or_none():
        raise ValueError("This hint has already been purchased.")

    # 4. Fetch N (number of other groups who completed this level before us)
    completions_res = await db.execute(
        select(func.count(GroupStationProgress.id))
        .where(GroupStationProgress.station_level_id == station_level_id)
    )
    N = completions_res.scalar() or 0

    # 5. Calculate discounted cost percentage
    base_percentage = hint_number * 0.05  # 5%, 10%, 15%
    cost_percentage = max(0.03, base_percentage - (N * 0.01))
    cost = round(group.population * cost_percentage)

    # 6. Validate safety net
    new_population = group.population - cost
    if new_population < 10:
        raise ValueError(
            "❌ *Purchase Blocked:* Your congregation cannot drop below the 10-member safety net. You do not have enough members to buy this hint."
        )

    # 7. Record purchase and deduct population
    purchase = GroupHintPurchase(
        group_id=group_id,
        station_level_id=station_level_id,
        hint_number=hint_number,
    )
    db.add(purchase)
    group.population = new_population

    await db.commit()

    # Get hint text and photo
    level_num = level.level_number
    hint_data = CHURCH_HINTS.get(level_num, {}).get(hint_number, {"text": "No hint available.", "photo": None})
    hint_text = hint_data.get("text", "No hint available.")
    hint_photo = hint_data.get("photo")

    return {
        "cost": cost,
        "new_population": new_population,
        "hint_text": hint_text,
        "hint_photo": hint_photo,
    }


async def get_group_church_bonus(db: AsyncSession, group_id: int) -> float:
    """
    Calculate the dynamic cumulative church earning bonus for a group.
    It sums the dynamic rank-based boost earned for each completed Church Upgrade level.
    Boost = max(1, 15 - N)% where N is the number of groups that upgraded before you (N=13 -> 1%).
    """
    # Find the station named "Church Upgrade"
    station_res = await db.execute(select(Station).where(Station.name == "Church Upgrade"))
    church_station = station_res.scalar_one_or_none()
    if not church_station:
        return 0.0

    # Find all levels of this station
    levels_res = await db.execute(
        select(StationLevel.id).where(StationLevel.station_id == church_station.id)
    )
    church_level_ids = [row[0] for row in levels_res.all()]

    if not church_level_ids:
        return 0.0

    # Fetch group's completions for these levels
    progress_res = await db.execute(
        select(GroupStationProgress)
        .where(GroupStationProgress.group_id == group_id)
        .where(GroupStationProgress.station_level_id.in_(church_level_ids))
    )
    completions = progress_res.scalars().all()

    total_boost = 0
    for comp in completions:
        # Find all completions for this specific level ordered by completed_at ascending
        all_comp_res = await db.execute(
            select(GroupStationProgress)
            .where(GroupStationProgress.station_level_id == comp.station_level_id)
            .order_by(GroupStationProgress.completed_at.asc())
        )
        all_comps = all_comp_res.scalars().all()
        
        # Find index of our group in the ordered completion list
        rank_idx = 0
        for idx, c in enumerate(all_comps):
            if c.group_id == group_id:
                rank_idx = idx
                break
        
        # Calculate rank-based boost: max(1, 15 - rank_idx)
        # Special case: 14th group (index 13) gets exactly 1%
        boost = 15 - rank_idx
        if rank_idx == 13:
            boost = 1
        elif boost < 1:
            boost = 1
            
        total_boost += boost

    return total_boost / 100.0


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


async def check_and_broadcast_upgrade_eligibility(
    db: AsyncSession,
    group_id: int,
    old_pop: float,
    new_pop: float,
) -> None:
    """
    Check if a group's population has crossed the threshold for their next church level upgrade.
    If so, broadcast a Telegram notification to that group only.
    """
    # 1. Fetch group
    group_res = await db.execute(select(Group).where(Group.id == group_id))
    group = group_res.scalar_one_or_none()
    if not group:
        return

    # 2. Get next church upgrade level
    next_level = group.church_level + 1
    if next_level > 3:
        return  # Already at max church level

    # 3. Check threshold
    threshold = get_church_min_pop(next_level)
    if old_pop < threshold <= new_pop:
        # We crossed the threshold!
        # 4. Fetch all chat sessions for this group
        from app.models import ChatState
        cs_res = await db.execute(
            select(ChatState.chat_id).where(ChatState.group_id == group_id)
        )
        chat_ids = cs_res.scalars().all()
        if not chat_ids:
            return

        # 5. Broadcast message
        from aiogram import Bot
        from aiogram.client.default import DefaultBotProperties
        from aiogram.enums import ParseMode
        from app.config import settings
        import asyncio

        tier_name = get_church_tier_name(next_level)
        message_text = (
            f"🔔 <b>CHURCH ELIGIBLE FOR UPGRADE!</b> ⛪\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"🎉 Great news, <b>{group.name}</b>!\n"
            f"Your congregation has reached <b>{int(new_pop):,}</b> members, passing the threshold of <b>{threshold:,}</b>!\n\n"
            f"You are now eligible to upgrade to a <b>{tier_name}</b> (Level {next_level})! 🏛️\n"
            f"Head over to the physical station to upgrade! Hints are available in the <b>🔑 Admin Section</b>."
        )

        bot = Bot(
            token=settings.TELEGRAM_BOT_TOKEN,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
        try:
            for chat_id in chat_ids:
                try:
                    await bot.send_message(chat_id=chat_id, text=message_text)
                    await asyncio.sleep(0.08)
                except Exception as e:
                    import logging
                    logging.getLogger(__name__).warning("Failed to send eligibility broadcast to %s: %s", chat_id, e)
        finally:
            await bot.session.close()


def trigger_eligibility_check(group_id: int, old_pop: float, new_pop: float) -> None:
    """Spawns check_and_broadcast_upgrade_eligibility in a background task."""
    import asyncio
    asyncio.create_task(_check_and_broadcast_upgrade_eligibility_async(group_id, old_pop, new_pop))


async def _check_and_broadcast_upgrade_eligibility_async(group_id: int, old_pop: float, new_pop: float) -> None:
    from app.database import AsyncSessionLocal
    async with AsyncSessionLocal() as db:
        await check_and_broadcast_upgrade_eligibility(db, group_id, old_pop, new_pop)

