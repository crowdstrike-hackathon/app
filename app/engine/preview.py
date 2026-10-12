from __future__ import annotations

import asyncio
import time

from ..config import settings
from ..world.tickets import load_tickets
from .chat import Chat

_cache: dict[tuple[str, str], dict] = {}
_running: dict[tuple[str, str], asyncio.Task] = {}
TTL = 600


def _summarise(chat: Chat, turn: dict) -> dict:
    steps = []
    for event in chat.events:
        if event["type"] != "call" or event.get("turn") != turn["index"] or event["tool"] == "read_ticket":
            continue
        decision = event.get("decision") or {}
        action = decision.get("action") or "allow"
        verdict = {"block": "blocked", "hold": "asks you"}.get(action, "allowed")
        steps.append({
            "tool": event["tool"],
            "args": event.get("args") or {},
            "thought": event.get("thought") or "",
            "verdict": verdict,
            "rule": decision.get("rule") or "",
            "title": decision.get("title") or "",
            "reason": decision.get("reason") or "",
        })
    return {
        "steps": steps,
        "answer": turn.get("answer") or "",
        "status": turn.get("status") or "",
        "model": settings.agent_model,
        "created_at": time.time(),
    }


async def _run(user_id: str, ticket_id: str) -> dict:
    items, _ = load_tickets(user_id)
    chat = Chat(user_id, "riposte")
    chat.preview = True
    turn = chat.begin(f"Handle @{ticket_id}", items)
    await chat.run_turn(turn)
    return _summarise(chat, turn)


async def preview(user_id: str, ticket_id: str, fresh: bool = False) -> dict:
    key = (user_id, ticket_id)
    cached = _cache.get(key)
    if cached and not fresh and time.time() - cached["created_at"] < TTL:
        return cached
    task = _running.get(key)
    if task is None or task.done():
        task = asyncio.create_task(_run(user_id, ticket_id))
        _running[key] = task
    try:
        result = await task
    finally:
        if _running.get(key) is task and task.done():
            _running.pop(key, None)
    _cache[key] = result
    return result
