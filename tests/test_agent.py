"""Agent-loop tests with a stubbed LLM - no API key, no network to the model.

    python -m pytest tests -q
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot import agent, config, state, tools  # noqa: E402

QUESTION = ('Add 2 and 2. Reply with ONLY this JSON object and nothing else: '
            '{"answer": {"value": <number>}, "log_url": "<public wget-able URL>"}')


def tool_call(call_id, name, args):
    return {"id": call_id, "type": "function",
            "function": {"name": name, "arguments": json.dumps(args)}}


def fake_llm(responses):
    """Returns an async stand-in for llm.chat that replays `responses`."""
    queue = list(responses)

    async def _chat(messages, tools=None, tool_choice=None, model=None, retries=3):
        return queue.pop(0) if queue else {"role": "assistant", "content": "{}"}

    return _chat


def run(conversation):
    return asyncio.run(agent.answer_question(conversation))


def test_tool_then_submit_produces_exact_shape(monkeypatch):
    monkeypatch.setattr(agent.llm, "chat", fake_llm([
        {"role": "assistant", "content": None,
         "tool_calls": [tool_call("c1", "run_python", {"code": "print(2+2)"})]},
        {"role": "assistant", "content": None,
         "tool_calls": [tool_call("c2", "submit_answer",
                                  {"answer": '{"answer": {"value": 4}, "log_url": "<LOG_URL>"}'})]},
    ]))

    reply, run_log = run([{"role": "user", "content": QUESTION}])
    parsed = json.loads(reply)

    assert parsed["answer"] == {"value": 4}
    assert parsed["log_url"] == run_log.url()
    assert "<LOG_URL>" not in reply


def test_run_python_output_reaches_the_model(monkeypatch):
    seen = {}

    async def _chat(messages, tools=None, tool_choice=None, model=None, retries=3):
        if any(m.get("role") == "tool" for m in messages):
            seen["tool_content"] = [m for m in messages if m.get("role") == "tool"][-1]["content"]
            return {"role": "assistant", "content": None,
                    "tool_calls": [tool_call("c2", "submit_answer", {"answer": '{"value": 4}'})]}
        return {"role": "assistant", "content": None,
                "tool_calls": [tool_call("c1", "run_python", {"code": "print(2+2)"})]}

    monkeypatch.setattr(agent.llm, "chat", _chat)
    run([{"role": "user", "content": QUESTION}])
    assert "4" in seen["tool_content"]


def test_bare_answer_gets_wrapped_into_the_requested_shape(monkeypatch):
    monkeypatch.setattr(agent.llm, "chat", fake_llm([
        {"role": "assistant", "content": None,
         "tool_calls": [tool_call("c1", "submit_answer", {"answer": '{"value": 4}'})]},
    ]))
    parsed = json.loads(run([{"role": "user", "content": QUESTION}])[0])
    assert parsed["answer"] == {"value": 4} and parsed["log_url"]


def test_model_that_never_calls_a_tool_still_yields_json(monkeypatch):
    monkeypatch.setattr(config, "AGENT_MAX_STEPS", 2)
    monkeypatch.setattr(agent.llm, "chat", fake_llm([
        {"role": "assistant", "content": "Let me think about this."},
        {"role": "assistant", "content": "Still thinking."},
        {"role": "assistant", "content": '```json\n{"answer": {"value": 4}, "log_url": "<LOG_URL>"}\n```'},
    ]))
    parsed = json.loads(run([{"role": "user", "content": QUESTION}])[0])
    assert parsed["answer"] == {"value": 4}


def test_llm_failure_still_returns_one_json_object(monkeypatch):
    async def _boom(*a, **kw):
        raise RuntimeError("provider down")

    monkeypatch.setattr(agent.llm, "chat", _boom)
    reply, run_log = run([{"role": "user", "content": QUESTION}])
    parsed = json.loads(reply)
    assert parsed["log_url"] == run_log.url()
    assert any(e["event"] == "error" for e in run_log.events)


def test_multi_turn_uses_the_last_message(monkeypatch):
    captured = {}

    async def _chat(messages, tools=None, tool_choice=None, model=None, retries=3):
        captured["messages"] = list(messages)   # the agent keeps appending to the original
        return {"role": "assistant", "content": None,
                "tool_calls": [tool_call("c1", "submit_answer", {"answer": '{"values": [1]}'})]}

    monkeypatch.setattr(agent.llm, "chat", _chat)
    run([
        {"role": "user", "content": "Model: next = 1.02 * current."},
        {"role": "assistant", "content": '{"ok": true}'},
        {"role": "user", "content": QUESTION},
    ])
    roles = [m["role"] for m in captured["messages"]]
    assert roles == ["system", "user", "assistant", "user"]
    assert captured["messages"][-1]["content"] == QUESTION


def test_run_log_is_jsonl_with_the_expected_events(monkeypatch):
    monkeypatch.setattr(agent.llm, "chat", fake_llm([
        {"role": "assistant", "content": None,
         "tool_calls": [tool_call("c1", "submit_answer", {"answer": '{"value": 4}'})]},
    ]))
    _, run_log = run([{"role": "user", "content": QUESTION}])
    lines = [json.loads(line) for line in run_log.to_jsonl().splitlines()]
    events = [line["event"] for line in lines]

    assert events[0] == "run_start" and events[-1] == "run_end"
    assert {"submit_answer", "final_reply"} <= set(events)
    assert all(line["run_id"] == run_log.run_id for line in lines)


def test_sandbox_inherits_the_parent_import_path():
    """Serverless runtimes add the bundled deps to sys.path at runtime, so the
    subprocess only finds pandas/numpy if we pass that path along."""
    import os
    import sys

    child_path = tools._child_env()["PYTHONPATH"].split(os.pathsep)
    assert all(p in child_path for p in sys.path if p)


def test_sandbox_can_import_pandas():
    out = asyncio.run(tools.run_python("import pandas as pd; print(pd.Series([1, 2, 3]).mean())"))
    assert "2.0" in out


def test_duplicate_updates_are_ignored(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DATA_ROOT", tmp_path)
    monkeypatch.setattr(state, "_cache", None)
    assert state.is_new_update(42) is True
    assert state.is_new_update(42) is False
    assert state.is_new_update(43) is True
