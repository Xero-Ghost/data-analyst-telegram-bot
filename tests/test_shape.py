"""Offline tests for the reply-shaping rules - no network, no API keys.

    python -m pytest tests -q
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot import shape  # noqa: E402

LOG_URL = "https://raw.githubusercontent.com/me/repo/main/logs/run1.jsonl"
ASK_WRAPPED = ('Which state has the highest maternal mortality rate? Reply with ONLY this JSON '
               'object and nothing else: {"answer": {"state": "<state name>"}, "log_url": "<url>"}')
ASK_BARE = 'Reply with ONLY a JSON object like {"state": "<state name>"}'


def test_extract_plain_json():
    assert shape.extract_json('{"state": "Assam"}') == {"state": "Assam"}


def test_extract_from_code_fence_and_prose():
    text = 'Here you go:\n```json\n{"state": "Assam"}\n```\nHope that helps!'
    assert shape.extract_json(text) == {"state": "Assam"}


def test_extract_ignores_trailing_prose():
    assert shape.extract_json('{"a": {"b": 1}} — done') == {"a": {"b": 1}}


def test_extract_handles_braces_inside_strings():
    assert shape.extract_json('{"note": "a } brace"}') == {"note": "a } brace"}


def test_wrapped_shape_is_built_when_the_model_returns_only_the_inner_answer():
    reply = shape.finalize({"state": "Assam"}, ASK_WRAPPED, LOG_URL)
    assert json.loads(reply) == {"answer": {"state": "Assam"}, "log_url": LOG_URL}


def test_already_wrapped_answer_keeps_its_shape():
    reply = shape.finalize({"answer": {"state": "Assam"}, "log_url": "<LOG_URL>"}, ASK_WRAPPED, LOG_URL)
    assert json.loads(reply) == {"answer": {"state": "Assam"}, "log_url": LOG_URL}


def test_bare_shape_gets_no_log_url_appended():
    reply = shape.finalize({"state": "Assam"}, ASK_BARE, LOG_URL)
    assert json.loads(reply) == {"state": "Assam"}


def test_placeholder_is_replaced_anywhere():
    reply = shape.finalize({"answer": {"url": "<LOG_URL>"}, "log_url": "<LOG_URL>"}, ASK_WRAPPED, LOG_URL)
    assert json.loads(reply)["answer"]["url"] == LOG_URL


def test_list_answers_are_wrapped_not_mangled():
    reply = shape.finalize([1, 2, 3], ASK_WRAPPED, LOG_URL)
    assert json.loads(reply) == {"answer": [1, 2, 3], "log_url": LOG_URL}


def test_reply_is_always_a_single_parseable_json_object():
    for payload in ({"a": 1}, [1, 2], "text", 42, None):
        json.loads(shape.finalize(payload, ASK_WRAPPED, LOG_URL))


def test_fallback_reply_is_valid_json_in_the_requested_shape():
    parsed = json.loads(shape.fallback_reply(ASK_WRAPPED, LOG_URL, "boom"))
    assert parsed["log_url"] == LOG_URL and "answer" in parsed
