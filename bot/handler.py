"""One place that turns a Telegram update into a reply, shared by the webhook
server and the long-polling runner."""
from __future__ import annotations

import logging

from . import agent, state, telegram_api
from .runlog import RunLog

log = logging.getLogger("bot.handler")

GREETING = (
    "Data-analyst bot ready. Send me one data-analysis question and I reply with "
    "a single JSON object in the shape you ask for, plus a log_url for the run log."
)


async def handle_update(update: dict, check_duplicate: bool = True) -> str | None:
    """Process one update. Returns the reply text (handy for tests), or None.

    `check_duplicate=False` is for callers that already claimed the update_id."""
    if check_duplicate and not state.is_new_update(update.get("update_id")):
        log.info("duplicate update %s ignored", update.get("update_id"))
        return None

    message = update.get("message") or update.get("edited_message")
    if not message:
        return None

    chat_id = message.get("chat", {}).get("id")
    text = (message.get("text") or message.get("caption") or "").strip()
    if chat_id is None or not text:
        return None

    if text.startswith("/"):
        command = text.split()[0].split("@")[0].lower()
        if command in ("/start", "/help"):
            await telegram_api.send_message(chat_id, GREETING)
            return GREETING
        if command == "/reset":
            state.reset(chat_id)
            await telegram_api.send_message(chat_id, "Conversation cleared.")
            return "Conversation cleared."
        # anything else starting with "/" is treated as a normal question

    return await answer_and_send(chat_id, text)


async def answer_and_send(chat_id: int | str, text: str) -> str:
    state.append(chat_id, "user", text)
    conversation = state.history(chat_id)

    run_log = RunLog()
    log.info("run %s | chat %s | %s", run_log.run_id, chat_id, text[:120].replace("\n", " "))
    await telegram_api.send_chat_action(chat_id, "typing")

    reply, run_log = await agent.answer_question(conversation, run_log)
    await run_log.publish()          # the reply already points at this URL

    state.append(chat_id, "assistant", reply)
    await telegram_api.send_message(chat_id, reply)
    log.info("run %s | replied: %s", run_log.run_id, reply[:200])
    return reply


async def answer_offline(text: str, conversation: list[dict[str, str]] | None = None) -> tuple[str, RunLog]:
    """Answer without touching Telegram - used by scripts/ask.py and tests."""
    turns = list(conversation or []) + [{"role": "user", "content": text}]
    run_log = RunLog()
    reply, run_log = await agent.answer_question(turns, run_log)
    await run_log.publish()
    return reply, run_log
