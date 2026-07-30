#!/usr/bin/env python3
"""Ask the agent a question without going through Telegram.

    python scripts/ask.py "Which state has the highest maternal mortality rate ...?"
    python scripts/ask.py --file question.txt

Prints the exact text the bot would reply with, then where the run log went.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if hasattr(sys.stdout, "reconfigure"):      # Windows consoles default to cp1252
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from bot import handler  # noqa: E402


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("question", nargs="*", help="the question text")
    ap.add_argument("--file", help="read the question from a file instead")
    ap.add_argument("--show-log", action="store_true", help="dump the JSONL run log too")
    args = ap.parse_args()

    question = Path(args.file).read_text(encoding="utf-8") if args.file else " ".join(args.question)
    if not question.strip():
        question = sys.stdin.read()
    if not question.strip():
        ap.error("no question given")

    reply, run_log = await handler.answer_offline(question.strip())

    print("\n--- reply ---")
    print(reply)
    print("\n--- checks ---")
    try:
        parsed = json.loads(reply)
        print(f"valid JSON: yes   top-level keys: {list(parsed) if isinstance(parsed, dict) else type(parsed).__name__}")
    except json.JSONDecodeError as exc:
        print(f"valid JSON: NO ({exc})")
    print(f"log_url: {run_log.url()}")
    print(f"local log: {(Path.cwd() / '.state' / 'logs' / run_log.filename)}")

    if args.show_log:
        print("\n--- run.jsonl ---")
        print(run_log.to_jsonl())
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
