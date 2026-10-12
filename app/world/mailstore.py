from __future__ import annotations

import json
import sqlite3
from pathlib import Path

SCHEMA = (
    "CREATE TABLE IF NOT EXISTS tickets (id TEXT PRIMARY KEY, seq INTEGER NOT NULL, receipt TEXT NOT NULL UNIQUE, data TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS mail (n INTEGER PRIMARY KEY AUTOINCREMENT, ticket_id TEXT NOT NULL, key TEXT NOT NULL UNIQUE, data TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS ticket_status (user_id TEXT NOT NULL, ticket_id TEXT NOT NULL, data TEXT NOT NULL, PRIMARY KEY (user_id, ticket_id))",
)


class MailStore:
    def __init__(self, path: str | Path = ":memory:") -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        if str(path) != ":memory:":
            self.db.execute("PRAGMA journal_mode=WAL")
        with self.db:
            for statement in SCHEMA:
                self.db.execute(statement)

    def close(self) -> None:
        self.db.close()

    def save_ticket(self, ticket: dict, seq: int, receipt: str) -> None:
        with self.db:
            self.db.execute(
                "INSERT INTO tickets(id, seq, receipt, data) VALUES (?, ?, ?, ?)",
                (ticket["id"], seq, receipt, json.dumps(ticket)),
            )

    def save_mail(self, ticket_id: str, key: str, message: dict) -> bool:
        with self.db:
            done = self.db.execute(
                "INSERT OR IGNORE INTO mail(ticket_id, key, data) VALUES (?, ?, ?)",
                (ticket_id, key, json.dumps(message)),
            )
        return done.rowcount == 1

    def save_status(self, user_id: str, ticket_id: str, data: dict) -> None:
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO ticket_status(user_id, ticket_id, data) VALUES (?, ?, ?)",
                (user_id, ticket_id, json.dumps(data)),
            )

    def drop_status(self, user_id: str, ticket_id: str) -> None:
        with self.db:
            self.db.execute("DELETE FROM ticket_status WHERE user_id = ? AND ticket_id = ?", (user_id, ticket_id))

    def statuses(self) -> list[tuple[str, str, dict]]:
        rows = self.db.execute("SELECT user_id, ticket_id, data FROM ticket_status")
        return [(user_id, ticket_id, json.loads(data)) for user_id, ticket_id, data in rows]

    def tickets(self) -> list[tuple[dict, int, str]]:
        rows = self.db.execute("SELECT data, seq, receipt FROM tickets ORDER BY seq")
        return [(json.loads(data), seq, receipt) for data, seq, receipt in rows]

    def mail(self) -> list[tuple[str, dict]]:
        rows = self.db.execute("SELECT ticket_id, data FROM mail ORDER BY n")
        return [(ticket_id, json.loads(data)) for ticket_id, data in rows]
