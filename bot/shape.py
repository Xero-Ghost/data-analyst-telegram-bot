"""Turn whatever the model produced into the exact JSON the question asked for.

The grader does `json.loads(reply)` on the last message and compares it to the
expected object, so the reply has to be one JSON object and nothing else - no
code fences, no "Here is the answer:", no trailing note.
"""
from __future__ import annotations

import json
import re
from typing import Any

from . import config

LOG_URL_PLACEHOLDER = "<LOG_URL>"


def extract_json(text: str) -> Any:
    """Best-effort: pull the first complete JSON value out of a model reply."""
    if text is None:
        return None
    text = text.strip()
    if not text:
        return None

    fenced = re.search(r"```(?:json)?\s*(.+?)\s*```", text, re.S)
    if fenced:
        text = fenced.group(1).strip()

    try:
        return json.loads(text)
    except (ValueError, TypeError):
        pass

    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        if start == -1:
            continue
        depth, in_string, escaped = 0, False, False
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
            elif ch == opener:
                depth += 1
            elif ch == closer:
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except ValueError:
                        break
    return None


def wants_log_url(question: str) -> bool:
    return "log_url" in (question or "").lower()


def wants_answer_key(question: str) -> bool:
    return bool(re.search(r"""["']answer["']\s*:""", question or ""))


def _fill_placeholder(value: Any, log_url: str) -> Any:
    if isinstance(value, str):
        return value.replace(LOG_URL_PLACEHOLDER, log_url)
    if isinstance(value, list):
        return [_fill_placeholder(v, log_url) for v in value]
    if isinstance(value, dict):
        return {k: _fill_placeholder(v, log_url) for k, v in value.items()}
    return value


def finalize(answer: Any, question: str, log_url: str) -> str:
    """Return the exact text to send back to Telegram."""
    answer = _fill_placeholder(answer, log_url)

    needs_wrapper = wants_answer_key(question)
    needs_log = wants_log_url(question) or config.ALWAYS_INCLUDE_LOG_URL

    if needs_wrapper and not (isinstance(answer, dict) and "answer" in answer):
        answer = {"answer": answer}
    if needs_log:
        if not isinstance(answer, dict):
            answer = {"answer": answer}
        answer["log_url"] = log_url

    return json.dumps(answer, ensure_ascii=False, default=str)


def fallback_reply(question: str, log_url: str, detail: str = "") -> str:
    """Something valid to say when the agent could not finish - a parseable
    JSON object still beats prose, and keeps the shape the grader expects."""
    payload: Any = {"error": detail[:200] or "could not compute an answer"}
    return finalize(payload, question, log_url)
