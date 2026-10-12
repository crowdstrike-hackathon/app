from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def _uuid() -> str:
    return uuid4().hex


def _now() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120), default="")
    org: Mapped[str] = mapped_column(String(120), default="")
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    runs: Mapped[list["Run"]] = relationship(back_populates="user", cascade="all, delete-orphan")


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    scenario_id: Mapped[str] = mapped_column(String(64))
    scenario_name: Mapped[str] = mapped_column(String(160))
    mode: Mapped[str] = mapped_column(String(16), default="mock")
    status: Mapped[str] = mapped_column(String(16), default="ready")
    turn: Mapped[int] = mapped_column(Integer, default=0)
    max_turns: Mapped[int] = mapped_column(Integer, default=0)
    blue_points: Mapped[int] = mapped_column(Integer, default=0)
    red_points: Mapped[int] = mapped_column(Integer, default=0)
    detected: Mapped[bool] = mapped_column(Boolean, default=False)
    detection_turn: Mapped[int | None] = mapped_column(Integer, nullable=True)
    score: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped[User] = relationship(back_populates="runs")
    events: Mapped[list["Event"]] = relationship(back_populates="run", cascade="all, delete-orphan")
    investigations: Mapped[list["InvestigationRow"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )
    actions: Mapped[list["ActionRow"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class Event(Base):
    __tablename__ = "events"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    turn: Mapped[int] = mapped_column(Integer, default=0)
    domain: Mapped[str] = mapped_column(String(24))
    source: Mapped[str] = mapped_column(String(64))
    host: Mapped[str | None] = mapped_column(String(64), nullable=True)
    actor: Mapped[str | None] = mapped_column(String(64), nullable=True)
    message: Mapped[str] = mapped_column(Text)
    attack_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    attack_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    severity: Mapped[str] = mapped_column(String(16), default="info")
    is_decoy: Mapped[bool] = mapped_column(Boolean, default=False)

    run: Mapped[Run] = relationship(back_populates="events")


class InvestigationRow(Base):
    __tablename__ = "investigations"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    turn: Mapped[int] = mapped_column(Integer, default=0)
    verdict: Mapped[str] = mapped_column(String(16))
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    headline: Mapped[str] = mapped_column(Text, default="")
    reasoning: Mapped[str] = mapped_column(Text, default="")
    trusted_event_ids: Mapped[list] = mapped_column(JSON, default=list)
    suspected_decoy_ids: Mapped[list] = mapped_column(JSON, default=list)

    run: Mapped[Run] = relationship(back_populates="investigations")


class ActionRow(Base):
    __tablename__ = "actions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(160))
    description: Mapped[str] = mapped_column(Text, default="")
    target: Mapped[str] = mapped_column(String(80))
    attack_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="proposed")

    run: Mapped[Run] = relationship(back_populates="actions")


class AgentRun(Base):
    __tablename__ = "agent_runs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    ticket_id: Mapped[str] = mapped_column(String(32), default="")
    subject: Mapped[str] = mapped_column(String(200), default="")
    defense: Mapped[bool] = mapped_column(Boolean, default=True)
    mode: Mapped[str] = mapped_column(String(16), default="mock")
    status: Mapped[str] = mapped_column(String(16), default="ready")
    outcome: Mapped[str] = mapped_column(String(24), default="")
    step_count: Mapped[int] = mapped_column(Integer, default=0)
    blocked_count: Mapped[int] = mapped_column(Integer, default=0)
    final_answer: Mapped[str] = mapped_column(Text, default="")
    trace: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Connection(Base):
    __tablename__ = "connections"

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    sim_mode: Mapped[str] = mapped_column(String(16), default="mock")
    blue_kind: Mapped[str] = mapped_column(String(24), default="anthropic")
    blue_base_url: Mapped[str] = mapped_column(String(255), default="")
    blue_api_key: Mapped[str] = mapped_column(String(255), default="")
    blue_model: Mapped[str] = mapped_column(String(120), default="")
    red_kind: Mapped[str] = mapped_column(String(24), default="openai")
    red_base_url: Mapped[str] = mapped_column(String(255), default="")
    red_api_key: Mapped[str] = mapped_column(String(255), default="")
    red_model: Mapped[str] = mapped_column(String(120), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)
