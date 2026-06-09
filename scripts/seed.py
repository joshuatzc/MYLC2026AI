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
    "Church Upgrade",
]

# Reward multipliers per level (index 0 = Level 1, etc.)
MULTIPLIERS = [1.3, 1.7, 3.0]

# ---------------------------------------------------------------------------
# Hint texts  (vague / story-style, shown to all players)
# ---------------------------------------------------------------------------

HINTS: dict[str, list[str]] = {
    "Social Media": [
        "Lights. Camera. 15 seconds of courage.",
        "The stakes are higher. So is the cringe.",
        "No going back. The internet never forgets.",
    ],
    "Worship Team": [
        "Dust off something very, very old.",
        "Your feet know the words better than your mouth does.",
        "Make music — but leave your instruments at home.",
    ],
    "Sound Crew": [
        "Order from chaos. You have the clock against you.",
        "Crack the code hidden in the colours.",
        "Look closely. The answer was always right in front of you.",
    ],
    "Powerpoint Team": [
        "Every keystroke counts. Don't slow the team down.",
        "Someone shuffled the story. Put it back together.",
        "Lost in translation? That's the point.",
    ],
    "Preachers": [
        "No words allowed — but the story must get through.",
        "One object. Five voices. One message.",
        "Whisper it. Act it. Hope something survives.",
    ],
    "Welcome Team / Ushers": [
        "Just act normal. (Good luck with that.)",
        "Normal is harder with things in the way.",
        "Can you find normal when you can't even find the floor?",
    ],
    "Children Ministry": [
        "Stay in the lines — and know exactly which lines.",
        "Same task, different canvas. Do you know your colours?",
        "Follow the story carefully. The fold is hidden in the words.",
    ],
    "Finance": [
        "The shadow knows the shape. Do you?",
        "Same pieces, different problem. Think harder.",
        "This is the final test. There is no mercy.",
    ],
    "Church Upgrade": [
        "A stronger foundation. Space for more souls.",
        "Enlarging our tents. The harvest is plentiful.",
        "A glorious sanctuary. Built to last generations.",
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
#   1. All stations: must complete Level N before Level N+1 (same-station sequential)
#      Level 1s have no prerequisites.
#      Level 2s need Level 1 of that same ministry.
#      Level 3s need Level 2 of that same ministry.
# ---------------------------------------------------------------------------

PREREQS: list[tuple[str, int, str, int]] = [
    # ── Same-station sequential (all 9 stations, levels 2 and 3) ──────────
    ("Social Media",          2, "Social Media",          1),
    ("Social Media",          3, "Social Media",          2),
    ("Worship Team",          2, "Worship Team",          1),
    ("Worship Team",          3, "Worship Team",          2),
    ("Sound Crew",            2, "Sound Crew",            1),
    ("Sound Crew",            3, "Sound Crew",            2),
    ("Powerpoint Team",       2, "Powerpoint Team",       1),
    ("Powerpoint Team",       3, "Powerpoint Team",       2),
    ("Preachers",             2, "Preachers",             1),
    ("Preachers",             3, "Preachers",             2),
    ("Welcome Team / Ushers", 2, "Welcome Team / Ushers", 1),
    ("Welcome Team / Ushers", 3, "Welcome Team / Ushers", 2),
    ("Children Ministry",     2, "Children Ministry",     1),
    ("Children Ministry",     3, "Children Ministry",     2),
    ("Finance",               2, "Finance",               1),
    ("Finance",               3, "Finance",               2),
    ("Church Upgrade",        2, "Church Upgrade",        1),
    ("Church Upgrade",        3, "Church Upgrade",        2),
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
            hint = HINTS.get(name, ["", "", ""])[i]
            if existing_lv:
                # Always sync config fields — game progress rows are untouched
                existing_lv.hint_text = hint
                existing_lv.reward_multiplier = multiplier
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

    # ------------------------------------------------------------------
    # Global state settings
    # ------------------------------------------------------------------
    from app.models import GlobalState
    for key, val_bool, val_int, val_str in [
        ("super_pastor_active", False, None, None),
        ("super_pastor_reward", None, 1000, None),
        ("super_pastor_claimed_by", None, None, None),
        ("super_pastor_claim_count", None, 0, None),
        ("super_pastor_claims", None, None, ""),
        ("infestation_active", False, None, None),
        ("corruption_active", False, None, None),
        ("corruption_duration", None, None, None),
        ("corruption_started_at", None, None, None),
        # Game mode: starts as 'icebreaker' (ice breaker day) and is switched
        # to 'nightgame' by the admin via scripts/icebreaker.sh before night games.
        ("game_mode", None, None, "icebreaker"),
    ]:
        res = await db.execute(select(GlobalState).where(GlobalState.key == key))
        if not res.scalar_one_or_none():
            db.add(GlobalState(key=key, value_bool=val_bool, value_int=val_int, value_str=val_str))

    # ------------------------------------------------------------------
    # Pre-seed Ice Breaker Games
    # ------------------------------------------------------------------
    from app.models import IceBreakerGame
    ice_breaker_games = [
        ("Longest Pinky", "ranking"),
        ("Biggest Forehead", "ranking"),
        ("Chubby Bunny (Mouth Volume)", "points"),
        ("Most 6/7", "ranking"),
        ("Closest Weight", "ranking"),
        ("Furthest Throw", "single"),
        ("Blind Karaoke", "points"),
        ("Chemistry Competition", "points"),
        ("Scratch My Back", "points"),
        ("Taste So Good", "points"),
    ]
    for name, s_type in ice_breaker_games:
        res_game = await db.execute(select(IceBreakerGame).where(IceBreakerGame.name == name))
        existing_game = res_game.scalar_one_or_none()
        if not existing_game:
            db.add(IceBreakerGame(name=name, scoring_type=s_type))
            print(f"  ✚  Pre-seeded Ice Breaker Game: {name} ({s_type})")
        else:
            if existing_game.scoring_type != s_type:
                existing_game.scoring_type = s_type
                print(f"  ⚡ Updated Ice Breaker Game: {name} type to {s_type}")

    await db.commit()

    print()
    print("─" * 50)
    print(f"✅  Stations : {len(STATIONS)} ({len(STATIONS)} stations × 3 levels = {len(STATIONS)*3} levels)")
    print(f"✅  Prereqs  : {prereqs_added} added, {prereqs_skipped} already existed")
    print(f"✅  Groups   : {groups_added} added ({len(GROUPS)} total)")
    print("─" * 50)


async def reseed() -> dict:
    """
    Wipe the entire database and re-run the seed from scratch.
    All game progress, groups, stations, and chat state are reset.
    """
    from app.database import engine
    from app.models import Base

    # Drop every table, then recreate the schema
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    print("💥  All tables dropped and recreated.")

    # Fresh seed
    async with AsyncSessionLocal() as db:
        await seed(db)

    return {"status": "ok", "message": "Database wiped and reseeded successfully."}


async def main() -> None:
    await init_db()
    async with AsyncSessionLocal() as db:
        await seed(db)


if __name__ == "__main__":
    asyncio.run(main())
