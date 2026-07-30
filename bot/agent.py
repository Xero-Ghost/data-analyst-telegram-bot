"""The agent loop: question in, one JSON object out, every step logged."""
from __future__ import annotations

import json
import time
from typing import Any

from . import config, llm, shape, tools
from .runlog import RunLog

SYSTEM_PROMPT = """\
You are a meticulous data-analyst agent. You get one data-analysis question per \
conversation and you answer it with exactly one JSON object.

OUTPUT CONTRACT
- The message states the exact JSON shape it wants. Your answer must match that \
shape exactly - same keys, same nesting, same types.
- Deliver it by calling submit_answer exactly once, with the JSON object \
serialised as a string.
- If the requested shape contains "log_url", put the literal string "<LOG_URL>" \
there; it is substituted with the real URL automatically.
- Values carry no units, currency symbols, thousands separators or commentary \
unless the question asks for them. Numbers must be JSON numbers, not strings.
- Obey every formatting instruction literally: rounding, decimal places, sort \
order, ascending/descending, list vs object, upper/lower case.

METHOD
1. Restate to yourself: what is asked, the exact output shape, the filters \
(year, unit, region), and any rounding.
2. Data inline in the message -> go straight to run_python and copy the numbers \
exactly as given.
3. Data in a public dataset (MOSPI, NSO, SRS, NFHS, data.gov.in, RBI, Census, \
World Bank, ...) -> web_search for the authoritative page, fetch_url to pull it, \
run_python to compute. Prefer primary/official sources. Use the latest published \
figure unless the question names a period.
4. Never do arithmetic mentally. Sums, means, medians, percentages, growth \
rates, regressions, correlations, sorting and date maths all go through \
run_python, printed to full precision, then rounded as asked.
5. Before submitting, check: right entity, right direction (highest vs lowest), \
right units, right shape, plausible magnitude.

PRACTICAL NOTES
- Indian official statistics: mospi.gov.in and esankhyiki.mospi.gov.in (MOSPI), \
data.gov.in, SRS bulletins (MMR/IMR), NFHS (health), RBI DBIE (finance), \
censusindia.gov.in.
- Spell names exactly as the source does ("Odisha", "Uttar Pradesh", "Kerala").
- Multi-turn: earlier turns are context - answer the LAST user message.
- Budget is limited. Two or three good sources beat ten mediocre ones; if a \
fetch fails twice, try a different source instead of retrying.
- fetch_url already gives you the readable text of a PDF and the head of a \
CSV/Excel file - only re-open the saved file in run_python when you need more \
of it than the preview showed.
- Check how old a source is. Prefer the most recent release; an older bulletin \
is a fallback, not a first choice.
- If you cannot fully verify, submit your best-supported answer anyway. Never \
submit null, an apology, or an error message as the answer.
"""

FINAL_NUDGE = (
    "Time is up. Reply now with ONLY the final JSON object in the exact shape the "
    "question asked for - no prose, no code fences.\n"
    "Base it on the figures you actually retrieved in this run. If the sources did "
    "not load, fall back to what you reliably know about this dataset - the "
    "long-standing published value - rather than picking a plausible-sounding name. "
    "A remembered fact beats a guess; a guess is always wrong."
)


def _tool_args(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw or "{}")
        return parsed if isinstance(parsed, dict) else {"value": parsed}
    except (ValueError, TypeError):
        return {}


def _submitted_answer(args: dict[str, Any]) -> Any:
    """submit_answer's payload, whether the model sent a JSON string or an object."""
    payload = args.get("answer", args)
    if isinstance(payload, str):
        parsed = shape.extract_json(payload)
        return parsed if parsed is not None else payload
    return payload


def build_messages(conversation: list[dict[str, str]]) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM_PROMPT}]
    history = conversation[-(2 * config.MAX_HISTORY_TURNS):]
    messages.extend({"role": turn["role"], "content": turn["content"]} for turn in history)
    return messages


async def answer_question(
    conversation: list[dict[str, str]],
    run_log: RunLog | None = None,
    time_budget: float | None = None,
) -> tuple[str, RunLog]:
    """Run the agent over `conversation` (last entry = the message to answer).

    Returns (reply_text, run_log). reply_text is always exactly one JSON object.
    """
    run_log = run_log or RunLog()
    budget = time_budget or config.AGENT_TIME_BUDGET
    deadline = time.time() + budget
    question = next((t["content"] for t in reversed(conversation) if t["role"] == "user"), "")
    log_url = run_log.url()

    run_log.add("run_start", model=config.LLM_MODEL, version=config.VERSION,
                log_url=log_url, question=question, turns=len(conversation))

    messages = build_messages(conversation)
    fetched: dict[str, str] = {}   # per-run URL cache: a repeat fetch must not burn a step
    answer: Any = None
    status = "ok"

    try:
        for step in range(1, config.AGENT_MAX_STEPS + 1):
            if time.time() > deadline - 20:
                run_log.add("budget_exhausted", step=step)
                break

            run_log.add("llm_request", step=step, messages=len(messages))
            message = await llm.chat(messages, tools=tools.TOOL_SPECS)
            tool_calls = message.get("tool_calls") or []
            run_log.add("llm_response", step=step, content=message.get("content"),
                        tool_calls=[{"name": c["function"]["name"],
                                     "arguments": c["function"].get("arguments")}
                                    for c in tool_calls])
            messages.append(message)

            if not tool_calls:
                # No tool call: either it answered in prose, or it is stuck.
                parsed = shape.extract_json(message.get("content") or "")
                if parsed is not None:
                    answer = parsed
                    break
                messages.append({"role": "user", "content":
                                 "Continue: use a tool, or call submit_answer with the final JSON."})
                continue

            submitted = False
            for call in tool_calls:
                name = call["function"]["name"]
                args = _tool_args(call["function"].get("arguments"))

                if name == "submit_answer":
                    answer = _submitted_answer(args)
                    run_log.add("submit_answer", step=step, answer=answer)
                    submitted = True
                    break

                started = time.time()
                result = await tools.execute(name, args, fetched)
                run_log.add("tool_result", step=step, tool=name, args=args,
                            seconds=round(time.time() - started, 2), result=result)
                messages.append({"role": "tool", "tool_call_id": call["id"],
                                 "name": name, "content": result})
            if submitted:
                break

        if answer is None:
            # Out of steps or time: one last plain call, no tools, JSON only.
            run_log.add("final_nudge")
            messages.append({"role": "user", "content": FINAL_NUDGE})
            message = await llm.chat(messages, tools=None)
            run_log.add("llm_response", step="final", content=message.get("content"))
            answer = shape.extract_json(message.get("content") or "")

    except Exception as exc:
        status = "error"
        run_log.add("error", error=f"{type(exc).__name__}: {exc}")

    if answer is None:
        status = "no_answer" if status == "ok" else status
        reply = shape.fallback_reply(question, log_url, "agent could not produce an answer")
    else:
        reply = shape.finalize(answer, question, log_url)

    run_log.add("final_reply", reply=reply, status=status)
    run_log.add("run_end", status=status, seconds=round(budget - (deadline - time.time()), 2))
    return reply, run_log
