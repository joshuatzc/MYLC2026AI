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

  --- Ice Breaker pre-game ---
  GET  /admin/icebreaker/mode                    – get current game mode
  POST /admin/icebreaker/mode                    – set game mode manually
  GET  /admin/icebreaker/games                   – list all games + results
  POST /admin/icebreaker/games                   – register a new game
  POST /admin/icebreaker/games/{id}/results      – record/overwrite results
  DELETE /admin/icebreaker/games/{id}/results    – clear results for a game
  GET  /admin/icebreaker/standings               – preview standings + bonuses
  POST /admin/icebreaker/finalize                – apply bonuses + switch to nightgame
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.database import get_session, AsyncSessionLocal
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

    # Trigger AI news
    from app.services.ai_news import trigger_event_broadcast
    import asyncio
    asyncio.create_task(trigger_event_broadcast("create_group", {
        "group_name": group.name,
        "population": group.population,
    }))

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
        # Fetch group name before upgrade just in case
        group_res = await db.execute(select(Group.name).where(Group.id == group_id))
        group_name = group_res.scalar_one_or_none() or "Unknown Group"

        result = await game_logic.apply_level_upgrade(
            db,
            group_id,
            body.station_level_id,
            recorded_by=body.recorded_by or "admin",
        )

        # Trigger AI news
        from app.services.ai_news import trigger_event_broadcast
        import asyncio
        event_type = "church_upgrade" if result.get("church_upgraded") else "upgrade"
        details = {
            "group_name": group_name,
            "station_name": result["station_name"],
            "level_number": result["level_number"],
            "old_population": result["old_population"],
            "new_population": result["new_population"],
        }
        if event_type == "church_upgrade":
            details.update({
                "tier_name": result.get("tier_name"),
                "theft_applied": result.get("theft_applied"),
                "stolen_amount": result.get("stolen_amount"),
                "target_name": result.get("target_name"),
            })
        asyncio.create_task(trigger_event_broadcast(event_type, details))

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


# ---------------------------------------------------------------------------
# Super Pastor Timed Event Control
# ---------------------------------------------------------------------------

class EventStartRequest(BaseModel):
    reward_amount: int = 1000


@router.post("/events/super-pastor/start")
async def start_super_pastor_event(
    body: EventStartRequest,
    db: AsyncSession = Depends(get_session),
    _: None = Depends(_require_admin_key),
) -> dict:
    """Start the Super Pastor event and broadcast it via THE MYLC TIMES."""
    from app.models import GlobalState
    
    # Check if active
    sp_active_res = await db.execute(select(GlobalState).where(GlobalState.key == "super_pastor_active"))
    sp_active_row = sp_active_res.scalar_one_or_none()
    
    if not sp_active_row:
        sp_active_row = GlobalState(key="super_pastor_active", value_bool=True)
        db.add(sp_active_row)
    else:
        if sp_active_row.value_bool:
            return {"status": "error", "message": "Super Pastor event is already active."}
        sp_active_row.value_bool = True

    # Set reward
    sp_reward_res = await db.execute(select(GlobalState).where(GlobalState.key == "super_pastor_reward"))
    sp_reward_row = sp_reward_res.scalar_one_or_none()
    if not sp_reward_row:
        sp_reward_row = GlobalState(key="super_pastor_reward", value_int=body.reward_amount)
        db.add(sp_reward_row)
    else:
        sp_reward_row.value_int = body.reward_amount

    # Reset claimed_by
    claimed_by_res = await db.execute(select(GlobalState).where(GlobalState.key == "super_pastor_claimed_by"))
    claimed_by_row = claimed_by_res.scalar_one_or_none()
    if not claimed_by_row:
        claimed_by_row = GlobalState(key="super_pastor_claimed_by", value_int=None)
        db.add(claimed_by_row)
    else:
        claimed_by_row.value_int = None

    # Reset claim_count
    claim_count_res = await db.execute(select(GlobalState).where(GlobalState.key == "super_pastor_claim_count"))
    claim_count_row = claim_count_res.scalar_one_or_none()
    if not claim_count_row:
        claim_count_row = GlobalState(key="super_pastor_claim_count", value_int=0)
        db.add(claim_count_row)
    else:
        claim_count_row.value_int = 0

    # Reset claims
    claims_res = await db.execute(select(GlobalState).where(GlobalState.key == "super_pastor_claims"))
    claims_row = claims_res.scalar_one_or_none()
    if not claims_row:
        claims_row = GlobalState(key="super_pastor_claims", value_str="")
        db.add(claims_row)
    else:
        claims_row.value_str = ""

    # Store started at time and duration
    started_res = await db.execute(select(GlobalState).where(GlobalState.key == "super_pastor_started_at"))
    started_row = started_res.scalar_one_or_none()
    if not started_row:
        db.add(GlobalState(key="super_pastor_started_at", value_str=datetime.utcnow().isoformat()))
    else:
        started_row.value_str = datetime.utcnow().isoformat()

    dur_res = await db.execute(select(GlobalState).where(GlobalState.key == "super_pastor_duration"))
    dur_row = dur_res.scalar_one_or_none()
    if not dur_row:
        db.add(GlobalState(key="super_pastor_duration", value_int=20))
    else:
        dur_row.value_int = 20

    await db.commit()

    # Trigger AI announcement broadcast + auto-expire timer
    from app.services.ai_news import trigger_event_broadcast
    from app.services.timed_events import super_pastor_expire_timer
    asyncio.create_task(trigger_event_broadcast("super_pastor_start", {
        "reward_amount": body.reward_amount,
        "duration_minutes": 20
    }))
    asyncio.create_task(super_pastor_expire_timer(20))

    return {"status": "ok", "message": f"Super Pastor event started with reward {body.reward_amount}. Auto-expires in 20 minutes."}


