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

from ..agent import runtime
from ..agent.llm import LLMError, model_status
from ..auth import decode_token, get_current_user
from ..db import SessionLocal, get_db
from ..engine.chat import Chat, store
from ..engine.preview import preview
from ..orm import RunRecord, User
from ..riposte import Policy
from ..riposte.baseline import _PATTERNS
from ..world.seed import COMPANY, KB
from ..world.tickets import add_ticket, load_tickets
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


class ChatPatch(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=300)
    archived: Optional[bool] = None


class AgentBody(BaseModel):
    base_url: str = Field(min_length=1, max_length=300)
    model: str = Field(min_length=1, max_length=120)
    api_key: str = Field(default="", max_length=400)


class TicketBody(BaseModel):
    sender: str = Field(alias="from", min_length=1, max_length=200)
    subject: str = Field(min_length=1, max_length=300)
    body: str = Field(min_length=1, max_length=16000)


def _agent_values(body: AgentBody) -> tuple[str, str, str]:
    base_url = body.base_url.strip().rstrip("/")
    if not base_url.startswith(("http://", "https://")):
        raise HTTPException(422, "The base URL has to start with http:// or https://")
    model = body.model.strip()
    if not model:
        raise HTTPException(422, "A model name is required")
    return base_url, model, body.api_key.strip()


@router.get("/settings/agent")
async def get_agent_settings(user: User = Depends(get_current_user)) -> dict:
    return {**runtime.describe(), "status": await model_status()}


@router.post("/settings/agent/test")
async def test_agent_settings(body: AgentBody, user: User = Depends(get_current_user)) -> dict:
    base_url, model, api_key = _agent_values(body)
    return await model_status(base_url, model, api_key)


@router.put("/settings/agent")
async def save_agent_settings(body: AgentBody, user: User = Depends(get_current_user)) -> dict:
    if any(chat.busy for chat in store.chats.values()):
        raise HTTPException(409, "Nova is in the middle of a conversation. Try again when it finishes.")
    runtime.save(*_agent_values(body))
    return {**runtime.describe(), "status": await model_status()}


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
    items, info = load_tickets(user.id)
    return {"tickets": items, "file": info}


@router.post("/tickets/{ticket_id}/preview")
async def preview_ticket(ticket_id: str, fresh: bool = Query(False), user: User = Depends(get_current_user)) -> dict:
    items, _ = load_tickets(user.id)
    if not any(t["id"].upper() == ticket_id.upper() for t in items):
        raise HTTPException(404, "No ticket with that id")
    try:
        return await preview(user.id, ticket_id.upper(), fresh)
    except LLMError as exc:
        raise HTTPException(502, str(exc)) from exc


@router.post("/tickets")
def create_ticket(body: TicketBody, user: User = Depends(get_current_user)):
    try:
        return add_ticket(body.model_dump(by_alias=True), user.id)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


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
    items, _ = load_tickets(chat.user_id)
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


async def _record(chat_id: str, user: User, db: AsyncSession) -> RunRecord:
    record = await db.get(RunRecord, chat_id)
    if record is None or record.user_id != user.id or record.kind != "chat":
        raise HTTPException(404, "Conversation not found")
    return record


@router.patch("/chats/{chat_id}")
async def update_chat(chat_id: str, body: ChatPatch, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> dict:
    record = await _record(chat_id, user, db)
    if body.title is not None:
        title = body.title.strip()
        if not title:
            raise HTTPException(422, "A conversation needs a name")
        record.subject = title
        live = store.get(chat_id, user.id)
        if live is not None:
            live.title = title
    if body.archived is not None:
        record.status = "archived" if body.archived else "done"
    await db.commit()
    return {"id": record.id, "title": record.subject, "archived": record.status == "archived"}


@router.delete("/chats/{chat_id}")
async def delete_chat(chat_id: str, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> dict:
    record = await _record(chat_id, user, db)
    live = store.get(chat_id, user.id)
    if live is not None:
        if live.busy:
            raise HTTPException(409, "Nova is still working in that conversation")
        store.chats.pop(chat_id, None)
    await db.delete(record)
    await db.commit()
    return {"ok": True}


@router.post("/chats/{chat_id}/holds/{hold_id}")
async def decide_hold(chat_id: str, hold_id: str, body: HoldBody, user: User = Depends(get_current_user)) -> dict:
    chat = _chat(chat_id, user.id)
    if not chat.decide(hold_id, body.decision == "approve", by=user.email):
        raise HTTPException(409, "That hold is not waiting for a decision")
    return {"ok": True, "hold_id": hold_id, "decision": body.decision}


@router.post("/chats/{chat_id}/cancel")
async def cancel_chat(chat_id: str, user: User = Depends(get_current_user)) -> dict:
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
            "id": r.id, "title": r.subject, "archived": r.status == "archived", "mode": r.mode, "model": r.model, "outcome": r.outcome,
            "summary": r.summary, "turns": len((r.trace or {}).get("turns", [])),
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "updated_at": r.finished_at.isoformat() if r.finished_at else None,
        }
        for r in rows.scalars()
    ]
