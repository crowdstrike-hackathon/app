from __future__ import annotations

import re

_PATTERNS = [
    r"ignore (all |any )?(previous|prior|above) (instructions|rules)",
    r"disregard (all |any )?(previous|prior|above)",
    r"new instructions?:",
    r"system (notice|prompt|override|message)",
    r"you are now",
    r"(admin|administrator|developer|debug) mode",
    r"do not (mention|tell|reveal)",
    r"note to (the )?(support )?(agent|assistant|ai)",
    r"as an ai",
    r"pre-?authori[sz]ed",
    r"override",
    r"jailbreak",
]
_COMPILED = [re.compile(p, re.I) for p in _PATTERNS]


class KeywordFilter:
    name = "keyword"

    def scan(self, text: str) -> list[str]:
        return [m.group(0) for rx in _COMPILED if (m := rx.search(text))]
