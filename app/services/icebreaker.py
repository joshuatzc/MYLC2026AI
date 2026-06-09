"""
services/icebreaker.py – Ice Breaker pre-game logic.

Flow:
  1. Admin registers mini-games via register_game().
  2. Admin enters ranked results per game via record_results().
     - Each call replaces any existing results for that game (overwrite semantics).
  3. get_standings() computes total points across all games and derives
     the starting-population bonus for each group (rank 1 → +13, rank 14 → +0).
  4. apply_bonuses() writes the population bonus to each group and flips
     game_mode → "nightgame".
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select, func, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import (
    Group,
    GlobalState,
    IceBreakerGame,
    IceBreakerResult,
    PLACEMENT_POINTS,
    SCORING_RANKING,
    SCORING_SINGLE,
    SCORING_POINTS,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_RANKED_PLACES = 5  # placements 1-5 earn points; 6th+ earn nothing
NUM_GROUPS = 14        # total groups in the game


def _bonus_for_rank(rank: int) -> int:
    """Starting-population bonus for a final overall rank (1-indexed).

    Rank 1  → +13 pop
    Rank 2  → +12 pop
    ...
    Rank 13 → +1 pop
    Rank 14 → +0 pop
    """
    return max(0, NUM_GROUPS - rank)


# ---------------------------------------------------------------------------
# Game-mode helpers
# ---------------------------------------------------------------------------

async def get_game_mode(db: AsyncSession) -> str:
    """Return the current game mode ('icebreaker' or 'nightgame')."""
    res = await db.execute(
        select(GlobalState).where(GlobalState.key == "game_mode")
    )
    row = res.scalar_one_or_none()
    return row.value_str if row else "icebreaker"


async def set_game_mode(db: AsyncSession, mode: str) -> None:
    """Set the game mode key in GlobalState."""
    res = await db.execute(
        select(GlobalState).where(GlobalState.key == "game_mode")
    )
    row = res.scalar_one_or_none()
    if row is None:
        db.add(GlobalState(key="game_mode", value_str=mode))
    else:
        row.value_str = mode
    await db.commit()


# ---------------------------------------------------------------------------
# Game registration
# ---------------------------------------------------------------------------

async def register_game(
    db: AsyncSession,
    name: str,
    scoring_type: str = "ranking",
) -> dict[str, Any]:
    """Create a new ice breaker game entry.

    Raises ValueError if a game with that name already exists.
    """
    existing = (
        await db.execute(
            select(IceBreakerGame).where(IceBreakerGame.name == name)
        )
    ).scalar_one_or_none()

    if existing:
        raise ValueError(f"A game named '{name}' already exists (id={existing.id}).")

    game = IceBreakerGame(name=name, scoring_type=scoring_type)
    db.add(game)
    await db.commit()
    return {
        "id": game.id,
        "name": game.name,
        "scoring_type": game.scoring_type,
        "has_single_winner": game.scoring_type == "single",
    }


# ---------------------------------------------------------------------------
# Recording results
# ---------------------------------------------------------------------------

async def record_results(
    db: AsyncSession,
    game_id: int,
    placements: list[int] | None = None,
    scores: dict[int, int] | None = None,
) -> dict[str, Any]:
    """Record or overwrite results for a game.

    For ranking/single games, ``placements`` is an ordered list of group_ids.
    For points games, ``scores`` is a dict of group_id -> points.

    Raises ValueError if validation fails.
    """
    # Load game
    game_res = await db.execute(
        select(IceBreakerGame)
        .options(selectinload(IceBreakerGame.results))
        .where(IceBreakerGame.id == game_id)
    )
    game = game_res.scalar_one_or_none()
    if game is None:
        raise ValueError(f"Game id={game_id} not found.")

    # Validate group ids
    valid_groups_res = await db.execute(select(Group.id, Group.name))
    valid_groups = {row[0]: row[1] for row in valid_groups_res.all()}

    # Wipe existing results for this game (overwrite semantics)
    await db.execute(
        delete(IceBreakerResult).where(IceBreakerResult.game_id == game_id)
    )

    recorded = []

    if game.scoring_type in (SCORING_RANKING, SCORING_SINGLE):
        if not placements:
            raise ValueError("Placements list cannot be empty for ranking/single games.")
        if len(placements) != len(set(placements)):
            raise ValueError("Duplicate groups found in placements list.")
        for gid in placements:
            if gid not in valid_groups:
                raise ValueError(f"Group id={gid} not found.")

        if game.scoring_type == SCORING_SINGLE and len(placements) > 1:
            raise ValueError("This is a single-winner game. Only 1 group can be placed.")

        for idx, group_id in enumerate(placements):
            placement = idx + 1  # 1-indexed
            if game.scoring_type == SCORING_SINGLE:
                pts = 500
            else:
                if placement > MAX_RANKED_PLACES:
                    break  # 6th+ earn nothing — don't store them
                pts = PLACEMENT_POINTS.get(placement, 0)

            result = IceBreakerResult(
                game_id=game_id,
                group_id=group_id,
                placement=placement,
                points=pts,
            )
            db.add(result)
            recorded.append({
                "placement": placement,
                "group_id": group_id,
                "group_name": valid_groups[group_id],
                "points": pts,
            })

    elif game.scoring_type == SCORING_POINTS:
        if not scores:
            raise ValueError("Scores dict cannot be empty for direct-points games.")
        for gid, pts in scores.items():
            if gid not in valid_groups:
                raise ValueError(f"Group id={gid} not found.")
            if pts < 0:
                raise ValueError("Points cannot be negative.")
            # Record non-zero points
            if pts > 0:
                result = IceBreakerResult(
                    game_id=game_id,
                    group_id=gid,
                    placement=None,
                    points=pts,
                )
                db.add(result)
                recorded.append({
                    "placement": None,
                    "group_id": gid,
                    "group_name": valid_groups[gid],
                    "points": pts,
                })
    else:
        raise ValueError(f"Unsupported scoring type: {game.scoring_type}")

    await db.commit()
    return {"game_id": game_id, "game_name": game.name, "results": recorded}


# ---------------------------------------------------------------------------
# Standings
# ---------------------------------------------------------------------------

async def get_standings(db: AsyncSession) -> list[dict[str, Any]]:
    """Return the current overall standings across all games.

    Returns a list of dicts, sorted by total_points descending, with ties
    broken alphabetically by group name.  Each dict includes the
    starting-pop bonus that would be applied if finalized now.
    """
    # All groups
    groups_res = await db.execute(select(Group).order_by(Group.name))
    all_groups = {g.id: g.name for g in groups_res.scalars().all()}

    # Sum points per group
    points_res = await db.execute(
        select(
            IceBreakerResult.group_id,
            func.sum(IceBreakerResult.points).label("total_points"),
        ).group_by(IceBreakerResult.group_id)
    )
    points_map: dict[int, int] = {row[0]: row[1] for row in points_res.all()}

    # Build standings list (include groups with 0 points)
    entries = [
        {"group_id": gid, "group_name": gname, "total_points": points_map.get(gid, 0)}
        for gid, gname in all_groups.items()
    ]

    # Sort: highest points first, alphabetical on ties
    entries.sort(key=lambda e: (-e["total_points"], e["group_name"]))

    # Assign ranks + bonuses
    for rank_idx, entry in enumerate(entries):
        rank = rank_idx + 1
        entry["rank"] = rank
        entry["starting_pop_bonus"] = _bonus_for_rank(rank)
        entry["final_starting_pop"] = 10 + entry["starting_pop_bonus"]

    return entries


# ---------------------------------------------------------------------------
# Apply bonuses & finalize
# ---------------------------------------------------------------------------

async def apply_bonuses(db: AsyncSession) -> list[dict[str, Any]]:
    """Apply ice breaker bonuses to each group's population and switch to nightgame mode.

    Idempotent if called again — it sets population to base + bonus each time
    (so calling it twice won't double-apply).

    Returns the standings list with applied values.
    """
    standings = await get_standings(db)

    for entry in standings:
        group_res = await db.execute(
            select(Group).where(Group.id == entry["group_id"])
        )
        group = group_res.scalar_one_or_none()
        if group is None:
            continue
        group.population = float(entry["final_starting_pop"])

    # Switch mode
    mode_res = await db.execute(
        select(GlobalState).where(GlobalState.key == "game_mode")
    )
    mode_row = mode_res.scalar_one_or_none()
    if mode_row is None:
        db.add(GlobalState(key="game_mode", value_str="nightgame"))
    else:
        mode_row.value_str = "nightgame"

    await db.commit()
    return standings


# ---------------------------------------------------------------------------
# Listing helpers
# ---------------------------------------------------------------------------

async def get_all_games(db: AsyncSession) -> list[dict[str, Any]]:
    """Return all registered games with their results."""
    games_res = await db.execute(
        select(IceBreakerGame)
        .options(
            selectinload(IceBreakerGame.results).selectinload(IceBreakerResult.group)
        )
        .order_by(IceBreakerGame.created_at)
    )
    games = games_res.scalars().all()

    return [
        {
            "id": g.id,
            "name": g.name,
            "scoring_type": g.scoring_type,
            "has_single_winner": g.scoring_type == "single",
            "results": [
                {
                    "placement": r.placement,
                    "group_id": r.group_id,
                    "group_name": r.group.name,
                    "points": r.points,
                }
                for r in g.results
            ],
        }
        for g in games
    ]


async def delete_game_results(db: AsyncSession, game_id: int) -> dict[str, Any]:
    """Clear all results for a given game (to allow re-entry)."""
    game_res = await db.execute(
        select(IceBreakerGame).where(IceBreakerGame.id == game_id)
    )
    game = game_res.scalar_one_or_none()
    if game is None:
        raise ValueError(f"Game id={game_id} not found.")

    await db.execute(
        delete(IceBreakerResult).where(IceBreakerResult.game_id == game_id)
    )
    await db.commit()
    return {"game_id": game_id, "game_name": game.name, "status": "results_cleared"}
