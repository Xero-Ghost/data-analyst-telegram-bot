#!/usr/bin/env python3
"""Check that every moving part is wired up: env, LLM, Telegram, search,
python execution and log publishing.

    python scripts/selftest.py

Run it locally before deploying, and again against the deployed environment
(same env vars) if something misbehaves.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if hasattr(sys.stdout, "reconfigure"):      # Windows consoles default to cp1252
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from bot import config, llm, telegram_api, tools  # noqa: E402
from bot.runlog import RunLog  # noqa: E402

OK, BAD = "PASS", "FAIL"


async def check(name: str, coro) -> bool:
    try:
        detail = await coro
        print(f"[{OK}] {name}: {detail}")
        return True
    except Exception as exc:
        print(f"[{BAD}] {name}: {type(exc).__name__}: {exc}")
        return False


async def _env() -> str:
    missing = config.missing_required()
    if missing:
        raise RuntimeError(f"missing {', '.join(missing)}")
    return f"model={config.LLM_MODEL} log_store={config.LOG_STORE} data_root={config.DATA_ROOT}"


async def _llm() -> str:
    message = await llm.chat([{"role": "user", "content": "Reply with the single word: ready"}])
    return f"{config.LLM_MODEL} -> {(message.get('content') or '').strip()[:40]!r}"


async def _tool_calling() -> str:
    message = await llm.chat(
        [{"role": "user", "content": "Use run_python to print 6*7, then submit_answer with {\"v\": 42}."}],
        tools=tools.TOOL_SPECS)
    calls = [c["function"]["name"] for c in (message.get("tool_calls") or [])]
    if not calls:
        raise RuntimeError("model returned no tool call - tool calling may be unsupported for this model")
    return f"tool_calls={calls}"


async def _telegram() -> str:
    me = await telegram_api.get_me()
    username = me.get("username", "")
    warn = "" if username.lower().endswith("bot") else "  (WARNING: username must end in 'bot')"
    return f"@{username}{warn}"


async def _python_exec() -> str:
    out = await tools.run_python("import pandas as pd\nprint(pd.Series([1,2,3]).mean())")
    if "2.0" not in out:
        raise RuntimeError(f"unexpected output: {out[:200]}")
    return "pandas works in the sandbox"


async def _search() -> str:
    out = await tools.web_search("MOSPI maternal mortality ratio SRS bulletin", 3)
    if out.startswith("No search backend"):
        raise RuntimeError(out[:200])
    return out.splitlines()[0]


async def _fetch() -> str:
    out = await tools.fetch_url("https://www.mospi.gov.in/")
    if out.startswith("ERROR"):
        raise RuntimeError(out[:200])
    return f"{len(out)} chars of readable text"


async def _log() -> str:
    run_log = RunLog()
    run_log.add("selftest", note="publishing a throwaway log")
    url = await run_log.publish()
    failed = [e for e in run_log.events if e["event"].startswith("log_")]
    if failed:
        raise RuntimeError(str(failed[-1]))
    return url


async def main() -> int:
    print(f"--- selftest (version {config.VERSION}) ---")
    results = [
        await check("env", _env()),
        await check("llm chat", _llm()),
        await check("llm tool calling", _tool_calling()),
        await check("telegram getMe", _telegram()),
        await check("run_python", _python_exec()),
        await check("web_search", _search()),
        await check("fetch_url", _fetch()),
        await check("log publish", _log()),
    ]
    failed = results.count(False)
    print(f"\n{len(results) - failed}/{len(results)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
