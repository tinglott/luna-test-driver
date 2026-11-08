"""Real tools for the Luna brain — replaces the paste's keyword guessing.

The ARIA Nexus pastes guessed tool use from keywords ("email" in the text
-> send_email) and stubbed the tools themselves. This module is the fill:

* OpenAI-style function schemas the brain can call for real
  (``TOOL_SCHEMAS``), via Groq native tool calling on gpt-oss-120b.
* ``execute_tool`` — the only entry point. It enforces the role config's
  allow-list FIRST (a tool not named in ``config["allowedTools"]`` is
  refused, never executed), validates arguments, then dispatches.
* Real implementations:
  - ``send_email`` — Gmail. Bench transport is the connected Gmail CLI
    (``hatch_gws_cli gmail +send``, proven). Cloud hosts have no Gmail
    credential (the Luna OAuth client carries calendar scope only), so
    there the tool composes the message, logs it, and returns
    ``composed_not_sent`` — it never pretends a mail went out.
  - ``create_task`` — Google Tasks. Bench transport is the connected
    Tasks CLI (real insert). Cloud hosts queue the task in the activity
    log (same shape as the sales_lead.py JSONL follow-ups) until a Tasks
    credential exists on that host.
  - ``get_product_info`` — read-only product lookup. Whop: bundled
    snapshot (``data/whop-products.json``, 473 real products, Nov 7 audit)
    with a live-API path when ``WHOP_API_KEY`` is set in env. Gumroad:
    live API when ``GUMROAD_API_KEY`` is set; no usable snapshot exists
    (the workspace copy is partial — 5 rows). Prices come from the live
    source or the snapshot's own variant data, never hardcoded.

Safety defaults (fail closed):
* ``ctx["allow_real_writes"]`` defaults to False. With it off, send_email
  only composes + validates and create_task only queues — nothing leaves
  the box. A real send/insert needs an explicit opt-in from the caller.
* ``send_email`` sends ONLY to the role config's owner addresses unless
  ``ctx["allow_customer_mail"]`` is True (Ting's per-client decision).
* No secrets here. API keys come from the environment only, and are never
  printed, logged, or returned in a result.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

HATCH_GWS_CLI = "/opt/hatch/bin/hatch_gws_cli"
SNAPSHOT_NAME = "whop-products.json"
SNAPSHOT_DATE = "2026-11-07"
WHOP_STORE_URL = "https://whop.com/tlott12/{route}/"

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

TOOL_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "send_email",
            "description": (
                "Send a short email from the business. By default it may only "
                "go to the business owner (owner summaries, alerts). Customer "
                "mail needs the client's explicit opt-in."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "to": {
                        "type": "string",
                        "description": (
                            "Recipient email address, or comma-separated list "
                            "(max 5). Owner addresses only unless customer "
                            "mail is enabled for this client."
                        ),
                    },
                    "subject": {
                        "type": "string",
                        "description": "Email subject, 1-200 characters.",
                    },
                    "body": {
                        "type": "string",
                        "description": "Email body, plain text, 1-10000 characters.",
                    },
                },
                "required": ["to", "subject", "body"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_task",
            "description": (
                "Save a follow-up task for the business owner (call-back, "
                "follow-up, to-do). Stored in Google Tasks when available, "
                "otherwise queued in the activity log."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Task title, 1-200 characters.",
                    },
                    "notes": {
                        "type": "string",
                        "description": "Task notes, up to 2000 characters.",
                    },
                    "due": {
                        "type": "string",
                        "description": (
                            "Due date as YYYY-MM-DD, or a full RFC 3339 "
                            "timestamp. Optional."
                        ),
                    },
                },
                "required": ["title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_product_info",
            "description": (
                "Look up a real product by id or title words, on the Whop "
                "shelf and/or Gumroad. Returns title, price, URL and "
                "visibility from the live source or the dated snapshot — "
                "never invented."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "Product id (prod_... / Gumroad id) or title "
                            "words to search for."
                        ),
                    },
                    "shelf": {
                        "type": "string",
                        "enum": ["whop", "gumroad", "auto"],
                        "description": "Which shelf to search (default auto).",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max matches to return, 1-10 (default 5).",
                    },
                },
                "required": ["query"],
            },
        },
    },
]

SCHEMA_TOOL_NAMES = {s["function"]["name"] for s in TOOL_SCHEMAS}


def tools_for_config(config: dict) -> list[dict]:
    """Schemas for the tools this role config actually allows.

    Only tools named in ``config["allowedTools"]`` AND defined here are
    offered to the model. A config that lists no schema'd tool gets an
    empty list, and the caller falls back to the plain brain_reply path.
    """
    allowed = set(config.get("allowedTools") or [])
    return [s for s in TOOL_SCHEMAS if s["function"]["name"] in allowed]


# ------------------------------------------------------------------ helpers

def _fail(message: str) -> dict:
    return {"ok": False, "status": "refused", "error": message}


def _validate_emails(raw: str) -> list[str] | None:
    addrs = [a.strip() for a in (raw or "").split(",") if a.strip()]
    if not addrs or len(addrs) > 5:
        return None
    if any(not _EMAIL_RE.match(a) for a in addrs):
        return None
    return addrs


def _norm_due(due: str | None) -> str | None:
    """YYYY-MM-DD -> RFC 3339 midnight UTC; pass RFC 3339 through."""
    if not due:
        return None
    due = due.strip()
    if _DATE_RE.match(due):
        return f"{due}T00:00:00.000Z"
    return due


def _cli_available() -> bool:
    return os.path.exists(HATCH_GWS_CLI)


# ---------------------------------------------------------------- send_email

def _send_email(args: dict, ctx: dict) -> dict:
    addrs = _validate_emails(str(args.get("to") or ""))
    subject = str(args.get("subject") or "").strip()
    body = str(args.get("body") or "").strip()
    if addrs is None:
        return _fail("send_email: 'to' must be 1-5 valid email addresses")
    if not (1 <= len(subject) <= 200):
        return _fail("send_email: 'subject' must be 1-200 characters")
    if not (1 <= len(body) <= 10000):
        return _fail("send_email: 'body' must be 1-10000 characters")

    # Recipient policy: owner addresses only, unless this client opted in
    # to customer mail (Ting's per-client decision, recorded in ctx).
    owners = {a.lower() for a in (ctx.get("owner_emails") or [])}
    if not ctx.get("allow_customer_mail"):
        outside = [a for a in addrs if a.lower() not in owners]
        if outside:
            return _fail(
                "send_email: customer mail is not enabled for this client — "
                f"refused recipients: {', '.join(outside)}"
            )

    composed = {"to": addrs, "subject": subject, "body": body}

    if not ctx.get("allow_real_writes"):
        # Dry run (the default): compose + validate, send nothing.
        return {
            "ok": True,
            "status": "composed_not_sent",
            "reason": "dry run — real sends need an explicit opt-in",
            "composed": composed,
        }
    if not _cli_available():
        # Cloud host: no Gmail CLI and no Gmail credential here (the Luna
        # OAuth client carries calendar scope only). Compose + log only.
        return {
            "ok": True,
            "status": "composed_not_sent",
            "reason": "no Gmail transport on this host — composed for the log",
            "composed": composed,
        }

    cmd = [HATCH_GWS_CLI, "gmail", "+send", "--to", ",".join(addrs),
           "--subject", subject, "--body", body]
    if ctx.get("gmail_account"):
        cmd[3:3] = ["--account", str(ctx["gmail_account"])]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except Exception as exc:  # noqa: BLE001 - CLI failure is a tool error, not a crash
        return {"ok": False, "status": "send_failed", "error": f"gmail CLI error: {exc}"}
    if proc.returncode != 0:
        return {"ok": False, "status": "send_failed",
                "error": f"gmail CLI exit {proc.returncode}: {proc.stderr.strip()[:300]}"}
    return {"ok": True, "status": "sent", "composed": composed,
            "cli_output": proc.stdout.strip()[:300]}


# ---------------------------------------------------------------- create_task

def _create_task(args: dict, ctx: dict) -> dict:
    title = str(args.get("title") or "").strip()
    notes = str(args.get("notes") or "").strip()
    due = _norm_due(str(args.get("due") or "") or None)
    if not (1 <= len(title) <= 200):
        return _fail("create_task: 'title' must be 1-200 characters")
    if len(notes) > 2000:
        return _fail("create_task: 'notes' must be at most 2000 characters")

    task = {"title": title, "notes": notes, "due": due,
            "client_id": ctx.get("client_id") or ""}

    if not ctx.get("allow_real_writes"):
        return {"ok": True, "status": "queued",
                "reason": "dry run — queued in the activity log, not inserted",
                "task": task}
    if not _cli_available():
        # Cloud host: no Tasks CLI here. Queue in the activity log (the
        # caller persists the returned task); same shape as the
        # sales_lead.py JSONL follow-ups.
        return {"ok": True, "status": "queued",
                "reason": "no Google Tasks transport on this host — queued",
                "task": task}

    payload = {"title": title}
    if notes:
        payload["notes"] = notes
    if due:
        payload["due"] = due
    cmd = [HATCH_GWS_CLI, "tasks", "tasks", "insert",
           "--params", json.dumps({"tasklist": "@default"}),
           "--json", json.dumps(payload)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except Exception as exc:  # noqa: BLE001 - CLI failure is a tool error, not a crash
        return {"ok": False, "status": "insert_failed", "error": f"tasks CLI error: {exc}"}
    if proc.returncode != 0:
        return {"ok": False, "status": "insert_failed",
                "error": f"tasks CLI exit {proc.returncode}: {proc.stderr.strip()[:300]}"}
    try:
        created = json.loads(proc.stdout)
    except json.JSONDecodeError:
        created = {"raw": proc.stdout.strip()[:300]}
    return {"ok": True, "status": "created", "task": task,
            "task_id": created.get("id"), "created": created}


# ------------------------------------------------------------ get_product_info

_SNAPSHOT_CACHE: list[dict] | None = None


def _snapshot_path(ctx: dict) -> Path:
    if ctx.get("snapshot_path"):
        return Path(ctx["snapshot_path"])
    return Path(__file__).resolve().parent / "data" / SNAPSHOT_NAME


def _load_snapshot(ctx: dict) -> list[dict]:
    global _SNAPSHOT_CACHE
    if _SNAPSHOT_CACHE is not None:
        return _SNAPSHOT_CACHE
    path = _snapshot_path(ctx)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - a missing/unreadable snapshot is "no data", not a crash
        return []
    _SNAPSHOT_CACHE = data if isinstance(data, list) else []
    return _SNAPSHOT_CACHE


def _whop_price(product: dict) -> str:
    variant = product.get("default_variant") or {}
    price = (variant.get("initial_price") or {})
    amount = price.get("amount")
    if amount is None:
        return "price not listed"
    try:
        value = float(amount)
    except (TypeError, ValueError):
        return "price not listed"
    if value == 0:
        return "Free"
    currency = (price.get("currency") or "usd").upper()
    return f"{value:.2f} {currency}"


def _whop_summary(product: dict, source: str) -> dict:
    route = product.get("route") or ""
    return {
        "id": product.get("id"),
        "title": product.get("title"),
        "price": _whop_price(product),
        "url": WHOP_STORE_URL.format(route=route) if route else "",
        "visibility": product.get("visibility"),
        "source": source,
    }


def _search_snapshot(query: str, limit: int, ctx: dict) -> list[dict]:
    products = _load_snapshot(ctx)
    q = query.strip().lower()
    if not q:
        return []
    exact = [p for p in products if str(p.get("id", "")).lower() == q]
    if exact:
        return [_whop_summary(p, f"snapshot-{SNAPSHOT_DATE}") for p in exact[:limit]]
    words = q.split()
    scored: list[tuple[int, dict]] = []
    for product in products:
        hay = f"{product.get('title', '')} {product.get('description', '')}".lower()
        hits = sum(1 for w in words if w in hay)
        if hits:
            scored.append((hits, product))
    scored.sort(key=lambda item: -item[0])
    return [_whop_summary(p, f"snapshot-{SNAPSHOT_DATE}")
            for _, p in scored[:limit]]


def _whop_live(product_id: str) -> dict | None:
    """Live Whop lookup. Returns a summary dict, or None on any failure
    (the caller falls back to the snapshot — a live miss is never fatal)."""
    key = os.environ.get("WHOP_API_KEY", "").strip()
    if not key or not product_id.startswith("prod_"):
        return None
    url = f"https://api.whop.com/api/v1/products/{product_id}"
    request = urllib.request.Request(
        url, method="GET",
        headers={"Authorization": f"Bearer {key}",
                 "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:  # noqa: BLE001 - live failure -> snapshot fallback
        return None
    product = data.get("data") or data
    if not isinstance(product, dict) or not product.get("id"):
        return None
    return _whop_summary(product, "live-whop-api")


def _gumroad_live(product_id: str) -> dict | None:
    """Live Gumroad lookup. Returns a summary dict, or None on any failure."""
    key = os.environ.get("GUMROAD_API_KEY", "").strip()
    if not key or not product_id:
        return None
    url = f"https://api.gumroad.com/v2/products/{product_id}"
    request = urllib.request.Request(
        url, method="GET",
        headers={"Authorization": f"Bearer {key}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:  # noqa: BLE001 - live failure -> honest "unavailable"
        return None
    product = (data.get("product") or {}) if isinstance(data, dict) else {}
    if not product.get("id"):
        return None
    price = product.get("price")
    return {
        "id": product.get("id"),
        "title": product.get("name"),
        "price": f"{float(price) / 100:.2f} USD" if isinstance(price, (int, float)) else "price not listed",
        "url": product.get("short_url") or product.get("url") or "",
        "visibility": "listed" if product.get("published") else "unlisted",
        "source": "live-gumroad-api",
    }


def _get_product_info(args: dict, ctx: dict) -> dict:
    query = str(args.get("query") or "").strip()
    shelf = str(args.get("shelf") or "auto").lower()
    try:
        limit = int(args.get("limit") or 5)
    except (TypeError, ValueError):
        limit = 5
    limit = max(1, min(10, limit))
    if not query or len(query) > 200:
        return _fail("get_product_info: 'query' must be 1-200 characters")
    if shelf not in ("whop", "gumroad", "auto"):
        return _fail("get_product_info: 'shelf' must be whop, gumroad or auto")

    if shelf == "gumroad":
        found = _gumroad_live(query)
        if found is None:
            return {"ok": False, "status": "unavailable",
                    "error": "Gumroad lookup needs GUMROAD_API_KEY in env, or the id was not found",
                    "matches": []}
        return {"ok": True, "status": "found", "matches": [found]}

    # Whop (or auto): live by id first, snapshot otherwise.
    matches: list[dict] = []
    if query.startswith("prod_"):
        live = _whop_live(query)
        if live is not None:
            matches.append(live)
    if not matches:
        matches = _search_snapshot(query, limit, ctx)
    if shelf == "auto" and not matches and not query.startswith("prod_"):
        found = _gumroad_live(query)
        if found is not None:
            matches.append(found)
    if not matches:
        return {"ok": True, "status": "not_found",
                "matches": [],
                "note": f"No product matched {query!r} on the "
                        f"{'Whop snapshot' if shelf == 'whop' else 'shelves searched'}."}
    return {"ok": True, "status": "found", "matches": matches}


# ------------------------------------------------------------------ dispatch

_DISPATCH = {
    "send_email": _send_email,
    "create_task": _create_task,
    "get_product_info": _get_product_info,
}


def execute_tool(name: str, arguments: dict | object, ctx: dict | None = None) -> dict:
    """Run one tool call. The allow-list is enforced here, before anything
    executes: ``name`` must be a known tool AND named in
    ``ctx["allowed_tools"]``. Unknown tools, unlisted tools, and bad
    arguments are refused with a reason — never executed, never fatal."""
    ctx = dict(ctx or {})
    allowed = set(ctx.get("allowed_tools") or [])
    if name not in SCHEMA_TOOL_NAMES:
        return _fail(f"unknown tool {name!r} — not a Luna tool")
    if name not in allowed:
        return _fail(f"tool {name!r} is not in this role's allowedTools {sorted(allowed)}")
    if not isinstance(arguments, dict):
        return _fail(f"tool {name!r}: arguments must be a JSON object")
    try:
        return _DISPATCH[name](arguments, ctx)
    except Exception as exc:  # noqa: BLE001 - a tool bug must not kill the call
        return {"ok": False, "status": "tool_error",
                "error": f"tool {name!r} failed: {exc}"}
