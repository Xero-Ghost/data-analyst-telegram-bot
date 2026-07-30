"""FastAPI app: Telegram webhook + a place to serve run logs.

Telegram re-sends an update when the webhook does not answer within ~60s, and a
duplicate reply would break a multi-turn exchange. So the request is answered at
RESPOND_AFTER_SECONDS while the agent keeps running in the background, and every
update_id is de-duplicated on the way in.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from . import config, handler, state, telegram_api

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("bot.server")

# Keeps background agent runs referenced so the event loop cannot collect them.
_running: set[asyncio.Task] = set()


def _spawn(coro) -> asyncio.Task:
    task = asyncio.create_task(coro)
    _running.add(task)
    task.add_done_callback(_running.discard)
    return task


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # On an always-on host (Render/Docker/VM) set RUN_POLLING=1: the bot then
    # long-polls Telegram from inside this process and needs no webhook at all.
    if (os.getenv("RUN_POLLING") or "").lower() in ("1", "true", "yes"):
        from . import polling
        _spawn(polling.run())
        log.info("RUN_POLLING=1 - long-polling loop started")
    yield


app = FastAPI(title="TDS data-analyst telegram bot", version=config.VERSION, lifespan=lifespan)


@app.get("/")
async def root():
    return {
        "service": "tds-data-analyst-telegram-bot",
        "version": config.VERSION,
        "status": "ok",
        "webhook_path": "/telegram/webhook",
    }


@app.get("/health")
async def health():
    return {"ok": True, "missing_env": config.missing_required()}


@app.get("/status")
async def status():
    """Non-secret view of how this deployment is wired up."""
    return {
        "version": config.VERSION,
        "model": config.LLM_MODEL,
        "fallback_model": config.LLM_FALLBACK_MODEL,
        "llm_base_url": config.LLM_BASE_URL,
        "llm_key_set": bool(config.LLM_API_KEY),
        "telegram_token_set": bool(config.TELEGRAM_BOT_TOKEN),
        "log_store": config.LOG_STORE,
        "log_repo": config.GITHUB_REPO or None,
        "public_base_url": config.PUBLIC_BASE_URL or None,
        "search_backends": [n for n, on in (
            ("tavily", config.TAVILY_API_KEY), ("serper", config.SERPER_API_KEY),
            ("brave", config.BRAVE_API_KEY)) if on] + ["duckduckgo", "wikipedia"],
        "serverless": config.IS_SERVERLESS,
        "respond_after_seconds": config.RESPOND_AFTER_SECONDS,
        "missing_env": config.missing_required(),
    }


async def _process(update: dict) -> None:
    try:
        await handler.handle_update(update, check_duplicate=False)
    except Exception:
        log.exception("update processing failed")


@app.post("/telegram/webhook")
@app.post("/webhook")
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
):
    if config.TELEGRAM_WEBHOOK_SECRET and x_telegram_bot_api_secret_token != config.TELEGRAM_WEBHOOK_SECRET:
        raise HTTPException(status_code=403, detail="bad secret token")

    update = await request.json()
    if not state.is_new_update(update.get("update_id")):
        return {"ok": True, "duplicate": True}

    task = _spawn(_process(update))

    if config.RESPOND_AFTER_SECONDS <= 0:
        # Serverless: the instance is suspended the moment we respond, so an
        # unfinished run would simply vanish. Hold the request until the answer
        # is sent; Telegram's retries meanwhile are dropped as duplicates above.
        await task
        return {"ok": True}

    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=config.RESPOND_AFTER_SECONDS)
        return {"ok": True}
    except asyncio.TimeoutError:
        # Long-lived process: answer Telegram now, the agent finishes and sends
        # the reply itself.
        return {"ok": True, "status": "processing"}


@app.get("/logs/{name}")
async def get_log(name: str):
    if not re.fullmatch(r"[A-Za-z0-9._-]+\.jsonl", name):
        raise HTTPException(status_code=400, detail="bad log name")
    path = config.LOG_DIR / name
    if not path.exists():
        raise HTTPException(status_code=404, detail="no such run log on this instance")
    return PlainTextResponse(path.read_text(encoding="utf-8"), media_type="application/x-ndjson")


@app.get("/run.jsonl")
async def latest_log():
    path = config.LOG_DIR / "run.jsonl"
    if not path.exists():
        return PlainTextResponse("", media_type="application/x-ndjson")
    return PlainTextResponse(path.read_text(encoding="utf-8"), media_type="application/x-ndjson")


@app.get("/setup-webhook")
async def setup_webhook(secret: str = "", base_url: str = ""):
    """Point Telegram at this deployment. Call it once after deploying:
    /setup-webhook?secret=<TELEGRAM_WEBHOOK_SECRET>"""
    if secret != config.TELEGRAM_WEBHOOK_SECRET:
        raise HTTPException(status_code=403, detail="bad secret")
    base = (base_url or config.PUBLIC_BASE_URL).rstrip("/")
    if not base:
        raise HTTPException(status_code=400, detail="set PUBLIC_BASE_URL or pass ?base_url=")
    await telegram_api.set_webhook(f"{base}/telegram/webhook", config.TELEGRAM_WEBHOOK_SECRET)
    return JSONResponse(await telegram_api.get_webhook_info())
