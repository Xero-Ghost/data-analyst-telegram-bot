"""Conversation history + update de-duplication.

Multi-turn questions arrive as separate updates, so the previous turns have to
survive between them. In-memory is enough while the process stays alive
(polling hosts); a small JSON file carries it across serverless invocations
that land on the same warm instance.
"""
from __future__ import annotations

import json
import threading
import time
from typing import Any

from . import config

MAX_MESSAGES_PER_CHAT = 20
MAX_SEEN_UPDATES = 500
CHAT_TTL_SECONDS = 6 * 3600

_lock = threading.Lock()
_cache: dict[str, Any] | None = None


def _path():
    return config.DATA_ROOT / "state.json"


def _load() -> dict[str, Any]:
    global _cache
    if _cache is not None:
        return _cache
    data: dict[str, Any] = {"chats": {}, "seen": []}
    try:
        if _path().exists():
            loaded = json.loads(_path().read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = {"chats": loaded.get("chats", {}), "seen": loaded.get("seen", [])}
    except Exception:
        pass  # a corrupt/absent state file must never block answering
    _cache = data
    return data


def _save(data: dict[str, Any]) -> None:
    try:
        config.DATA_ROOT.mkdir(parents=True, exist_ok=True)
        tmp = _path().with_suffix(".tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        tmp.replace(_path())
    except Exception:
        pass  # read-only fs: in-memory state still works for this instance


def _prune(data: dict[str, Any]) -> None:
    now = time.time()
    for chat_id, messages in list(data["chats"].items()):
        messages[:] = messages[-MAX_MESSAGES_PER_CHAT:]
        if not messages or now - messages[-1].get("ts", now) > CHAT_TTL_SECONDS:
            data["chats"].pop(chat_id, None)
    data["seen"] = data["seen"][-MAX_SEEN_UPDATES:]


def history(chat_id: int | str) -> list[dict[str, str]]:
    with _lock:
        data = _load()
        return [{"role": m["role"], "content": m["content"]}
                for m in data["chats"].get(str(chat_id), [])]


def append(chat_id: int | str, role: str, content: str) -> None:
    with _lock:
        data = _load()
        data["chats"].setdefault(str(chat_id), []).append(
            {"role": role, "content": content, "ts": time.time()})
        _prune(data)
        _save(data)


def is_new_update(update_id: int | None) -> bool:
    """True the first time an update_id is seen. Telegram re-sends an update if
    the webhook is slow, and a duplicate reply would corrupt a multi-turn run."""
    if update_id is None:
        return True
    with _lock:
        data = _load()
        if update_id in data["seen"]:
            return False
        data["seen"].append(update_id)
        _prune(data)
        _save(data)
        return True


def reset(chat_id: int | str | None = None) -> None:
    with _lock:
        data = _load()
        if chat_id is None:
            data["chats"].clear()
        else:
            data["chats"].pop(str(chat_id), None)
        _save(data)
