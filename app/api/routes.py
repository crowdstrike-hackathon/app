from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Literal, Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from ..agent.llm import model_status
from ..auth import decode_token, get_current_user
from ..db import SessionLocal, get_db
from ..engine.runs import MODES, RunSession, store
from ..orm import RunRecord, User
from ..riposte import Policy
from ..riposte.baseline import _PATTERNS
from ..world.seed import COMPANY, KB
from ..world.tickets import load_tickets, normalize_ticket
from ..world.world import World, tool_catalog

router = APIRouter(prefix="/api")

MODE_INFO = [
    {"id": "off", "label": "Off", "blurb": "No defense. Every tool call the agent makes is executed."},
    {"id": "keyword", "label": "Keyword filter", "blurb": "Scans the ticket text for known injection phrases and quarantines matches."},
    {"id": "riposte", "label": "Riposte", "blurb": "Traces where every tool argument came from and blocks or holds calls driven by untrusted content."},
]


class CustomTicket(BaseModel):
    sender: str = Field(alias="from", min_length=3, max_length=200)
    subject: str = Field(min_length=1, max_length=300)
    body: str = Field(min_length=1, max_length=6000)
    customer_id: Optional[str] = Field(default=None, max_length=32)

    model_config = {"populate_by_name": True}


class RunBody(BaseModel):
    mode: Literal["off", "keyword", "riposte"] = "riposte"
    ticket_id: Optional[str] = None
    ticket: Optional[CustomTicket] = None


class HoldBody(BaseModel):
    decision: Literal["approve", "deny"]


@router.get("/meta")
async def meta() -> dict:
    policy = Policy()
    _, info = load_tickets()
    return {
        "company": COMPANY,
        "agent": await model_status(),
        "modes": MODE_INFO,
        "policy": policy.to_dict(),
        "tools": tool_catalog(),
        "keyword_patterns": len(_PATTERNS),
        "tickets_file": info,
    }


@router.get("/tickets")
def tickets(user: User = Depends(get_current_user)) -> dict:
    items, info = load_tickets()
    return {"tickets": items, "file": info}


@router.get("/world")
def world(user: User = Depends(get_current_user)) -> dict:
    policy = Policy()
    sandbox = World(tickets={}, internal_domains=policy.internal_domains, refund_limit=policy.refund_limit)
    return {
        "company": COMPANY,
        "customers": sandbox.public_customers(),
        "kb": [{"id": a["id"], "title": a["title"]} for a in KB],
        "policy": policy.to_dict(),
        "tools": tool_catalog(),
    }


async def _persist(session: RunSession) -> None:
    async with SessionLocal() as db:
        db.add(RunRecord(
            id=session.id,
            user_id=session.user_id,
            ticket_id=session.ticket["id"],
            subject=session.ticket["subject"][:300],
            kind=session.ticket.get("kind", ""),
            mode=session.mode,
            model=session.snapshot(False)["model"],
            status=session.status,
            outcome=session.outcome,
            summary=session.summary,
            steps=session.stats["steps"],
            blocked=session.stats["blocked"],
            held=session.stats["held"],
            trace=session.snapshot(),
            created_at=datetime.fromisoformat(session.created_at),
            finished_at=datetime.fromisoformat(session.finished_at) if session.finished_at else None,
        ))
        await db.commit()


async def _drive(session: RunSession) -> None:
    try:
        await session.run()
    finally:
        await asyncio.shield(_persist(session))


@router.post("/runs")
async def create_run(body: RunBody, user: User = Depends(get_current_user)) -> dict:
    items, _ = load_tickets()
    if body.ticket is not None:
        try:
            ticket = normalize_ticket({
                "id": f"TK-LIVE-{uuid4().hex[:4].upper()}",
                "from": body.ticket.sender,
                "subject": body.ticket.subject,
                "body": body.ticket.body,
                "customer_id": (body.ticket.customer_id or "").strip().upper() or None,
                "kind": "custom",
            })
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
    elif body.ticket_id:
        ticket = next((t for t in items if t["id"].upper() == body.ticket_id.strip().upper()), None)
        if ticket is None:
            raise HTTPException(404, "No ticket with that id in tickets.json")
    else:
        raise HTTPException(422, "Provide ticket_id or ticket")
    session = store.create(user.id, ticket, items, body.mode)
    session.task = asyncio.create_task(_drive(session))
    return session.snapshot()


def _session(run_id: str, user_id: str) -> RunSession:
    session = store.get(run_id, user_id)
    if session is None:
        raise HTTPException(404, "Run not found")
    return session


@router.get("/runs/{run_id}")
async def get_run(run_id: str, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> dict:
    session = store.get(run_id, user.id)
    if session is not None:
        return session.snapshot()
    record = await db.get(RunRecord, run_id)
    if record is None or record.user_id != user.id:
        raise HTTPException(404, "Run not found")
    return record.trace


@router.post("/runs/{run_id}/holds/{hold_id}")
def decide_hold(run_id: str, hold_id: str, body: HoldBody, user: User = Depends(get_current_user)) -> dict:
    session = _session(run_id, user.id)
    if not session.decide(hold_id, body.decision == "approve"):
        raise HTTPException(409, "That hold is not waiting for a decision")
    return {"ok": True, "hold_id": hold_id, "decision": body.decision}


@router.post("/runs/{run_id}/cancel")
def cancel_run(run_id: str, user: User = Depends(get_current_user)) -> dict:
    session = _session(run_id, user.id)
    session.cancel()
    return {"ok": True}


@router.get("/runs/{run_id}/stream")
async def stream_run(run_id: str, token: str = Query("")):
    user_id = decode_token(token)
    if not user_id:
        raise HTTPException(401, "Not authenticated")
    session = _session(run_id, user_id)

    async def events():
        queue = session.subscribe()
        try:
            while True:
                event = await queue.get()
                if event is None:
                    break
                yield {"event": event["type"], "id": str(event["seq"]), "data": json.dumps(event)}
        finally:
            session.unsubscribe(queue)

    return EventSourceResponse(events(), ping=15)


@router.get("/history")
async def history(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> list[dict]:
    rows = await db.execute(
        select(RunRecord).where(RunRecord.user_id == user.id).order_by(RunRecord.created_at.desc()).limit(100)
    )
    return [
        {
            "id": r.id, "ticket_id": r.ticket_id, "subject": r.subject, "kind": r.kind, "mode": r.mode,
            "model": r.model, "status": r.status, "outcome": r.outcome, "summary": r.summary,
            "steps": r.steps, "blocked": r.blocked, "held": r.held,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows.scalars()
    ]
