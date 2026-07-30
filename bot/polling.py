"""Long-polling runner: `python -m bot.polling`.

No public URL needed, so this is the fastest way to test the real bot from a
laptop (and it is also how you would run it on an always-on host). Polling and
webhooks are mutually exclusive, so this deletes any webhook first.
"""
from __future__ import annotations

import asyncio
import logging

from . import config, handler, telegram_api

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("bot.polling")

_running: set[asyncio.Task] = set()


def _spawn(coro) -> None:
    task = asyncio.create_task(coro)
    _running.add(task)
    task.add_done_callback(_running.discard)


async def _process(update: dict) -> None:
    try:
        await handler.handle_update(update)
    except Exception:
        log.exception("update processing failed")


async def run() -> None:
    missing = config.missing_required()
    if missing:
        raise SystemExit(f"missing env vars: {', '.join(missing)} (see .env.example)")

    await telegram_api.delete_webhook(drop_pending=False)
    me = await telegram_api.get_me()
    log.info("polling as @%s (%s) | model=%s | log_store=%s",
             me.get("username"), me.get("id"), config.LLM_MODEL, config.LOG_STORE)

    offset: int | None = None
    while True:
        try:
            updates = await telegram_api.get_updates(offset, timeout=25)
        except Exception as exc:
            log.warning("getUpdates failed (%s); retrying in 5s", exc)
            await asyncio.sleep(5)
            continue

        for update in updates:
            offset = update["update_id"] + 1
            _spawn(_process(update))          # concurrent: a slow question never blocks the queue


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        print("\nstopped")
