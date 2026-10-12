from __future__ import annotations

import hashlib
import json
import secrets
from datetime import datetime, timezone
from pathlib import Path

from ..config import settings
from .mailstore import MailStore
from .seed import CUSTOMERS, SUPPORT_ADDRESS
from ..riposte.taint import destination_key

FIRST_ID = 3001
MAIL_FIELDS = ("from", "to", "subject", "body")

_runtime: dict[str, list[dict]] = {}
_inbound: list[dict] = []
_receipts: dict[str, str] = {}
_mail: dict[str, list[dict]] = {}
_store = MailStore()
_next = FIRST_ID


def open_store(path: str | Path = ":memory:") -> MailStore:
    global _store, _next
    _store.close()
    _store = MailStore(path)
    _inbound.clear()
    _receipts.clear()
    _mail.clear()
    _next = FIRST_ID
    for ticket, seq, receipt in _store.tickets():
        _inbound.append(ticket)
        _receipts[receipt] = ticket["id"]
        _next = max(_next, seq + 1)
    for ticket_id, message in _store.mail():
        _mail.setdefault(ticket_id, []).append(message)
    return _store


def store() -> MailStore:
    return _store


def _take_id() -> tuple[str, int]:
    global _next
    seq = _next
    _next += 1
    return f"TK-{seq}", seq


def _account(sender: str) -> str | None:
    customer = next((c for c in CUSTOMERS if c["email"] == sender), None)
    return customer["id"] if customer else None


def _digest(receipt: str) -> str:
    return hashlib.sha256(receipt.encode("utf-8")).hexdigest()


def add_ticket(raw: dict, user_id: str) -> dict:
    ticket_id, _ = _take_id()
    ticket = normalize_ticket({**raw, "id": ticket_id, "customer_id": _account(destination_key(raw.get("from", "")))})
    _runtime.setdefault(user_id, []).append(ticket)
    return ticket


def receive(raw: dict) -> tuple[dict, str]:
    ticket_id, seq = _take_id()
    ticket = normalize_ticket({
        **raw, "id": ticket_id, "customer_id": _account(destination_key(raw.get("from", ""))),
        "kind": "inbound", "received_at": datetime.now(timezone.utc).isoformat(),
    })
    receipt = "TK-" + secrets.token_urlsafe(24)
    _store.save_ticket(ticket, seq, _digest(receipt))
    _inbound.append(ticket)
    _receipts[_digest(receipt)] = ticket["id"]
    return ticket, receipt


def inbound_ticket(ticket_id: str) -> dict | None:
    return next((t for t in _inbound if t["id"] == ticket_id), None)


def ticket_for_receipt(receipt: str) -> dict | None:
    ticket_id = _receipts.get(_digest(receipt)) if receipt else None
    return inbound_ticket(ticket_id) if ticket_id else None


def deliver(ticket_id: str, message: dict, key: str = "") -> bool:
    ticket = inbound_ticket(ticket_id)
    if ticket is None:
        return False
    mail = {
        "from": str(message.get("from") or SUPPORT_ADDRESS).strip(),
        "to": destination_key(str(message.get("to") or "")),
        "subject": str(message.get("subject") or "").strip() or f"Re: {ticket['subject']}",
        "body": str(message.get("body") or "").strip(),
    }
    if "@" not in mail["to"] or not all(mail[name] for name in MAIL_FIELDS):
        return False
    if not _store.save_mail(ticket_id, key or secrets.token_hex(8), mail):
        return False
    _mail.setdefault(ticket_id, []).append(mail)
    return True


def mail_for(ticket_id: str, address: str) -> list[dict]:
    return [{name: m[name] for name in MAIL_FIELDS} for m in _mail.get(ticket_id, []) if m["to"] == address]


def mail_count(ticket_id: str) -> int:
    return len(_mail.get(ticket_id, []))


FIELDS = ("id", "from", "subject", "body")


def tickets_file() -> Path | None:
    for candidate in (settings.tickets_path, settings.tickets_fallback_path):
        path = settings.resolve(candidate)
        if path.is_file():
            return path
    return None


def normalize_ticket(raw: dict, index: int = 0) -> dict:
    ticket = {
        "id": str(raw.get("id") or f"TK-{9000 + index}").strip(),
        "from": str(raw.get("from") or raw.get("sender") or "").strip(),
        "customer_id": (str(raw["customer_id"]).strip() if raw.get("customer_id") else None),
        "subject": str(raw.get("subject") or "").strip(),
        "body": str(raw.get("body") or "").strip(),
        "kind": str(raw.get("kind") or "").strip().lower(),
        "title": str(raw.get("title") or raw.get("subject") or "").strip(),
        "note": str(raw.get("note") or "").strip(),
    }
    if raw.get("received_at"):
        ticket["received_at"] = raw["received_at"]
    missing = [f for f in FIELDS if not ticket[f]]
    if missing:
        raise ValueError(f"ticket {ticket['id']} is missing {', '.join(missing)}")
    return ticket


def _load_file_tickets() -> tuple[list[dict], dict]:
    path = tickets_file()
    if path is None:
        return [], {"path": settings.tickets_path, "found": False, "errors": []}
    info: dict = {"path": path.name, "found": True, "errors": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        info["errors"].append(f"{path.name}: {exc}")
        return [], info
    rows = data.get("tickets", []) if isinstance(data, dict) else data
    tickets: list[dict] = []
    seen: set[str] = set()
    for i, raw in enumerate(rows if isinstance(rows, list) else []):
        try:
            ticket = normalize_ticket(raw, i)
        except (ValueError, AttributeError, TypeError) as exc:
            info["errors"].append(str(exc))
            continue
        if ticket["id"] in seen:
            info["errors"].append(f"duplicate ticket id {ticket['id']}")
            continue
        seen.add(ticket["id"])
        tickets.append(ticket)
    return tickets, info


def load_tickets(user_id: str = "") -> tuple[list[dict], dict]:
    tickets, info = _load_file_tickets()
    runtime = _runtime.get(user_id, [])
    return tickets + list(runtime) + list(_inbound), {**info, "runtime_count": len(runtime), "inbound_count": len(_inbound)}
