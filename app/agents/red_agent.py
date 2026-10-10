from __future__ import annotations

import random
from typing import Optional

from ..llm.provider import LLMProvider
from ..models import LogEvent
from ..sim.attack_catalog import Scenario, Stage
from ..sim.decoys import benign_noise, planted_decoys
from ..sim.environment import Environment


def _events_from_stage(stage: Stage, turn: int) -> list[LogEvent]:
    out = []
    for t in stage.events:
        out.append(LogEvent(
            turn=turn, domain=t.domain, source=t.source, host=t.host, actor=t.actor,
            message=t.message, severity=t.severity, attack_id=stage.attack_id,
            attack_name=stage.attack_name, raw=t.raw or {}, is_decoy=False,
        ))
    return out


RED_SYSTEM = (
    "You are the red-team operator in a CONTROLLED, SIMULATED security exercise. "
    "You never touch real systems; you only decide which attacker technique comes "
    "next and describe the LOG TELEMETRY a defender would observe. "
    "Respond ONLY with JSON: "
    '{"narrative": str, "log_message": str, "severity": "low|medium|high|critical"}. '
    "Pick wording consistent with the given MITRE technique. Do not include exploit "
    "code, payloads, or commands — only the observable log line."
)


async def _author_stage_live(provider: LLMProvider, env: Environment, stage: Stage, turn: int) -> list[LogEvent]:
    user = (
        f"{env.brief()}\n\n"
        f"Current attacker step (turn {turn}):\n"
        f"  technique: {stage.attack_id} {stage.attack_name} (tactic: {stage.tactic})\n"
        f"  intent: {stage.narrative}\n\n"
        "Produce the single most incriminating log line a SIEM would record for this step."
    )
    data = await provider.complete_json(RED_SYSTEM, user, temperature=0.9)
    base = _events_from_stage(stage, turn)
    if base and data.get("log_message"):
        base[0].message = str(data["log_message"])[:400]
        if data.get("severity") in {"low", "medium", "high", "critical"}:
            base[0].severity = data["severity"]
    return base


async def generate_turn(
    scenario: Scenario,
    env: Environment,
    turn_index: int,
    rng: random.Random,
    provider: Optional[LLMProvider] = None,
) -> tuple[list[LogEvent], str]:
    stage = scenario.stages[turn_index - 1]

    if provider is not None:
        try:
            real = await _author_stage_live(provider, env, stage, turn_index)
        except Exception:
            real = _events_from_stage(stage, turn_index)
    else:
        real = _events_from_stage(stage, turn_index)

    events = list(real)
    events += benign_noise(turn_index, n=rng.randint(1, 2), rng=rng)
    if turn_index >= 2:
        events += planted_decoys(turn_index, n=rng.randint(0, 2), rng=rng)

    rng.shuffle(events)
    return events, stage.narrative
