from __future__ import annotations

from typing import Optional

from ..models import GuardDecision
from ..sim.tools import TOOLS, contains_sensitive, is_external
from .injection import scan_injection


class RiposteGuard:
    name = "riposte"

    def __init__(self, provider=None) -> None:
        self._provider = provider
        self._tainted = False
        self._taint_reason = ""
        self._sensitive_read = False

    def prescreen(self, ticket) -> Optional[GuardDecision]:
        score, hits = scan_injection(ticket.body)
        if score >= 3:
            self._tainted = True
            self._taint_reason = ", ".join(hits[:4])
            return GuardDecision(
                layer="input-firewall",
                allow=True,
                severity="high",
                attack_id="T1566.002",
                attack_name="Indirect Prompt Injection",
                reason=(
                    f"Untrusted ticket content flagged as prompt injection "
                    f"({self._taint_reason}). Context marked tainted; privileged and "
                    f"egress tools will be held for approval."
                ),
            )
        return None

    def inspect(self, step_index: int, tool_name: str, args: dict) -> GuardDecision:
        tool = TOOLS.get(tool_name)
        if tool is None:
            return self._allow(step_index, f"unknown tool {tool_name!r} ignored")

        payload = " ".join(str(v) for v in args.values())

        if tool.egress:
            if contains_sensitive(payload):
                return self._block(
                    step_index, "exfil-detector", "critical", "T1041",
                    "Exfiltration Over Web Service",
                    f"Outbound {tool_name} payload carries a production secret or customer PII.",
                )
            dest = args.get("to") or args.get("url", "")
            if is_external(dest) and self._sensitive_read:
                return self._block(
                    step_index, "exfil-detector", "critical", "T1041",
                    "Exfiltration Over Web Service",
                    f"External egress to {dest!r} right after reading sensitive data — anomalous tool chain.",
                )

        if tool.privileged and self._tainted:
            return self._block(
                step_index, "tool-policy", "high", "T1552.005",
                "Unauthorized Credential Access",
                f"Privileged {tool_name} invoked from injection-tainted context; blocked and escalated.",
            )

        if tool.privileged or tool_name == "get_customer":
            self._sensitive_read = True
        return self._allow(step_index, f"{tool_name} within policy")

    def _block(self, step_index, layer, severity, attack_id, attack_name, reason) -> GuardDecision:
        return GuardDecision(
            step_index=step_index, layer=layer, allow=False, severity=severity,
            attack_id=attack_id, attack_name=attack_name, reason=reason,
        )

    def _allow(self, step_index, reason) -> GuardDecision:
        return GuardDecision(step_index=step_index, layer="tool-policy", allow=True, severity="info", reason=reason)
