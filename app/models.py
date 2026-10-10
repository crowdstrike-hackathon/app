from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional
from uuid import uuid4

from pydantic import BaseModel, Field


def _uuid() -> str:
    return uuid4().hex[:12]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Domain(str, Enum):
    auth = "auth"
    endpoint = "endpoint"
    network = "network"
    cloud = "cloud"
    identity = "identity"
    email = "email"


class Verdict(str, Enum):
    benign = "benign"
    suspicious = "suspicious"
    malicious = "malicious"


class ActionStatus(str, Enum):
    proposed = "proposed"
    approved = "approved"
    denied = "denied"
    executed = "executed"


class LogEvent(BaseModel):
    id: str = Field(default_factory=_uuid)
    ts: str = Field(default_factory=_now)
    turn: int = 0
    domain: Domain = Domain.endpoint
    source: str = "unknown"
    host: Optional[str] = None
    actor: Optional[str] = None
    message: str = ""
    attack_id: Optional[str] = None
    attack_name: Optional[str] = None
    severity: str = "info"
    raw: dict[str, Any] = Field(default_factory=dict)
    is_decoy: bool = False


class ResponseAction(BaseModel):
    id: str = Field(default_factory=_uuid)
    title: str
    description: str
    target: str
    attack_id: Optional[str] = None
    requires_approval: bool = True
    status: ActionStatus = ActionStatus.proposed


class Investigation(BaseModel):
    id: str = Field(default_factory=_uuid)
    turn: int
    ts: str = Field(default_factory=_now)
    verdict: Verdict = Verdict.benign
    confidence: float = 0.0
    headline: str = ""
    reasoning: str = ""
    trusted_event_ids: list[str] = Field(default_factory=list)
    suspected_decoy_ids: list[str] = Field(default_factory=list)
    recommended_actions: list[ResponseAction] = Field(default_factory=list)


class Score(BaseModel):
    turns: int = 0
    real_events: int = 0
    decoy_events: int = 0
    detected: bool = False
    detection_turn: Optional[int] = None
    decoys_flagged_correct: int = 0
    decoys_missed: int = 0
    real_flagged_as_decoy: int = 0
    false_positive: bool = False
    blue_points: int = 0
    red_points: int = 0


class SessionState(BaseModel):
    id: str
    scenario_id: str
    scenario_name: str
    status: str = "ready"
    mode: str = "mock"
    turn: int = 0
    max_turns: int = 0
    events: list[LogEvent] = Field(default_factory=list)
    investigations: list[Investigation] = Field(default_factory=list)
    actions: list[ResponseAction] = Field(default_factory=list)
    score: Score = Field(default_factory=Score)
