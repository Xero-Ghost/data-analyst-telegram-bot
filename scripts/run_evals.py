#!/usr/bin/env python3
"""Offline eval harness: run questions through the agent without Telegram.

    python scripts/run_evals.py                       # evals/questions.json
    python scripts/run_evals.py --file evals/questions.json --only inline_stats

Question file format (same `messages` shape as the official grading repo, so
questions can be copied between the two):

    [
      {
        "id": "inline_stats",
        "messages": ["... question text ..."],       # a list = multi-turn
        "expected": {"mean": 12.5}                   # optional
      }
    ]

Grading here mirrors grade.py in the grading repo: the LAST reply must parse as
JSON. If `expected` is given it is compared exactly - against reply["answer"]
when the reply is wrapped, otherwise against the whole object minus log_url.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if hasattr(sys.stdout, "reconfigure"):      # Windows consoles default to cp1252
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from bot import agent  # noqa: E402
from bot.runlog import RunLog  # noqa: E402


def compare(reply_obj, expected):
    if isinstance(reply_obj, dict) and "answer" in reply_obj and not (
            isinstance(expected, dict) and "answer" in expected):
        return reply_obj["answer"] == expected
    if isinstance(reply_obj, dict):
        stripped = {k: v for k, v in reply_obj.items() if k != "log_url"}
        if stripped == expected:
            return True
    return reply_obj == expected


async def run_question(question: dict) -> dict:
    conversation: list[dict[str, str]] = []
    replies: list[str] = []
    started = time.time()

    for text in question["messages"]:
        conversation.append({"role": "user", "content": text})
        run_log = RunLog()
        reply, run_log = await agent.answer_question(conversation, run_log)
        await run_log.publish()
        conversation.append({"role": "assistant", "content": reply})
        replies.append(reply)

    last = replies[-1] if replies else ""
    try:
        parsed = json.loads(last)
        json_ok = True
    except json.JSONDecodeError:
        parsed, json_ok = None, False

    result = {
        "id": question.get("id", "?"),
        "seconds": round(time.time() - started, 1),
        "json_ok": json_ok,
        "reply": last,
        "log_url": run_log.url(),
    }
    if "expected" in question:
        result["correct"] = bool(json_ok and compare(parsed, question["expected"]))
        result["expected"] = question["expected"]
    return result


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--file", default="evals/questions.json")
    ap.add_argument("--only", help="run just this question id")
    args = ap.parse_args()

    questions = json.loads(Path(args.file).read_text(encoding="utf-8"))
    if args.only:
        questions = [q for q in questions if q.get("id") == args.only]
    if not questions:
        print("no questions to run")
        return 1

    results = []
    for question in questions:
        print(f"\n=== {question.get('id')} ===")
        result = await run_question(question)
        results.append(result)
        print(f"reply ({result['seconds']}s): {result['reply'][:400]}")
        if "correct" in result:
            print(f"expected: {json.dumps(result['expected'])}")
            print("verdict :", "CORRECT" if result["correct"] else "WRONG")
        elif not result["json_ok"]:
            print("verdict : FORMAT ERROR (reply is not a single JSON object)")

    graded = [r for r in results if "correct" in r]
    print("\n--- summary ---")
    print(f"{sum(r['json_ok'] for r in results)}/{len(results)} replies were valid JSON")
    if graded:
        print(f"{sum(r['correct'] for r in graded)}/{len(graded)} matched the expected answer")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
