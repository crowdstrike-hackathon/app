from __future__ import annotations

import json
import os

from ..config import settings

FILE = "agent.settings.json"
FIELDS = ("agent_base_url", "agent_model", "agent_api_key")


def load() -> None:
    path = settings.resolve(FILE)
    if not path.exists():
        return
    try:
        saved = json.loads(path.read_text())
    except (OSError, ValueError):
        return
    for name in FIELDS:
        value = saved.get(name)
        if isinstance(value, str) and value:
            setattr(settings, name, value)


def save(base_url: str, model: str, api_key: str = "") -> None:
    settings.agent_base_url = base_url
    settings.agent_model = model
    if api_key:
        settings.agent_api_key = api_key
    path = settings.resolve(FILE)
    path.write_text(json.dumps({name: getattr(settings, name) for name in FIELDS}, indent=2))
    os.chmod(path, 0o600)


def describe() -> dict:
    key = settings.agent_api_key
    return {
        "base_url": settings.agent_base_url,
        "model": settings.agent_model,
        "has_key": bool(key),
        "key_hint": key[-4:] if len(key) >= 12 else "",
    }
