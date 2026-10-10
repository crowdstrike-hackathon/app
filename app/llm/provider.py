from __future__ import annotations

import json
from typing import Optional

import httpx

from ..config import settings


class LLMError(RuntimeError):
    pass


class LLMProvider:
    name: str = "base"

    async def complete_json(self, system: str, user: str, *, temperature: float = 0.4) -> dict:
        raise NotImplementedError


def _extract_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
    start = text.find("{")
    if start == -1:
        raise LLMError(f"no JSON object in model reply: {text[:200]!r}")
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start : i + 1])
    raise LLMError("unbalanced JSON in model reply")


class ClaudeProvider(LLMProvider):
    name = "claude"

    def __init__(self) -> None:
        from anthropic import AsyncAnthropic

        if not settings.blue_ready:
            raise LLMError("ANTHROPIC_API_KEY is not set")
        self._client = AsyncAnthropic(api_key=settings.anthropic_api_key)
        self._model = settings.blue_model_name

    async def complete_json(self, system: str, user: str, *, temperature: float = 0.4) -> dict:
        msg = await self._client.messages.create(
            model=self._model,
            max_tokens=1500,
            temperature=temperature,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        text = "".join(block.text for block in msg.content if block.type == "text")
        return _extract_json(text)


class LocalOpenAIProvider(LLMProvider):
    name = "local-openai"

    def __init__(self) -> None:
        if not settings.red_ready:
            raise LLMError("RED_MODEL_API_KEY is not set")
        self._base = settings.red_model_base_url.rstrip("/")
        self._key = settings.red_model_api_key
        self._model = settings.red_model_name

    async def complete_json(self, system: str, user: str, *, temperature: float = 0.8) -> dict:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{self._base}/chat/completions",
                headers={"Authorization": f"Bearer {self._key}"},
                json={
                    "model": self._model,
                    "temperature": temperature,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                },
            )
            resp.raise_for_status()
            text = resp.json()["choices"][0]["message"]["content"]
        return _extract_json(text)


def get_blue_provider() -> Optional[LLMProvider]:
    if settings.is_live and settings.blue_ready:
        try:
            return ClaudeProvider()
        except LLMError:
            return None
    return None


def get_red_provider() -> Optional[LLMProvider]:
    if settings.is_live and settings.red_ready:
        try:
            return LocalOpenAIProvider()
        except LLMError:
            return None
    return None
