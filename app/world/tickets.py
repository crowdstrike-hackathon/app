from __future__ import annotations

import json
from uuid import uuid4
from pathlib import Path

from ..config import settings
from .seed import CUSTOMERS
from ..riposte.taint import destination_key

_runtime: dict[str, list[dict]] = {}


def add_ticket(raw: dict, user_id: str) -> dict:
    sender = destination_key(raw.get("from", ""))
    customer = next((c for c in CUSTOMERS if c["email"] == sender), None)
    if customer is None:
        raise ValueError("Sender must be one of the three sandbox customers")
    ticket = normalize_ticket({**raw, "id": "TK-" + uuid4().hex[:12].upper(),
                               "customer_id": customer["id"]})
    _runtime.setdefault(user_id, []).append(ticket)
    return ticket

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
    return tickets + list(runtime), {**info, "runtime_count": len(runtime)}
