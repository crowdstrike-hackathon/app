from __future__ import annotations

import time
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from .config import settings
from .llm.provider import ClaudeProvider, LLMError, LLMProvider, LocalOpenAIProvider
from .orm import Connection

MASK = "__KEEP__"


def _defaults() -> dict:
    return {
        "sim_mode": settings.sim_mode,
        "blue": {"kind": "anthropic", "base_url": "", "api_key": settings.anthropic_api_key, "model": settings.blue_model_name},
        "red": {"kind": "openai", "base_url": settings.red_model_base_url, "api_key": settings.red_model_api_key, "model": settings.red_model_name},
    }


async def get_connection(db: AsyncSession, user_id: str) -> dict:
    cfg = _defaults()
    row = await db.get(Connection, user_id)
    if row is not None:
        if row.sim_mode:
            cfg["sim_mode"] = row.sim_mode
        for side, prefix in (("blue", "blue"), ("red", "red")):
            kind = getattr(row, f"{prefix}_kind")
            if kind:
                cfg[side]["kind"] = kind
            for fld in ("base_url", "api_key", "model"):
                val = getattr(row, f"{prefix}_{fld}")
                if val:
                    cfg[side][fld] = val
    return cfg


async def upsert_connection(db: AsyncSession, user_id: str, data: dict) -> dict:
    row = await db.get(Connection, user_id)
    if row is None:
        row = Connection(user_id=user_id)
        db.add(row)
    if data.get("sim_mode"):
        row.sim_mode = data["sim_mode"]
    for side in ("blue", "red"):
        side_data = data.get(side) or {}
        for fld in ("kind", "base_url", "model"):
            if side_data.get(fld) is not None:
                setattr(row, f"{side}_{fld}", side_data[fld])
        key = side_data.get("api_key")
        if key is not None and key not in ("", MASK):
            setattr(row, f"{side}_api_key", key)
    await db.commit()
    return await get_connection(db, user_id)


def mask(cfg: dict) -> dict:
    out = {"sim_mode": cfg["sim_mode"]}
    for side in ("blue", "red"):
        s = dict(cfg[side])
        key = s.get("api_key") or ""
        s["api_key"] = (key[:4] + "…" + key[-4:]) if len(key) > 10 else ("set" if key else "")
        s["has_key"] = bool(key)
        out[side] = s
    return out


def build_provider(cfg: dict) -> Optional[LLMProvider]:
    kind = (cfg.get("kind") or "").lower()
    try:
        if kind == "anthropic":
            return ClaudeProvider(api_key=cfg.get("api_key"), model=cfg.get("model"))
        if kind in ("openai", "local", "vllm", "local-openai"):
            if not cfg.get("base_url"):
                return None
            return LocalOpenAIProvider(
                base_url=cfg.get("base_url"),
                api_key=cfg.get("api_key") or "sk-local",
                model=cfg.get("model"),
            )
    except LLMError:
        return None
    return None


async def resolve_providers(db: AsyncSession, user_id: str) -> tuple[Optional[LLMProvider], Optional[LLMProvider]]:
    cfg = await get_connection(db, user_id)
    if str(cfg["sim_mode"]).lower() != "live":
        return None, None
    return build_provider(cfg["blue"]), build_provider(cfg["red"])


async def test_side(cfg_side: dict) -> dict:
    provider = build_provider(cfg_side)
    if provider is None:
        return {"ok": False, "error": "No endpoint configured for this connection."}
    t0 = time.perf_counter()
    try:
        data = await provider.complete_json(
            "You are a connectivity probe. Reply only with JSON.",
            'Return exactly {"ok": true}.',
            temperature=0.0,
        )
        return {"ok": True, "latency_ms": round((time.perf_counter() - t0) * 1000), "model": cfg_side.get("model"), "sample": data}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)[:300]}
