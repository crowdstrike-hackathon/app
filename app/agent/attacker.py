"""Render the text-only Qwen template explicitly for imported Ollama GGUFs."""
import json

import httpx

from .llm import LLMError, chat_json
from ..config import settings

SCHEMA = {"type": "object", "properties": {k: {"type": "string", "minLength": 1}
          for k in ("from", "subject", "body")},
          "required": ["from", "subject", "body"], "additionalProperties": False}


def render_ticket_prompt(messages):
    # Embedded GGUF Jinja can take precedence over a Modelfile TEMPLATE. Raw mode
    # makes the ticket writer's role boundaries and closed thinking prefix explicit.
    def escape(content):
        for token in ("<|im_start|>", "<|im_end|>", "<|endoftext|>"):
            content = content.replace(token, token.replace("<|", "< |"))
        return content
    return "".join(f"<|im_start|>{m['role']}\n{escape(m['content'])}<|im_end|>\n" for m in messages) + "<|im_start|>assistant\n<think>\n\n</think>\n\n"


async def attacker_json(messages, **kwargs):
    if settings.redteam_transport == "openai":
        return await chat_json(messages, **kwargs)
    base = kwargs["base_url"].rstrip("/").removesuffix("/v1")
    try:
        async with httpx.AsyncClient(timeout=settings.agent_timeout_seconds) as client:
            response = await client.post(base + "/api/generate", headers={
                "Authorization": "Bearer " + kwargs["api_key"]}, json={
                "model": kwargs["model"], "prompt": render_ticket_prompt(messages),
                "raw": True, "stream": False, "format": SCHEMA,
                "options": {"temperature": kwargs.get("temperature", 0.8), "num_predict": 2048,
                            "num_ctx": 16384, "stop": ["<|im_end|>", "<|endoftext|>"]},
            })
            response.raise_for_status()
            data = response.json()
            if data.get("done_reason") == "length":
                raise LLMError("Attacker output exceeded token limit")
            value = json.loads(data.get("response", ""))
    except httpx.HTTPError as exc:
        raise LLMError(f"Attacker endpoint failed at {base}: {exc}") from exc
    except (ValueError, TypeError) as exc:
        raise LLMError("Attacker must return only strict JSON") from exc
    if not isinstance(value, dict):
        raise LLMError("Attacker reply must be an object")
    return value
