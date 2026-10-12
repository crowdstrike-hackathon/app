from __future__ import annotations

import asyncio
import random
from datetime import datetime, timezone
from typing import Any, Optional

from ..agents import nova_agent
from ..db import SessionLocal
from ..guard import build_guard
from ..llm.provider import LLMProvider
from ..models import AgentRunState
from ..orm import AgentRun
from ..sim.tickets import TICKETS_BY_ID, Ticket, synthesize_ticket


class AgentSession:
    def __init__(self, run_id: str, user_id: str, ticket: Ticket, defense: bool, provider: Optional[LLMProvider]):
        self.id = run_id
        self.user_id = user_id
        self.ticket = ticket
        self.defense = defense
        self._provider = provider
        self._guard = build_guard("riposte", provider) if defense else None
        self.state = AgentRunState(
            id=run_id, ticket_id=ticket.id, subject=ticket.subject,
            defense=defense, mode="live" if provider is not None else "mock",
        )
        self._subscribers: list[asyncio.Queue] = []
        self._started = False

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        if q in self._subscribers:
            self._subscribers.remove(q)

    async def _emit(self, kind: str, payload: Any) -> None:
        for q in list(self._subscribers):
            await q.put({"type": kind, "payload": payload})

    async def run(self) -> None:
        if self._started:
            return
        self._started = True

        async def emit(kind: str, payload: dict) -> None:
            await self._emit(kind, payload)

        self.state = await nova_agent.run(
            self.ticket, self._provider, self._guard, emit=emit, run_id=self.id
        )
        await self._persist()

    async def _persist(self) -> None:
        async with SessionLocal() as db:
            row = await db.get(AgentRun, self.id)
            if row:
                s = self.state
                row.status = "finished"
                row.outcome = s.outcome
                row.step_count = len(s.steps)
                row.blocked_count = sum(1 for st in s.steps if st.blocked)
                row.final_answer = s.final_answer
                row.trace = s.model_dump()
                row.finished_at = datetime.now(timezone.utc)
            await db.commit()


class AgentSessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, AgentSession] = {}

    async def create(
        self,
        user_id: str,
        defense: bool,
        provider: Optional[LLMProvider],
        ticket_id: Optional[str] = None,
        subject: str = "",
        body: str = "",
    ) -> AgentSession:
        ticket = TICKETS_BY_ID.get(ticket_id) if ticket_id else None
        if ticket is None:
            if not body:
                raise KeyError(ticket_id or "no ticket")
            ticket = synthesize_ticket(subject, body)
        run_id = random.Random().randbytes(16).hex()
        async with SessionLocal() as db:
            db.add(AgentRun(
                id=run_id, user_id=user_id, ticket_id=ticket.id, subject=ticket.subject,
                defense=defense, mode="live" if provider is not None else "mock", status="ready",
            ))
            await db.commit()
        sess = AgentSession(run_id, user_id, ticket, defense, provider)
        self._sessions[run_id] = sess
        return sess

    def get(self, session_id: str) -> Optional[AgentSession]:
        return self._sessions.get(session_id)


store = AgentSessionStore()


class RedTeamCampaign:
    def __init__(self, cid: str, user_id: str, n: int, red_provider: Optional[LLMProvider]):
        self.id = cid
        self.user_id = user_id
        self.n = n
        self._red = red_provider
        self._subscribers: list[asyncio.Queue] = []
        self._started = False
        self.report: Optional[dict] = None

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        if q in self._subscribers:
            self._subscribers.remove(q)

    async def _emit(self, kind: str, payload: Any) -> None:
        for q in list(self._subscribers):
            await q.put({"type": kind, "payload": payload})

    async def run(self) -> None:
        if self._started:
            return
        self._started = True
        from ..agents import red_fuzzer

        async def emit(kind: str, payload: dict) -> None:
            await self._emit(kind, payload)

        self.report = await red_fuzzer.run_campaign(self.n, self._red, emit)


class CampaignStore:
    def __init__(self) -> None:
        self._campaigns: dict[str, RedTeamCampaign] = {}

    def create(self, user_id: str, n: int, red_provider: Optional[LLMProvider]) -> RedTeamCampaign:
        cid = random.Random().randbytes(12).hex()
        c = RedTeamCampaign(cid, user_id, n, red_provider)
        self._campaigns[cid] = c
        return c

    def get(self, cid: str) -> Optional[RedTeamCampaign]:
        return self._campaigns.get(cid)


campaigns = CampaignStore()
