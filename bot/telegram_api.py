"""Thin async wrapper over the Telegram Bot HTTP API.

Deliberately dependency-free (just httpx): the bot only needs four calls, and
a small surface keeps the serverless bundle small and the behaviour obvious.
"""
from __future__ import annotations

from typing import Any

import httpx

from . import config

MAX_MESSAGE_CHARS = 4096


class TelegramError(RuntimeError):
    pass


def _url(method: str) -> str:
    if not config.TELEGRAM_BOT_TOKEN:
        raise TelegramError("TELEGRAM_BOT_TOKEN is not set")
    return f"{config.TELEGRAM_API}/bot{config.TELEGRAM_BOT_TOKEN}/{method}"


async def call(method: str, payload: dict[str, Any] | None = None, timeout: float = 60.0) -> Any:
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(_url(method), json=payload or {})
    try:
        body = resp.json()
    except ValueError:
        raise TelegramError(f"{method}: non-JSON response {resp.status_code}: {resp.text[:200]}")
    if not body.get("ok"):
        raise TelegramError(f"{method}: {body.get('description', body)}")
    return body.get("result")


async def send_message(chat_id: int | str, text: str) -> Any:
    """Send plain text. The reply must stay byte-exact JSON, so: no parse_mode,
    no link previews, nothing that could make Telegram rewrite the body."""
    if len(text) > MAX_MESSAGE_CHARS:
        text = text[:MAX_MESSAGE_CHARS]
    return await call(
        "sendMessage",
        {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": True,
            "link_preview_options": {"is_disabled": True},
        },
    )


async def send_chat_action(chat_id: int | str, action: str = "typing") -> None:
    """Best-effort 'typing...' so a long analysis does not look dead."""
    try:
        await call("sendChatAction", {"chat_id": chat_id, "action": action}, timeout=10.0)
    except Exception:
        pass


async def get_me() -> Any:
    return await call("getMe", timeout=30.0)


async def get_updates(offset: int | None = None, timeout: int = 25) -> list[dict[str, Any]]:
    payload: dict[str, Any] = {"timeout": timeout, "allowed_updates": ["message", "edited_message"]}
    if offset is not None:
        payload["offset"] = offset
    return await call("getUpdates", payload, timeout=timeout + 20)


async def set_webhook(url: str, secret: str, drop_pending: bool = True) -> Any:
    return await call(
        "setWebhook",
        {
            "url": url,
            "secret_token": secret,
            "drop_pending_updates": drop_pending,
            "allowed_updates": ["message", "edited_message"],
            "max_connections": 40,
        },
    )


async def delete_webhook(drop_pending: bool = True) -> Any:
    return await call("deleteWebhook", {"drop_pending_updates": drop_pending})


async def get_webhook_info() -> Any:
    return await call("getWebhookInfo", timeout=30.0)