@router.post("/events/super-pastor/stop")
async def stop_super_pastor_event(
    db: AsyncSession = Depends(get_session),
    _: None = Depends(_require_admin_key),
) -> dict:
    """Manually stop the Super Pastor event."""
    from app.models import GlobalState
    
    sp_active_res = await db.execute(select(GlobalState).where(GlobalState.key == "super_pastor_active"))
    sp_active_row = sp_active_res.scalar_one_or_none()
    
    if not sp_active_row or not sp_active_row.value_bool:
        return {"status": "error", "message": "Super Pastor event is not currently active."}

    sp_active_row.value_bool = False
    await db.commit()

    return {"status": "ok", "message": "Super Pastor event stopped."}


@router.get("/events/super-pastor/status")
async def super_pastor_event_status(
    db: AsyncSession = Depends(get_session),
    _: None = Depends(_require_admin_key),
) -> dict:
    """Check status of the Super Pastor event."""
    from app.models import GlobalState
    
    sp_active_res = await db.execute(select(GlobalState).where(GlobalState.key == "super_pastor_active"))
    sp_active_row = sp_active_res.scalar_one_or_none()
    active = sp_active_row.value_bool if sp_active_row else False

    sp_reward_res = await db.execute(select(GlobalState).where(GlobalState.key == "super_pastor_reward"))
    sp_reward_row = sp_reward_res.scalar_one_or_none()
    reward = sp_reward_row.value_int if sp_reward_row else 1000

    claimed_by_res = await db.execute(select(GlobalState).where(GlobalState.key == "super_pastor_claimed_by"))
    claimed_by_row = claimed_by_res.scalar_one_or_none()
    claimed_by_id = claimed_by_row.value_int if claimed_by_row else None

    claimed_by_name = None
    if claimed_by_id:
        group_res = await db.execute(select(Group.name).where(Group.id == claimed_by_id))
        claimed_by_name = group_res.scalar_one_or_none()

    claim_count_res = await db.execute(select(GlobalState).where(GlobalState.key == "super_pastor_claim_count"))
    claim_count_row = claim_count_res.scalar_one_or_none()
    claim_count = claim_count_row.value_int if claim_count_row else 0

    claims_res = await db.execute(select(GlobalState).where(GlobalState.key == "super_pastor_claims"))
    claims_row = claims_res.scalar_one_or_none()
    claims_str = claims_row.value_str if claims_row else ""
    claims_list = [int(x) for x in claims_str.split(",") if x.strip()]

    claimed_group_names = []
    if claims_list:
        for gid in claims_list:
            g_res = await db.execute(select(Group.name).where(Group.id == gid))
            name = g_res.scalar_one_or_none()
            if name:
                claimed_group_names.append(name)

    return {
        "active": active,
        "reward_amount": reward,
        "claimed_by_id": claimed_by_id,
        "claimed_by_name": claimed_by_name,
        "claim_count": claim_count,
        "claims": claims_list,
        "claimed_group_names": claimed_group_names
    }


