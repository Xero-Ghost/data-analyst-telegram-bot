# Data-Analyst Telegram Bot

An LLM agent on Telegram. Message it a data-analysis question; it works the
answer out — running Python, searching the web, downloading datasets — and
replies with **exactly one JSON object**, in the shape the question asked for,
together with a public URL to that run's JSONL log.

```
> Which state has the highest maternal mortality rate based on MOSPI data?
  Reply with ONLY this JSON object and nothing else:
  {"answer": {"state": "<state name>"}, "log_url": "<public wget-able URL to your agent's JSONL log>"}

< {"answer": {"state": "Assam"}, "log_url": "https://raw.githubusercontent.com/<user>/<repo>/main/logs/20260730T101500-a1b2c3.jsonl"}
```

Built for the TDS Project 1 grading pipeline at
[Jivraj-18/tds-p1-t2-2026-telegram-bot](https://github.com/Jivraj-18/tds-p1-t2-2026-telegram-bot).

---

## How it works

```
Telegram update
      │
      ▼
bot/server.py ── verify secret ── de-dup update_id ── answer Telegram in <60s
      │                                                (agent keeps running)
      ▼
bot/handler.py ── conversation history (bot/state.py)
      │
      ▼
bot/agent.py ── LLM loop (bot/llm.py → AI Pipe / any OpenAI-compatible API)
      │            tools: run_python · fetch_url · web_search · submit_answer
      │            every step appended to bot/runlog.py
      ▼
bot/shape.py ── force the reply into the exact JSON shape the question asked for
      │
      ├── run log published  →  https://raw.githubusercontent.com/<repo>/main/logs/<run_id>.jsonl
      └── reply sent          →  {"answer": …, "log_url": …}
```

Design decisions worth knowing:

| Decision | Why |
| --- | --- |
| Reply to **every** message with a valid JSON object | A multi-turn question arrives as separate messages and the grader reads the *last* reply. Answering each turn means the last one is always a real answer. |
| Serverless waits for the run; long-lived hosts answer early | Telegram re-sends an update if the webhook is slow. A long-lived process can reply 200 at `RESPOND_AFTER_SECONDS` and finish in the background — but a serverless instance is *suspended the moment it responds*, which silently kills the run. So on Vercel the webhook holds the request (`RESPOND_AFTER_SECONDS=0`, the default there) and the retries are dropped as duplicates. |
| `update_id` de-duplication | Makes those Telegram retries harmless — without it a retry would answer twice and corrupt a multi-turn exchange. |
| Log published to GitHub, not local disk | Serverless disks are ephemeral and per-instance; `raw.githubusercontent.com` stays `wget`-able long after the run. |
| The model never formats the final message | `bot/shape.py` extracts the JSON, wraps it under `answer`, injects the real `log_url`, and serialises it — so prose or code fences can't leak into the reply. |
| Answers computed, never recalled | The system prompt forces all arithmetic through `run_python`. |

---

## 1. Get the three credentials

**Telegram bot token** — in Telegram, message [@BotFather](https://t.me/BotFather):

```
/newbot
→ name:      TDS Data Analyst
→ username:  something_unique_bot        (must end in "bot")
```

It replies with a token like `8123456789:AAH...`. Then send `/setprivacy` →
pick your bot → **Disable**, so it reliably receives every message.

**AI Pipe token** — log in at [aipipe.org/login](https://aipipe.org/login) with
your IITM Google account and copy the token. (Any OpenAI-compatible key works:
set `LLM_BASE_URL=https://api.openai.com/v1` and `OPENAI_API_KEY` instead.)

**GitHub token** (for publishing run logs) — GitHub → Settings → Developer
settings → *Fine-grained tokens* → new token, **Repository access:** only this
repo, **Permissions:** `Contents: Read and write`. Copy the `github_pat_...`
value.

---

## 2. Run it locally

```bash
python -m venv .venv
.venv/Scripts/activate            # Windows;  source .venv/bin/activate on macOS/Linux
pip install -r requirements.txt
cp .env.example .env              # then fill in the values
```

Check every moving part before doing anything else:

```bash
python scripts/selftest.py
```

```
[PASS] env: model=gpt-4.1-mini log_store=github data_root=...
[PASS] llm chat: gpt-4.1-mini -> 'ready'
[PASS] llm tool calling: tool_calls=['run_python']
[PASS] telegram getMe: @your_bot
[PASS] run_python: pandas works in the sandbox
[PASS] web_search: [search results via duckduckgo-lite]
[PASS] fetch_url: 41230 chars of readable text
[PASS] log publish: https://raw.githubusercontent.com/...
```

Ask a question without involving Telegram at all:

```bash
python scripts/ask.py "Monthly sales: 12.5, 18.3, 9.7. Mean rounded to 2 decimals? Reply with ONLY this JSON object and nothing else: {\"answer\": {\"mean\": <number>}, \"log_url\": \"<url>\"}"
```

Talk to the real bot from your laptop (long polling, no public URL needed):

```bash
python -m bot.polling
```

Now message your bot in Telegram — it answers from your machine.

---

## 3. Deploy to Vercel

The repo is Vercel-ready: `api/index.py` is the entrypoint, `vercel.json` sets
`maxDuration: 300`.

1. Push this repo to GitHub (public).
2. [vercel.com/new](https://vercel.com/new) → import the repo → **Deploy**.
3. Project → Settings → **Environment Variables**, add:

   | Name | Value |
   | --- | --- |
   | `TELEGRAM_BOT_TOKEN` | from BotFather |
   | `TELEGRAM_WEBHOOK_SECRET` | any random string |
   | `AIPIPE_TOKEN` | from aipipe.org |
   | `LLM_MODEL` | `gpt-4.1-mini` |
   | `LOG_STORE` | `github` |
   | `GITHUB_TOKEN` | fine-grained PAT |
   | `GITHUB_REPO` | `<user>/<repo>` |
   | `PUBLIC_BASE_URL` | `https://<project>.vercel.app` |

4. Settings → Functions → make sure **Fluid Compute** is on (that is what
   allows the 300s `maxDuration`), then **Redeploy**.
5. Point Telegram at the deployment:

   ```bash
   python scripts/set_webhook.py --url https://<project>.vercel.app
   ```

   or open `https://<project>.vercel.app/setup-webhook?secret=<TELEGRAM_WEBHOOK_SECRET>` once.
6. Verify: `https://<project>.vercel.app/status` should show
   `"telegram_token_set": true`, `"llm_key_set": true`, `"missing_env": []`.

Then message the bot from a normal Telegram account — replies should arrive in
20–60s.

### Other hosts

`Dockerfile` and `render.yaml` are included. On an always-on host set
`RUN_POLLING=1` and the process long-polls Telegram — no webhook, no cold
starts, and `LOG_STORE=local` works because the disk persists:

```bash
docker build -t tds-bot . && docker run --env-file .env -e RUN_POLLING=1 -p 8000:8000 tds-bot
```

---

## 4. Testing workflow

### a. Unit tests — no keys, no network

```bash
python -m pytest tests -q
```

Covers JSON extraction from messy model output, the shaping rules
(`{"state": …}` → `{"answer": {"state": …}, "log_url": …}`), the agent loop with
a stubbed LLM, log structure, webhook auth, update de-duplication and the
early-response path.

### b. Offline evals — real agent, no Telegram

`evals/questions.json` uses the same `messages` shape as the official grading
repo, so questions copy across.

```bash
python scripts/run_evals.py                       # all questions
python scripts/run_evals.py --only inline_stats   # just one
```

```
=== inline_stats ===
reply (23.1s): {"answer": {"mean": 18.19, "median": 18.05}, "log_url": "https://raw.githubusercontent.com/..."}
expected: {"mean": 18.19, "median": 18.05}
verdict : CORRECT

--- summary ---
6/6 replies were valid JSON
5/5 matched the expected answer
```

Questions with an `expected` are graded exactly like the real pipeline;
questions without one (the MOSPI ones) just prove the search → fetch → compute
path works. Add your own questions to the file — that is the cheapest way to
find weak spots.

### c. End-to-end against the official grading pipeline

This is the one that proves the whole thing, because it drives your bot from a
real Telegram **user account**, exactly as the graders will.

```bash
git clone https://github.com/Jivraj-18/tds-p1-t2-2026-telegram-bot
cd tds-p1-t2-2026-telegram-bot
pip install -r requirements.txt
```

1. Get Telegram API credentials at [my.telegram.org](https://my.telegram.org) →
   *API development tools* → create an app → note `api_id` and `api_hash`.
2. `cp .env.example .env`, fill `TELEGRAM_API_ID` / `TELEGRAM_API_HASH`.
3. `python login.py` → enter your phone and the code Telegram sends you → paste
   the printed string into `TELEGRAM_SESSION_STRING` in `.env`.
4. Roster (`students.csv`) — one row, yours:

   ```csv
   email,github_url,telegram_bot_username
   you@example.com,https://github.com/<user>/<repo>,@your_bot
   ```

5. Put real questions in `evals/questions.json` (copy them from this repo's
   `evals/questions.json`, and set `expected` to the answer you expect).
6. Run the three stages:

   ```bash
   python generate.py --students students.csv     # inputs.json + key.json
   python collect.py  --students students.csv     # messages your bot over Telegram
   python grade.py    --students students.csv     # data/<slug>/grade.json
   ```

`collect.py` prints `ok` / `timeout` / `bad_bot` per question, and `grade.py`
prints `n/m correct`. What to look for:

| Symptom | Cause |
| --- | --- |
| `bad_bot` | wrong username in the roster, or it doesn't end in `bot` |
| `timeout` | bot not deployed / webhook not set / agent slower than `timeout_seconds` |
| `format_error` | the reply was not exactly one JSON object |
| `expected X, got Y` | shape is right, analysis is wrong — read the run log |

### d. Reading a run log

Every reply carries a `log_url`. `wget` it and you get one JSON object per
line: `run_start`, each `llm_response`, each `tool_result` (code, output,
fetched URL), `submit_answer`, `final_reply`, `run_end`.

```bash
wget -qO - "<log_url>" | python -m json.tool --json-lines
```

That log is the fastest way to see *why* an answer was wrong — usually a source
that didn't parse, or a filter the model skipped.

---

## Configuration reference

| Variable | Default | Notes |
| --- | --- | --- |
| `TELEGRAM_BOT_TOKEN` | — | required |
| `TELEGRAM_WEBHOOK_SECRET` | `tds-webhook-secret` | must match what `setWebhook` sent |
| `AIPIPE_TOKEN` / `OPENAI_API_KEY` | — | required |
| `LLM_BASE_URL` | `https://aipipe.org/openai/v1` | any OpenAI-compatible endpoint |
| `LLM_MODEL` | `gpt-4.1-mini` | needs tool calling |
| `LLM_FALLBACK_MODEL` | `gpt-4.1-nano` | used if the primary model errors |
| `LOG_STORE` | `github` if a token+repo are set, else `local` | |
| `GITHUB_TOKEN` / `GITHUB_REPO` / `GITHUB_BRANCH` | — / — / `main` | for `LOG_STORE=github` |
| `PUBLIC_BASE_URL` | — | deployment URL, no trailing slash |
| `AGENT_MAX_STEPS` | `12` | tool calls per question |
| `AGENT_TIME_BUDGET` | `180` | seconds per answer; the grader's 300s covers a whole multi-turn exchange, and a serverless function is killed at 300s |
| `RESPOND_AFTER_SECONDS` | `0` on serverless, `50` elsewhere | `0` = hold the webhook until the answer is sent. Only raise it where the process outlives the response |
| `ALWAYS_INCLUDE_LOG_URL` | `false` | by default the reply mirrors the requested shape exactly; set `1` to append `log_url` even when the question doesn't ask for it |
| `TAVILY_API_KEY` / `SERPER_API_KEY` / `BRAVE_API_KEY` | — | optional; better search than the keyless fallbacks |
| `RUN_POLLING` | unset | `1` = long-poll instead of webhook (always-on hosts) |

Search backends are tried in order: Tavily → Serper → Brave → DuckDuckGo lite →
DuckDuckGo HTML → Wikipedia. The keyless ones work, but a free Tavily key
noticeably improves dataset-hunting questions.

---

## Repo layout

```
api/index.py          Vercel entrypoint (exports the FastAPI app)
bot/config.py         every env var, one place
bot/server.py         webhook, /status, /logs/<id>.jsonl, /setup-webhook
bot/polling.py        long-polling runner (python -m bot.polling)
bot/handler.py        update → answer → publish log → reply
bot/agent.py          the LLM loop and system prompt
bot/llm.py            OpenAI-compatible chat client
bot/tools.py          run_python, fetch_url, web_search
bot/shape.py          force the reply into the requested JSON shape
bot/runlog.py         JSONL run log + GitHub/local publishing
bot/state.py          conversation history + update de-duplication
scripts/selftest.py   check env, LLM, Telegram, tools, log publishing
scripts/ask.py        ask one question from the CLI
scripts/run_evals.py  run evals/questions.json through the agent
scripts/set_webhook.py  point Telegram at a deployment
tests/                offline tests (no keys needed)
```

## Troubleshooting

| Problem | Fix |
| --- | --- |
| Bot silent on Telegram | `python scripts/set_webhook.py --info` — check `url`, `pending_update_count` and `last_error_message` |
| `403` in Telegram's webhook error | `TELEGRAM_WEBHOOK_SECRET` differs between the deployment and `setWebhook` |
| Replies contain prose | shouldn't happen — `bot/shape.py` serialises the reply; check `/status` shows the deployed version |
| `log_url` 404s | GitHub PAT lacks `Contents: Read and write`, or `GITHUB_REPO` is wrong — the `log_publish_failed` event in the run log says which |
| Everything times out | `LLM_MODEL` may not support tool calling, or the AI Pipe quota is spent (`curl https://aipipe.org/usage -H "Authorization: Bearer $AIPIPE_TOKEN"`) |
| Vercel function times out at 60s | Fluid Compute is off; turn it on so `maxDuration: 300` applies |
| Quick questions answered, slow ones silent (no log published either) | `RESPOND_AFTER_SECONDS` is above 0 on a serverless host — the instance froze mid-run. Set it to `0` |

## License

MIT — see [LICENSE](LICENSE).
