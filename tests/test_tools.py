"""fetch_url resilience: broken TLS chains and repeated URLs.

Both were found in a real production run - censusindia.gov.in (the SRS
maternal-mortality bulletins) serves an incomplete certificate chain, and the
model burned two of its steps re-fetching a URL that had already failed.
"""
import asyncio
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot import config, tools  # noqa: E402

URL = "https://example.gov.in/data.txt"


class FakeResponse:
    status_code = 200
    headers = {"content-type": "text/plain"}
    content = b"hello from the dataset"
    text = "hello from the dataset"
    url = URL


@pytest.fixture(autouse=True)
def workdir(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "WORK_DIR", tmp_path / "work")
    monkeypatch.setattr(config, "LOG_DIR", tmp_path / "logs")


def test_fetch_retries_without_verification_on_a_broken_chain(monkeypatch):
    attempts = []

    async def fake_get(url, headers, verify):
        attempts.append(verify)
        if verify:
            raise httpx.ConnectError("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed")
        return FakeResponse()

    monkeypatch.setattr(tools, "_http_get", fake_get)
    out = asyncio.run(tools.fetch_url(URL))

    assert attempts == [True, False]           # verified first, unverified only as fallback
    assert "without verification" in out
    assert "hello from the dataset" in out


def test_other_connection_errors_are_not_retried_unverified(monkeypatch):
    attempts = []

    async def fake_get(url, headers, verify):
        attempts.append(verify)
        raise httpx.ConnectError("All connection attempts failed")

    monkeypatch.setattr(tools, "_http_get", fake_get)
    out = asyncio.run(tools.fetch_url(URL))

    assert attempts == [True]
    assert out.startswith("ERROR fetching")


def test_a_repeated_url_is_served_from_the_run_cache(monkeypatch):
    attempts = []

    async def fake_get(url, headers, verify):
        attempts.append(url)
        return FakeResponse()

    monkeypatch.setattr(tools, "_http_get", fake_get)
    memo: dict[str, str] = {}
    asyncio.run(tools.fetch_url(URL, memo))
    second = asyncio.run(tools.fetch_url(URL, memo))

    assert len(attempts) == 1
    assert "Already fetched" in second and "hello from the dataset" in second


def test_a_url_that_failed_is_not_fetched_twice(monkeypatch):
    attempts = []

    async def fake_get(url, headers, verify):
        attempts.append(url)
        raise httpx.ConnectError("All connection attempts failed")

    monkeypatch.setattr(tools, "_http_get", fake_get)
    memo: dict[str, str] = {}
    asyncio.run(tools.fetch_url(URL, memo))
    second = asyncio.run(tools.fetch_url(URL, memo))

    assert len(attempts) == 1
    assert "Do not retry" in second
