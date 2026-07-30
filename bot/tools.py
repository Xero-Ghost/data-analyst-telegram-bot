"""The agent's hands: run Python, fetch a URL, search the web.

Every tool returns a plain string - that string is what the model sees next,
so each one is written to be short, factual and immediately useful (previews
plus the local path of anything big, so run_python can open it properly).
"""
from __future__ import annotations

import asyncio
import io
import json
import os
import re
import sys
import urllib.parse
import uuid
from pathlib import Path
from typing import Any

import httpx

from . import config

MAX_TOOL_CHARS = 12000
MAX_DOWNLOAD_BYTES = 40 * 1024 * 1024


def truncate(text: str, limit: int = MAX_TOOL_CHARS) -> str:
    text = text or ""
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... [truncated, {len(text) - limit} more chars]"


# --------------------------------------------------------------------------
# tool schemas handed to the model
# --------------------------------------------------------------------------
TOOL_SPECS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "run_python",
            "description": (
                "Execute a Python 3 script and return its stdout/stderr. pandas, numpy, "
                "openpyxl, bs4, httpx and pypdf are installed and the script has network "
                "access. Use this for ALL arithmetic, statistics, regression, sorting, "
                "date maths and parsing of downloaded files. You must print() what you "
                "want to see. Files saved by fetch_url are in the working directory."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "The complete Python script to run."},
                },
                "required": ["code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_url",
            "description": (
                "Download a URL and return a readable preview. HTML becomes text, JSON is "
                "pretty-printed, CSV/Excel show shape + head, PDF shows extracted text. The "
                "raw file is also saved locally and the path is returned so run_python can "
                "read the full data."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "Absolute http(s) URL."},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": (
                "Search the web for pages, datasets or documents. Use it to locate the "
                "authoritative source (e.g. MOSPI / data.gov.in / RBI / NSO releases) before "
                "fetching it. Returns title, URL and snippet for each hit."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "num_results": {"type": "integer", "description": "Default 6, max 10."},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "submit_answer",
            "description": (
                "Submit the final answer and end the run. Call this exactly once, with the "
                "JSON object shaped exactly as the question demanded."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "answer": {
                        "type": "string",
                        "description": (
                            "The final JSON object, serialised as a JSON string. Example: "
                            "{\"answer\": {\"state\": \"Assam\"}, \"log_url\": \"<LOG_URL>\"}"
                        ),
                    },
                },
                "required": ["answer"],
            },
        },
    },
]


# --------------------------------------------------------------------------
# run_python
# --------------------------------------------------------------------------
async def run_python(code: str, timeout: float | None = None) -> str:
    timeout = timeout or config.PYTHON_EXEC_TIMEOUT
    config.ensure_dirs()
    script = config.WORK_DIR / f"snippet_{uuid.uuid4().hex[:8]}.py"
    script.write_text(code, encoding="utf-8")

    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, str(script),
            cwd=str(config.WORK_DIR),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**os.environ, "PYTHONIOENCODING": "utf-8", "MPLBACKEND": "Agg"},
        )
    except NotImplementedError:  # no subprocess support on this platform
        return _run_python_inprocess(code)

    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return f"ERROR: script exceeded {timeout:.0f}s and was killed. Make it cheaper."

    out = stdout.decode("utf-8", "replace").strip()
    err = stderr.decode("utf-8", "replace").strip()
    parts = []
    if out:
        parts.append(f"stdout:\n{out}")
    if err:
        parts.append(f"stderr:\n{err}")
    if not parts:
        parts.append("(no output - remember to print() the values you need)")
    return truncate("\n\n".join(parts))


def _run_python_inprocess(code: str) -> str:
    """Last-resort fallback if the platform forbids subprocesses."""
    import contextlib

    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            exec(compile(code, "<agent>", "exec"), {"__name__": "__main__"})
    except Exception as exc:  # the traceback is useful to the model
        import traceback
        return truncate(buf.getvalue() + "\n" + traceback.format_exc())
    return truncate(buf.getvalue() or "(no output - remember to print())")


# --------------------------------------------------------------------------
# fetch_url
# --------------------------------------------------------------------------
def _filename_for(url: str, content_type: str) -> str:
    name = os.path.basename(urllib.parse.urlparse(url).path) or "download"
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", name)[:60]
    if "." not in name:
        ext = {
            "text/html": ".html", "application/json": ".json", "text/csv": ".csv",
            "application/pdf": ".pdf",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
            "application/vnd.ms-excel": ".xls",
        }.get(content_type.split(";")[0].strip(), ".bin")
        name += ext
    return f"{uuid.uuid4().hex[:6]}_{name}"


