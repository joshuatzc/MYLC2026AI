"""
scripts/seed.py – populate the database with the 8 stations, their 3 levels,
prerequisite edges, and 14 groups.

Run from the project root:
  python -m scripts.seed

Safe to re-run: skips inserts that already exist.
"""
from __future__ import annotations

import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import AsyncSessionLocal, init_db
from app.models import Group, Station, StationLevel, StationLevelPrerequisite

# ---------------------------------------------------------------------------
# Station definitions  (order = sort_order in DB)
# ---------------------------------------------------------------------------

STATIONS: list[str] = [
    "Social Media",
    "Worship Team",
    "Sound Crew",
    "Powerpoint Team",
    "Preachers",
    "Welcome Team / Ushers",
    "Children Ministry",
    "Finance",
]

# Reward multipliers per level (index 0 = Level 1, etc.)
MULTIPLIERS = [1.2, 1.5, 3.0]

# ---------------------------------------------------------------------------
# Hint texts  (vague / story-style, shown to all players)
# ---------------------------------------------------------------------------

HINTS: dict[str, list[str]] = {
    "Social Media": [
        "Your first post reaches the neighbourhood.",
        "A viral moment puts your church on the map.",
        "Millions watch your Sunday stream.",
    ],
    "Worship Team": [
        "A few musicians gather for the first practice.",
        "The harmonies bring tears to the front row.",
        "Your choir is invited to record a worship album.",
    ],
    "Sound Crew": [
        "Someone figures out how to stop the feedback.",
        "Crystal-clear sound fills every corner.",
        "The PA system becomes the envy of every church in the district.",
    ],
    "Powerpoint Team": [
        "Slides appear on screen — most of the time.",
        "Seamless transitions and on-time lyrics every week.",
        "Custom motion backgrounds and live lyric sync.",
    ],
    "Preachers": [
        "The message is short but sincere.",
        "People stay behind to ask questions.",
        "A sermon series goes regional.",
    ],
    "Welcome Team / Ushers": [
        "Doors are open and smiles are ready.",
        "Every visitor gets a follow-up call.",
        "First-timers never leave without a friend.",
    ],
    "Children Ministry": [
        "A small Sunday school class meets in the foyer.",
        "Parents stop worrying mid-sermon.",
        "Kids bring their friends — and their parents follow.",
    ],
    "Finance": [
        "Tithes are counted, offerings tracked.",
        "A transparent budget builds trust.",
        "Generous giving unlocks a building project.",
    ],
}

# ---------------------------------------------------------------------------
# Groups
# ---------------------------------------------------------------------------

GROUPS: list[str] = [f"Group {i}" for i in range(1, 15)]  # Group 1 … Group 14

# ---------------------------------------------------------------------------
# Prerequisite edges
#
# Format: (station_that_needs_prereq, level, required_station, required_level)
#
# Rules implemented:
#   1. All stations: must complete Level N before Level N+1  (same-station sequential)
#   2. Worship Team L2 → Sound Crew L1 + Powerpoint L1
#      Worship Team L3 → Sound Crew L2 + Powerpoint L2
#   3. Welcome Team L1 → Social Media L1
#      Welcome Team L2 → Social Media L2   (+ Welcome L1 from rule 1)
#      Welcome Team L3 → Social Media L3   (+ Welcome L2 from rule 1)
#   4. Children Ministry L2 → Welcome Team L1  (+ Children L1 from rule 1)
#      Children Ministry L3 → Welcome Team L2  (+ Children L2 from rule 1)
#   5. Preachers L1 → Powerpoint L1
#      Preachers L2 → Finance L1 + Powerpoint L2  (+ Preachers L1 from rule 1)
#      Preachers L3 → Finance L2 + Powerpoint L3  (+ Preachers L2 from rule 1)
#   Social Media, Finance, Sound Crew, Powerpoint: only sequential within-station
# ---------------------------------------------------------------------------

