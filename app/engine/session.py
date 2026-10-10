from __future__ import annotations

import asyncio
import random
from typing import Any, Optional

from ..agents import blue_agent, red_agent
from ..db import SessionLocal
from ..llm.provider import get_blue_provider, get_red_provider
from ..models import (
    ActionStatus,
    Investigation,
    LogEvent,
    ResponseAction,
    Score,
    SessionState,
    Verdict,
)
from ..orm import ActionRow, Event, InvestigationRow, Run
from ..sim.attack_catalog import SCENARIOS, Scenario
from ..sim.environment import NORTHWIND


class Session:
    def __init__(self, scenario: Scenario, mode: str, user_id: str, run_id: str, seed: int | None = None):
        self.scenario = scenario
        self.mode = mode
        self.user_id = user_id
        self.rng = random.Random(seed)
        self.env = NORTHWIND
        self.state = SessionState(
            id=run_id,
            scenario_id=scenario.id,
            scenario_name=scenario.name,
            mode=mode,
            max_turns=len(scenario.stages),
        )
        self._blue = get_blue_provider()
        self._red = get_red_provider()
        self._subscribers: list[asyncio.Queue] = []
        self._lock = asyncio.Lock()

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

    async def step(self) -> Optional[dict]:
        async with self._lock:
            if self.state.turn >= self.state.max_turns:
                self.state.status = "finished"
                await self._emit("finished", self.state.score.model_dump())
                return None

            self.state.status = "running"
            self.state.turn += 1
            turn = self.state.turn

            events, narrative = await red_agent.generate_turn(
                self.scenario, self.env, turn, self.rng, self._red
            )
            self.state.events.extend(events)
            await self._emit(
                "red_turn",
                {"turn": turn, "narrative": narrative, "events": [e.model_dump() for e in events]},
            )

            inv = await blue_agent.investigate(turn, self.state.events, self.env.brief(), self._blue)
            self.state.investigations.append(inv)
            self.state.actions.extend(inv.recommended_actions)
            await self._emit("blue_turn", inv.model_dump())

            self._rescore()
            await self._emit("score", self.state.score.model_dump())

            finished = self.state.turn >= self.state.max_turns
            if finished:
                self.state.status = "finished"

            await self._persist_turn(turn, events, inv, finished)

            if finished:
                await self._emit("finished", self.state.score.model_dump())
            return {"turn": turn, "narrative": narrative}

    async def run(self, delay: float = 1.1) -> None:
        while self.state.status != "finished":
            if await self.step() is None:
                break
            await asyncio.sleep(delay)

    def _rescore(self) -> None:
        latest = self.state.investigations[-1]
        s = Score(turns=self.state.turn)
        real_attack = [e for e in self.state.events if not e.is_decoy and e.attack_id not in (None, "DECEPTION")]
        decoys = [e for e in self.state.events if e.is_decoy]
        s.real_events = len(real_attack)
        s.decoy_events = len(decoys)
        decoy_ids = {e.id for e in decoys}
        real_ids = {e.id for e in real_attack}

        for inv in self.state.investigations:
            if inv.verdict == Verdict.malicious:
                s.detected = True
                s.detection_turn = inv.turn
                break

        flagged = set(latest.suspected_decoy_ids)
        s.decoys_flagged_correct = len(flagged & decoy_ids)
        s.decoys_missed = len(decoy_ids - flagged)
        s.real_flagged_as_decoy = len(flagged & real_ids)

        for inv in self.state.investigations:
            real_by_then = any(e.id in real_ids and e.turn <= inv.turn for e in real_attack)
            if inv.verdict == Verdict.malicious and not real_by_then:
                s.false_positive = True
                break

        blue = 0
        if s.detected and s.detection_turn:
            blue += max(0, 60 - (s.detection_turn - 1) * 10)
        blue += s.decoys_flagged_correct * 8
        blue -= s.decoys_missed * 3
        blue -= s.real_flagged_as_decoy * 6
        if s.false_positive:
            blue -= 25
        s.blue_points = max(0, blue)

        red = self.state.turn * 5
        if s.detected and s.detection_turn:
            red -= max(0, 40 - (s.detection_turn - 1) * 10)
        red += s.decoys_missed * 4
        red += s.real_flagged_as_decoy * 6
        s.red_points = max(0, red)

        self.state.score = s

    def set_action_status(self, action_id: str, status: ActionStatus) -> Optional[ResponseAction]:
        for a in self.state.actions:
            if a.id == action_id:
                a.status = status
                return a
        return None

    async def _persist_turn(self, turn: int, events: list[LogEvent], inv: Investigation, finished: bool) -> None:
        async with SessionLocal() as db:
            for e in events:
                db.add(Event(
                    id=e.id, run_id=self.state.id, turn=e.turn, domain=e.domain.value,
                    source=e.source, host=e.host, actor=e.actor, message=e.message,
                    attack_id=e.attack_id, attack_name=e.attack_name, severity=e.severity,
                    is_decoy=e.is_decoy,
                ))
            db.add(InvestigationRow(
                id=inv.id, run_id=self.state.id, turn=inv.turn, verdict=inv.verdict.value,
                confidence=inv.confidence, headline=inv.headline, reasoning=inv.reasoning,
                trusted_event_ids=inv.trusted_event_ids, suspected_decoy_ids=inv.suspected_decoy_ids,
            ))
            for a in inv.recommended_actions:
                db.add(ActionRow(
                    id=a.id, run_id=self.state.id, title=a.title, description=a.description,
                    target=a.target, attack_id=a.attack_id, status=a.status.value,
                ))
            run = await db.get(Run, self.state.id)
            if run:
                s = self.state.score
                run.turn = self.state.turn
                run.status = self.state.status
                run.blue_points = s.blue_points
                run.red_points = s.red_points
                run.detected = s.detected
                run.detection_turn = s.detection_turn
                run.score = s.model_dump()
                if finished:
                    from datetime import datetime, timezone
                    run.finished_at = datetime.now(timezone.utc)
            await db.commit()

    async def persist_action_status(self, action_id: str, status: ActionStatus) -> None:
        async with SessionLocal() as db:
            row = await db.get(ActionRow, action_id)
            if row:
                row.status = status.value
                await db.commit()


class SessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}

    async def create(self, scenario_id: str, mode: str, user_id: str, seed: int | None = None) -> Session:
        scenario = SCENARIOS.get(scenario_id)
        if scenario is None:
            raise KeyError(scenario_id)
        run_id = random.Random().randbytes(16).hex()
        async with SessionLocal() as db:
            db.add(Run(
                id=run_id, user_id=user_id, scenario_id=scenario.id, scenario_name=scenario.name,
                mode=mode, status="ready", max_turns=len(scenario.stages), score={},
            ))
            await db.commit()
        sess = Session(scenario, mode, user_id, run_id, seed)
        self._sessions[run_id] = sess
        return sess

    def get(self, session_id: str) -> Optional[Session]:
        return self._sessions.get(session_id)


store = SessionStore()
