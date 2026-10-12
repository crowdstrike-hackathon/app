from __future__ import annotations

from typing import Optional

from ..models import GuardDecision
from ..sim.tools import TOOLS
from .injection import scan_injection_naive


class NaiveGuard:
    name = "naive"

    def __init__(self) -> None:
        self._tainted = False

    def prescreen(self, ticket) -> Optional[GuardDecision]:
        score, hits = scan_injection_naive(ticket.body)
        if score >= 1:
            self._tainted = True
            return GuardDecision(
                layer="regex-blocklist", allow=True, severity="medium",
                attack_id="T1566.002", attack_name="Prompt Injection (keyword match)",
                reason=f"Ticket matched blocklist phrase ({hits[0]!r}).",
            )
        return None

    def inspect(self, step_index: int, tool_name: str, args: dict) -> GuardDecision:
        tool = TOOLS.get(tool_name)
        if tool is not None and self._tainted and (tool.privileged or tool.egress):
            return GuardDecision(
                step_index=step_index, layer="regex-blocklist", allow=False, severity="medium",
                attack_id="T1059", attack_name="Blocked Tool Call",
                reason=f"{tool_name} blocked because the ticket matched a blocklist phrase.",
            )
        return GuardDecision(step_index=step_index, layer="regex-blocklist", allow=True, severity="info", reason="allowed")