PREREQS: list[tuple[str, int, str, int]] = [
    # ── Same-station sequential (all 8 stations, levels 2 and 3) ──────────
    ("Social Media",        2, "Social Media",        1),
    ("Social Media",        3, "Social Media",        2),
    ("Worship Team",        2, "Worship Team",        1),
    ("Worship Team",        3, "Worship Team",        2),
    ("Sound Crew",          2, "Sound Crew",          1),
    ("Sound Crew",          3, "Sound Crew",          2),
    ("Powerpoint Team",     2, "Powerpoint Team",     1),
    ("Powerpoint Team",     3, "Powerpoint Team",     2),
    ("Preachers",           2, "Preachers",           1),
    ("Preachers",           3, "Preachers",           2),
    ("Welcome Team / Ushers", 2, "Welcome Team / Ushers", 1),
    ("Welcome Team / Ushers", 3, "Welcome Team / Ushers", 2),
    ("Children Ministry",   2, "Children Ministry",   1),
    ("Children Ministry",   3, "Children Ministry",   2),
    ("Finance",             2, "Finance",             1),
    ("Finance",             3, "Finance",             2),

    # ── Worship Team: needs previous-level Sound Crew + Powerpoint ────────
    # L1 has no external prereq (needs "L0" which doesn't exist)
    ("Worship Team",        2, "Sound Crew",          1),
    ("Worship Team",        2, "Powerpoint Team",     1),
    ("Worship Team",        3, "Sound Crew",          2),
    ("Worship Team",        3, "Powerpoint Team",     2),

    # ── Welcome Team: needs same-level Social Media ───────────────────────
    ("Welcome Team / Ushers", 1, "Social Media",      1),
    ("Welcome Team / Ushers", 2, "Social Media",      2),
    ("Welcome Team / Ushers", 3, "Social Media",      3),

    # ── Children Ministry: needs previous-level Welcome Team ──────────────
    # L1 has no external prereq (needs "L0" Welcome which doesn't exist)
    ("Children Ministry",   2, "Welcome Team / Ushers", 1),
    ("Children Ministry",   3, "Welcome Team / Ushers", 2),

    # ── Preachers: needs same-level Powerpoint + previous-level Finance ───
    ("Preachers",           1, "Powerpoint Team",     1),          # Finance L0 = none
    ("Preachers",           2, "Powerpoint Team",     2),
    ("Preachers",           2, "Finance",             1),
    ("Preachers",           3, "Powerpoint Team",     3),
    ("Preachers",           3, "Finance",             2),
]


# ---------------------------------------------------------------------------
# Seed logic
# ---------------------------------------------------------------------------

async def seed(db: AsyncSession) -> None:
    # ------------------------------------------------------------------
    # Stations + Levels
    # ------------------------------------------------------------------
    station_obj: dict[str, Station] = {}
    level_obj: dict[tuple[str, int], StationLevel] = {}

    for sort_idx, name in enumerate(STATIONS):
        # Check if already exists
        result = await db.execute(select(Station).where(Station.name == name))
        existing = result.scalar_one_or_none()
        if existing:
            station_obj[name] = existing
            print(f"  ↩  Station already exists: {name}")
        else:
            s = Station(name=name, sort_order=sort_idx)
            db.add(s)
            await db.flush()
            station_obj[name] = s
            print(f"  ✚  Created station: {name}")

        # Levels
        for i, multiplier in enumerate(MULTIPLIERS):
            level_num = i + 1
            station_id = station_obj[name].id
            result2 = await db.execute(
                select(StationLevel).where(
                    StationLevel.station_id == station_id,
                    StationLevel.level_number == level_num,
                )
            )
            existing_lv = result2.scalar_one_or_none()
            if existing_lv:
                level_obj[(name, level_num)] = existing_lv
            else:
                hint = HINTS.get(name, ["", "", ""])[i]
                lv = StationLevel(
                    station_id=station_id,
                    level_number=level_num,
                    hint_text=hint,
                    reward_multiplier=multiplier,
                )
                db.add(lv)
                await db.flush()
                level_obj[(name, level_num)] = lv

    # ------------------------------------------------------------------
    # Prerequisite edges
    # ------------------------------------------------------------------
    prereqs_added = 0
    prereqs_skipped = 0
    for (sn, ln, rsn, rln) in PREREQS:
        dependent = level_obj.get((sn, ln))
        required = level_obj.get((rsn, rln))
        if not dependent or not required:
            print(f"  ⚠  Could not resolve prereq: ({sn} L{ln}) → ({rsn} L{rln})")
            continue
        result3 = await db.execute(
            select(StationLevelPrerequisite).where(
                StationLevelPrerequisite.station_level_id == dependent.id,
                StationLevelPrerequisite.required_station_level_id == required.id,
            )
        )
        if result3.scalar_one_or_none():
            prereqs_skipped += 1
            continue
        db.add(
            StationLevelPrerequisite(
                station_level_id=dependent.id,
                required_station_level_id=required.id,
            )
        )
        prereqs_added += 1

    # ------------------------------------------------------------------
    # Groups
    # ------------------------------------------------------------------
    groups_added = 0
    for gname in GROUPS:
        result4 = await db.execute(select(Group).where(Group.name == gname))
        if result4.scalar_one_or_none():
            continue
        db.add(Group(name=gname, population=settings.STARTING_POPULATION))
        groups_added += 1

    await db.commit()

    print()
    print("─" * 50)
    print(f"✅  Stations : {len(STATIONS)} (8 stations × 3 levels = {len(STATIONS)*3} levels)")
    print(f"✅  Prereqs  : {prereqs_added} added, {prereqs_skipped} already existed")
    print(f"✅  Groups   : {groups_added} added ({len(GROUPS)} total)")
    print("─" * 50)


async def main() -> None:
    await init_db()
    async with AsyncSessionLocal() as db:
        await seed(db)


if __name__ == "__main__":
    asyncio.run(main())