def _html_to_text(raw: bytes) -> str:
    from bs4 import BeautifulSoup

    try:
        soup = BeautifulSoup(raw, "lxml")
    except Exception:
        soup = BeautifulSoup(raw, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg", "form"]):
        tag.decompose()

    links = []
    for a in soup.find_all("a", href=True)[:400]:
        label = " ".join(a.get_text(" ", strip=True).split())
        href = a["href"]
        if label and re.search(r"\.(csv|xlsx?|json|pdf|zip)(\?|$)", href, re.I):
            links.append(f"  - {label} -> {href}")

    text = soup.get_text("\n", strip=True)
    text = re.sub(r"\n{3,}", "\n\n", text)
    n_tables = len(soup.find_all("table"))
    head = f"[html: {n_tables} <table> elements found]"
    if links:
        head += "\ndata-file links on this page:\n" + "\n".join(links[:25])
    return head + "\n\n" + text


def _tabular_preview(path: Path) -> str:
    import pandas as pd

    suffix = path.suffix.lower()
    try:
        if suffix in (".xlsx", ".xls", ".xlsm"):
            xl = pd.ExcelFile(path)
            out = [f"[excel: sheets = {xl.sheet_names}]"]
            for sheet in xl.sheet_names[:3]:
                df = xl.parse(sheet, nrows=15)
                out.append(f"--- sheet '{sheet}' (first rows) ---\n{df.to_string(max_cols=25)}")
            return "\n".join(out)
        sep = "\t" if suffix in (".tsv", ".tab") else None
        df = pd.read_csv(path, sep=sep, engine="python", nrows=5000)
        return (f"[table: {df.shape[0]} rows (capped at 5000) x {df.shape[1]} cols]\n"
                f"columns: {list(df.columns)}\n\n{df.head(15).to_string(max_cols=25)}")
    except Exception as exc:
        return f"[could not parse as a table: {type(exc).__name__}: {exc}]"


def _pdf_preview(path: Path) -> str:
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        pages = [(p.extract_text() or "") for p in reader.pages[:15]]
        return f"[pdf: {len(reader.pages)} pages, text of first {len(pages)}]\n" + "\n".join(pages)
    except Exception as exc:
        return f"[could not extract pdf text: {type(exc).__name__}: {exc}]"


async def fetch_url(url: str) -> str:
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url.lstrip("/")
    config.ensure_dirs()

    headers = {
        "User-Agent": config.USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/json,text/csv,*/*",
        "Accept-Language": "en-US,en;q=0.9",
    }
    try:
        async with httpx.AsyncClient(timeout=config.HTTP_TIMEOUT, follow_redirects=True,
                                     verify=True) as client:
            resp = await client.get(url, headers=headers)
    except Exception as exc:
        return f"ERROR fetching {url}: {type(exc).__name__}: {exc}"

    if resp.status_code >= 400:
        return f"ERROR fetching {url}: HTTP {resp.status_code}\n{truncate(resp.text, 1000)}"

    raw = resp.content[:MAX_DOWNLOAD_BYTES]
    ctype = resp.headers.get("content-type", "")
    path = config.WORK_DIR / _filename_for(str(resp.url), ctype)
    path.write_bytes(raw)

    lower_url = str(resp.url).lower()
    header = (f"URL: {resp.url}\ncontent-type: {ctype or 'unknown'}\nbytes: {len(raw)}\n"
              f"saved_to: {path.name}  (in the run_python working directory)\n")

    if "json" in ctype or lower_url.endswith(".json"):
        try:
            body = json.dumps(json.loads(raw.decode("utf-8", "replace")), indent=1)[:MAX_TOOL_CHARS]
        except Exception:
            body = raw.decode("utf-8", "replace")
    elif "pdf" in ctype or lower_url.endswith(".pdf"):
        body = _pdf_preview(path)
    elif re.search(r"\.(csv|tsv|xlsx?|xlsm)(\?|$)", lower_url) or "spreadsheet" in ctype or "csv" in ctype:
        body = _tabular_preview(path)
    elif "html" in ctype or "xml" in ctype or raw[:200].lstrip().lower().startswith(b"<"):
        body = _html_to_text(raw)
    else:
        body = raw.decode("utf-8", "replace")

    return truncate(header + "\n" + body)


# --------------------------------------------------------------------------
# web_search - first backend that answers wins
# --------------------------------------------------------------------------
def _format_hits(hits: list[dict[str, str]], backend: str) -> str:
    if not hits:
        return ""
    lines = [f"[search results via {backend}]"]
    for i, h in enumerate(hits, 1):
        lines.append(f"{i}. {h.get('title', '').strip()}\n   {h.get('url', '')}\n   "
                     f"{' '.join((h.get('snippet') or '').split())[:400]}")
    return "\n".join(lines)


async def _tavily(client: httpx.AsyncClient, query: str, n: int) -> list[dict[str, str]]:
    r = await client.post("https://api.tavily.com/search", json={
        "api_key": config.TAVILY_API_KEY, "query": query,
        "max_results": n, "search_depth": "basic", "include_answer": False,
    })
    r.raise_for_status()
    return [{"title": x.get("title", ""), "url": x.get("url", ""), "snippet": x.get("content", "")}
            for x in r.json().get("results", [])]


async def _serper(client: httpx.AsyncClient, query: str, n: int) -> list[dict[str, str]]:
    r = await client.post("https://google.serper.dev/search",
                          headers={"X-API-KEY": config.SERPER_API_KEY},
                          json={"q": query, "num": n})
    r.raise_for_status()
    return [{"title": x.get("title", ""), "url": x.get("link", ""), "snippet": x.get("snippet", "")}
            for x in r.json().get("organic", [])]


async def _brave(client: httpx.AsyncClient, query: str, n: int) -> list[dict[str, str]]:
    r = await client.get("https://api.search.brave.com/res/v1/web/search",
                         headers={"X-Subscription-Token": config.BRAVE_API_KEY,
                                  "Accept": "application/json"},
                         params={"q": query, "count": n})
    r.raise_for_status()
    return [{"title": x.get("title", ""), "url": x.get("url", ""),
             "snippet": re.sub("<[^>]+>", "", x.get("description", ""))}
            for x in r.json().get("web", {}).get("results", [])]


def _unwrap_ddg(href: str) -> str:
    """DDG sometimes wraps targets as /l/?uddg=<encoded url>."""
    query = urllib.parse.urlparse(href).query
    return urllib.parse.parse_qs(query).get("uddg", [href])[0]


async def _duckduckgo_lite(client: httpx.AsyncClient, query: str, n: int) -> list[dict[str, str]]:
    """The lite endpoint is plain HTML and, unlike the main one, is not behind
    an anomaly check for server IPs."""
    from bs4 import BeautifulSoup

    r = await client.post("https://lite.duckduckgo.com/lite/", data={"q": query},
                          headers={"User-Agent": config.USER_AGENT})
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    links = soup.select("a.result-link")
    snippets = soup.select("td.result-snippet")
    hits = []
    for i, a in enumerate(links[:n]):
        snippet = snippets[i].get_text(" ", strip=True) if i < len(snippets) else ""
        hits.append({"title": a.get_text(" ", strip=True),
                     "url": _unwrap_ddg(a.get("href", "")), "snippet": snippet})
    return hits


async def _duckduckgo(client: httpx.AsyncClient, query: str, n: int) -> list[dict[str, str]]:
    from bs4 import BeautifulSoup

    r = await client.post("https://html.duckduckgo.com/html/", data={"q": query},
                          headers={"User-Agent": config.USER_AGENT})
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    hits = []
    for result in soup.select(".result")[: n * 2]:
        a = result.select_one("a.result__a")
        if not a:
            continue
        snippet_el = result.select_one(".result__snippet")
        hits.append({"title": a.get_text(" ", strip=True), "url": _unwrap_ddg(a.get("href", "")),
                     "snippet": snippet_el.get_text(" ", strip=True) if snippet_el else ""})
        if len(hits) >= n:
            break
    return hits


async def _wikipedia(client: httpx.AsyncClient, query: str, n: int) -> list[dict[str, str]]:
    r = await client.get("https://en.wikipedia.org/w/api.php", params={
        "action": "query", "list": "search", "srsearch": query,
        "srlimit": n, "format": "json",
    }, headers={"User-Agent": config.API_USER_AGENT})
    r.raise_for_status()
    return [{"title": x["title"],
             "url": "https://en.wikipedia.org/wiki/" + urllib.parse.quote(x["title"].replace(" ", "_")),
             "snippet": re.sub("<[^>]+>", "", x.get("snippet", ""))}
            for x in r.json().get("query", {}).get("search", [])]


async def web_search(query: str, num_results: int = 6) -> str:
    n = max(1, min(int(num_results or 6), 10))
    backends: list[tuple[str, Any]] = []
    if config.TAVILY_API_KEY:
        backends.append(("tavily", _tavily))
    if config.SERPER_API_KEY:
        backends.append(("serper", _serper))
    if config.BRAVE_API_KEY:
        backends.append(("brave", _brave))
    backends.append(("duckduckgo-lite", _duckduckgo_lite))
    backends.append(("duckduckgo-html", _duckduckgo))
    backends.append(("wikipedia", _wikipedia))

    errors = []
    async with httpx.AsyncClient(timeout=config.HTTP_TIMEOUT, follow_redirects=True) as client:
        for name, fn in backends:
            try:
                hits = await fn(client, query, n)
                if hits:
                    return truncate(_format_hits(hits, name))
                errors.append(f"{name}: no results")
            except Exception as exc:
                errors.append(f"{name}: {type(exc).__name__}")
    return ("No search backend returned results (" + "; ".join(errors) +
            "). Try fetch_url on a known source directly, e.g. "
            "https://www.mospi.gov.in or https://data.gov.in .")


# --------------------------------------------------------------------------
# dispatch
# --------------------------------------------------------------------------
async def execute(name: str, args: dict[str, Any]) -> str:
    if name == "run_python":
        return await run_python(args.get("code", ""))
    if name == "fetch_url":
        return await fetch_url(args.get("url", ""))
    if name == "web_search":
        return await web_search(args.get("query", ""), args.get("num_results", 6))
    return f"ERROR: unknown tool {name!r}"
