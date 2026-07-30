"""Run log: one JSON object per line, published at a public URL.

The bot has to hand the grader a `log_url` in the same message as the answer,
so the URL is derived from the run id up front and the file is uploaded just
before the reply goes out.

Two stores:
  github - commits logs/<run_id>.jsonl to a public repo and serves it from
           raw.githubusercontent.com. Survives redeploys; the right choice on
           serverless hosts, whose disks are ephemeral.
  local  - writes the file next to the app and serves it from
           PUBLIC_BASE_URL/logs/<run_id>.jsonl.
"""
from __future__ import annotations

import base64
import json
import time
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx

from . import config

FIELD_LIMIT = 6000


def _clip(value: Any) -> Any:
    if isinstance(value, str) and len(value) > FIELD_LIMIT:
        return value[:FIELD_LIMIT] + f"...[+{len(value) - FIELD_LIMIT} chars]"
    if isinstance(value, list):
        return [_clip(v) for v in value]
    if isinstance(value, dict):
        return {k: _clip(v) for k, v in value.items()}
    return value


def new_run_id() -> str:
    return f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:6]}"


class RunLog:
    def __init__(self, run_id: str | None = None):
        self.run_id = run_id or new_run_id()
        self.started = time.time()
        self.events: list[dict[str, Any]] = []

    # -- recording ---------------------------------------------------------
    def add(self, event: str, **fields: Any) -> None:
        self.events.append({
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "elapsed_s": round(time.time() - self.started, 3),
            "run_id": self.run_id,
            "event": event,
            **{k: _clip(v) for k, v in fields.items()},
        })

    def to_jsonl(self) -> str:
        return "".join(json.dumps(e, ensure_ascii=False, default=str) + "\n" for e in self.events)

    # -- publishing --------------------------------------------------------
    @property
    def filename(self) -> str:
        return f"{self.run_id}.jsonl"

    def url(self) -> str:
        """Where this run's log will live - known before it is uploaded."""
        if config.LOG_STORE == "github" and config.GITHUB_REPO:
            return (f"https://raw.githubusercontent.com/{config.GITHUB_REPO}/"
                    f"{config.GITHUB_BRANCH}/{config.GITHUB_LOG_DIR}/{self.filename}")
        base = config.PUBLIC_BASE_URL or "http://localhost:8000"
        return f"{base}/logs/{self.filename}"

    def write_local(self) -> None:
        config.ensure_dirs()
        body = self.to_jsonl()
        (config.LOG_DIR / self.filename).write_text(body, encoding="utf-8")
        (config.LOG_DIR / "run.jsonl").write_text(body, encoding="utf-8")

    async def publish(self) -> str:
        """Upload the log and return its public URL. Never raises - a failed
        upload must not cost the student the answer."""
        try:
            self.write_local()
        except Exception as exc:  # read-only fs, disk full, ...
            self.add("log_local_write_failed", error=f"{type(exc).__name__}: {exc}")

        if config.LOG_STORE == "github" and config.GITHUB_TOKEN and config.GITHUB_REPO:
            try:
                await self._push_to_github()
            except Exception as exc:
                self.add("log_publish_failed", error=f"{type(exc).__name__}: {exc}")
        return self.url()

    async def _push_to_github(self) -> None:
        path = f"{config.GITHUB_LOG_DIR}/{self.filename}"
        api = f"https://api.github.com/repos/{config.GITHUB_REPO}/contents/{path}"
        headers = {
            "Authorization": f"Bearer {config.GITHUB_TOKEN}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        payload = {
            "message": f"log: run {self.run_id}",
            "content": base64.b64encode(self.to_jsonl().encode("utf-8")).decode("ascii"),
            "branch": config.GITHUB_BRANCH,
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            existing = await client.get(api, headers=headers,
                                        params={"ref": config.GITHUB_BRANCH})
            if existing.status_code == 200:
                payload["sha"] = existing.json().get("sha")
            resp = await client.put(api, headers=headers, json=payload)
        if resp.status_code >= 300:
            raise RuntimeError(f"github {resp.status_code}: {resp.text[:200]}")
