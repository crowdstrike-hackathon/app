import tempfile
import unittest
from pathlib import Path

import httpx

from app.api import inbound
from app.main import app
from app.world import tickets
from app.world.seed import CUSTOMERS

DANA = CUSTOMERS[0]["email"]
MARCUS = CUSTOMERS[1]["email"]
STRANGER = "ops@lookalike-example.net"


class InboundTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "mailbox.db"
        tickets.open_store(self.path)
        inbound._senders.clear()
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")

    async def asyncTearDown(self):
        await self.client.aclose()
        tickets.open_store(":memory:")
        self.dir.cleanup()

    async def send(self, sender, subject="Support", body="Help please"):
        return await self.client.post("/inbound", json={"from": sender, "subject": subject, "body": body})

    async def test_mail_lands_in_the_shared_inbox(self):
        resp = await self.send(DANA)
        self.assertEqual(resp.status_code, 202)
        data = resp.json()
        self.assertEqual(set(data), {"ticket_id", "receipt"})
        self.assertNotEqual(data["ticket_id"], data["receipt"])
        items, info = tickets.load_tickets("anyone")
        ticket = next(t for t in items if t["id"] == data["ticket_id"])
        self.assertEqual(ticket["kind"], "inbound")
        self.assertEqual(ticket["customer_id"], CUSTOMERS[0]["id"])
        self.assertNotIn("desk", ticket)
        self.assertEqual(info["inbound_count"], 1)

    async def test_unknown_sender_gets_no_account(self):
        data = (await self.send(STRANGER)).json()
        self.assertIsNone(tickets.inbound_ticket(data["ticket_id"])["customer_id"])

    async def test_replies_need_the_receipt_and_matching_sender(self):
        data = (await self.send(MARCUS)).json()
        self.assertTrue(tickets.deliver(data["ticket_id"], {"to": MARCUS, "subject": "Re", "body": "Done"}))
        tickets.deliver(data["ticket_id"], {"to": "someone@else.net", "subject": "x", "body": "not yours"})
        ok = await self.client.get("/inbound/replies", params={"from": MARCUS}, headers={"X-Ticket-ID": data["receipt"]})
        self.assertEqual([m["body"] for m in ok.json()["replies"]], ["Done"])
        by_id = await self.client.get("/inbound/replies", params={"from": MARCUS}, headers={"X-Ticket-ID": data["ticket_id"]})
        self.assertEqual(by_id.status_code, 404)
        wrong_sender = await self.client.get("/inbound/replies", params={"from": DANA}, headers={"X-Ticket-ID": data["receipt"]})
        self.assertEqual(wrong_sender.status_code, 404)

    async def test_inbox_survives_a_restart(self):
        data = (await self.send(DANA)).json()
        tickets.deliver(data["ticket_id"], {"to": DANA, "subject": "Re", "body": "Saved"})
        tickets.open_store(self.path)
        self.assertIsNotNone(tickets.inbound_ticket(data["ticket_id"]))
        self.assertEqual([m["body"] for m in tickets.mail_for(data["ticket_id"], DANA)], ["Saved"])
        self.assertEqual(tickets.ticket_for_receipt(data["receipt"])["id"], data["ticket_id"])

    async def test_rejects_bad_input_and_floods(self):
        self.assertEqual((await self.client.post("/inbound", json={"from": "a@b.co"})).status_code, 422)
        self.assertEqual((await self.send("not-an-address")).status_code, 422)
        codes = [(await self.send("flood@example.com")).status_code for _ in range(15)]
        self.assertIn(429, codes)

    async def test_desk_routes_are_gone(self):
        self.assertEqual((await self.client.post("/inbound/plain", json={})).status_code, 404)
        self.assertEqual((await self.client.get("/api/desks")).status_code, 404)


if __name__ == "__main__":
    unittest.main()