# ---------------------------------------------------------------------------
# Infestation Timed Event Control
# ---------------------------------------------------------------------------

class InfestationStartRequest(BaseModel):
    cutoff: int
    penalty: int = 300
    duration_minutes: int = 20


@router.post("/events/infestation/start")
async def start_infestation_event(
    body: InfestationStartRequest,
    db: AsyncSession = Depends(get_session),
    _: None = Depends(_require_admin_key),
) -> dict:
    """Start the Infestation timed audit event."""
    from app.models import GlobalState

    active_res = await db.execute(select(GlobalState).where(GlobalState.key == "infestation_active"))
    active_row = active_res.scalar_one_or_none()

    if active_row and active_row.value_bool:
        return {"status": "error", "message": "Infestation event is already active."}

    for key, val_bool, val_int, val_str in [
        ("infestation_active",   True,         None,         None),
        ("infestation_penalty",  None,         body.penalty, None),
        ("infestation_cutoff",   None,         body.cutoff,  None),
        ("infestation_duration", None,         body.duration_minutes, None),
        ("infestation_started_at", None,       None,         datetime.utcnow().isoformat()),
    ]:
        row = (await db.execute(select(GlobalState).where(GlobalState.key == key))).scalar_one_or_none()
        if not row:
            row = GlobalState(key=key, value_bool=val_bool, value_int=val_int, value_str=val_str)
            db.add(row)
        else:
            row.value_bool = val_bool
            row.value_int = val_int
            row.value_str = val_str

    await db.commit()

    from app.services.ai_news import trigger_event_broadcast
    from app.services.timed_events import infestation_audit_timer
    asyncio.create_task(trigger_event_broadcast("infestation_start", {
        "penalty": body.penalty,
        "cutoff": body.cutoff,
        "duration_minutes": body.duration_minutes
    }))
    asyncio.create_task(infestation_audit_timer(body.duration_minutes, body.cutoff, body.penalty))

    return {
        "status": "ok",
        "message": (
            f"Infestation event started. Cutoff score: {body.cutoff}. "
            f"Penalty: {body.penalty}. Fires in {body.duration_minutes} minutes."
        )
    }


@router.post("/events/infestation/stop")
async def stop_infestation_event(
    db: AsyncSession = Depends(get_session),
    _: None = Depends(_require_admin_key),
) -> dict:
    """Manually cancel the Infestation event before the timer fires."""
    from app.models import GlobalState
    row = (await db.execute(select(GlobalState).where(GlobalState.key == "infestation_active"))).scalar_one_or_none()
    if not row or not row.value_bool:
        return {"status": "error", "message": "Infestation event is not currently active."}
    row.value_bool = False
    await db.commit()
    return {"status": "ok", "message": "Infestation event cancelled."}


@router.get("/events/infestation/status")
async def infestation_event_status(
    db: AsyncSession = Depends(get_session),
    _: None = Depends(_require_admin_key),
) -> dict:
    """Check current status of the Infestation event."""
    from app.models import GlobalState
    keys = ["infestation_active", "infestation_cutoff", "infestation_penalty", "infestation_duration"]
    rows = {
        r.key: r for r in
        (await db.execute(select(GlobalState).where(GlobalState.key.in_(keys)))).scalars().all()
    }
    return {
        "active": rows.get("infestation_active") and rows["infestation_active"].value_bool,
        "cutoff": rows.get("infestation_cutoff") and rows["infestation_cutoff"].value_int,
        "penalty": rows.get("infestation_penalty") and rows["infestation_penalty"].value_int,
        "duration_minutes": rows.get("infestation_duration") and rows["infestation_duration"].value_int,
    }


