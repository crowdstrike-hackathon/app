from __future__ import annotations

import json
import re

import httpx

from ..config import settings


_ANSWER_TOOL = re.compile(r'"(?:tool|action)"\s*:\s*"answer"')
_ANSWER_FIELD = re.compile(r'"(?:message|text|body|content)"\s*:\s*"')
_THOUGHT_FIELD = re.compile(r'^\s*\{\s*"thought"\s*:\s*"')
_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f"}


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


async def _stream(client: httpx.AsyncClient, url: str, headers: dict, payload: dict, on_text) -> str:
    text = ""
    async with client.stream("POST", url, headers=headers, json={**payload, "stream": True}) as resp:
        if resp.status_code >= 400:
            await resp.aread()
            resp.raise_for_status()
        async for line in resp.aiter_lines():
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            choices = json.loads(data).get("choices") or []
            piece = (choices[0].get("delta") or {}).get("content") if choices else None
            if piece:
                text += piece
                on_text(text)
    return text


def partial_answer(text: str) -> str:
    """Return the answer message written so far in a half-streamed action, or "" if it is not an answer."""
    tool = _ANSWER_TOOL.search(text)
    if not tool:
        return ""
    field = _ANSWER_FIELD.search(text, tool.end())
    return _partial_string(text, field.end()) if field else ""


def partial_thought(text: str) -> str:
    field = _THOUGHT_FIELD.search(text)
    return _partial_string(text, field.end()) if field else ""


def _partial_string(text: str, start: int) -> str:
    out = []
    i = start
    while i < len(text):
        ch = text[i]
        if ch == '"':
            break
        if ch != "\\":
            out.append(ch)
            i += 1
            continue
        if i + 1 >= len(text):
            break
        esc = text[i + 1]
        if esc == "u":
            code = text[i + 2 : i + 6]
            if len(code) < 4:
                break
            try:
                out.append(chr(int(code, 16)))
            except ValueError:
                pass
            i += 6
            continue
        out.append(_ESCAPES.get(esc, esc))
        i += 2
    return "".join(out).encode("utf-16", "surrogatepass").decode("utf-16", "ignore")


def _reasoning(model: str) -> bool:
    name = model.lower()
    return name.startswith(("gpt-5", "o1", "o3", "o4")) and "chat" not in name


async def chat_json(
    messages: list[dict], *, temperature: float = 0.0, base_url: str = "", model: str = "", api_key: str = "",
    extra: dict | None = None, strict: bool = False, on_text=None,
) -> dict:
    base = (base_url or settings.agent_base_url).rstrip("/")
    name = model or settings.agent_model
    payload = {"model": name, "messages": messages, "response_format": {"type": "json_object"}}
    if extra is None and _reasoning(name):
        payload["reasoning_effort"] = settings.agent_reasoning_effort
    else:
        payload.update({"temperature": temperature, **(extra or {"seed": 7})})
    headers = {"Authorization": f"Bearer {api_key or settings.agent_api_key}"}
    try:
        async with httpx.AsyncClient(timeout=settings.agent_timeout_seconds) as client:
            if on_text:
                text = await _stream(client, f"{base}/chat/completions", headers, payload, on_text)
            else:
                resp = await client.post(f"{base}/chat/completions", headers=headers, json=payload)
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
