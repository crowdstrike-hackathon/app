from __future__ import annotations

from datetime import datetime, timezone

from ..world.tickets import set_status, status_of
from .chat import Chat


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def steps_for(chat: Chat, index: int) -> list[dict]:
    resolved = {e["step"]: e for e in chat.events if e["type"] == "hold_resolved" and e.get("turn") == index}
    results = {e["step"]: e for e in chat.events if e["type"] == "result" and e.get("turn") == index}
    steps = []
    for event in chat.events:
        if event["type"] != "call" or event.get("turn") != index or event["tool"] == "read_ticket":
            continue
        decision = event.get("decision") or {}
        action = decision.get("action") or "allow"
        result = results.get(event["step"]) or {}
        if action == "block":
            verdict = "blocked"
        elif action == "hold":
            hold = resolved.get(event["step"])
            verdict = "pending" if hold is None else "approved" if hold.get("approved") else "denied"
        else:
            verdict = "done" if result.get("ok") else "failed"
        steps.append({
            "tool": event["tool"], "args": event.get("args") or {}, "verdict": verdict,
            "title": decision.get("title") or "", "reason": decision.get("reason") or "",
            "rule": decision.get("rule") or "",
        })
    return steps


def tickets_in(chat: Chat, turn: dict) -> list[str]:
    found = [r["id"].upper() for r in turn.get("refs", []) if r.get("kind") == "ticket"]
    ok = {e["step"] for e in chat.events if e["type"] == "result" and e.get("turn") == turn["index"] and e.get("ok")}
    for event in chat.events:
        if event["type"] == "call" and event.get("turn") == turn["index"] and event["tool"] == "read_ticket" and event["step"] in ok:
            ticket_id = str((event.get("args") or {}).get("ticket_id", "")).strip().upper()
            if ticket_id and ticket_id not in found:
                found.append(ticket_id)
    return found


def mark_handling(chat: Chat, turn: dict) -> None:
    for ticket_id in tickets_in(chat, turn):
        previous = status_of(chat.user_id, ticket_id) or {}
        set_status(chat.user_id, ticket_id, {
            **previous, "state": "handling", "chat_id": chat.id, "mode": chat.mode, "turn": turn["index"],
            "updated_at": _now(),
        })


def record_turn(chat: Chat, turn: dict) -> None:
    if not turn.get("acted") and turn.get("status") == "done":
        return
    for ticket_id in tickets_in(chat, turn):
        previous = status_of(chat.user_id, ticket_id) or {}
        runs = previous.get("runs", [])
        entry = {
            "chat_id": chat.id, "turn": turn["index"], "mode": chat.mode, "model": chat.snapshot(False)["model"],
            "outcome": turn.get("outcome", ""), "summary": turn.get("summary", ""), "answer": turn.get("answer", ""),
            "steps": steps_for(chat, turn["index"]), "finished_at": _now(),
        }
        set_status(chat.user_id, ticket_id, {
            "state": "done", "chat_id": chat.id, "mode": chat.mode, "turn": turn["index"],
            "outcome": entry["outcome"], "summary": entry["summary"], "updated_at": entry["finished_at"],
            "runs": (runs + [entry])[-10:],
        })
