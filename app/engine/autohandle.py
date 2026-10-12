from __future__ import annotations

import asyncio
import logging
from typing import Optional

from sqlalchemy import select

from ..config import settings
from ..db import SessionLocal
from ..orm import User
from ..world.tickets import load_tickets
from .chat import store

log = logging.getLogger("riposte.autohandle")

_queue: Optional[asyncio.Queue] = None
_worker: Optional[asyncio.Task] = None


def enabled() -> bool:
    return settings.auto_handle_inbound and bool(settings.auto_handle_as.strip())


async def _owner() -> Optional[str]:
    async with SessionLocal() as db:
        row = await db.execute(select(User).where(User.email == settings.auto_handle_as.strip().lower()))
        user = row.scalars().first()
    return user.id if user else None


async def _handle(ticket_id: str) -> None:
    from ..api.routes import _drive

    owner = await _owner()
    if owner is None:
        log.warning("auto-handle skipped %s: no console account %s", ticket_id, settings.auto_handle_as)
        return
    chat = store.create(owner, "riposte")
    chat.policy.confirm_outgoing = False
    chat.hold_timeout = settings.auto_handle_hold_seconds or None
    items, _ = load_tickets(owner)
    turn = chat.begin(f"Handle @{ticket_id}", items)
    chat.task = asyncio.create_task(_drive(chat, turn))
    await asyncio.gather(chat.task, return_exceptions=True)


async def _work() -> None:
    while True:
        ticket_id = await _queue.get()
        try:
            await _handle(ticket_id)
        except Exception:
            log.exception("auto-handle failed for %s", ticket_id)
        finally:
            _queue.task_done()


def submit(ticket_id: str) -> None:
    global _queue, _worker
    if not enabled():
        return
    if _queue is None:
        _queue = asyncio.Queue()
    if _worker is None or _worker.done():
        _worker = asyncio.create_task(_work())
    _queue.put_nowait(ticket_id)
