from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Optional

from .taint import Record, Secret, Span, destination_key, domain_of

TRUSTED = "trusted"
UNTRUSTED = "untrusted"
REQUESTER = "requester"
UNKNOWN = "unknown"


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    params: tuple[str, ...]
    returns: str = TRUSTED
    destination: Optional[str] = None
    payload: tuple[str, ...] = ()
    scope: Optional[str] = None
    amount: Optional[str] = None
    confidential: bool = False
    contact: Optional[str] = None

    @property
    def sends(self) -> bool:
        return self.destination is not None


@dataclass
class Policy:
    internal_domains: list[str] = field(default_factory=lambda: ["northwind.io"])
    refund_limit: float = 200.0
    enforce_scope: bool = True
    hold_unverified: bool = True
    confirm_outgoing: bool = True

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Evidence:
    arg: str
    value: str
    source: str
    origins: list[str] = field(default_factory=list)


@dataclass
class Decision:
    action: str = "allow"
    rule: str = ""
    title: str = ""
    reason: str = ""
    evidence: list[Evidence] = field(default_factory=list)

    @property
    def allowed(self) -> bool:
        return self.action == "allow"

    def to_dict(self) -> dict:
        return asdict(self)


class Gateway:
    def __init__(self, policy: Optional[Policy] = None) -> None:
        self.policy = policy or Policy()
        self.spans: list[Span] = []
        self.secrets: list[Secret] = []
        self.read_owners: dict[str, Secret] = {}
        self.requester: Optional[str] = None
        self.scope: Optional[str] = None
        self.authorized = True
        self.step = 0
        self.refunded: dict[str, float] = {}
        self.records: dict[str, Record] = {}

    def trust(self, text: str, origin: str) -> None:
        self.spans.append(Span(text, TRUSTED, origin, self.step))

    def observe(self, text: str, label: str, origin: str) -> None:
        self.spans.append(Span(text, label, origin, self.step))

    def set_requester(self, address: str, scope: Optional[str]) -> None:
        if self.requester is None:
            self.requester = destination_key(address)
        if self.scope is None and scope:
            self.scope = scope

    @staticmethod
    def parse_amount(value: Any) -> float:
        try:
            return float(str(value).replace("$", "").replace(",", ""))
        except ValueError:
            return 0.0

    def record_refund(self, customer_id: str, amount: float) -> None:
        key = str(customer_id).strip().upper()
        self.refunded[key] = self.refunded.get(key, 0.0) + amount

    def register_record(self, record: Record) -> None:
        self.records[record.owner_id] = record

    def _record_leak(self, text: str, dest: str, channel: str, evidence: list[Evidence]) -> Optional[Decision]:
        for record in self.records.values():
            if dest in record.allowed or self._is_internal(dest):
                continue
            hits = record.found_in(text)
            if hits:
                return Decision(
                    "block", "record", f"{record.owner_name}'s customer details would leave to the wrong person",
                    f"The {channel} contains {record.owner_name}'s {', '.join(hits)} from their customer record. "
                    f"Those details may only go to {', '.join(record.allowed)}, and this goes to "
                    f"{dest or 'an unknown sender'}.",
                    evidence,
                )
        return None

    def flows(self, tool: Tool, args: dict, rule: str = "") -> dict:
        """Where each argument of this call came from, for the flow map. Records only, never blocks."""
        out: list[dict] = []
        seen: set[tuple] = set()

        def add(arg: str, origin: str, trust: str, carries: str) -> None:
            key = (arg, origin, carries)
            if key not in seen:
                seen.add(key)
                out.append({"arg": arg, "origin": origin, "trust": trust, "carries": carries})

        free_text = set(tool.payload) | {"message"}
        for e in self.annotate(tool, args):
            # Message text is checked for secrets and customer details below; tracing its words would link
            # everyday phrases ("API key") to whatever source happens to contain them.
            if e.arg not in free_text and e.source in (TRUSTED, UNTRUSTED, REQUESTER):
                for origin in e.origins:
                    add(e.arg, origin, e.source, "value")
        for name in tool.params:
            text = str(args.get(name, "") or "")
            if not text:
                continue
            for secret in self.secrets:
                if secret.found_in(text):
                    add(name, "the sign-in service", TRUSTED, "secret")
            for record in self.records.values():
                if record.found_in(text):
                    add(name, f"the customer record for {record.owner_name}", TRUSTED, "record")
        if rule == "twin" and self.secrets:
            add(tool.params[0] if tool.params else "message", "the sign-in service", TRUSTED, "derived")
        to = None
        if tool.destination:
            to = destination_key(str(args.get(tool.destination, ""))) or None
        elif tool.name == "reply":
            to = self.requester
        return {"flows": out, "to": to}

    def secrets_at_risk(self, dest: str) -> list[Secret]:
        """Secrets the agent has read that this destination is not allowed to learn anything about."""
        if dest and self._is_internal(dest):
            return []
        return [s for s in self.secrets if dest not in s.allowed]

    def register_secret(self, secret: Secret) -> None:
        self.secrets.append(secret)
        self.read_owners[secret.owner_id] = secret

    def trace(self, value: Any) -> tuple[str, list[str]]:
        text = str(value)
        hits: dict[str, list[str]] = {TRUSTED: [], UNTRUSTED: [], REQUESTER: []}
        for span in self.spans:
            if span.contains(text) and span.origin not in hits[span.label]:
                hits[span.label].append(span.origin)
        if hits[TRUSTED]:
            return TRUSTED, hits[TRUSTED]
        if hits[UNTRUSTED]:
            return UNTRUSTED, hits[UNTRUSTED]
        if hits[REQUESTER]:
            return REQUESTER, hits[REQUESTER]
        return UNKNOWN, []

    def annotate(self, tool: Tool, args: dict) -> list[Evidence]:
        out = []
        for name in tool.params:
            if name not in args or args[name] in ("", None):
                continue
            value = str(args[name])
            probe = destination_key(value) if name == tool.destination else value
            source, origins = self.trace(probe) if len(value) <= 200 else (UNKNOWN, [])
            out.append(Evidence(name, value[:240], source, origins))
        return out

    def _secrets_in(self, text: str) -> list[tuple[Secret, str]]:
        found = []
        for secret in self.secrets:
            how = secret.found_in(text)
            if how:
                found.append((secret, how))
        return found

    def _is_internal(self, dest: str) -> bool:
        dom = domain_of(dest)
        return any(dom == d or dom.endswith("." + d) for d in self.policy.internal_domains)

    def _unrequested(self, name: str, evidence: list[Evidence]) -> Decision:
        return Decision(
            "block", "intent", "The operator did not ask for this",
            f"The operator asked a question and did not ask for any action. {name} was prompted by content "
            f"the agent read, not by the operator.",
            evidence,
        )

    def _confirm(self, name: str, evidence: list[Evidence]) -> Decision:
        if not self.policy.confirm_outgoing:
            return Decision(evidence=evidence)
        return Decision(
            "hold", "outgoing", "A message to a customer is ready to send",
            f"{name} would send this to someone outside {', '.join(self.policy.internal_domains)}. "
            f"Approve it to send it, or deny it to stop it.",
            evidence,
        )

    def check(self, tool: Tool, args: dict) -> Decision:
        evidence = self.annotate(tool, args)

        if not self.authorized and (tool.sends or tool.amount or tool.confidential or tool.contact):
            return self._unrequested(tool.name, evidence)

        if tool.scope and self.policy.enforce_scope and self.scope:
            asked = str(args.get(tool.scope, "")).strip().upper()
            if tool.confidential and asked and asked != self.scope.upper():
                return Decision(
                    "block", "scope", "Outside this ticket's customer",
                    f"This ticket belongs to {self.scope}. {tool.name} was called for {asked}, "
                    f"so it would read another customer's confidential data.",
                    evidence,
                )
            if tool.contact and asked and asked != self.scope.upper():
                return Decision(
                    "block", "scope", "Outside this ticket's customer",
                    f"This ticket belongs to {self.scope}. {tool.name} was called for {asked}, "
                    f"so it would change who controls another customer's account.",
                    evidence,
                )

        if tool.contact:
            return self._check_contact(tool, args, evidence)

        if tool.amount and tool.scope and self.policy.enforce_scope and self.scope:
            asked = str(args.get(tool.scope, "")).strip().upper()
            if asked and asked != self.scope.upper():
                return Decision(
                    "hold", "scope", "Money moving to a different customer",
                    f"This ticket belongs to {self.scope}, but {tool.name} targets {asked}. "
                    f"A person needs to approve it.",
                    evidence,
                )

        if tool.amount:
            amount = self.parse_amount(args.get(tool.amount, 0))
            limit = self.policy.refund_limit
            if amount > limit:
                return Decision(
                    "hold", "limit", "Amount above the approval limit",
                    f"{tool.name} for ${amount:,.2f} is above the ${limit:,.0f} limit "
                    f"and needs a person to approve it.",
                    evidence,
                )
            customer = str(args.get(tool.scope, "")).strip().upper() if tool.scope else ""
            already = self.refunded.get(customer, 0.0)
            if already + amount > limit:
                return Decision(
                    "hold", "cumulative", "Refunds add up past the approval limit",
                    f"{customer or 'This customer'} has already received ${already:,.2f} in automatic refunds "
                    f"in this conversation. Another ${amount:,.2f} brings the total to ${already + amount:,.2f}, "
                    f"above the ${limit:,.0f} limit. Splitting a refund into smaller ones does not get around "
                    f"approval, so a person needs to approve it.",
                    evidence,
                )

        if not tool.sends:
            return Decision(evidence=evidence)

        raw_dest = str(args.get(tool.destination, ""))
        dest = destination_key(raw_dest)
        source, origins = self.trace(dest)
        verified = source == TRUSTED or self._is_internal(dest)
        is_requester = dest == self.requester
        where = f"it appears only in {origins[0]}" if source == UNTRUSTED else "it does not appear in any trusted source"

        payload = " ".join(str(args.get(p, "")) for p in tool.payload)
        for secret, how in self._secrets_in(payload):
            if dest not in secret.allowed and not self._is_internal(dest):
                return Decision(
                    "block", "confidential", f"{secret.owner_name}'s {secret.name} would leave to the wrong place",
                    f"The message contains {secret.owner_name}'s {secret.name} ({how}). It may only be sent to "
                    f"{', '.join(secret.allowed)}. The destination is {dest}; {where}.",
                    evidence,
                )

        leak = self._record_leak(payload, dest, "message", evidence)
        if leak:
            return leak

        if not verified and not is_requester:
            if self.read_owners:
                names = ", ".join(s.owner_name for s in self.read_owners.values())
                return Decision(
                    "block", "destination", "Unverified destination after reading confidential data",
                    f"{tool.name} targets {dest}; {where}. The agent has already read confidential data "
                    f"for {names}, so nothing may be sent there.",
                    evidence,
                )
            if self.policy.hold_unverified:
                return Decision(
                    "hold", "destination", "Destination was not given by a trusted source",
                    f"{tool.name} targets {dest}; {where}. A person needs to approve it.",
                    evidence,
                )

        if self.read_owners and not self._is_internal(dest):
            allowed = {a for s in self.read_owners.values() for a in s.allowed}
            if dest not in allowed:
                names = ", ".join(s.owner_name for s in self.read_owners.values())
                return Decision(
                    "hold", "confidential", "Sending outside the data owner",
                    f"The agent has read confidential data for {names} and is now sending to {dest}, "
                    f"which is not the owner's address on file.",
                    evidence,
                )

        if self._is_internal(dest):
            return Decision(evidence=evidence)
        return self._confirm(tool.name, evidence)

    def _check_contact(self, tool: Tool, args: dict, evidence: list[Evidence]) -> Decision:
        address = destination_key(str(args.get(tool.contact, "")))
        if self.read_owners:
            names = ", ".join(f"{s.owner_name}'s {s.name}" for s in self.read_owners.values())
            return Decision(
                "block", "contact", "Contact email change after a sign-in link was created",
                f"{tool.name} would make {address} the address on file. The agent has already read {names}, "
                f"and a new address on file could then receive it, so the address cannot change in this conversation.",
                evidence,
            )
        source, origins = self.trace(address)
        record = self.records.get(str(args.get(tool.scope, "")).strip().upper())
        own_record = f"the customer record for {record.owner_name}" if record else ""
        vouched = [o for o in origins if o in ("the operator's message", own_record)]
        if source == TRUSTED and vouched:
            return Decision(evidence=evidence)
        where = f"it appears only in {origins[0]}" if source == UNTRUSTED else "neither the operator nor this customer's record gave it"
        return Decision(
            "hold", "contact", "New contact email was not given by a trusted source",
            f"{tool.name} would make {address} the address on file; {where}. Whoever holds the address on file "
            f"can sign in to the account, so a person needs to approve it.",
            evidence,
        )

    def check_reply(self, text: str) -> Decision:
        if not self.authorized:
            return self._unrequested("reply", [Evidence("message", text[:240], UNKNOWN, [])])
        dest = self.requester or ""
        for secret, how in self._secrets_in(text):
            if dest not in secret.allowed:
                return Decision(
                    "block", "confidential", f"Reply would reveal {secret.owner_name}'s {secret.name}",
                    f"The reply contains {secret.owner_name}'s {secret.name} ({how}) and goes to "
                    f"{dest or 'the requester'}, which is not {', '.join(secret.allowed)}.",
                    [Evidence("message", text[:240], TRUSTED, [])],
                )
        leak = self._record_leak(text, dest, "reply", [Evidence("message", text[:240], UNKNOWN, [])])
        if leak:
            return leak
        return self._confirm("reply", [Evidence("message", text[:240], UNKNOWN, [])])
