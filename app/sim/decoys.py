from __future__ import annotations

import random

from ..models import Domain, LogEvent

_BENIGN = [
    (Domain.endpoint, "crowdstrike-edr", "Scheduled EDR definition update applied on ws-eng-042", "info"),
    (Domain.auth, "okta", "s.lindqvist successful SSO login from known SF office IP", "info"),
    (Domain.network, "vpc-flow", "Routine outbound to s3.amazonaws.com from backup job", "info"),
    (Domain.cloud, "aws-cloudtrail", "Nightly Lambda rotate-logs executed by svc-ci", "info"),
    (Domain.email, "google-workspace", "Calendar invite accepted by a.petrov", "info"),
    (Domain.endpoint, "crowdstrike-edr", "Chrome auto-update on ws-eng-042", "info"),
]

_DECOYS = [
    (Domain.auth, "okta", "Failed login for admin.break-glass from 10.0.0.9 (spoofed internal source; timestamp skewed +6h)", "medium"),
    (Domain.endpoint, "crowdstrike-edr", "'mimikatz.exe' string seen in s.lindqvist Downloads — file is a 0-byte planted lure", "high"),
    (Domain.network, "ids", "Beacon-like pattern attributed to ws-eng-042 (source IP later shown spoofed)", "high"),
    (Domain.cloud, "aws-cloudtrail", "Console login by a.petrov flagged 'impossible travel' — GeoIP DB stale, user actually at desk", "medium"),
]


def benign_noise(turn: int, n: int = 1, rng: random.Random | None = None) -> list[LogEvent]:
    rng = rng or random
    out = []
    for domain, source, msg, sev in rng.sample(_BENIGN, k=min(n, len(_BENIGN))):
        out.append(LogEvent(turn=turn, domain=domain, source=source, message=msg, severity=sev, is_decoy=False))
    return out


def planted_decoys(turn: int, n: int = 1, rng: random.Random | None = None) -> list[LogEvent]:
    rng = rng or random
    out = []
    for domain, source, msg, sev in rng.sample(_DECOYS, k=min(n, len(_DECOYS))):
        out.append(LogEvent(
            turn=turn, domain=domain, source=source, message=msg, severity=sev,
            attack_id="DECEPTION", attack_name="Adversary Deception", is_decoy=True,
        ))
    return out
