from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from ..agent.llm import model_status
from ..auth import decode_token, get_current_user
from ..db import SessionLocal, get_db
from ..engine.chat import Chat, store
from ..orm import RunRecord, User
from ..riposte import Policy
from ..riposte.baseline import _PATTERNS
from ..world.seed import COMPANY, KB
from ..world.tickets import load_tickets
from ..world.world import World, tool_catalog

router = APIRouter(prefix="/api")

MODE_INFO = [
    {"id": "off", "label": "Off", "blurb": "No defense. Every tool call the agent makes is executed."},
    {"id": "keyword", "label": "Keyword filter", "blurb": "Scans each ticket for known injection phrases and quarantines matches."},
    {"id": "riposte", "label": "Riposte", "blurb": "Traces where every tool argument came from and blocks or holds calls driven by untrusted content."},
]


class ChatBody(BaseModel):
    mode: Literal["off", "keyword", "riposte"] = "riposte"
    message: str = Field(min_length=1, max_length=6000)


class MessageBody(BaseModel):
    message: str = Field(min_length=1, max_length=6000)


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


async def _persist(chat: Chat) -> None:
    snapshot = chat.snapshot()
    snapshot["live"] = False
    stats = [t["stats"] for t in chat.turns]
    async with SessionLocal() as db:
        await db.merge(RunRecord(
            id=chat.id,
            user_id=chat.user_id,
            ticket_id=next((r["id"] for t in chat.turns for r in t["refs"] if r["kind"] == "ticket"), ""),
            subject=chat.title[:300],
            kind="chat",
            mode=chat.mode,
            model=snapshot["model"],
            status="done",
            outcome=chat.outcome,
            summary=chat.turns[-1]["summary"] if chat.turns else "",
            steps=sum(s["steps"] for s in stats),
            blocked=sum(s["blocked"] for s in stats),
            held=sum(s["held"] for s in stats),
            trace=snapshot,
            created_at=datetime.fromisoformat(chat.created_at),
            finished_at=datetime.fromisoformat(chat.updated_at),
        ))
        await db.commit()


async def _drive(chat: Chat, turn: dict) -> None:
    try:
        await chat.run_turn(turn)
    finally:
        await asyncio.shield(_persist(chat))


def _start(chat: Chat, message: str) -> dict:
    items, _ = load_tickets()
    turn = chat.begin(message.strip(), items)
    chat.task = asyncio.create_task(_drive(chat, turn))
    return chat.snapshot()


def _chat(chat_id: str, user_id: str) -> Chat:
    chat = store.get(chat_id, user_id)
    if chat is None:
        raise HTTPException(404, "That conversation is no longer active")
    return chat


@router.post("/chats")
async def create_chat(body: ChatBody, user: User = Depends(get_current_user)) -> dict:
    return _start(store.create(user.id, body.mode), body.message)


@router.post("/chats/{chat_id}/messages")
async def send_message(chat_id: str, body: MessageBody, user: User = Depends(get_current_user)) -> dict:
    chat = _chat(chat_id, user.id)
    if chat.busy:
        raise HTTPException(409, "Nova is still working on the last message")
    return _start(chat, body.message)


@router.get("/chats/{chat_id}")
async def get_chat(chat_id: str, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> dict:
    chat = store.get(chat_id, user.id)
    if chat is not None:
        return chat.snapshot()
    record = await db.get(RunRecord, chat_id)
    if record is None or record.user_id != user.id or record.kind != "chat":
        raise HTTPException(404, "Conversation not found")
    return record.trace


@router.post("/chats/{chat_id}/holds/{hold_id}")
def decide_hold(chat_id: str, hold_id: str, body: HoldBody, user: User = Depends(get_current_user)) -> dict:
    chat = _chat(chat_id, user.id)
    if not chat.decide(hold_id, body.decision == "approve"):
        raise HTTPException(409, "That hold is not waiting for a decision")
    return {"ok": True, "hold_id": hold_id, "decision": body.decision}


@router.post("/chats/{chat_id}/cancel")
def cancel_chat(chat_id: str, user: User = Depends(get_current_user)) -> dict:
    _chat(chat_id, user.id).cancel()
    return {"ok": True}


@router.get("/chats/{chat_id}/stream")
async def stream_chat(chat_id: str, token: str = Query(""), after: int = Query(-1)):
    user_id = decode_token(token)
    if not user_id:
        raise HTTPException(401, "Not authenticated")
    chat = _chat(chat_id, user_id)

    async def events():
        queue = chat.subscribe(after)
        try:
            while True:
                event = await queue.get()
                if event is None:
                    break
                yield {"event": event["type"], "id": str(event["seq"]), "data": json.dumps(event)}
        finally:
            chat.unsubscribe(queue)

    return EventSourceResponse(events(), ping=15)


@router.get("/history")
async def history(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> list[dict]:
    rows = await db.execute(
        select(RunRecord)
        .where(RunRecord.user_id == user.id, RunRecord.kind == "chat")
        .order_by(RunRecord.finished_at.desc())
        .limit(100)
    )
    return [
        {
            "id": r.id, "title": r.subject, "mode": r.mode, "model": r.model, "outcome": r.outcome,
            "summary": r.summary, "turns": len((r.trace or {}).get("turns", [])),
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "updated_at": r.finished_at.isoformat() if r.finished_at else None,
        }
        for r in rows.scalars()
    ]
