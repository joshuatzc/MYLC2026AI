"""
services/auth.py – chat state management and leader authentication.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import ChatState, RoleEnum


async def _get_or_create_state(db: AsyncSession, chat_id: str) -> ChatState:
    result = await db.execute(
        select(ChatState).where(ChatState.chat_id == chat_id)
    )
    state = result.scalar_one_or_none()
    if state is None:
        state = ChatState(chat_id=chat_id, role=RoleEnum.normal)
        db.add(state)
        await db.flush()  # populate defaults without full commit
    return state


async def get_current_group_id(db: AsyncSession, chat_id: str) -> int | None:
    state = await _get_or_create_state(db, chat_id)
    return state.group_id


async def set_current_group(
    db: AsyncSession, chat_id: str, group_id: int
) -> None:
    """
    Update the group for this chat and reset role to 'normal'
    (per spec: changing group resets leader status).
    """
    state = await _get_or_create_state(db, chat_id)
    state.group_id = group_id
    state.role = RoleEnum.normal
    state.awaiting = None
    await db.commit()


async def get_role(db: AsyncSession, chat_id: str) -> str:
    state = await _get_or_create_state(db, chat_id)
    await db.commit()  # commit the possible INSERT
    return state.role.value


async def become_leader(
    db: AsyncSession, chat_id: str, password: str
) -> bool:
    """
    Check the global leader password.
    If correct, promote the current chat to leader.
    Returns True on success, False on wrong password.
    """
    if password != settings.LEADER_PASSWORD:
        return False

    state = await _get_or_create_state(db, chat_id)
    if state.group_id is None:
        # Must have a group selected first
        return False

    state.role = RoleEnum.leader
    state.awaiting = None
    await db.commit()
    return True


async def set_awaiting(
    db: AsyncSession, chat_id: str, awaiting: str | None
) -> None:
    """Track which input the bot is waiting for from this chat."""
    state = await _get_or_create_state(db, chat_id)
    state.awaiting = awaiting
    await db.commit()


async def get_awaiting(db: AsyncSession, chat_id: str) -> str | None:
    state = await _get_or_create_state(db, chat_id)
    await db.commit()
    return state.awaiting


async def get_full_state(db: AsyncSession, chat_id: str) -> ChatState:
    """Return the full ChatState row (creates one if absent)."""
    state = await _get_or_create_state(db, chat_id)
    await db.commit()
    return state