# ---------------------------------------------------------------------------
# Corruption of Leaders Quiz Event Control
# ---------------------------------------------------------------------------

class CorruptionStartRequest(BaseModel):
    duration_minutes: int = 20


@router.post("/events/corruption/start")
async def start_corruption_event(
    body: CorruptionStartRequest,
    db: AsyncSession = Depends(get_session),
    _: None = Depends(_require_admin_key),
) -> dict:
    """Start the Corruption of Leaders quiz event."""
    from app.models import GlobalState, GroupQuizState

    active_res = await db.execute(select(GlobalState).where(GlobalState.key == "corruption_active"))
    active_row = active_res.scalar_one_or_none()

    if active_row and active_row.value_bool:
        return {"status": "error", "message": "Corruption event is already active."}

    # Set active flag
    if not active_row:
        active_row = GlobalState(key="corruption_active", value_bool=True)
        db.add(active_row)
    else:
        active_row.value_bool = True

    # Store duration
    dur_res = await db.execute(select(GlobalState).where(GlobalState.key == "corruption_duration"))
    dur_row = dur_res.scalar_one_or_none()
    if not dur_row:
        db.add(GlobalState(key="corruption_duration", value_int=body.duration_minutes))
    else:
        dur_row.value_int = body.duration_minutes

    # Store started at time
    from datetime import datetime
    started_res = await db.execute(select(GlobalState).where(GlobalState.key == "corruption_started_at"))
    started_row = started_res.scalar_one_or_none()
    if not started_row:
        db.add(GlobalState(key="corruption_started_at", value_str=datetime.utcnow().isoformat()))
    else:
        started_row.value_str = datetime.utcnow().isoformat()

    # Clear all existing quiz states for a fresh round
    existing_states = (await db.execute(select(GroupQuizState))).scalars().all()
    for s in existing_states:
        await db.delete(s)

    await db.commit()

    import json, os
    quiz_path = os.path.join("app", "config", "corruption_quiz.json")
    total_questions = 12
    if os.path.exists(quiz_path):
        with open(quiz_path) as f:
            total_questions = len(json.load(f))

    from app.services.ai_news import trigger_event_broadcast
    from app.services.timed_events import corruption_expire_timer
    asyncio.create_task(trigger_event_broadcast("corruption_start", {"duration_minutes": body.duration_minutes}))
    asyncio.create_task(corruption_expire_timer(body.duration_minutes, total_questions))

    return {
        "status": "ok",
        "message": f"Corruption of Leaders quiz started. {total_questions} questions. Auto-expires in {body.duration_minutes} minutes."
    }


@router.post("/events/corruption/stop")
async def stop_corruption_event(
    db: AsyncSession = Depends(get_session),
    _: None = Depends(_require_admin_key),
) -> dict:
    """Manually cancel the Corruption event."""
    from app.models import GlobalState
    row = (await db.execute(select(GlobalState).where(GlobalState.key == "corruption_active"))).scalar_one_or_none()
    if not row or not row.value_bool:
        return {"status": "error", "message": "Corruption event is not currently active."}
    row.value_bool = False
    await db.commit()
    return {"status": "ok", "message": "Corruption event cancelled."}


@router.get("/events/corruption/status")
async def corruption_event_status(
    db: AsyncSession = Depends(get_session),
    _: None = Depends(_require_admin_key),
) -> dict:
    """Check current status of the Corruption quiz event, including per-group progress."""
    from app.models import GlobalState, GroupQuizState

    active_res = await db.execute(select(GlobalState).where(GlobalState.key == "corruption_active"))
    active_row = active_res.scalar_one_or_none()
    active = active_row.value_bool if active_row else False

    quiz_states = (await db.execute(
        select(GroupQuizState).options()
    )).scalars().all()

    groups_progress = []
    for qs in quiz_states:
        group_res = await db.execute(select(Group.name).where(Group.id == qs.group_id))
        gname = group_res.scalar_one_or_none() or f"Group {qs.group_id}"
        groups_progress.append({
            "group": gname,
            "question": qs.current_question_index,
            "correct": qs.correct_count,
            "wrong": qs.wrong_count,
            "completed": qs.completed,
        })

    return {"active": active, "groups_progress": groups_progress}


