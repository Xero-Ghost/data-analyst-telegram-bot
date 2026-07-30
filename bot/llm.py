"""OpenAI-compatible chat client (AI Pipe by default).

Only /chat/completions with tool calling is needed, so this is a hand-rolled
httpx call rather than the openai SDK: fewer megabytes in the serverless
bundle, and no surprises when the proxy differs slightly from upstream.
"""
from __future__ import annotations

import asyncio
from typing import Any

import httpx

from . import config


class LLMError(RuntimeError):
    pass


def _supports_temperature(model: str) -> bool:
    """Reasoning models (gpt-5*, o1/o3/o4*) reject an explicit temperature."""
    m = model.lower()
    return not (m.startswith("gpt-5") or m.startswith("o1") or m.startswith("o3") or m.startswith("o4"))


def _payload(model: str, messages: list[dict[str, Any]], tools: list[dict] | None,
             tool_choice: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"model": model, "messages": messages}
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = tool_choice or "auto"
    if _supports_temperature(model):
        payload["temperature"] = 0
    return payload


async def chat(
    messages: list[dict[str, Any]],
    tools: list[dict] | None = None,
    tool_choice: Any = None,
    model: str | None = None,
    retries: int = 3,
) -> dict[str, Any]:
    """Return the assistant message dict, e.g.
    {"role": "assistant", "content": ..., "tool_calls": [...]}."""
    if not config.LLM_API_KEY:
        raise LLMError("No LLM key: set AIPIPE_TOKEN (or OPENAI_API_KEY)")

    candidates = [model or config.LLM_MODEL]
    if config.LLM_FALLBACK_MODEL and config.LLM_FALLBACK_MODEL not in candidates:
        candidates.append(config.LLM_FALLBACK_MODEL)

    url = f"{config.LLM_BASE_URL}/chat/completions"
    headers = {"Authorization": f"Bearer {config.LLM_API_KEY}", "Content-Type": "application/json"}
    last_error: Exception | None = None

    for candidate in candidates:
        for attempt in range(retries):
            try:
                async with httpx.AsyncClient(timeout=config.LLM_TIMEOUT) as client:
                    resp = await client.post(
                        url, headers=headers,
                        json=_payload(candidate, messages, tools, tool_choice),
                    )
                if resp.status_code in (408, 429, 500, 502, 503, 504):
                    last_error = LLMError(f"{resp.status_code}: {resp.text[:300]}")
                    await asyncio.sleep(1.5 * (attempt + 1))
                    continue
                if resp.status_code >= 400:
                    # 4xx is usually "bad model" / "no quota" - try the next model
                    last_error = LLMError(f"{resp.status_code}: {resp.text[:300]}")
                    break
                body = resp.json()
                if "error" in body and not body.get("choices"):
                    last_error = LLMError(str(body["error"])[:300])
                    break
                return body["choices"][0]["message"]
            except (httpx.HTTPError, KeyError, ValueError) as exc:
                last_error = exc
                await asyncio.sleep(1.5 * (attempt + 1))

    raise LLMError(f"chat completion failed: {last_error}")
