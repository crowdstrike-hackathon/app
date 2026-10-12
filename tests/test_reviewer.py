import json
import unittest
from unittest.mock import patch

from app.config import settings
from app.engine.chat import Chat
from app.riposte import Policy
from app.world import tickets


def tool(name, **args):
    return {"thought": "next", "tool": name, "args": args}


class Nova:
    def __init__(self, steps, verdict):
        self.steps = steps
        self.verdict = verdict
        self.n = 0
        self.reviews = []

    async def __call__(self, messages, **kwargs):
        first = messages[0]["content"]
        if first.startswith("A support operator"):
            return {"intent": "task"}
        if first.startswith("You review one action"):
            self.reviews.append(messages)
            return self.verdict
        step = self.steps[min(self.n, len(self.steps) - 1)]
        self.n += 1
        return step


class ReviewerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        tickets.open_store(":memory:")
        self.patch = patch.object(settings, "reviewer_enabled", True)
        self.patch.start()

    async def asyncTearDown(self):
        self.patch.stop()

    async def run_turn(self, steps, verdict):
        items, _ = tickets.load_tickets("op")
        chat = Chat("op", "riposte", policy=Policy(confirm_outgoing=False), auto_deny=True)
        turn = chat.begin("Handle @TK-1002", items)
        nova = Nova(steps, verdict)
        with patch("app.engine.chat.chat_json", nova), patch("app.engine.reviewer.chat_json", nova):
            await chat.run_turn(turn)
        calls = [e for e in chat.events if e["type"] == "call"]
        return chat, turn, calls, nova

    async def test_reviewer_can_hold_an_action_the_rules_allow(self):
        steps = [tool("read_ticket", ticket_id="TK-1002"), tool("issue_refund", customer_id="C-1002", amount="150", reason="goodwill"),
                 tool("answer", message="done")]
        chat, turn, calls, nova = await self.run_turn(steps, {"verdict": "hold", "reason": "The operator never asked for a refund."})
        refund = next(c for c in calls if c["tool"] == "issue_refund")
        self.assertEqual(refund["decision"]["action"], "hold")
        self.assertEqual(refund["decision"]["rule"], "reviewer")
        self.assertIn("never asked for a refund", refund["decision"]["reason"])
        self.assertEqual(chat.world.refunds, [])

    async def test_ok_from_the_reviewer_changes_nothing(self):
        steps = [tool("read_ticket", ticket_id="TK-1002"), tool("issue_refund", customer_id="C-1002", amount="150", reason="goodwill"),
                 tool("answer", message="done")]
        chat, turn, calls, nova = await self.run_turn(steps, {"verdict": "ok", "reason": "fine"})
        refund = next(c for c in calls if c["tool"] == "issue_refund")
        self.assertEqual(refund["decision"]["action"], "allow")
        self.assertEqual(len(chat.world.refunds), 1)

    async def test_reviewer_cannot_loosen_a_block_and_is_not_asked(self):
        steps = [tool("read_ticket", ticket_id="TK-1002"), tool("create_login_link", customer_id="C-1001"), tool("answer", message="done")]
        chat, turn, calls, nova = await self.run_turn(steps, {"verdict": "ok", "reason": "approve it"})
        key = next(c for c in calls if c["tool"] == "create_login_link")
        self.assertEqual(key["decision"]["action"], "block")
        self.assertEqual(nova.reviews, [])

    async def test_reviewer_never_sees_the_email(self):
        steps = [tool("read_ticket", ticket_id="TK-1002"), tool("issue_refund", customer_id="C-1002", amount="150", reason="goodwill"),
                 tool("answer", message="done")]
        chat, turn, calls, nova = await self.run_turn(steps, {"verdict": "ok", "reason": "fine"})
        ticket = next(t for t in tickets.load_tickets("op")[0] if t["id"] == "TK-1002")
        seen = json.dumps(nova.reviews)
        self.assertTrue(nova.reviews)
        self.assertNotIn(ticket["body"][:40], seen)

    async def test_reviewer_is_skipped_with_no_defense(self):
        items, _ = tickets.load_tickets("op")
        chat = Chat("op", "off", auto_deny=True)
        turn = chat.begin("Handle @TK-1002", items)
        nova = Nova([tool("read_ticket", ticket_id="TK-1002"), tool("issue_refund", customer_id="C-1002", amount="150", reason="x"),
                     tool("answer", message="done")], {"verdict": "hold", "reason": "no"})
        with patch("app.engine.chat.chat_json", nova), patch("app.engine.reviewer.chat_json", nova):
            await chat.run_turn(turn)
        self.assertEqual(nova.reviews, [])
        self.assertEqual(len(chat.world.refunds), 1)


if __name__ == "__main__":
    unittest.main()
