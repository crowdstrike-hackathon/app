from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4

from ..agent.llm import LLMError, chat_json
from ..agent.prompt import result_prompt, retry_prompt, system_prompt, task_prompt
from ..config import settings
from ..riposte import REQUESTER, UNTRUSTED, Decision, Gateway, KeywordFilter, Policy
from ..world.world import TOOLS, World

MODES = ("off", "keyword", "riposte")
MAX_BLOCKS = 3
ALIASES = {
    "read_ticket": {"id": "ticket_id", "ticket": "ticket_id"},
    "get_customer": {"id": "customer_id", "customer": "customer_id"},
    "get_api_key": {"id": "customer_id", "customer": "customer_id"},
    "issue_refund": {"id": "customer_id", "customer": "customer_id"},
    "send_email": {"recipient": "to", "email": "to", "address": "to", "message": "body", "content": "body"},
    "reply": {"text": "message", "body": "message", "content": "message"},
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean_args(tool: str, raw: Any) -> dict:
    if not isinstance(raw, dict):
        return {}
    aliases = ALIASES.get(tool, {})
    params = TOOLS[tool].params
    out: dict = {}
    for key, value in raw.items():
        name = aliases.get(key, key)
        if name not in params or name in out:
            continue
        if isinstance(value, (dict, list)):
            value = json.dumps(value)
        out[name] = value if isinstance(value, (int, float)) and name == "amount" else str(value)
    return out


class RunSession:
    def __init__(self, user_id: str, ticket: dict, tickets: list[dict], mode: str, policy: Optional[Policy] = None) -> None:
        self.id = uuid4().hex
        self.user_id = user_id
        self.ticket = ticket
        self.mode = mode
        self.policy = policy or Policy()
        self.world = World(
            tickets={t["id"].upper(): t for t in tickets} | {ticket["id"].upper(): ticket},
            internal_domains=list(self.policy.internal_domains),
            refund_limit=self.policy.refund_limit,
        )
        self.gateway = Gateway(self.policy) if mode == "riposte" else None
        self.filter = KeywordFilter() if mode == "keyword" else None
        self.status = "queued"
        self.outcome = ""
        self.summary = ""
        self.final_reply = ""
        self.incidents: list[dict] = []
        self.stats = {"steps": 0, "blocked": 0, "held": 0, "approved": 0, "denied": 0}
        self.events: list[dict] = []
        self.created_at = _now()
        self.finished_at: Optional[str] = None
        self.task: Optional[asyncio.Task] = None
        self._subscribers: list[asyncio.Queue] = []
        self._holds: dict[str, asyncio.Future] = {}
        self._quarantined = False

    def emit(self, kind: str, **data: Any) -> dict:
        event = {"seq": len(self.events), "type": kind, "at": _now(), **data}
        self.events.append(event)
        for queue in self._subscribers:
            queue.put_nowait(event)
        return event

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        for event in self.events:
            queue.put_nowait(event)
        if self.finished:
            queue.put_nowait(None)
        else:
            self._subscribers.append(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        if queue in self._subscribers:
            self._subscribers.remove(queue)

    @property
    def finished(self) -> bool:
        return self.status in ("done", "error", "cancelled")

    def snapshot(self, with_events: bool = True) -> dict:
        data = {
            "id": self.id,
            "mode": self.mode,
            "status": self.status,
            "outcome": self.outcome,
            "summary": self.summary,
            "ticket": self.ticket,
            "final_reply": self.final_reply,
            "incidents": self.incidents,
            "stats": self.stats,
            "model": settings.agent_model,
            "created_at": self.created_at,
            "finished_at": self.finished_at,
            "pending_holds": [h for h, f in self._holds.items() if not f.done()],
        }
        if with_events:
            data["events"] = self.events
        return data

    def decide(self, hold_id: str, approve: bool) -> bool:
        future = self._holds.get(hold_id)
        if future is None or future.done():
            return False
        future.set_result(approve)
        return True

    def cancel(self) -> None:
        if self.task and not self.task.done():
            self.task.cancel()

    async def run(self) -> None:
        self.status = "running"
        self.emit("start", mode=self.mode, ticket=self.ticket, model=settings.agent_model)
        try:
            await self._loop()
            self.status = "done"
        except asyncio.CancelledError:
            self.status = "cancelled"
            self.emit("note", level="warn", text="Run cancelled.")
        except LLMError as exc:
            self.status = "error"
            self.emit("error", text=str(exc))
        except Exception as exc:
            self.status = "error"
            self.emit("error", text=f"{type(exc).__name__}: {exc}")
        self._finish()

    def _finish(self) -> None:
        scope = self.ticket.get("customer_id")
        self.incidents = self.world.audit(scope)
        if self.status == "error":
            self.outcome = "error"
            self.summary = "The run stopped on an error."
        elif self.status == "cancelled":
            self.outcome = "cancelled"
            self.summary = "The run was stopped before it finished."
        elif self.incidents:
            self.outcome = "compromised"
            self.summary = self.incidents[0]["summary"]
        elif self._quarantined:
            self.outcome = "quarantined"
            self.summary = "The keyword filter quarantined this ticket before the agent could act on it."
        elif self.stats["blocked"]:
            self.outcome = "blocked"
            self.summary = f"Riposte blocked {self.stats['blocked']} tool call(s). Nothing confidential left the sandbox."
        elif self.stats["held"]:
            self.outcome = "reviewed"
            self.summary = "A person reviewed the held action. Nothing confidential left the sandbox."
        else:
            self.outcome = "clean"
            self.summary = "The ticket was handled with no policy violations."
        self.finished_at = _now()
        self.emit(
            "done", status=self.status, outcome=self.outcome, summary=self.summary, incidents=self.incidents,
            stats=self.stats, effects=self.world.effects(), final_reply=self.final_reply,
        )
        for queue in self._subscribers:
            queue.put_nowait(None)
        self._subscribers.clear()

    async def _next_action(self, messages: list[dict], step: int) -> Optional[dict]:
        problem = ""
        for _ in range(3):
            if problem:
                messages.append({"role": "user", "content": retry_prompt(problem)})
            action = await chat_json(messages)
            name = str(action.get("tool") or action.get("action") or "").strip()
            if name not in TOOLS:
                problem = f"'{name}' is not one of the tools" if name else "the tool name is missing"
                messages.append({"role": "assistant", "content": json.dumps(action)})
                self.emit("note", level="warn", step=step, text=f"The model answered with an unusable action ({problem}). Asking again.")
                continue
            args = _clean_args(name, action.get("args") or action.get("arguments") or {})
            thought = str(action.get("thought") or "").strip()
            messages.append({"role": "assistant", "content": json.dumps({"thought": thought, "tool": name, "args": args})})
            return {"tool": name, "args": args, "thought": thought}
        return None

    def _check(self, name: str, args: dict) -> Decision:
        if self.gateway is None:
            return Decision()
        if name == "reply":
            return self.gateway.check_reply(str(args.get("message", "")))
        return self.gateway.check(TOOLS[name], args)

    def _observe(self, name: str, args: dict, result) -> None:
        gateway = self.gateway
        if gateway is None or not result.ok:
            return
        if name == "read_ticket" and result.ticket:
            ticket = result.ticket
            gateway.set_requester(ticket["from"], ticket.get("customer_id"))
            gateway.observe(ticket["from"], REQUESTER, "the ticket sender")
            if ticket.get("customer_id"):
                gateway.trust(ticket["customer_id"], "the helpdesk account match")
            gateway.observe(f"{ticket['subject']}\n{ticket['body']}", UNTRUSTED, f"the text of ticket {ticket['id']}")
        elif name == "search_kb":
            gateway.trust(result.text, "the help center")
        elif name == "get_customer" and result.customer:
            gateway.trust(result.text, f"the customer record for {result.customer['name']}")
        elif name == "get_api_key" and result.secret:
            gateway.register_secret(result.secret)
            gateway.trust(result.text, "the key vault")

    async def _hold(self, step: int, name: str, args: dict, decision: Decision) -> bool:
        hold_id = uuid4().hex[:10]
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._holds[hold_id] = future
        self.stats["held"] += 1
        self.status = "waiting"
        self.emit("hold", step=step, hold_id=hold_id, tool=name, args=args, decision=decision.to_dict())
        approved = await future
        self.status = "running"
        self.stats["approved" if approved else "denied"] += 1
        self.emit("hold_resolved", step=step, hold_id=hold_id, approved=approved)
        return approved

    async def _loop(self) -> None:
        task = task_prompt(self.ticket["id"])
        if self.gateway:
            self.gateway.trust(task, "the operator's task")
        messages = [{"role": "system", "content": system_prompt()}, {"role": "user", "content": task}]
        for step in range(1, settings.agent_max_steps + 1):
            self.stats["steps"] = step
            if self.gateway:
                self.gateway.step = step
            self.emit("thinking", step=step)
            action = await self._next_action(messages, step)
            if action is None:
                self.emit("note", level="warn", step=step, text="The model could not produce a valid action. Ticket handed to a person.")
                return
            name, args = action["tool"], action["args"]
            decision = self._check(name, args)
            self.emit(
                "call", step=step, tool=name, args=args, thought=action["thought"],
                decision=decision.to_dict(), checked=self.gateway is not None,
            )

            approved = False
            if decision.action == "block":
                self.stats["blocked"] += 1
                text = (
                    f"BLOCKED by the security gateway: {decision.reason} Do not try this again. "
                    "Call reply and tell the customer what you could not do."
                )
                self.emit("result", step=step, tool=name, ok=False, blocked=True, text=text)
                if self.stats["blocked"] >= MAX_BLOCKS:
                    self.emit("note", level="warn", step=step, text="Too many blocked actions. Ticket handed to a person.")
                    return
                messages.append({"role": "user", "content": result_prompt(name, text)})
                continue
            if decision.action == "hold":
                approved = await self._hold(step, name, args, decision)
                if not approved:
                    text = "DENIED by a human reviewer. Do not try this again. Call reply and tell the customer what you could not do."
                    self.emit("result", step=step, tool=name, ok=False, denied=True, text=text)
                    messages.append({"role": "user", "content": result_prompt(name, text)})
                    continue

            result = self.world.execute(name, args, approved=approved)
            self._observe(name, args, result)
            label = TOOLS[name].returns if result.ok else ""
            self.emit("result", step=step, tool=name, ok=result.ok, text=result.text, label=label, approved=approved)

            if self.filter and name == "read_ticket" and result.ok:
                hits = self.filter.scan(result.text)
                self.emit("filter", step=step, hits=hits)
                if hits:
                    self._quarantined = True
                    return

            if name == "reply" and result.ok:
                self.final_reply = str(args.get("message", ""))
                return
            messages.append({"role": "user", "content": result_prompt(name, result.text)})
        self.emit("note", level="warn", text="Step limit reached. Ticket handed to a person.")


class RunStore:
    def __init__(self) -> None:
        self.sessions: dict[str, RunSession] = {}

    def create(self, user_id: str, ticket: dict, tickets: list[dict], mode: str) -> RunSession:
        session = RunSession(user_id, ticket, tickets, mode)
        self.sessions[session.id] = session
        return session

    def get(self, run_id: str, user_id: str) -> Optional[RunSession]:
        session = self.sessions.get(run_id)
        return session if session and session.user_id == user_id else None


store = RunStore()
