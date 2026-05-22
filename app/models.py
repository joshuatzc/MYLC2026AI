"""
models.py – SQLAlchemy ORM models for the Church-Building game.

All tables described in the spec are present here.
timed_events is included but intentionally unused in MVP code.
"""
from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# Groups
# ---------------------------------------------------------------------------

class Group(Base):
    __tablename__ = "groups"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), unique=True, nullable=False)
    population = Column(Float, nullable=False, default=10)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)

    # relationships
    progress_records = relationship(
        "GroupStationProgress", back_populates="group", cascade="all, delete-orphan"
    )
    chat_states = relationship("ChatState", back_populates="group")

    def __repr__(self) -> str:
        return f"<Group id={self.id} name={self.name!r} pop={self.population}>"


# ---------------------------------------------------------------------------
# Stations & Station Levels
# ---------------------------------------------------------------------------

class Station(Base):
    __tablename__ = "stations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False)
    # display order (optional helper field)
    sort_order = Column(Integer, default=0)

    levels = relationship(
        "StationLevel", back_populates="station", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Station id={self.id} name={self.name!r}>"


class StationLevel(Base):
    __tablename__ = "station_levels"

    id = Column(Integer, primary_key=True, autoincrement=True)
    station_id = Column(Integer, ForeignKey("stations.id"), nullable=False)
    level_number = Column(Integer, nullable=False)  # 1, 2, or 3

    # Player-facing short / ambiguous hint
    hint_text = Column(String(500), nullable=False, default="")

    # Reward
    reward_multiplier = Column(Float, nullable=False, default=1.2)

    # Raw prerequisite notes (human-readable; for admin reference)
    requirements_text = Column(Text, nullable=True)

    # Internal / logistics text – shown only to admin, never to players
    internal_notes = Column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("station_id", "level_number", name="uq_station_level"),
    )

    # relationships
    station = relationship("Station", back_populates="levels")
    progress_records = relationship(
        "GroupStationProgress", back_populates="station_level"
    )
    # Structured prerequisites (this level depends on these)
    prerequisites = relationship(
        "StationLevelPrerequisite",
        foreign_keys="StationLevelPrerequisite.station_level_id",
        back_populates="station_level",
        cascade="all, delete-orphan",
    )
    # Levels that depend on this level
    depended_on_by = relationship(
        "StationLevelPrerequisite",
        foreign_keys="StationLevelPrerequisite.required_station_level_id",
        back_populates="required_station_level",
    )

    def __repr__(self) -> str:
        return (
            f"<StationLevel id={self.id} station_id={self.station_id}"
            f" level={self.level_number}>"
        )


class StationLevelPrerequisite(Base):
    """Directed prerequisite edge: station_level_id REQUIRES required_station_level_id."""

    __tablename__ = "station_level_prerequisites"

    id = Column(Integer, primary_key=True, autoincrement=True)
    station_level_id = Column(
        Integer, ForeignKey("station_levels.id"), nullable=False
    )
    required_station_level_id = Column(
        Integer, ForeignKey("station_levels.id"), nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "station_level_id",
            "required_station_level_id",
            name="uq_prereq_edge",
        ),
    )

    station_level = relationship(
        "StationLevel",
        foreign_keys=[station_level_id],
        back_populates="prerequisites",
    )
    required_station_level = relationship(
        "StationLevel",
        foreign_keys=[required_station_level_id],
        back_populates="depended_on_by",
    )


# ---------------------------------------------------------------------------
# Group progress
# ---------------------------------------------------------------------------

class GroupStationProgress(Base):
    __tablename__ = "group_station_progress"

    id = Column(Integer, primary_key=True, autoincrement=True)
    group_id = Column(Integer, ForeignKey("groups.id"), nullable=False)
    station_level_id = Column(
        Integer, ForeignKey("station_levels.id"), nullable=False
    )
    completed_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    population_after = Column(Float, nullable=False)
    # Optional audit field: Telegram chat_id of the leader who confirmed it
    recorded_by = Column(String(100), nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "group_id", "station_level_id", name="uq_group_level_completion"
        ),
    )

    group = relationship("Group", back_populates="progress_records")
    station_level = relationship("StationLevel", back_populates="progress_records")

    def __repr__(self) -> str:
        return (
            f"<GroupStationProgress group={self.group_id}"
            f" level={self.station_level_id} pop_after={self.population_after}>"
        )


# ---------------------------------------------------------------------------
# Chat state (Telegram)
# ---------------------------------------------------------------------------

class RoleEnum(str, enum.Enum):
    normal = "normal"
    leader = "leader"


class ChatState(Base):
    __tablename__ = "chat_state"

    # chat_id is the primary key (one row per Telegram chat)
    chat_id = Column(String(50), primary_key=True)
    group_id = Column(Integer, ForeignKey("groups.id"), nullable=True)
    role = Column(
        Enum(RoleEnum, name="role_enum"),
        nullable=False,
        default=RoleEnum.normal,
    )
    # Track which conversation step this chat is currently in (for FSM)
    awaiting = Column(String(50), nullable=True)  # e.g. "leader_password"

    group = relationship("Group", back_populates="chat_states")

    def __repr__(self) -> str:
        return (
            f"<ChatState chat={self.chat_id!r} group={self.group_id}"
            f" role={self.role}>"
        )


# ---------------------------------------------------------------------------
# Timed events (future / non-MVP)
# ---------------------------------------------------------------------------

class TimedEventEffectType(str, enum.Enum):
    MULTIPLY = "MULTIPLY"
    ADD = "ADD"
    SUBTRACT_PERCENT = "SUBTRACT_PERCENT"


class TimedEvent(Base):
    """
    Planned future feature – not used in MVP logic.
    Schema is defined so that migrations are straightforward later.
    """

    __tablename__ = "timed_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)
    effect_type = Column(
        Enum(TimedEventEffectType, name="timed_event_effect_type_enum"),
        nullable=False,
    )
    effect_value = Column(Float, nullable=False)
    scheduled_time = Column(DateTime, nullable=True)
    is_applied = Column(Boolean, default=False, nullable=False)

    def __repr__(self) -> str:
        return f"<TimedEvent id={self.id} name={self.name!r}>"
