"""Vercel entrypoint: exposes the FastAPI app as a Vercel Function.

vercel.json rewrites every path here, so /telegram/webhook, /status and
/logs/... all land on the same ASGI app.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.server import app  # noqa: E402

__all__ = ["app"]
