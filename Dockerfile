# Fallback host (Render / Railway / Fly / Hugging Face Spaces / any VM).
# Set RUN_POLLING=1 in the environment and the container needs no webhook at all.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PORT=8000 DATA_ROOT=/tmp/tds-bot
EXPOSE 8000

CMD ["sh", "-c", "uvicorn bot.server:app --host 0.0.0.0 --port ${PORT:-8000}"]
