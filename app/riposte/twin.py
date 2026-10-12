"""Twin run: catch replies that depend on a secret without containing it.

Text matching only finds a secret that appears in the outgoing message. A reply such as
"Yes" or "it ends in 3f9a" leaks part of a key while containing almost none of it.

Before an outgoing message goes to someone who may not see a secret, the agent's last step is
asked again with every secret swapped for a decoy. If the decoy changes what the agent would send,
the message carries information about the real secret, so it is blocked. A second, unchanged
re-run (the control) separates real dependence on the secret from ordinary model randomness.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Optional

_HEX = "0123456789abcdef"
_PREFIX = re.compile(r"^([A-Za-z]+_[A-Za-z]+_)")

SAME = "same"
DEPENDS = "depends"
UNCLEAR = "unclear"


def decoy_for(secret: str) -> str:
    """A fake secret in the same format whose every random character differs from the real one.

    Digits become letters and letters become digits, so questions about any single character
    (its value, or whether it is a digit) get a different answer for the decoy.
    """
    match = _PREFIX.match(secret)
    prefix = match.group(1) if match else ""
    out = []
    for ch in secret[len(prefix):]:
        low = ch.lower()
        if low.isdigit():
            out.append("abcdef"[int(low) % 6])
        elif low in "abcdef":
            out.append(str((_HEX.index(low) + 3) % 10))
        elif low.isalpha():
            out.append(chr((ord(low) - 97 + 13) % 26 + 97))
        else:
            out.append(ch)
    return prefix + "".join(out)


def swap(text: str, decoys: dict[str, str]) -> str:
    for real, fake in decoys.items():
        text = text.replace(real, fake)
    return text


def swap_messages(messages: list[dict], decoys: dict[str, str]) -> list[dict]:
    return [{**m, "content": swap(str(m.get("content", "")), decoys)} for m in messages]


def outgoing(tool: str, args: dict) -> str:
    """What would leave the sandbox if this action ran."""
    if tool == "reply":
        return f"reply: {args.get('message', '')}"
    if tool == "send_email":
        return f"send_email to {args.get('to', '')}: {args.get('subject', '')} {args.get('body', '')}"
    return f"{tool}: {json.dumps(args, sort_keys=True)}"


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def similarity(a: str, b: str) -> float:
    a, b = _norm(a), _norm(b)
    return 1.0 if a == b else SequenceMatcher(None, a, b).ratio()


@dataclass
class Verdict:
    result: str
    original: str
    twin: str
    control: Optional[str] = None
    twin_similarity: float = 1.0
    control_similarity: Optional[float] = None


def judge(original: str, twin: str, control: Optional[str], decoys: dict[str, str]) -> Verdict:
    """Compare what the agent would send with the real secret against the decoy run.

    `original` and `control` were produced with the real secret and are compared in decoy space,
    so copying the secret verbatim (already policed elsewhere) is not counted as a difference.
    """
    original_d = swap(original, decoys)
    twin_sim = similarity(original_d, twin)
    if twin_sim == 1.0:
        return Verdict(SAME, original, twin)
    if control is None:
        return Verdict(UNCLEAR, original, twin, twin_similarity=twin_sim)
    control_sim = similarity(original_d, swap(control, decoys))
    result = DEPENDS if twin_sim < control_sim else UNCLEAR
    return Verdict(result, original, twin, control, twin_sim, control_sim)


def disclosed_fragment(secret: str, text: str, minimum: int = 4) -> Optional[str]:
    """A standalone token in `text` that is the start or end of the secret's random part."""
    match = _PREFIX.match(secret)
    body = secret[len(match.group(1)):] if match else secret
    for token in re.findall(r"[A-Za-z0-9]+", text):
        if len(token) >= minimum and len(token) < len(body) and (
            body.lower().startswith(token.lower()) or body.lower().endswith(token.lower())
        ):
            return token
    return None