# ---------------------------------------------------------------------------
# Ice Breaker pre-game
# ---------------------------------------------------------------------------

class GameModeSet(BaseModel):
    mode: str  # "icebreaker" or "nightgame"


class IceBreakerGameCreate(BaseModel):
    name: str
    scoring_type: str = "ranking"  # "ranking", "single", "points"


class IceBreakerResultsSubmit(BaseModel):
    placements: list[int] | None = None
    scores: dict[int, int] | None = None


@router.get("/icebreaker/mode")
async def get_icebreaker_mode(
    db: AsyncSession = Depends(get_session),
    _: None = Depends(_require_admin_key),
) -> dict:
    """Return the current game mode ('icebreaker' or 'nightgame')."""
    from app.services import icebreaker
    mode = await icebreaker.get_game_mode(db)
    return {"game_mode": mode}


@router.post("/icebreaker/mode")
async def set_icebreaker_mode(
    body: GameModeSet,
    db: AsyncSession = Depends(get_session),
    _: None = Depends(_require_admin_key),
) -> dict:
    """Manually override the game mode."""
    from app.services import icebreaker
    if body.mode not in ("icebreaker", "nightgame"):
        raise HTTPException(status_code=400, detail="mode must be 'icebreaker' or 'nightgame'.")
    await icebreaker.set_game_mode(db, body.mode)
    return {"game_mode": body.mode}


@router.get("/icebreaker/games")
async def list_icebreaker_games(
    db: AsyncSession = Depends(get_session),
    _: None = Depends(_require_admin_key),
) -> list[dict]:
    """List all registered ice breaker games and their results."""
    from app.services import icebreaker
    return await icebreaker.get_all_games(db)


@router.post("/icebreaker/games", status_code=201)
async def create_icebreaker_game(
    body: IceBreakerGameCreate,
    db: AsyncSession = Depends(get_session),
    _: None = Depends(_require_admin_key),
) -> dict:
    """Register a new ice breaker mini-game."""
    from app.services import icebreaker
    try:
        return await icebreaker.register_game(db, body.name, body.scoring_type)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/icebreaker/games/{game_id}/results")
async def submit_icebreaker_results(
    game_id: int,
    body: IceBreakerResultsSubmit,
    db: AsyncSession = Depends(get_session),
    _: None = Depends(_require_admin_key),
) -> dict:
    """Record (or overwrite) results for a game."""
    from app.services import icebreaker
    try:
        return await icebreaker.record_results(
            db, game_id, placements=body.placements, scores=body.scores
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.delete("/icebreaker/games/{game_id}/results")
async def clear_icebreaker_results(
    game_id: int,
    db: AsyncSession = Depends(get_session),
    _: None = Depends(_require_admin_key),
) -> dict:
    """Clear all results for a game so they can be re-entered."""
    from app.services import icebreaker
    try:
        return await icebreaker.delete_game_results(db, game_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.get("/icebreaker/standings")
async def get_icebreaker_standings(
    db: AsyncSession = Depends(get_session),
    _: None = Depends(_require_admin_key),
) -> list[dict]:
    """Preview the current overall standings and starting-pop bonuses."""
    from app.services import icebreaker
    return await icebreaker.get_standings(db)


@router.post("/icebreaker/finalize")
async def finalize_icebreaker(
    db: AsyncSession = Depends(get_session),
    _: None = Depends(_require_admin_key),
) -> dict:
    """Apply ice breaker population bonuses to all groups and switch to nightgame mode.

    This is idempotent — calling it a second time resets populations to the
    same values again (useful for corrections).
    """
    from app.services import icebreaker
    standings = await icebreaker.apply_bonuses(db)
    return {
        "status": "ok",
        "message": "Ice breaker bonuses applied. Game mode switched to nightgame.",
        "standings": standings,
    }
