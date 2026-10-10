from __future__ import annotations

import asyncio
import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from ..agents import blue_agent
from ..auth import decode_token, get_current_user
from ..config import settings
from ..db import get_db
from ..engine.session import Session, store
from ..llm.provider import get_blue_provider
from ..models import ActionStatus, LogEvent
from ..orm import Run, User
from ..sim.attack_catalog import list_scenarios
from ..sim.environment import NORTHWIND

router = APIRouter(prefix="/api")


@router.get("/meta")
def meta() -> dict:
    live = settings.is_live
    return {
        "mode": settings.sim_mode,
        "blue": {
            "provider": "claude" if (live and settings.blue_ready) else "mock-heuristic",
            "model": settings.blue_model_name,
            "ready": settings.blue_ready,
        },
        "red": {
            "provider": "local-a100" if (live and settings.red_ready) else "mock-scripted",
            "model": settings.red_model_name,
            "ready": settings.red_ready,
        },
        "environment": {
            "company": NORTHWIND.company,
            "summary": NORTHWIND.summary,
            "assets": [a.__dict__ for a in NORTHWIND.assets],
            "crown_jewels": NORTHWIND.crown_jewels,
        },
        "scenarios": list_scenarios(),
    }


@router.get("/scenarios")
def scenarios() -> list[dict]:
    return list_scenarios()


class CreateSession(BaseModel):
    scenario_id: str
    auto: bool = False
    seed: Optional[int] = None


def _own(sess: Optional[Session], user: User) -> Session:
    if not sess or sess.user_id != user.id:
        raise HTTPException(404, "no such session")
    return sess


@router.post("/sessions")
async def create_session(body: CreateSession, user: User = Depends(get_current_user)) -> dict:
    try:
        sess = await store.create(body.scenario_id, settings.sim_mode, user.id, body.seed)
    except KeyError:
        raise HTTPException(404, f"unknown scenario {body.scenario_id!r}")
    if body.auto:
        asyncio.create_task(sess.run())
    return sess.state.model_dump()


@router.get("/sessions/{session_id}")
async def get_session(session_id: str, user: User = Depends(get_current_user)) -> dict:
    return _own(store.get(session_id), user).state.model_dump()


@router.post("/sessions/{session_id}/step")
async def step_session(session_id: str, user: User = Depends(get_current_user)) -> dict:
    sess = _own(store.get(session_id), user)
    await sess.step()
    return sess.state.model_dump()


@router.post("/sessions/{session_id}/run")
async def run_session(session_id: str, user: User = Depends(get_current_user)) -> dict:
    sess = _own(store.get(session_id), user)
    asyncio.create_task(sess.run())
    return {"status": "running"}


@router.get("/sessions/{session_id}/score")
async def score(session_id: str, user: User = Depends(get_current_user)) -> dict:
    return _own(store.get(session_id), user).state.score.model_dump()


@router.get("/sessions/{session_id}/stream")
async def stream(session_id: str, token: str = Query(default=""), db: AsyncSession = Depends(get_db)):
    user_id = decode_token(token)
    sess = store.get(session_id)
    if not user_id or not sess or sess.user_id != user_id:
        raise HTTPException(404, "no such session")
    q = sess.subscribe()

    async def gen():
        yield {"event": "snapshot", "data": json.dumps(sess.state.model_dump())}
        try:
            while True:
                msg = await q.get()
                yield {"event": msg["type"], "data": json.dumps(msg["payload"])}
        except asyncio.CancelledError:
            raise
        finally:
            sess.unsubscribe(q)

    return EventSourceResponse(gen())


@router.post("/sessions/{session_id}/actions/{action_id}/{decision}")
async def decide_action(
    session_id: str, action_id: str, decision: str, user: User = Depends(get_current_user)
) -> dict:
    sess = _own(store.get(session_id), user)
    mapping = {
        "approve": ActionStatus.approved,
        "deny": ActionStatus.denied,
        "execute": ActionStatus.executed,
    }
    if decision not in mapping:
        raise HTTPException(400, "decision must be approve|deny|execute")
    action = sess.set_action_status(action_id, mapping[decision])
    if not action:
        raise HTTPException(404, "no such action")
    await sess.persist_action_status(action_id, mapping[decision])
    await sess._emit("action_update", action.model_dump())
    return action.model_dump()


@router.get("/history")
async def history(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> list[dict]:
    result = await db.execute(
        select(Run).where(Run.user_id == user.id).order_by(Run.created_at.desc()).limit(50)
    )
    runs = result.scalars().all()
    return [
        {
            "id": r.id,
            "scenario_id": r.scenario_id,
            "scenario_name": r.scenario_name,
            "mode": r.mode,
            "status": r.status,
            "turn": r.turn,
            "max_turns": r.max_turns,
            "blue_points": r.blue_points,
            "red_points": r.red_points,
            "detected": r.detected,
            "detection_turn": r.detection_turn,
            "score": r.score,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "finished_at": r.finished_at.isoformat() if r.finished_at else None,
        }
        for r in runs
    ]


@router.get("/stats")
async def stats(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> dict:
    result = await db.execute(select(Run).where(Run.user_id == user.id))
    runs = result.scalars().all()
    finished = [r for r in runs if r.status == "finished"]
    detected = [r for r in finished if r.detected]
    avg_blue = round(sum(r.blue_points for r in finished) / len(finished), 1) if finished else 0
    detect_turns = [r.detection_turn for r in detected if r.detection_turn]
    return {
        "total_runs": len(runs),
        "finished_runs": len(finished),
        "detection_rate": round(len(detected) / len(finished), 3) if finished else 0,
        "avg_blue_points": avg_blue,
        "avg_detection_turn": round(sum(detect_turns) / len(detect_turns), 2) if detect_turns else None,
    }


class IngestBody(BaseModel):
    logs: list[dict]


@router.post("/ingest")
async def ingest(body: IngestBody, user: User = Depends(get_current_user)) -> dict:
    events = []
    for raw in body.logs:
        events.append(
            LogEvent(
                turn=1,
                source=str(raw.get("source", "uploaded")),
                host=raw.get("host"),
                actor=raw.get("actor"),
                message=str(raw.get("message", json.dumps(raw))),
                severity=str(raw.get("severity", "info")),
            )
        )
    inv = await blue_agent.investigate(1, events, NORTHWIND.brief(), get_blue_provider())
    return {"events": [e.model_dump() for e in events], "investigation": inv.model_dump()}
