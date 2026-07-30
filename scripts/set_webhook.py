#!/usr/bin/env python3
"""Point Telegram at your deployment (run once after deploying).

    python scripts/set_webhook.py                  # uses PUBLIC_BASE_URL from .env
    python scripts/set_webhook.py --url https://my-app.vercel.app
    python scripts/set_webhook.py --info           # just show current webhook
    python scripts/set_webhook.py --delete         # back to long polling
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot import config, telegram_api  # noqa: E402


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default=config.PUBLIC_BASE_URL, help="public base URL of the deployment")
    ap.add_argument("--info", action="store_true")
    ap.add_argument("--delete", action="store_true")
    args = ap.parse_args()

    if args.info:
        print(json.dumps(await telegram_api.get_webhook_info(), indent=2))
        return 0
    if args.delete:
        await telegram_api.delete_webhook()
        print("webhook deleted - long polling can take over")
        return 0

    base = (args.url or "").rstrip("/")
    if not base:
        ap.error("no URL: pass --url or set PUBLIC_BASE_URL in .env")

    me = await telegram_api.get_me()
    await telegram_api.set_webhook(f"{base}/telegram/webhook", config.TELEGRAM_WEBHOOK_SECRET)
    info = await telegram_api.get_webhook_info()
    print(f"bot: @{me.get('username')}")
    print(json.dumps(info, indent=2))
    if info.get("last_error_message"):
        print("\nNOTE: Telegram reports a delivery error above - check the URL and secret.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
