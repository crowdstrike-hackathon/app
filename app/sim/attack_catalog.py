from __future__ import annotations

from dataclasses import dataclass, field

from ..models import Domain


@dataclass(frozen=True)
class EventTemplate:
    domain: Domain
    source: str
    message: str
    severity: str = "info"
    host: str | None = None
    actor: str | None = None
    raw: dict | None = None


@dataclass(frozen=True)
class Stage:
    attack_id: str
    attack_name: str
    tactic: str
    narrative: str
    events: list[EventTemplate] = field(default_factory=list)


@dataclass(frozen=True)
class Scenario:
    id: str
    name: str
    description: str
    stages: list[Stage]


PROMPT_INJECTION = Scenario(
    id="nova-prompt-injection",
    name="Prompt Injection → Cloud Exfil (Nova assistant)",
    description=(
        "An attacker opens a support ticket containing hidden instructions. Northwind's "
        "'Nova' assistant ingests the ticket, is hijacked via indirect prompt injection, "
        "abuses its tool access to read AWS credentials, and stages customer PII for exfil."
    ),
    stages=[
        Stage(
            "T1598", "Phishing for Information", "Reconnaissance",
            "Probe the support assistant's behavior with benign-looking tickets.",
            [EventTemplate(Domain.email, "zendesk", "New support ticket #48213 from external sender gmail address; unusually long body with formatting artifacts", "low", actor="external")],
        ),
        Stage(
            "T1566.002", "Spearphishing Link / Indirect Injection", "Initial Access",
            "Submit a ticket whose body hides instructions aimed at the Nova LLM.",
            [EventTemplate(Domain.endpoint, "nova-assistant", "Nova ingested ticket #48213; prompt guardrail flagged embedded 'ignore previous instructions' pattern but continued", "medium", host="nova-assistant", actor="svc-nova")],
        ),
        Stage(
            "T1059", "Command & Scripting (tool abuse)", "Execution",
            "Injected instructions make Nova call internal tools it shouldn't chain.",
            [EventTemplate(Domain.endpoint, "nova-assistant", "Nova tool-call sequence: read_kb() -> list_secrets() within one ticket resolution; anomalous tool chain", "high", host="nova-assistant", actor="svc-nova")],
        ),
        Stage(
            "T1552.005", "Credentials from Cloud Instance Metadata", "Credential Access",
            "Nova's service account reads AWS credentials via its tool access.",
            [EventTemplate(Domain.cloud, "aws-cloudtrail", "GetSecretValue on prod/aws-access-key by svc-nova from Nova runtime; first use of this secret by this principal", "high", actor="svc-nova")],
        ),
        Stage(
            "T1530", "Data from Cloud Storage", "Collection",
            "Enumerate and pull customer PII from the finance database export bucket.",
            [EventTemplate(Domain.cloud, "aws-cloudtrail", "ListObjects + GetObject burst on s3://northwind-fin-exports (customer PII) by svc-nova, 1,250 objects in 90s", "critical", actor="svc-nova")],
        ),
        Stage(
            "T1041", "Exfiltration Over C2 Channel", "Exfiltration",
            "Stage the data and push it to attacker-controlled storage.",
            [EventTemplate(Domain.network, "vpc-flow", "Outbound 412MB from nova-assistant subnet to 185.212.x.x (unrecognized ASN) over TLS/443", "critical", host="nova-assistant")],
        ),
    ],
)


IDENTITY_COMPROMISE = Scenario(
    id="okta-identity-compromise",
    name="Identity Compromise → Lateral Movement (Okta)",
    description=(
        "A support engineer's Okta session is hijacked via an MFA-fatigue push attack. "
        "The attacker registers a rogue device, escalates toward SRE access, and reaches "
        "the production AWS console."
    ),
    stages=[
        Stage(
            "T1589", "Gather Victim Identity Info", "Reconnaissance",
            "Harvest employee names/emails from public sources.",
            [EventTemplate(Domain.identity, "okta", "Spike in failed logins for j.okafor from 3 new geolocations in 10 min", "low", actor="j.okafor")],
        ),
        Stage(
            "T1621", "Multi-Factor Authentication Request Generation", "Credential Access",
            "Spam MFA pushes until the user approves one (MFA fatigue).",
            [EventTemplate(Domain.identity, "okta", "9 Okta Verify push challenges to j.okafor in 4 min; final challenge APPROVED", "high", actor="j.okafor")],
        ),
        Stage(
            "T1098.005", "Device Registration", "Persistence",
            "Enroll an attacker-controlled device for durable access.",
            [EventTemplate(Domain.identity, "okta", "New Okta Verify device enrolled for j.okafor from 185.212.x.x; prior devices all US-based", "high", actor="j.okafor")],
        ),
        Stage(
            "T1078", "Valid Accounts", "Lateral Movement",
            "Use the hijacked session to request access to SRE tooling.",
            [EventTemplate(Domain.identity, "okta", "j.okafor assigned to 'SRE-prod' group via self-service request auto-approved by stale rule", "high", actor="j.okafor")],
        ),
        Stage(
            "T1550.001", "Use Alternate Auth Material", "Lateral Movement",
            "Pivot into AWS prod with the elevated identity.",
            [EventTemplate(Domain.cloud, "aws-cloudtrail", "AssumeRole into arn:aws:iam::prod:role/SRE-Admin federated from Okta identity j.okafor; new session from EU IP", "critical", actor="j.okafor")],
        ),
        Stage(
            "T1537", "Transfer to Cloud Account", "Exfiltration",
            "Snapshot the finance DB and share it to an external account.",
            [EventTemplate(Domain.cloud, "aws-cloudtrail", "CreateDBSnapshot of fin-db-01 then ModifyDBSnapshotAttribute to share with external AWS account 9032...", "critical", actor="j.okafor")],
        ),
    ],
)


SCENARIOS: dict[str, Scenario] = {
    PROMPT_INJECTION.id: PROMPT_INJECTION,
    IDENTITY_COMPROMISE.id: IDENTITY_COMPROMISE,
}


def list_scenarios() -> list[dict]:
    return [
        {"id": s.id, "name": s.name, "description": s.description, "stages": len(s.stages)}
        for s in SCENARIOS.values()
    ]
