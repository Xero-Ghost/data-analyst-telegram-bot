"""Every knob the bot reads from the environment, in one place.

Local runs read a .env file; on Vercel/Render the same names are set as
project environment variables.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _str(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def _int(name: str, default: int) -> int:
    try:
        return int(_str(name) or default)
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(_str(name) or default)
    except ValueError:
        return default


# --- where we may write -----------------------------------------------------
# Serverless filesystems are read-only apart from /tmp, and /tmp is per-instance
# and ephemeral - which is exactly why the run log is published elsewhere.
IS_SERVERLESS = bool(_str("VERCEL") or _str("AWS_LAMBDA_FUNCTION_NAME"))
DATA_ROOT = Path(_str("DATA_ROOT") or ("/tmp/tds-bot" if IS_SERVERLESS else str(Path.cwd() / ".state")))
LOG_DIR = DATA_ROOT / "logs"
WORK_DIR = DATA_ROOT / "work"

# --- telegram ---------------------------------------------------------------
TELEGRAM_BOT_TOKEN = _str("TELEGRAM_BOT_TOKEN")
TELEGRAM_WEBHOOK_SECRET = _str("TELEGRAM_WEBHOOK_SECRET", "tds-webhook-secret")
TELEGRAM_API = "https://api.telegram.org"

# --- llm --------------------------------------------------------------------
# AI Pipe is OpenAI-compatible, so any OpenAI-shaped endpoint works by swapping
# LLM_BASE_URL + the key.
LLM_API_KEY = _str("AIPIPE_TOKEN") or _str("OPENAI_API_KEY") or _str("LLM_API_KEY")
LLM_BASE_URL = _str("LLM_BASE_URL", "https://aipipe.org/openai/v1").rstrip("/")
LLM_MODEL = _str("LLM_MODEL", "gpt-4.1-mini")
LLM_FALLBACK_MODEL = _str("LLM_FALLBACK_MODEL", "gpt-4.1-nano")
LLM_TIMEOUT = _float("LLM_TIMEOUT", 120.0)

# --- agent ------------------------------------------------------------------
# Steps are cheap next to the wall clock (a step is ~1-15s), and running out of
# them mid-hunt is what makes an agent invent an answer - so let time be the
# binding limit, not this.
AGENT_MAX_STEPS = _int("AGENT_MAX_STEPS", 20)
# The grader's timeout covers a whole (possibly multi-turn) exchange, usually
# 300s, and a serverless function is killed at 300s too - so one answer gets a
# comfortably smaller slice than either limit.
AGENT_TIME_BUDGET = _float("AGENT_TIME_BUDGET", 180.0)
PYTHON_EXEC_TIMEOUT = _float("PYTHON_EXEC_TIMEOUT", 60.0)
HTTP_TIMEOUT = _float("HTTP_TIMEOUT", 45.0)
MAX_HISTORY_TURNS = _int("MAX_HISTORY_TURNS", 8)

# By default the reply mirrors the shape the question asked for, so a question
# that wants only {"state": ...} gets exactly that. Set this if you would rather
# always append log_url, whether or not the question mentions it.
ALWAYS_INCLUDE_LOG_URL = _str("ALWAYS_INCLUDE_LOG_URL", "false").lower() in ("1", "true", "yes")

# Telegram re-sends an update if the webhook does not return 200 within ~60s.
# Answering early and finishing in the background avoids that - but only where
# the process survives the response. Serverless platforms suspend the instance
# as soon as the response is sent, killing the run, so there the webhook waits
# for the answer instead and the update_id de-duplication absorbs the retries.
# 0 = wait for the run to finish before responding.
RESPOND_AFTER_SECONDS = _float("RESPOND_AFTER_SECONDS", 0.0 if IS_SERVERLESS else 50.0)

# --- run log publishing -----------------------------------------------------
GITHUB_TOKEN = _str("GITHUB_TOKEN")
GITHUB_REPO = _str("GITHUB_REPO")           # "owner/repo"
GITHUB_BRANCH = _str("GITHUB_BRANCH", "main")
GITHUB_LOG_DIR = _str("GITHUB_LOG_DIR", "logs")
PUBLIC_BASE_URL = _str("PUBLIC_BASE_URL").rstrip("/")
LOG_STORE = _str("LOG_STORE", "github" if (GITHUB_TOKEN and GITHUB_REPO) else "local").lower()

# --- search backends (all optional) -----------------------------------------
TAVILY_API_KEY = _str("TAVILY_API_KEY")
SERPER_API_KEY = _str("SERPER_API_KEY")
BRAVE_API_KEY = _str("BRAVE_API_KEY")

VERSION = "1.0.0"

USER_AGENT = _str(
    "USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36",
)
# Wikimedia's robot policy rejects browser user-agents on the API and wants a
# descriptive one with a contact, so API calls use this instead.
API_USER_AGENT = _str(
    "API_USER_AGENT",
    f"tds-data-analyst-bot/{VERSION} (https://github.com/{GITHUB_REPO or 'tds-student/data-analyst-bot'})",
)


def ensure_dirs() -> None:
    for d in (LOG_DIR, WORK_DIR):
        d.mkdir(parents=True, exist_ok=True)


def missing_required() -> list[str]:
    """Names that must be set for the bot to actually answer anything."""
    missing = []
    if not TELEGRAM_BOT_TOKEN:
        missing.append("TELEGRAM_BOT_TOKEN")
    if not LLM_API_KEY:
        missing.append("AIPIPE_TOKEN (or OPENAI_API_KEY)")
    return missing
