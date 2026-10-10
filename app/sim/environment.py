from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Asset:
    name: str
    kind: str
    domain: str
    criticality: str


@dataclass(frozen=True)
class Identity:
    username: str
    role: str
    privileged: bool = False


@dataclass(frozen=True)
class Environment:
    company: str
    summary: str
    assets: list[Asset] = field(default_factory=list)
    identities: list[Identity] = field(default_factory=list)
    crown_jewels: list[str] = field(default_factory=list)

    def brief(self) -> str:
        lines = [f"Company: {self.company}", self.summary, "", "Key assets:"]
        for a in self.assets:
            lines.append(f"  - {a.name} ({a.kind}, {a.domain}, criticality={a.criticality})")
        lines.append("Identities:")
        for i in self.identities:
            lines.append(f"  - {i.username} ({i.role}{', PRIVILEGED' if i.privileged else ''})")
        lines.append("Crown-jewel data: " + ", ".join(self.crown_jewels))
        return "\n".join(lines)


NORTHWIND = Environment(
    company="Northwind Robotics",
    summary=(
        "Mid-size SaaS + robotics firm, ~600 employees. Okta SSO, Google Workspace, "
        "AWS prod account, GitHub org, and an internal ML support assistant ('Nova') "
        "that answers customer tickets and can call internal tools."
    ),
    assets=[
        Asset("okta-idp", "identity-provider", "identity", "crown-jewel"),
        Asset("aws-prod", "cloud", "cloud", "crown-jewel"),
        Asset("github-org", "saas", "cloud", "high"),
        Asset("nova-assistant", "saas", "endpoint", "high"),
        Asset("fin-db-01", "server", "endpoint", "crown-jewel"),
        Asset("ws-eng-042", "workstation", "endpoint", "medium"),
        Asset("vpn-gw", "server", "network", "high"),
    ],
    identities=[
        Identity("j.okafor", "Support Engineer"),
        Identity("s.lindqvist", "Staff SRE", privileged=True),
        Identity("a.petrov", "Finance Analyst"),
        Identity("svc-nova", "Service Account (Nova tools)", privileged=True),
        Identity("admin.break-glass", "Emergency Admin", privileged=True),
    ],
    crown_jewels=["customer PII in fin-db-01", "AWS prod credentials", "signing keys in GitHub"],
)
