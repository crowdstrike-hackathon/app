from __future__ import annotations

import base64
import binascii
import codecs
import re
import unicodedata
from dataclasses import dataclass, field
from urllib.parse import unquote, urlparse

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
URL_RE = re.compile(r"https?://[^\s<>\"')\]]+", re.I)
_B64_RE = re.compile(r"[A-Za-z0-9+/_-]{16,}={0,2}")
_HEX_RE = re.compile(r"(?:[0-9a-fA-F]{2}){8,}")
_ZERO_WIDTH = dict.fromkeys(map(ord, "​‌‍⁠﻿­"), None)
_HOMOGLYPHS = str.maketrans({
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "х": "x", "у": "y", "і": "i", "ѕ": "s",
    "ԁ": "d", "ɡ": "g", "ո": "n", "һ": "h", "ｍ": "m", "＠": "@", "．": ".",
})
_AT_RE = re.compile(r"\s*[\[\(\{<]\s*at\s*[\]\)\}>]\s*|\s+at\s+", re.I)
_DOT_RE = re.compile(r"\s*[\[\(\{<]\s*dot\s*[\]\)\}>]\s*|\s+dot\s+", re.I)


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", str(text))
    return text.translate(_ZERO_WIDTH).translate(_HOMOGLYPHS).lower()


def squash(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", normalize(text))


def deobfuscate(text: str) -> str:
    return _DOT_RE.sub(".", _AT_RE.sub("@", text))


def _try_b64(token: str) -> str | None:
    raw = token.replace("-", "+").replace("_", "/")
    raw += "=" * (-len(raw) % 4)
    try:
        out = base64.b64decode(raw, validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return None
    printable = sum(1 for c in out if c.isprintable() or c in "\n\t")
    return out if out and printable / len(out) > 0.9 else None


def _try_hex(token: str) -> str | None:
    try:
        out = bytes.fromhex(token).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None
    return out if out.isprintable() else None


def decodings(text: str) -> list[str]:
    text = str(text)
    out: list[str] = []
    for token in _B64_RE.findall(text):
        decoded = _try_b64(token)
        if decoded:
            out.append(decoded)
    for token in _HEX_RE.findall(text):
        decoded = _try_hex(token)
        if decoded:
            out.append(decoded)
    if "%" in text:
        unq = unquote(text)
        if unq != text:
            out.append(unq)
    return out


def views(text: str, deep: bool = True) -> list[str]:
    text = str(text)
    raw = [text, deobfuscate(text)]
    if deep:
        first = decodings(text)
        raw.extend(first)
        for item in first:
            raw.extend(decodings(item))
        raw.append(codecs.decode(text, "rot13"))
        raw.append(text[::-1])
    seen: set[str] = set()
    result = []
    for item in raw:
        n = normalize(item)
        if n and n not in seen:
            seen.add(n)
            result.append(n)
    return result


def destination_key(value: str) -> str:
    value = normalize(deobfuscate(str(value))).strip().strip("<>\"' ")
    m = EMAIL_RE.search(value)
    if m:
        return m.group(0).lower()
    if "://" in value:
        host = urlparse(value).hostname
        if host:
            return host.lower()
    return value


def domain_of(dest_key: str) -> str:
    return dest_key.rsplit("@", 1)[-1] if "@" in dest_key else dest_key


@dataclass
class Span:
    text: str
    label: str
    origin: str
    step: int = 0
    _views: list[str] = field(default_factory=list, repr=False)
    _squashed: list[str] = field(default_factory=list, repr=False)

    def __post_init__(self) -> None:
        self._views = views(self.text)
        self._squashed = [squash(v) for v in self._views]

    def contains(self, needle: str) -> bool:
        n = normalize(needle).strip()
        if not n:
            return False
        if any(n in v for v in self._views):
            return True
        s = squash(n)
        return len(s) >= 6 and any(s in v for v in self._squashed)


@dataclass
class Secret:
    value: str
    kind: str
    name: str
    owner_id: str
    owner_name: str
    allowed: tuple[str, ...]

    def found_in(self, payload: str) -> str | None:
        target = squash(self.value)
        if len(target) < 6:
            return None
        squashed = [squash(v) for v in views(payload)]
        if any(target in s for s in squashed):
            return "verbatim"
        encoded = (
            squash(base64.b64encode(self.value.encode()).decode().rstrip("=")),
            squash(self.value.encode().hex()),
        )
        if any(e and e in s for e in encoded for s in squashed):
            return "encoded"
        if len(target) >= 16:
            window = max(10, len(target) // 2)
            for i in range(0, len(target) - window + 1):
                piece = target[i : i + window]
                if any(piece in s for s in squashed):
                    return "partial"
        return None
