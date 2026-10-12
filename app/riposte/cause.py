"""Cause finder: which sentence of an untrusted email made the agent take a flagged action.

When Riposte blocks or holds an action, the agent's last step is asked again several times, each time
with one sentence of the email deleted. A sentence whose removal makes the flagged action go away is a
cause. An unchanged re-run first (the control) makes sure the agent repeats the action at all, so a
change can be credited to the missing sentence rather than to model randomness.
"""
from __future__ import annotations

import re

from .taint import destination_key
from .twin import similarity

# A sentence ends at . ! or ? followed by a space or the end, or at a line break, so the dots
# inside addresses and links ("partners.net") don't split it.
_SENTENCE = re.compile(r"(?:[^\n.!?]|[.!?](?=\S))+(?:[.!?]+(?=\s|$))?", re.M)


def sentence_spans(text: str) -> list[tuple[int, int]]:
    """Character ranges of the sentences in text, trimmed of surrounding spaces."""
    spans = []
    for m in _SENTENCE.finditer(text):
        start, end = m.start(), m.end()
        while start < end and text[start].isspace():
            start += 1
        while end > start and text[end - 1].isspace():
            end -= 1
        if end - start >= 2:
            spans.append((start, end))
    return spans


def without(messages: list[dict], marker: str, sentence: str) -> list[dict]:
    """The conversation with one sentence removed from the message that carries the email."""
    out = []
    for m in messages:
        content = str(m.get("content", ""))
        if m.get("role") == "user" and content.startswith(marker) and sentence in content:
            content = content.replace(sentence, "", 1)
        out.append({**m, "content": content})
    return out


def same_action(tool: str, args: dict, other_tool: str, other: dict) -> bool:
    """Whether a re-run would still take the flagged action in the way that mattered."""
    if other_tool != tool:
        return False
    if tool == "send_email":
        return destination_key(str(other.get("to", ""))) == destination_key(str(args.get("to", "")))
    if tool == "reply":
        return similarity(str(other.get("message", "")), str(args.get("message", ""))) >= 0.6
    if tool == "issue_refund":
        def amount(a: dict) -> str:
            return str(a.get("amount", "")).replace("$", "").replace(",", "").strip()
        same_customer = str(other.get("customer_id", "")).strip().upper() == str(args.get("customer_id", "")).strip().upper()
        return same_customer and amount(other) == amount(args)
    if "customer_id" in args:
        return str(other.get("customer_id", "")).strip().upper() == str(args.get("customer_id", "")).strip().upper()
    return other == args
