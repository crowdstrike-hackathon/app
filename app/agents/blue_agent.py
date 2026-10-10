from __future__ import annotations

import json
from typing import Optional

from ..llm.provider import LLMProvider
from ..models import ActionStatus, Investigation, LogEvent, ResponseAction, Verdict

_DECEPTION_TELLS = (
    "spoofed", "stale", "0-byte", "planted", "timestamp skewed",
    "later shown spoofed", "geoip db stale", "lure", "skewed +",
)

_SEVERITY_WEIGHT = {"info": 0, "low": 1, "medium": 3, "high": 6, "critical": 10}

_PLAYBOOK: dict[str, tuple[str, str, str]] = {
    "T1566.002": ("Quarantine Nova assistant", "Pause the Nova assistant's tool access and hold ticket #48213 for review.", "nova-assistant"),
    "T1059": ("Revoke Nova tool scope", "Strip list_secrets from svc-nova until injection is confirmed cleared.", "svc-nova"),
    "T1552.005": ("Rotate exposed AWS secret", "Rotate prod/aws-access-key and audit all GetSecretValue by svc-nova.", "aws-prod"),
    "T1530": ("Lock finance export bucket", "Apply deny policy on s3://northwind-fin-exports and snapshot access logs.", "aws-prod"),
    "T1041": ("Block egress to unknown ASN", "Null-route 185.212.0.0/16 from the Nova subnet and capture the session.", "vpc-gw"),
    "T1621": ("Reset j.okafor + enforce FIDO2", "Force password reset and require phishing-resistant MFA for j.okafor.", "okta-idp"),
    "T1098.005": ("Remove rogue Okta device", "Deregister the new Okta Verify device and terminate active sessions.", "okta-idp"),
    "T1078": ("Revert SRE-prod grant", "Remove j.okafor from SRE-prod and disable the stale auto-approval rule.", "okta-idp"),
    "T1550.001": ("Kill AWS federated session", "Revoke the AssumeRole session and quarantine the SRE-Admin role.", "aws-prod"),
    "T1537": ("Block snapshot sharing", "Revoke the DB snapshot share and SCP-deny external snapshot sharing.", "aws-prod"),
}


def sanitize(events: list[LogEvent]) -> list[dict]:
    return [
        {
            "id": e.id, "turn": e.turn, "domain": e.domain.value, "source": e.source,
            "host": e.host, "actor": e.actor, "message": e.message, "severity": e.severity,
        }
        for e in events
    ]


def _looks_deceptive(e: LogEvent) -> bool:
    m = e.message.lower()
    return any(t in m for t in _DECEPTION_TELLS)


def _actions_for(trusted: list[LogEvent]) -> list[ResponseAction]:
    seen: set[str] = set()
    actions: list[ResponseAction] = []
    for e in trusted:
        if not e.attack_id or e.attack_id in seen or e.attack_id not in _PLAYBOOK:
            continue
        seen.add(e.attack_id)
        title, desc, target = _PLAYBOOK[e.attack_id]
        actions.append(ResponseAction(
            title=title, description=desc, target=target, attack_id=e.attack_id,
            requires_approval=True, status=ActionStatus.proposed,
        ))
    return actions


def heuristic_investigate(turn: int, events: list[LogEvent]) -> Investigation:
    suspected, trusted = [], []
    for e in events:
        if _looks_deceptive(e) or e.attack_id == "DECEPTION":
            suspected.append(e)
        elif _SEVERITY_WEIGHT.get(e.severity, 0) >= 3:
            trusted.append(e)

    score = sum(_SEVERITY_WEIGHT.get(e.severity, 0) for e in trusted)
    has_critical = any(e.severity == "critical" for e in trusted)

    if has_critical or score >= 12:
        verdict, conf = Verdict.malicious, min(0.95, 0.55 + score / 40)
    elif trusted:
        verdict, conf = Verdict.suspicious, min(0.8, 0.3 + score / 30)
    else:
        verdict, conf = Verdict.benign, 0.2

    chain = sorted({f"{e.attack_id} {e.attack_name}" for e in trusted if e.attack_id})
    if verdict == Verdict.malicious:
        headline = "Active intrusion — attack chain confirmed across domains"
    elif verdict == Verdict.suspicious:
        headline = "Suspicious activity correlated; escalating for containment"
    else:
        headline = "Nominal — no trustworthy indicators of compromise"

    reason_bits = []
    if trusted:
        reason_bits.append("Trusted signal: " + "; ".join(sorted({e.message[:90] for e in trusted}))[:600])
    if suspected:
        reason_bits.append(
            f"Discounted {len(suspected)} event(s) as likely planted deception "
            f"(spoofed source / tampered metadata / lure)."
        )
    if chain:
        reason_bits.append("Observed kill-chain: " + " → ".join(chain))
    reasoning = " ".join(reason_bits) or "Only routine activity observed."

    return Investigation(
        turn=turn, verdict=verdict, confidence=round(conf, 2), headline=headline,
        reasoning=reasoning,
        trusted_event_ids=[e.id for e in trusted],
        suspected_decoy_ids=[e.id for e in suspected],
        recommended_actions=_actions_for(trusted),
    )


BLUE_SYSTEM = (
    "You are Riposte, an autonomous SOC analyst (blue team) in a simulated exercise. "
    "You are given a stream of security logs. SOME LOGS ARE ADVERSARY DECEPTION: spoofed "
    "sources, tampered timestamps, planted lures, or benign noise meant to mislead you. You "
    "are NOT told which. Reason carefully; trust corroborated cross-domain signal over any "
    "single sensational line. Respond ONLY with JSON matching this schema: "
    '{"verdict":"benign|suspicious|malicious","confidence":0..1,"headline":str,'
    '"reasoning":str,"trusted_event_ids":[str],"suspected_decoy_ids":[str],'
    '"recommended_actions":[{"title":str,"description":str,"target":str,"attack_id":str}]}. '
    "Recommend response actions ONLY when justified; they will be gated behind human approval."
)


async def live_investigate(provider: LLMProvider, turn: int, events: list[LogEvent], env_brief: str) -> Investigation:
    user = (
        f"Environment:\n{env_brief}\n\n"
        f"Logs observed so far (turn {turn}):\n{json.dumps(sanitize(events), indent=2)}\n\n"
        "Assess the current threat and return the JSON verdict."
    )
    data = await provider.complete_json(BLUE_SYSTEM, user, temperature=0.3)

    actions = [
        ResponseAction(
            title=a.get("title", "Response action"),
            description=a.get("description", ""),
            target=a.get("target", "unknown"),
            attack_id=a.get("attack_id"),
            requires_approval=True,
            status=ActionStatus.proposed,
        )
        for a in data.get("recommended_actions", [])
    ]
    try:
        verdict = Verdict(data.get("verdict", "benign"))
    except ValueError:
        verdict = Verdict.suspicious
    return Investigation(
        turn=turn,
        verdict=verdict,
        confidence=float(data.get("confidence", 0.5)),
        headline=data.get("headline", ""),
        reasoning=data.get("reasoning", ""),
        trusted_event_ids=list(data.get("trusted_event_ids", [])),
        suspected_decoy_ids=list(data.get("suspected_decoy_ids", [])),
        recommended_actions=actions,
    )


async def investigate(turn: int, events: list[LogEvent], env_brief: str, provider: Optional[LLMProvider] = None) -> Investigation:
    if provider is not None:
        try:
            return await live_investigate(provider, turn, events, env_brief)
        except Exception:
            pass
    return heuristic_investigate(turn, events)
