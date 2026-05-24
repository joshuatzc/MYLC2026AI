"""
routers/admin.py – FastAPI HTTP routes for the admin interface.

Endpoints:
  GET  /admin/leaderboard        – live leaderboard (HTML or JSON)
  GET  /admin/groups             – list groups
  POST /admin/groups             – create a group
  GET  /admin/groups/{id}        – group detail + history
  POST /admin/groups/{id}/upgrade – manually apply an upgrade (admin override)
  GET  /admin/stations           – list stations + levels
  POST /admin/stations           – create a station
  POST /admin/stations/{id}/levels – add a level to a station
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.database import get_session
from app.models import Group, Station, StationLevel
from app.services import game_logic

router = APIRouter(prefix="/admin", tags=["admin"])


# ---------------------------------------------------------------------------
# Auth helper
# ---------------------------------------------------------------------------

async def _require_admin_key(
    x_admin_key: str | None = Header(default=None),
) -> None:
    """
    All mutating admin endpoints must supply the correct secret key via the
    ``X-Admin-Key`` request header.  The value must match ``SECRET_KEY`` from
    the environment / .env file.
    """
    if x_admin_key != settings.SECRET_KEY:
        raise HTTPException(
            status_code=403,
            detail="Forbidden – supply a valid X-Admin-Key header.",
        )


# ---------------------------------------------------------------------------
# Pydantic request/response schemas
# ---------------------------------------------------------------------------

class GroupCreate(BaseModel):
    name: str
    population: float = 10.0


class StationCreate(BaseModel):
    name: str
    sort_order: int = 0


class StationLevelCreate(BaseModel):
    level_number: int
    hint_text: str = ""
    reward_multiplier: float = 1.2
    requirements_text: str | None = None
    internal_notes: str | None = None


class UpgradeRequest(BaseModel):
    station_level_id: int
    recorded_by: str | None = None


# ---------------------------------------------------------------------------
# Leaderboard
# ---------------------------------------------------------------------------

@router.get("/leaderboard")
async def admin_leaderboard(db: AsyncSession = Depends(get_session)) -> list[dict]:
    return await game_logic.get_leaderboard(db)


# ---------------------------------------------------------------------------
# Reseed
# ---------------------------------------------------------------------------

@router.post("/reseed")
async def reseed(
    _: None = Depends(_require_admin_key),
) -> dict:
    """
    Wipe the entire database and reseed it from scratch.

    - All tables are dropped and recreated.
    - Stations, levels, prerequisites, and groups are repopulated from seed.py.
    - All game progress and chat state is reset.

    Requires ``X-Admin-Key: <SECRET_KEY>`` header.
    """
    from scripts.seed import reseed as _reseed
    return await _reseed()


# ---------------------------------------------------------------------------
# Groups
# ---------------------------------------------------------------------------

@router.get("/groups")
async def list_groups(db: AsyncSession = Depends(get_session)) -> list[dict]:
    return await game_logic.list_groups(db)


@router.post("/groups", status_code=201)
async def create_group(
    body: GroupCreate, db: AsyncSession = Depends(get_session)
) -> dict:
    group = Group(name=body.name, population=body.population)
    db.add(group)
    await db.commit()
    return {"id": group.id, "name": group.name, "population": group.population}


@router.get("/groups/{group_id}")
async def get_group(
    group_id: int, db: AsyncSession = Depends(get_session)
) -> dict:
    result = await db.execute(select(Group).where(Group.id == group_id))
    group = result.scalar_one_or_none()
    if group is None:
        raise HTTPException(status_code=404, detail="Group not found")

    journey = await game_logic.get_journey(db, group_id)
    return {
        "id": group.id,
        "name": group.name,
        "population": group.population,
        "created_at": group.created_at.isoformat(),
        "history": journey,
    }


@router.post("/groups/{group_id}/upgrade")
async def admin_upgrade(
    group_id: int, body: UpgradeRequest, db: AsyncSession = Depends(get_session)
) -> dict:
    try:
        result = await game_logic.apply_level_upgrade(
            db,
            group_id,
            body.station_level_id,
            recorded_by=body.recorded_by or "admin",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return result


# ---------------------------------------------------------------------------
# Stations & Levels
# ---------------------------------------------------------------------------

@router.get("/stations")
async def list_stations(db: AsyncSession = Depends(get_session)) -> list[dict]:
    result = await db.execute(
        select(Station)
        .options(selectinload(Station.levels))
        .order_by(Station.sort_order, Station.id)
    )
    stations = result.scalars().all()
    return [
        {
            "id": s.id,
            "name": s.name,
            "levels": [
                {
                    "id": lv.id,
                    "level_number": lv.level_number,
                    "hint_text": lv.hint_text,
                    "reward_multiplier": lv.reward_multiplier,
                }
                for lv in sorted(s.levels, key=lambda l: l.level_number)
            ],
        }
        for s in stations
    ]


@router.post("/stations", status_code=201)
async def create_station(
    body: StationCreate, db: AsyncSession = Depends(get_session)
) -> dict:
    station = Station(name=body.name, sort_order=body.sort_order)
    db.add(station)
    await db.commit()
    return {"id": station.id, "name": station.name}


@router.post("/stations/{station_id}/levels", status_code=201)
async def add_level(
    station_id: int,
    body: StationLevelCreate,
    db: AsyncSession = Depends(get_session),
) -> dict:
    result = await db.execute(select(Station).where(Station.id == station_id))
    station = result.scalar_one_or_none()
    if station is None:
        raise HTTPException(status_code=404, detail="Station not found")

    level = StationLevel(
        station_id=station_id,
        level_number=body.level_number,
        hint_text=body.hint_text,
        reward_multiplier=body.reward_multiplier,
        requirements_text=body.requirements_text,
        internal_notes=body.internal_notes,
    )
    db.add(level)
    await db.commit()
    return {
        "id": level.id,
        "station_id": station_id,
        "level_number": level.level_number,
    }
