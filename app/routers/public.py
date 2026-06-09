"""
routers/public.py – FastAPI HTTP routes for the public livescores dashboard.

These endpoints do NOT require the X-Admin-Key header.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.services import game_logic

router = APIRouter(prefix="/api/public", tags=["public"])


@router.get("/icebreaker/mode")
async def get_icebreaker_mode(
    db: AsyncSession = Depends(get_session),
) -> dict:
    """Return the current game mode ('icebreaker' or 'nightgame') publicly."""
    from app.services import icebreaker
    mode = await icebreaker.get_game_mode(db)
    return {"game_mode": mode}


@router.get("/icebreaker/games")
async def list_icebreaker_games(
    db: AsyncSession = Depends(get_session),
) -> list[dict]:
    """List all registered ice breaker games and their results publicly."""
    from app.services import icebreaker
    return await icebreaker.get_all_games(db)


@router.get("/icebreaker/standings")
async def get_icebreaker_standings(
    db: AsyncSession = Depends(get_session),
) -> list[dict]:
    """Preview the current overall standings and starting-pop bonuses publicly."""
    from app.services import icebreaker
    return await icebreaker.get_standings(db)


@router.get("/leaderboard")
async def get_public_leaderboard(
    db: AsyncSession = Depends(get_session),
) -> list[dict]:
    """Get the current live nightgame leaderboard publicly."""
    return await game_logic.get_leaderboard(db)
