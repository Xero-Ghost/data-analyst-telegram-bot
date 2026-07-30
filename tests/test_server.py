"""Webhook behaviour: auth, de-duplication, and answering Telegram in time."""
import asyncio
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot import config, handler, server, state  # noqa: E402

SECRET = "test-secret"
UPDATE = {"update_id": 1001,
          "message": {"chat": {"id": 7}, "text": "What is 2+2? Reply with ONLY {\"v\": <number>}"}}


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DATA_ROOT", tmp_path)
    monkeypatch.setattr(state, "_cache", None)
    monkeypatch.setattr(config, "TELEGRAM_WEBHOOK_SECRET", SECRET)


@pytest.fixture
def client():
    with TestClient(server.app) as c:
        yield c


def stub_handler(monkeypatch, calls, delay=0.0):
    async def _handle(update, check_duplicate=True):
        await asyncio.sleep(delay)
        calls.append(update)
        return "ok"

    monkeypatch.setattr(handler, "handle_update", _handle)


def test_health_and_status(client):
    assert client.get("/health").status_code == 200
    body = client.get("/status").json()
    assert "model" in body and "log_store" in body


def test_webhook_rejects_a_wrong_secret(client, monkeypatch):
    calls = []
    stub_handler(monkeypatch, calls)
    resp = client.post("/telegram/webhook", json=UPDATE,
                       headers={"X-Telegram-Bot-Api-Secret-Token": "nope"})
    assert resp.status_code == 403
    assert calls == []


def test_webhook_processes_a_valid_update(client, monkeypatch):
    calls = []
    stub_handler(monkeypatch, calls)
    resp = client.post("/telegram/webhook", json=UPDATE,
                       headers={"X-Telegram-Bot-Api-Secret-Token": SECRET})
    assert resp.status_code == 200 and resp.json() == {"ok": True}
    assert len(calls) == 1


def test_webhook_ignores_a_resent_update(client, monkeypatch):
    calls = []
    stub_handler(monkeypatch, calls)
    headers = {"X-Telegram-Bot-Api-Secret-Token": SECRET}
    client.post("/telegram/webhook", json=UPDATE, headers=headers)
    second = client.post("/telegram/webhook", json=UPDATE, headers=headers)
    assert second.json() == {"ok": True, "duplicate": True}
    assert len(calls) == 1


def test_serverless_mode_finishes_the_run_before_responding(client, monkeypatch):
    """With RESPOND_AFTER_SECONDS=0 (the serverless default) the response must
    come after the answer was sent - the platform suspends the instance the
    moment we reply, so an unfinished background run would be lost."""
    calls = []
    stub_handler(monkeypatch, calls, delay=0.2)
    monkeypatch.setattr(config, "RESPOND_AFTER_SECONDS", 0.0)

    resp = client.post("/telegram/webhook", json=UPDATE,
                       headers={"X-Telegram-Bot-Api-Secret-Token": SECRET})
    assert resp.json() == {"ok": True}
    assert len(calls) == 1


def test_slow_run_answers_telegram_early_and_keeps_working(client, monkeypatch):
    """Telegram re-sends after ~60s, so a long analysis must still get a 200
    quickly while the agent carries on in the background."""
    calls = []
    stub_handler(monkeypatch, calls, delay=0.4)
    monkeypatch.setattr(config, "RESPOND_AFTER_SECONDS", 0.05)

    resp = client.post("/telegram/webhook", json=UPDATE,
                       headers={"X-Telegram-Bot-Api-Secret-Token": SECRET})
    assert resp.json() == {"ok": True, "status": "processing"}
    assert calls == []                      # still running at response time

    asyncio.run(asyncio.sleep(0))           # let the client's loop drain
    deadline = 2.0
    while not calls and deadline > 0:       # background task completes afterwards
        asyncio.run(asyncio.sleep(0.05))
        deadline -= 0.05
    assert len(calls) == 1


def test_log_endpoint_rejects_path_traversal(client):
    assert client.get("/logs/..%2F..%2Fetc%2Fpasswd").status_code in (400, 404)
