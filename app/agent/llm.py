from __future__ import annotations

import json

import httpx

from ..config import settings


class LLMError(RuntimeError):
    pass


def extract_json(text: str) -> dict:
    text = text.strip()
    start = text.find("{")
    if start == -1:
        raise LLMError(f"no JSON object in model reply: {text[:160]!r}")
    depth = 0
    in_string = False
    escaped = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    value = json.loads(text[start : i + 1])
                except json.JSONDecodeError as exc:
                    raise LLMError(f"invalid JSON from model: {exc}") from exc
                if not isinstance(value, dict):
                    raise LLMError("model reply was not a JSON object")
                return value
    raise LLMError("unbalanced JSON in model reply")


async def chat_json(
    messages: list[dict], *, temperature: float = 0.0, base_url: str = "", model: str = "", api_key: str = "",
    extra: dict | None = None, strict: bool = False,
) -> dict:
    base = (base_url or settings.agent_base_url).rstrip("/")
    payload = {
        "model": model or settings.agent_model,
        "messages": messages,
        "temperature": temperature,
        "response_format": {"type": "json_object"},
        **(extra or {"seed": 7}),
    }
    try:
        async with httpx.AsyncClient(timeout=settings.agent_timeout_seconds) as client:
            resp = await client.post(
                f"{base}/chat/completions",
                headers={"Authorization": f"Bearer {api_key or settings.agent_api_key}"},
                json=payload,
            )
            resp.raise_for_status()
            text = resp.json()["choices"][0]["message"]["content"]
    except httpx.HTTPStatusError as exc:
        raise LLMError(f"model endpoint returned {exc.response.status_code}: {exc.response.text[:200]}") from exc
    except httpx.HTTPError as exc:
        raise LLMError(f"cannot reach the model at {base} ({type(exc).__name__})") from exc
    except (KeyError, IndexError, ValueError) as exc:
        raise LLMError("unexpected response shape from the model endpoint") from exc
    if strict:
        try:
            value = json.loads(text or "")
        except (ValueError, TypeError) as exc:
            raise LLMError("Attacker must return only a JSON object, without reasoning or fences") from exc
        if not isinstance(value, dict):
            raise LLMError("Attacker reply must be a JSON object")
        return value
    return extract_json(text or "")


async def model_status(base_url: str = "", model: str = "", api_key: str = "") -> dict:
    base = (base_url or settings.agent_base_url).rstrip("/")
    model = model or settings.agent_model
    status = {"model": model, "base_url": base, "reachable": False, "available": False}
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(f"{base}/models", headers={"Authorization": f"Bearer {api_key or settings.agent_api_key}"})
            resp.raise_for_status()
            names = [m.get("id", "") for m in resp.json().get("data", [])]
    except (httpx.HTTPError, ValueError):
        return status
    status["reachable"] = True
    status["available"] = model in names
    return status
