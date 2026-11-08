#!/usr/bin/env python3
"""Test driver server for the ARTISY AI receptionist service —
PYTHONANYWHERE version.

Rebuilt from the Vercel driver (``../vercel-driver/index.py``) for a
PythonAnywhere deployment (free Beginner account, tinglott), so invited
affiliates can test Luna on a public link that installs on their phone
like the Willow app. The redesign:

  * Calendar: NO service account, NO key file. ``agent-service/
    calendar_booking.py`` calls the Google Calendar API with the
    Google account that signed in through the app's own /authorize
    flow (Google OAuth 2.0, offline access): the refresh token lives
    server-side in Upstash KV (``luna:google_oauth``), put there when
    Ting taps Allow once. See ``agent-service/google_oauth.py``.
  * TTS: NO Kokoro sidecar. ``groq_tts.py`` synthesizes with Groq
    Canopy Orpheus (voice ``hannah``) and returns a base64 data URI
    in the turn JSON (``audio_data``); the browser plays it from
    memory. **The voice CHANGES — Kokoro af_heart is gone in this
    build; the test validates the call flow, not the real voice.**
  * State: NO process memory, NO local disk. Sessions, queues, the
    do-not-call list, the tester log and the activity log all live in
    Upstash Redis via ``kvstore.py`` (REST). A PythonAnywhere worker
    is long-lived but reloads on code changes — anything kept in
    memory would vanish on reload and leak bookings.
  * Phone install: the app is a PWA modelled on Willow Tales
    (``manifest.json`` + ``sw.js`` served from the root, standalone
    display). Open the link, Chrome menu -> Install app.
  * No stub server: ``post_call_end`` writes the call-end payload to
    the KV activity log instead (and returns 201, as the stub did).
  * No relay routes, no /audio/<name> route, no sidecar startup.

Layout (PythonAnywhere manual web app): this file is the entrypoint
(``index.py`` at the project root, top-level ``app = Flask(__name__)``);
the WSGI file (``pythonanywhere_wsgi.py``) imports ``app`` from it.
Python 3.12. Env vars are set in the Web tab (see DEPLOY.md).

Disclosure, first, always: every test call opens with the AI identity
(the role config greeting) and every test call-back opens with the
DISCLAIMER.md Part A greeting verbatim. Per-tester logging goes to the
KV tester log (``luna:tester_log``).

TESTER GATE: only the nine invited affiliates (plus bench-battery)
may start a session. Anyone else gets a plain "invited testers only"
page — no session, no booking, no data recorded.

The gates are the Nov 7 FIXED gates (``agent-service/gates.py``,
role configs v1.0.3) — the same code the bench gate probe passes.
Do not weaken them.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import unicodedata
import uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from flask import Flask, jsonify, redirect, request, send_file

HERE = Path(__file__).resolve().parent
AGENT_SERVICE = HERE / "agent-service"
PACKAGE_ROOT = HERE  # role configs live at <project root>/roles/
sys.path.insert(0, str(AGENT_SERVICE))
sys.path.insert(0, str(HERE))

from booking_parse import TZ, is_confirmation, parse_booking_intent  # noqa: E402
from call_handler import CallSession  # noqa: E402
from calendar_booking import delete_event  # noqa: E402
from callback_queue import (  # noqa: E402
    add_to_dnc_list,
    all_callbacks,
    build_callback_greeting,
    dnc_numbers,
    is_on_dnc,
    pending_callbacks,
    request_callback,
    sweep,
)
from groq_tts import synthesize_data_uri  # noqa: E402
from groq_whisper import transcribe_file  # noqa: E402
from kvstore import StoreUnavailable, get_store  # noqa: E402
from google_oauth import (  # noqa: E402
    OAuthConfigError, OAuthFlowError, authorization_url, calendar_connected,
    exchange_code,
)
from role_loader import load_role_config  # noqa: E402
from walkin_queue import add_walkin, queue_status, serve_next  # noqa: E402
from approval_queue import all_items, approve, deny, pending_items  # noqa: E402

# -- KV keys ------------------------------------------------------------
# All state lives under the luna: prefix in Upstash Redis (or the
# in-process memory backend for local runs — see kvstore.py).
SESSION_KEY_PREFIX = "luna:session:"
SESSION_INDEX_KEY = "luna:session_index"
CALLBACK_KEY = "kv://luna:callback_queue"
DNC_KEY = "kv://luna:dnc"
WALKIN_KEY = "kv://luna:walkin_queue"
APPROVAL_KEY = "kv://luna:approval_queue"
TESTER_LOG_KEY = "luna:tester_log"
ACTIVITY_LOG_KEY = "luna:activity_log"
CALL_END_LOG_KEY = "luna:call_end_log"

HOST = os.environ.get("DRIVER_HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "18102"))
SESSION_TTL_SECONDS = 30 * 60
MAX_SESSIONS_PER_IP_PER_HOUR = 20
MAX_REQUESTS_PER_IP_PER_MINUTE = 120
MAX_BOOKINGS_PER_SESSION = 3
MAX_AUDIO_BYTES = 10 * 1024 * 1024

# -- tester gate: only invited affiliates may start a session -----------
# Normalised (accents stripped, lower-case) so ?tester=João matches.
ALLOWED_TESTERS = {
    "eliane", "joao", "gabriela", "fabiane", "ayush", "michael",
    "jackson", "david", "tulio", "bench-battery",
}


def _normalize_tester(name: str) -> str:
    decomposed = unicodedata.normalize("NFKD", str(name or ""))
    ascii_only = "".join(c for c in decomposed if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", ascii_only).strip().lower()


def _tester_allowed(name: str) -> bool:
    return _normalize_tester(name) in ALLOWED_TESTERS


INVITED_ONLY_HTML = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ARTISY AI Receptionist — Invited testers only</title>
<style>body{margin:0;font-family:system-ui,sans-serif;background:#0a0e1a;color:#eef1f8}
.wrap{max-width:640px;margin:0 auto;padding:40px 20px;line-height:1.6}
h1{color:#f5c451;font-size:1.3rem}.card{background:#151d36;border:1px solid #26305a;border-radius:10px;padding:16px}</style>
</head><body><div class="wrap">
<h1>🤖 ARTISY AI Receptionist — Test Driver</h1>
<div class="card"><p><b>This test link is for invited testers only.</b></p>
<p>If you are one of our invited affiliate testers, please open the
link exactly as it was sent to you — it carries your tester name.
If you followed a link without a tester name, ask us to resend it.</p>
<p>No session was started, nothing was booked, and nothing about
your visit was recorded.</p>
<p>The system under test is an AI receptionist: every test call opens
by saying it is AI, and every call-back opens with the same
disclosure. Saying “stop” ends a call and it never calls again.</p></div>
</div></body></html>"""

app = Flask(__name__)


# ---------------------------------------------------------------- logging
def log_event(tester: str, session_id: str, event: str,
              detail: dict | None = None, outcome: str = "") -> None:
    """Append one per-tester log line to the KV tester log (capped).
    Logging never raises — a failed log write must not break a call."""
    entry = {
        "ts": datetime.now(TZ).isoformat(),
        "tester": (tester or "anonymous")[:60],
        "session_id": session_id or "",
        "event": event,
        "detail": detail or {},
        "outcome": outcome,
    }
    try:
        get_store().list_append(TESTER_LOG_KEY, entry, max_len=5000)
    except StoreUnavailable:
        pass


def _tester() -> str:
    """Tester name/label from the request body, header, or query param."""
    body = request.get_json(silent=True) or {}
    raw = (body.get("tester") or request.headers.get("X-Tester")
           or request.args.get("tester") or request.args.get("t") or "")
    clean = re.sub(r"[^A-Za-z0-9 _.\-@]", "", str(raw)).strip()[:60]
    return clean or "anonymous"


def _client_ip() -> str:
    forwarded = request.headers.get("X-Forwarded-For", "")
    return forwarded.split(",")[0].strip() or request.remote_addr or "unknown"


@app.before_request
def _tester_gate():
    # CORS preflight: browsers send no tester header on OPTIONS —
    # let it through; the route's automatic OPTIONS reply answers it
    # and _cors_headers() adds the grant.
    if request.method == "OPTIONS":
        return None
    # /health is public (it reports store/TTS status only).
    if request.path == "/health":
        return None
    if request.path == "/":
        return None  # index() enforces the gate and serves the right page
    if not request.path.startswith("/api/"):
        return None
    if not _tester_allowed(_tester()):
        log_event(_tester(), "", "session_refused_not_invited",
                  {"path": request.path}, "refused")
        return jsonify({"error": "invited testers only — open the link "
                                 "exactly as it was sent to you"}), 403
    return None


@app.before_request
def _rate_limit():
    # CORS preflight is not a real API call — never rate-limit it.
    if request.method == "OPTIONS":
        return None
    if not request.path.startswith("/api/"):
        return None
    ip = _client_ip()
    now = time.time()
    try:
        count = get_store().incr(f"luna:rl:req:{ip}:{int(now // 60)}", ex=120)
    except StoreUnavailable:
        return jsonify({"error": "session store unavailable — try again shortly"}), 503
    if count > MAX_REQUESTS_PER_IP_PER_MINUTE:
        return jsonify({"error": "rate limited — try again in a minute"}), 429
    return None


# ------------------------------------------------------- CORS (GitHub Pages)
# The per-feature Pages frontend (https://tinglott.github.io/...) calls
# this backend cross-origin. Only that origin is allowed; the tester
# gate still applies to every /api/ call (the pages pass the tester
# name through — no gate logic lives in the pages alone).
ALLOWED_CORS_ORIGINS = {"https://tinglott.github.io"}


@app.after_request
def _cors_headers(response):
    """Grant the GitHub Pages frontend cross-origin access. Only the
    Pages origin gets the header — any other origin gets no CORS grant
    (the browser then blocks the read, as it should)."""
    origin = request.headers.get("Origin", "")
    if origin in ALLOWED_CORS_ORIGINS:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Vary"] = "Origin"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, X-Tester"
        response.headers["Access-Control-Max-Age"] = "86400"
    return response


# ---------------------------------------------------------------- helpers
def _tts(text: str) -> str | None:
    """Synthesize reply audio -> base64 data URI, or None (fallback:
    the turn still answers with text — TTS failure never kills a call).
    Groq Orpheus, voice hannah — NOT Kokoro af_heart (see header)."""
    try:
        return synthesize_data_uri(text)
    except Exception:  # noqa: BLE001 - TTS failure never kills the call
        return None


def _save_audio_file(file_storage) -> tuple[str | None, str | None]:
    """Save an uploaded audio file to /tmp -> (path, error).
    /tmp is writable scratch space on PythonAnywhere; the file is
    transcribed in-process and unlinked immediately."""
    if file_storage is None:
        return None, "no audio file"
    data = file_storage.read()
    if len(data) > MAX_AUDIO_BYTES:
        return None, "audio too large (max 10 MB)"
    suffix = Path(file_storage.filename or "audio.webm").suffix.lower()
    if suffix not in {".webm", ".wav", ".mp3", ".ogg", ".m4a", ".oga"}:
        suffix = ".webm"
    name = f"upload-{uuid.uuid4().hex[:12]}{suffix}"
    path = Path("/tmp") / name
    path.write_bytes(data)
    return str(path), None


def _transcribe(path: str) -> tuple[str | None, str | None]:
    """Groq Whisper, in-process -> (text, error)."""
    result = transcribe_file(path, "en")
    if "error" in result:
        return None, result["error"]
    return (result.get("text") or "").strip(), None


# ------------------------------------------------------------ sessions (KV)
def _session_key(session_id: str) -> str:
    return SESSION_KEY_PREFIX + session_id


def _save_session(record: dict) -> None:
    """Persist a session record to KV with a 30-minute TTL, and upsert
    its entry in the session index (used to purge expired sessions and
    to delete their bookings — see _purge_expired_sessions). The
    CallSession object is serialized; the config is reloaded on load.
    Every endpoint that mutates a session MUST call this before
    returning — a worker keeps nothing between reloads."""
    store = get_store()
    session: CallSession = record["session"]
    stored = {
        "session_id": record["session_id"],
        "session": session.to_state(),
        "client_id": record.get("client_id"),
        "role_id": record.get("role_id"),
        "scenario": record.get("scenario"),
        "tester": record.get("tester"),
        "created_at": record.get("created_at"),
        "pending_booking": record.get("pending_booking"),
        "booking_event_ids": record.get("booking_event_ids") or [],
        "calendar_id": record.get("calendar_id"),
        "callback_item_id": record.get("callback_item_id"),
    }
    store.set_json(_session_key(record["session_id"]), stored,
                   ex=SESSION_TTL_SECONDS)
    index = store.list_get(SESSION_INDEX_KEY)
    entry = {
        "session_id": record["session_id"],
        "expires_at": time.time() + SESSION_TTL_SECONDS,
        "calendar_id": stored["calendar_id"] or "primary",
        "booking_event_ids": stored["booking_event_ids"],
    }
    index = [e for e in index if e.get("session_id") != record["session_id"]]
    index.append(entry)
    store.list_set(SESSION_INDEX_KEY, index)


def _load_session(session_id: str) -> dict | None:
    """Load a session record from KV and restore its CallSession, or
    None when it does not exist or has expired (the KV TTL normally
    removes it; created_at is checked as defence in depth)."""
    if not session_id:
        return None
    stored = get_store().get_json(_session_key(session_id))
    if not stored:
        return None
    if time.time() - float(stored.get("created_at") or 0) > SESSION_TTL_SECONDS:
        _delete_session_record(session_id)
        return None
    record = dict(stored)
    record["session"] = CallSession.from_state(stored["session"])
    return record


def _delete_session_record(session_id: str) -> None:
    store = get_store()
    store.delete(_session_key(session_id))
    index = [e for e in store.list_get(SESSION_INDEX_KEY)
             if e.get("session_id") != session_id]
    store.list_set(SESSION_INDEX_KEY, index)


def _cleanup_session(record: dict) -> list[str]:
    """Delete any test bookings the session created, then drop it.

    Returns the list of event ids VERIFIED deleted. FIX #3 (bench):
    the old code called delete_event(event_id) with one argument (it
    takes calendar_id + event_id), so the delete raised, the failure
    was only logged, and the caller was still told the booking was
    deleted. Here the calendar id travels with the record.
    """
    session_id = record["session_id"]
    calendar_id = record.get("calendar_id") or "primary"
    deleted: list[str] = []
    for event_id in record.get("booking_event_ids") or []:
        try:
            if delete_event(calendar_id, event_id):
                deleted.append(event_id)
                log_event(record.get("tester", "anonymous"), session_id,
                          "booking_deleted", {"event_id": event_id},
                          "deleted on session cleanup")
            else:
                log_event(record.get("tester", "anonymous"), session_id,
                          "booking_delete_failed",
                          {"event_id": event_id, "error": "delete returned False"},
                          "failed")
        except Exception as exc:  # noqa: BLE001 - log, never raise on cleanup
            log_event(record.get("tester", "anonymous"), session_id,
                      "booking_delete_failed", {"event_id": event_id,
                                                "error": str(exc)}, "failed")
    _delete_session_record(session_id)
    return deleted


def _purge_expired_sessions() -> None:
    """Sweep the session index for expired entries and delete any
    bookings they left behind. A serverless function has no background
    process, so this runs opportunistically on session start — a
    booking from an abandoned call is deleted at the next test call,
    at the latest. Expired sessions whose bookings are already gone
    are simply dropped from the index."""
    store = get_store()
    index = store.list_get(SESSION_INDEX_KEY)
    now = time.time()
    kept: list[dict] = []
    changed = False
    for entry in index:
        if float(entry.get("expires_at") or 0) > now:
            kept.append(entry)
            continue
        changed = True
        calendar_id = entry.get("calendar_id") or "primary"
        for event_id in entry.get("booking_event_ids") or []:
            try:
                delete_event(calendar_id, event_id)
            except Exception:  # noqa: BLE001 - best effort on purge
                pass
        store.delete(_session_key(entry.get("session_id") or ""))
    if changed:
        store.list_set(SESSION_INDEX_KEY, kept)


def _post_call_end(payload: dict) -> tuple[int, str]:
    """Write the call-end payload to the KV activity log (the stub
    server's job on the bench) and to the call-end log the callback
    sweep's rebooked_check reads. Returns (201, "logged") on success
    — the same status the stub returned, so the battery's assertion
    holds. On store failure returns (503, ...) and the caller reports
    it honestly instead of claiming the payload was posted.

    The row is stamped with created_at (W1's Supabase shape — the W3
    monthly report filters on it)."""
    try:
        store = get_store()
        payload.setdefault("created_at", datetime.now(TZ).isoformat())
        store.list_append(ACTIVITY_LOG_KEY, payload, max_len=1000)
        store.list_append(CALL_END_LOG_KEY, payload, max_len=1000)
    except StoreUnavailable as exc:
        return 503, str(exc)
    return 201, "logged"


def _w2_summary(payload: dict, owner_email: str) -> dict:
    """Owner summary, mirroring the W2 Code node line for line (Python).

    The W2 node hardcodes owner_email as 'REPLACE_AT_INSTALL: owner email';
    this mirror uses the role config's ownerSummary.to instead (the value
    the install replaces the placeholder with). Subject/body logic is
    byte-identical to the node.
    """
    detail = payload.get("detail") or {}
    outcome = payload.get("outcome")
    caller = detail.get("caller_name") or "the caller"
    if outcome == "booked":
        when = "Booked: " + (detail.get("booking_or_message") or "see calendar")
    elif outcome == "pending_approval":
        when = ("NEEDS YOUR APPROVAL: "
                + (detail.get("booking_or_message") or "a new-client booking is waiting")
                + " — reply to Ting to approve or deny. Nothing is in the calendar yet.")
    elif outcome == "spam":
        when = "Spam call blocked — no message sent to you beyond this count."
    elif outcome == "callback_completed":
        when = f"Callback completed: we called {caller} back and reached them."
    elif outcome == "callback_attempted":
        when = (f"Callback attempted: we called {caller} back but did not reach them. "
                "The ball is in your court.")
    elif outcome == "callback_failed":
        when = ("Callback FAILED: the call-back could not be placed (voice service "
                "unreachable). Ting has been alerted — please call the customer yourself.")
    elif outcome == "callback_suppressed":
        when = (f"Callback not placed: {caller} re-booked or asked not to be called, "
                "so no call-back was made.")
    elif outcome == "callback_deferred":
        when = (f"Callback deferred until opening hours: {caller} will be called back "
                "when you open.")
    else:
        when = "Message taken: " + (detail.get("booking_or_message") or "")
    sales_line = None
    if payload.get("sales_qualified"):
        sales_line = ("SALES LEAD — follow up by " + (payload.get("follow_up_due") or "soon")
                      + ". This caller asked about buying. The sale is yours to close — "
                      "the agent never closes, never takes payment, never promises a result.")
    if payload.get("sales_qualified"):
        subject = "Sales lead — " + (detail.get("caller_name") or "caller")
    elif outcome == "booked":
        subject = "New booking — " + (detail.get("caller_name") or "caller")
    else:
        subject = "Call summary — " + (detail.get("caller_name") or "caller")
    lines = [
        "Hi — your AI receptionist took a call.",
        "",
        "Caller: " + (detail.get("caller_name") or "not given") + " ("
        + (detail.get("caller_phone") or "not given") + ")",
        "What happened: " + when,
        "Call length: " + str(payload.get("duration_seconds") or 0) + " seconds.",
    ]
    if sales_line:
        lines += [sales_line, ""]
    flags = payload.get("flags") or []
    lines.append("Flags for Ting to review: " + ", ".join(flags) if flags else "No flags.")
    lines += ["", "If anything here looks wrong, reply to this email and Ting will fix the script.",
              "— ARTISY AI Receptionist"]
    return {"owner_email": owner_email, "subject": subject,
            "body": "\n".join(lines), "row": payload}


def _book_pending(record: dict, tester: str) -> dict | None:
    """Confirm + book the session's pending slot. Returns the booking dict."""
    pending = record.get("pending_booking")
    if not pending:
        return None
    if len(record.get("booking_event_ids") or []) >= MAX_BOOKINGS_PER_SESSION:
        return {"error": "booking limit reached for this test call (3)"}
    session: CallSession = record["session"]
    session.confirm_booking()
    result = session.book(pending["service"], pending["start_iso"], pending["end_iso"])
    if result.action == "booked" and session.booking_event_id:
        record.setdefault("booking_event_ids", []).append(session.booking_event_id)
        record["calendar_id"] = (session.config.get("bookingRules") or {}).get(
            "calendarId", "primary")
        log_event(tester, record["session_id"], "booking_confirmed",
                  {"service": pending["service"], "start": pending["start_iso"],
                   "event_id": session.booking_event_id}, "booked")
        record["pending_booking"] = None
        return {"reply": result.reply, "action": result.action,
                "event_id": session.booking_event_id,
                "service": pending["service"], "start": pending["start_iso"],
                "end": pending["end_iso"]}
    if result.action == "pending_approval":
        # FIX #9 (bench battery, Nov 7): a new caller's booking is HELD for
        # the owner, not failed. The approval item now holds the details —
        # clear the pending slot so a second confirm cannot double-book it.
        log_event(tester, record["session_id"], "booking_pending_approval",
                  {"service": pending["service"], "start": pending["start_iso"]},
                  "pending_approval")
        record["pending_booking"] = None
        return {"reply": result.reply, "action": "pending_approval",
                "service": pending["service"], "start": pending["start_iso"],
                "end": pending["end_iso"]}
    log_event(tester, record["session_id"], "booking_failed",
              {"service": pending["service"], "reply": result.reply}, "failed")
    return {"error": result.reply}


# ------------------------------------------------------------------ pages
@app.get("/")
def index():
    # Tester gate: only invited affiliates see the driver. Unknown or
    # missing tester -> plain "invited testers only" page, no session,
    # no booking, nothing recorded.
    if not _tester_allowed(_tester()):
        return INVITED_ONLY_HTML, 200, {"Content-Type": "text/html; charset=utf-8"}
    return send_file(HERE / "driver.html", mimetype="text/html")


@app.get("/health")
def health():
    store = get_store()
    status = store.status()
    sessions_active = 0
    if status.get("configured"):
        try:
            sessions_active = len(store.list_get(SESSION_INDEX_KEY))
        except StoreUnavailable:
            sessions_active = 0
    return jsonify({
        "ok": True,
        "store": status,
        "store_ok": bool(status.get("configured")),
        "tts_worker": bool(os.environ.get("GROQ_API_KEY")),
        "tts_mode": "groq-orhpheus",
        "whisper_helper": True,
        "calendar_connected": calendar_connected(),
        "activity_log": bool(status.get("configured")),
        "sessions_active": sessions_active,
        "server_time": datetime.now(TZ).isoformat(),
    })


# ------------------------------------------------------- PWA (Willow model)
@app.get("/manifest.json")
def manifest():
    """Web app manifest — served from the root so the app installs
    on a phone like Willow (standalone, home-screen icon)."""
    return send_file(HERE / "manifest.json",
                     mimetype="application/manifest+json")


@app.get("/sw.js")
def service_worker():
    """Service worker — served from the root (root scope matters).
    Never cached itself: Cache-Control: no-cache, so an updated
    worker is picked up on the next visit."""
    resp = send_file(HERE / "sw.js", mimetype="application/javascript")
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return resp


@app.get("/icon-192.png")
def icon_192():
    return send_file(HERE / "icon-192.png", mimetype="image/png")


@app.get("/icon-512.png")
def icon_512():
    return send_file(HERE / "icon-512.png", mimetype="image/png")


# ------------------------------------------------- Google sign-in (OAuth)
def _oauth_result_page(ok: bool, message: str) -> tuple[str, int]:
    """Small result page after the Google sign-in flow (plain HTML in
    the ARTISY palette — no session, no booking, nothing recorded)."""
    title = "Calendar connected ✓" if ok else "Calendar not connected"
    body = (f"<p>{message}</p>" if ok else
            f"<p>Nothing was connected. {message}</p>")
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ARTISY AI Receptionist — {title}</title>
<style>body{{margin:0;font-family:system-ui,sans-serif;background:#0a0e1a;color:#eef1f8}}
.wrap{{max-width:640px;margin:0 auto;padding:40px 20px;line-height:1.6}}
h1{{color:#f5c451;font-size:1.3rem}}.card{{background:#151d36;border:1px solid #26305a;border-radius:10px;padding:16px}}
a{{color:#7fd4ff}}</style>
</head><body><div class="wrap"><h1>🤖 ARTISY AI Receptionist</h1>
<div class="card"><h2>{title}</h2>{body}
<p><a href="/">← Back to the test driver</a></p></div>
</div></body></html>""", 200


@app.get("/authorize")
def authorize():
    """Start Google sign-in (Ting, once). NOT tester-gated: the OAuth
    client only lets the calendar owner's account complete it — the
    callback verifies the id_token email before storing anything, so
    a tester signing in with their own account is refused, never
    stored."""
    redirect_uri = request.url_root.rstrip("/") + "/oauth2callback"
    try:
        auth_url, _state = authorization_url(redirect_uri)
    except (OAuthConfigError, OAuthFlowError) as exc:
        return _oauth_result_page(False, str(exc))
    return redirect(auth_url, code=302)


@app.get("/oauth2callback")
def oauth2callback():
    """Google redirects here after Allow (or Deny). Verifies state,
    exchanges the code, verifies the account, stores the refresh
    token — then back to the driver page with a connected banner."""
    if request.args.get("error"):
        return _oauth_result_page(
            False, "Google sign-in was declined or failed: "
                   + request.args.get("error", ""))
    code = request.args.get("code") or ""
    state = request.args.get("state") or ""
    if not code or not state:
        return _oauth_result_page(
            False, "Google did not return a sign-in code — try again "
                   "from the app.")
    redirect_uri = request.url_root.rstrip("/") + "/oauth2callback"
    try:
        record = exchange_code(redirect_uri, code, state)
    except (OAuthConfigError, OAuthFlowError) as exc:
        return _oauth_result_page(False, str(exc))
    log_event("owner", "", "calendar_connected",
              {"email": record.get("email")}, "connected")
    return redirect("/?calendar=connected", code=302)


@app.get("/api/calendar/status")
def calendar_status():
    """Whether a Google Calendar grant is stored (tester-gated like
    every other /api/ route). The driver page uses this to show
    Connect Google Calendar or Calendar connected ✓."""
    return jsonify({"connected": calendar_connected()})


# --------------------------------------------------------------- sessions
@app.post("/api/session/start")
def session_start():
    try:
        _purge_expired_sessions()
    except StoreUnavailable:
        return jsonify({"error": "session store unavailable — try again shortly"}), 503
    ip = _client_ip()
    now = time.time()
    try:
        count = get_store().incr(f"luna:rl:sess:{ip}:{int(now // 3600)}", ex=3600)
    except StoreUnavailable:
        return jsonify({"error": "session store unavailable — try again shortly"}), 503
    if count > MAX_SESSIONS_PER_IP_PER_HOUR:
        return jsonify({"error": "too many test calls from this address — try again later"}), 429

    body = request.get_json(silent=True) or {}
    tester = _tester()
    scenario = body.get("scenario") or "inbound"
    client_id = body.get("client_id") or "test-client"
    role_id = body.get("role_id") or ("sales-desk" if scenario == "sales" else "front-desk")
    caller_name = (body.get("caller_name") or "").strip()[:80]
    caller_phone = (body.get("caller_phone") or "").strip()[:30]
    caller_is_new = bool(body.get("caller_is_new"))

    session = CallSession(client_id=client_id, role_id=role_id,
                          caller_name=caller_name, caller_phone=caller_phone,
                          caller_is_new=caller_is_new,
                          base_dir=str(PACKAGE_ROOT),
                          dnc_file=DNC_KEY,
                          approval_queue_file=APPROVAL_KEY)
    config = session.config
    if scenario == "callback":
        item_id = body.get("callback_item_id")
        item = None
        for candidate in all_callbacks(CALLBACK_KEY):
            if candidate["id"] == item_id:
                item = candidate
                break
        if item is None:
            return jsonify({"error": "callback item not found — request a callback first"}), 400
        # DISCLAIMER.md Part A, verbatim — the first thing spoken.
        greeting = build_callback_greeting(config, item)
        record_extra = {"callback_item_id": item_id}
    else:
        greeting = config.get("greeting") or ""
        record_extra = {}
    # Fail closed: a test call NEVER opens without the AI disclosure.
    if "AI" not in greeting:
        log_event(tester, "", "session_start_refused",
                  {"scenario": scenario, "reason": "greeting lacks AI disclosure"},
                  "refused")
        return jsonify({"error": "refusing to start: greeting does not disclose the AI"}), 500

    session_id = session.call_id
    audio_data = _tts(greeting)
    record = {"session_id": session_id, "session": session,
              "client_id": client_id, "role_id": role_id,
              "scenario": scenario, "tester": tester,
              "created_at": time.time(), "pending_booking": None,
              "booking_event_ids": [], **record_extra}
    try:
        _save_session(record)
    except StoreUnavailable:
        return jsonify({"error": "session store unavailable — try again shortly"}), 503
    log_event(tester, session_id, "session_start",
              {"scenario": scenario, "client_id": client_id, "role_id": role_id,
               "business": (config.get("business") or {}).get("name"),
               "greeting": greeting}, "started")
    return jsonify({"session_id": session_id, "greeting": greeting,
                    "audio_data": audio_data,
                    "business": (config.get("business") or {}).get("name"),
                    "scenario": scenario})


@app.post("/api/session/turn")
def session_turn():
    body = request.get_json(silent=True) or {}
    tester = _tester()
    try:
        record = _load_session(body.get("session_id") or "")
    except StoreUnavailable:
        return jsonify({"error": "session store unavailable — try again shortly"}), 503
    if record is None:
        return jsonify({"error": "session not found or expired — start a new test call"}), 404
    text = (body.get("text") or "").strip()
    if not text:
        return jsonify({"error": "empty message"}), 400
    session: CallSession = record["session"]
    log_event(tester, record["session_id"], "turn",
              {"caller": text}, "received")

    result = session.handle_turn(text)
    reply, action = result.reply, result.action
    booking = None

    # Booking intent in the caller's words -> pending slot (needs confirm).
    slot, parse_reason = parse_booking_intent(text, session.config)
    if slot:
        # NOTE: the slot also carries "start"/"end" as datetime objects
        # — the bench kept them in process memory, but this record is
        # JSON-serialized into KV, and datetimes are not JSON-safe. Keep
        # only the ISO strings (everything downstream uses start_iso /
        # end_iso).
        record["pending_booking"] = {
            "service": slot["service"],
            "start_iso": slot["start_iso"],
            "end_iso": slot["end_iso"],
        }
        log_event(tester, record["session_id"], "booking_parsed",
                  {"service": slot["service"], "start": slot["start_iso"]}, "parsed")
    elif parse_reason in ("outside opening hours",) or (
            parse_reason and parse_reason.startswith("closed that day")):
        log_event(tester, record["session_id"], "booking_out_of_hours",
                  {"reason": parse_reason}, "refused")

    # Confirmation-by-repetition: only after the caller says yes.
    if (is_confirmation(text) and record.get("pending_booking")
            and action not in ("escalate", "escalate_crisis", "opt_out",
                               "spam_close", "pending_approval")):
        booked = _book_pending(record, tester)
        if booked and "error" not in booked:
            # FIX #9: propagate the real action — a pending_approval hold
            # was forced to "booked" here, so the approval_item lookup
            # below never fired and the owner queue stayed empty.
            reply, action, booking = (booked["reply"],
                                      booked.get("action", "booked"), booked)
        elif booked:
            reply = booked["error"]

    if action == "opt_out" or "opt_out" in (result.flags or []):
        # FIX #8: compare through is_on_dnc — the raw phone never matches
        # the normalized DNC set on its own.
        on_dnc = is_on_dnc(DNC_KEY, record["client_id"],
                           session.caller_phone or "") \
            if session.caller_phone else False
        log_event(tester, record["session_id"], "opt_out",
                  {"caller_phone": session.caller_phone, "on_dnc_list": on_dnc},
                  "opted out")

    approval_item = None
    if action == "pending_approval":
        for item in all_items(APPROVAL_KEY):
            if item.get("call_id") == session.call_id and item.get("status") == "pending":
                approval_item = item
                break

    audio_data = _tts(reply)
    log_event(tester, record["session_id"], "reply",
              {"reply": reply, "action": action}, "answered")
    try:
        _save_session(record)
    except StoreUnavailable:
        return jsonify({"error": "session store unavailable — try again shortly"}), 503
    return jsonify({"reply": reply, "action": action, "flags": result.flags,
                    "audio_data": audio_data,
                    "pending_booking": record.get("pending_booking"),
                    "booking": booking, "approval_item": approval_item,
                    "outcome": session.outcome})


@app.post("/api/session/transcribe")
def session_transcribe():
    tester = _tester()
    if "audio" not in request.files:
        return jsonify({"error": "no audio file (field 'audio')"}), 400
    path, error = _save_audio_file(request.files["audio"])
    if error:
        return jsonify({"error": error}), 400
    text, error = _transcribe(path)
    try:
        os.unlink(path)
    except OSError:  # noqa: BLE001 - cleanup is best-effort
        pass
    if error:
        log_event(tester, "", "transcribe_failed", {"error": error}, "failed")
        return jsonify({"error": error}), 502
    log_event(tester, "", "transcribe", {"text": text}, "transcribed")
    return jsonify({"text": text})


@app.post("/api/session/confirm")
def session_confirm():
    body = request.get_json(silent=True) or {}
    tester = _tester()
    try:
        record = _load_session(body.get("session_id") or "")
    except StoreUnavailable:
        return jsonify({"error": "session store unavailable — try again shortly"}), 503
    if record is None:
        return jsonify({"error": "session not found or expired"}), 404
    booked = _book_pending(record, tester)
    if booked is None:
        return jsonify({"error": "no pending booking to confirm"}), 400
    if "error" in booked:
        try:
            _save_session(record)
        except StoreUnavailable:
            pass
        return jsonify({"error": booked["error"]}), 400
    audio_data = _tts(booked["reply"])
    try:
        _save_session(record)
    except StoreUnavailable:
        return jsonify({"error": "session store unavailable — try again shortly"}), 503
    return jsonify({**booked, "audio_data": audio_data})


@app.post("/api/session/cancel-booking")
def session_cancel_booking():
    body = request.get_json(silent=True) or {}
    tester = _tester()
    try:
        record = _load_session(body.get("session_id") or "")
    except StoreUnavailable:
        return jsonify({"error": "session store unavailable — try again shortly"}), 503
    if record is None:
        return jsonify({"error": "session not found or expired"}), 404
    event_id = body.get("event_id")
    if event_id not in (record.get("booking_event_ids") or []):
        return jsonify({"error": "that booking is not part of this test call"}), 400
    calendar_id = record.get("calendar_id") or "primary"
    try:
        gone = delete_event(calendar_id, event_id)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 502
    if not gone:
        return jsonify({"error": "the calendar still shows that event — it was not deleted"}), 502
    record["booking_event_ids"].remove(event_id)
    log_event(tester, record["session_id"], "booking_deleted",
              {"event_id": event_id}, "deleted by tester")
    try:
        _save_session(record)
    except StoreUnavailable:
        return jsonify({"error": "session store unavailable — try again shortly"}), 503
    return jsonify({"deleted": True, "event_id": event_id})


@app.post("/api/session/end")
def session_end():
    body = request.get_json(silent=True) or {}
    tester = _tester()
    try:
        record = _load_session(body.get("session_id") or "")
    except StoreUnavailable:
        return jsonify({"error": "session store unavailable — try again shortly"}), 503
    if record is None:
        return jsonify({"error": "session not found or expired"}), 404
    session: CallSession = record["session"]
    if record["scenario"] == "callback" and record.get("callback_item_id"):
        from callback_queue import callback_payload
        item = next((c for c in all_callbacks(CALLBACK_KEY)
                     if c["id"] == record["callback_item_id"]), None)
        payload = callback_payload(item or {}, "callback_completed",
                                   flags=session.flags) if item else session.call_end_payload()
    else:
        payload = session.call_end_payload()
    status, _body = _post_call_end(payload)
    config = session.config
    owner_email = ((config.get("ownerSummary") or {}).get("to")
                   or "tinglott@gmail.com")
    summary = _w2_summary(payload, owner_email)
    # Test bookings are deleted when the test call ends — always, and
    # only reported as deleted once the calendar confirms they are gone
    # (FIX #3: the old code reported the attempt, not the result).
    pending_ids = list(record.get("booking_event_ids") or [])
    deleted = _cleanup_session(record)
    failed = [eid for eid in pending_ids if eid not in deleted]
    log_event(tester, record["session_id"], "session_end",
              {"outcome": payload.get("outcome"),
               "post_status": status, "deleted_bookings": deleted,
               "delete_failures": failed}, "ended")
    return jsonify({"payload": payload, "post_status": status,
                    "summary": summary, "deleted_bookings": deleted,
                    "delete_failures": failed})


@app.get("/api/summary/<session_id>")
def summary(session_id: str):
    try:
        record = _load_session(session_id)
    except StoreUnavailable:
        return jsonify({"error": "session store unavailable — try again shortly"}), 503
    if record is None:
        return jsonify({"error": "session not found or expired"}), 404
    session: CallSession = record["session"]
    owner_email = ((session.config.get("ownerSummary") or {}).get("to")
                   or "tinglott@gmail.com")
    return jsonify(_w2_summary(session.call_end_payload(), owner_email))


# -------------------------------------------------------------- callbacks
@app.post("/api/callback/request")
def callback_request():
    body = request.get_json(silent=True) or {}
    tester = _tester()
    item = request_callback(
        CALLBACK_KEY,
        client_id=body.get("client_id") or "test-client",
        role_id=body.get("role_id") or "front-desk",
        call_id="call-" + uuid.uuid4().hex[:12],
        caller_name=(body.get("caller_name") or "").strip()[:80],
        caller_phone=(body.get("caller_phone") or "").strip()[:30],
        reason=(body.get("reason") or "").strip()[:200],
    )
    log_event(tester, "", "callback_requested",
              {"item_id": item["id"], "reason": item["reason"],
               "status": item["status"]}, "queued")
    return jsonify(item)


@app.get("/api/callback/list")
def callback_list():
    return jsonify(all_callbacks(CALLBACK_KEY))


@app.post("/api/callback/sweep")
def callback_sweep():
    body = request.get_json(silent=True) or {}
    tester = _tester()
    recipient_tz_name = body.get("recipient_tz") or "America/New_York"
    try:
        recipient_tz = ZoneInfo(recipient_tz_name)
    except Exception:  # noqa: BLE001
        recipient_tz = TZ
    # now_iso is a TEST hook (bench only): the battery sets the sweep time
    # to a weekday 10:00 so the in-hours/in-window path can be exercised
    # at any hour of the day. Production sweeps always use the real now.
    now_iso = body.get("now_iso")
    if now_iso:
        try:
            now_local = datetime.fromisoformat(now_iso)
            if now_local.tzinfo is None:
                now_local = now_local.replace(tzinfo=TZ)
        except ValueError:
            now_local = datetime.now(TZ)
    else:
        now_local = datetime.now(TZ)
    opened: list[dict] = []

    def dial(item: dict) -> dict:
        config = load_role_config(
            str(PACKAGE_ROOT), item["client_id"], item["role_id"])
        greeting = build_callback_greeting(config, item)
        session = CallSession(client_id=item["client_id"], role_id=item["role_id"],
                              caller_name=item["caller_name"],
                              caller_phone=item["caller_phone"],
                              base_dir=str(PACKAGE_ROOT),
                              dnc_file=DNC_KEY,
                              approval_queue_file=APPROVAL_KEY)
        session_id = session.call_id
        audio_data = _tts(greeting)
        record = {"session_id": session_id, "session": session,
                  "client_id": item["client_id"],
                  "role_id": item["role_id"],
                  "scenario": "callback", "tester": tester,
                  "created_at": time.time(), "pending_booking": None,
                  "booking_event_ids": [],
                  "callback_item_id": item["id"]}
        _save_session(record)
        opened.append({"item_id": item["id"], "session_id": session_id,
                       "greeting": greeting, "audio_data": audio_data})
        log_event(tester, session_id, "callback_answered",
                  {"item_id": item["id"], "greeting": greeting}, "answered")
        return {"reached": True, "session_id": session_id, "greeting": greeting}

    def rebooked_check(phone: str, requested_at: str) -> bool:
        # The call-end log (KV) replaces the stub capture file: a caller
        # who booked after asking for a callback is never dialled.
        try:
            rows = get_store().list_get(CALL_END_LOG_KEY)
        except StoreUnavailable:
            return False
        for row in rows:
            items = row if isinstance(row, list) else [row]
            for item in items:
                if not isinstance(item, dict):
                    continue
                detail = item.get("detail") or {}
                if (detail.get("caller_phone") == phone
                        and item.get("outcome") == "booked"):
                    return True
        return False

    def post_log(payload: dict) -> None:
        try:
            # Same created_at stamp as _post_call_end (W3 filters on it).
            payload.setdefault("created_at", datetime.now(TZ).isoformat())
            get_store().list_append(ACTIVITY_LOG_KEY, payload, max_len=1000)
        except StoreUnavailable:
            pass

    config = load_role_config(str(PACKAGE_ROOT),
                              "test-client", "front-desk")
    hours_text = (config.get("business") or {}).get("hours") or ""
    results = sweep(CALLBACK_KEY, hours_text=hours_text, now_local=now_local,
                    dial=dial, rebooked_check=rebooked_check, post_log=post_log,
                    dnc_check=lambda phone: is_on_dnc(DNC_KEY, "test-client", phone),
                    recipient_tz=recipient_tz)
    for result in results:
        log_event(tester, "", "callback_sweep",
                  {"item_id": result.get("id"), "action": result.get("action"),
                   "status": result.get("status")}, result.get("action") or "")
    return jsonify({"results": results, "opened_sessions": opened,
                    "now_local": now_local.isoformat(),
                    "recipient_tz": recipient_tz_name})


# --------------------------------------------------------------- walk-ins
@app.post("/api/walkin/add")
def walkin_add():
    body = request.get_json(silent=True) or {}
    tester = _tester()
    entry = add_walkin(WALKIN_KEY, (body.get("name") or "").strip()[:80],
                       (body.get("service") or "").strip()[:80])
    log_event(tester, "", "walkin_add",
              {"name": entry["name"], "position": entry["position"],
               "wait_minutes": entry["estimated_wait_minutes"]}, "queued")
    return jsonify(entry)


@app.get("/api/walkin/status")
def walkin_status():
    return jsonify(queue_status(WALKIN_KEY))


@app.post("/api/walkin/serve")
def walkin_serve():
    tester = _tester()
    served = serve_next(WALKIN_KEY)
    if served:
        log_event(tester, "", "walkin_serve", {"name": served["name"]}, "serving")
    return jsonify(served or {"served": None})


# -------------------------------------------------------------- approvals
@app.get("/api/approval/pending")
def approval_pending():
    return jsonify(pending_items(APPROVAL_KEY))


@app.post("/api/approval/approve")
def approval_approve():
    body = request.get_json(silent=True) or {}
    tester = _tester()
    try:
        item = approve(APPROVAL_KEY, body.get("item_id") or "",
                       decided_by=tester)
    except (KeyError, ValueError) as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 502
    log_event(tester, "", "approval_approve",
              {"item_id": item["id"], "event_id": item.get("booking_event_id")},
              "approved")
    return jsonify(item)


@app.post("/api/approval/deny")
def approval_deny():
    body = request.get_json(silent=True) or {}
    tester = _tester()
    try:
        item = deny(APPROVAL_KEY, body.get("item_id") or "", decided_by=tester)
    except (KeyError, ValueError) as exc:
        return jsonify({"error": str(exc)}), 400
    log_event(tester, "", "approval_deny", {"item_id": item["id"]}, "denied")
    return jsonify(item)


# -------------------------------------------------------------- feedback
@app.post("/api/feedback")
def feedback():
    body = request.get_json(silent=True) or {}
    tester = _tester()
    text = (body.get("text") or "").strip()
    if not text:
        return jsonify({"error": "empty feedback"}), 400
    log_event(tester, body.get("session_id") or "", "feedback",
              {"text": text[:1000]}, "reported")
    return jsonify({"logged": True})


# ------------------------------------------------------- test reset (guarded)
# ------------------------------------------- W3 monthly report (live)
@app.get("/api/report/monthly")
def monthly_report():
    """W3 Monthly Report — the COMPILE step, live (mirrors the W3 Code
    node). Reads the KV activity log and compiles the month exactly
    like the node: calls answered, bookings made, messages taken, spam
    blocked, minutes used vs the 225-minute template allowance.
    ?month=YYYY-MM selects the month; default is the prior month.
    Returns the DRAFT — Ting reviews it before it goes anywhere. The
    Gmail send step is NOT in this test driver; nothing is emailed.
    Rows written before the created_at stamp exist are counted under
    "undated" and excluded from the month totals, honestly."""
    from datetime import date as _date

    month_arg = (request.args.get("month") or "").strip()
    now = datetime.now(TZ)
    if month_arg:
        try:
            year, month = (int(p) for p in month_arg.split("-"))
            if not (1 <= month <= 12):
                raise ValueError
        except ValueError:
            return jsonify({"error": "month must be YYYY-MM"}), 400
    else:
        year, month = (now.year, now.month - 1) if now.month > 1 else (now.year - 1, 12)
    start = datetime(year, month, 1, tzinfo=TZ)
    end = datetime(year + (1 if month == 12 else 0), 1 if month == 12 else month + 1,
                   1, tzinfo=TZ)
    label = start.strftime("%B %Y")
    try:
        rows = get_store().list_get(ACTIVITY_LOG_KEY) or []
    except StoreUnavailable:
        return jsonify({"error": "session store unavailable — try again shortly"}), 503
    month_rows, undated = [], 0
    for row in rows:
        items = row if isinstance(row, list) else [row]
        for item in items:
            if not isinstance(item, dict):
                continue
            if not (item.get("action") or "").startswith("call_"):
                continue
            created = item.get("created_at")
            if not created:
                undated += 1
                continue
            try:
                ts = datetime.fromisoformat(str(created).replace("Z", "+00:00"))
            except ValueError:
                undated += 1
                continue
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=TZ)
            if start <= ts.astimezone(TZ) < end:
                month_rows.append(item)
    count = lambda outcome: sum(1 for r in month_rows if r.get("outcome") == outcome)
    minutes = round(sum(r.get("duration_seconds") or 0 for r in month_rows) / 60)
    bookings = ["- " + ((r.get("detail") or {}).get("caller_name") or "caller") + ": "
                + ((r.get("detail") or {}).get("booking_or_message") or "")
                for r in month_rows if r.get("outcome") == "booked"]
    allowance = 225  # W3 template default; no per-client allowance is configured yet
    subject = f"Monthly report (DRAFT for Ting review) — test-client — {label}"
    body = "\n".join([
        "DRAFT — Ting reviews this before it goes to the owner.", "",
        f"AI Receptionist — monthly report for {label}",
        "Client: test-client", "",
        f"Calls answered: {len(month_rows)}",
        f"Bookings made: {count('booked')}",
        f"Messages taken: {count('message')}",
        f"Spam blocked: {count('spam')}",
        f"Minutes used: {minutes} of {allowance} included.", "",
        "Bookings:", "\n".join(bookings) if bookings else "(none this month)", "",
        "These are the calls that actually happened. Nothing here is a promise about next month.",
    ])
    return jsonify({"month": f"{year:04d}-{month:02d}", "label": label, "draft": True,
                    "subject": subject, "body": body,
                    "counts": {"calls_answered": len(month_rows),
                               "bookings": count("booked"),
                               "messages": count("message"),
                               "spam_blocked": count("spam"),
                               "undated_rows_excluded": undated},
                    "minutes_used": minutes, "allowance_minutes": allowance,
                    "bookings": bookings})


# --------------------------------------------- W4 heartbeat (live)
@app.post("/api/heartbeat")
def heartbeat():
    """W4 Monitoring + Heartbeat — the ROW + CHECK steps, live. Writes
    a heartbeat row to the KV activity log (keeps the store warm, the
    way the W4 node keeps the free Supabase project awake) and reports
    whether the voice service is up (store + calendar status), plus
    whether today is the Monday forwarding-check day. The Gmail alert
    + reminder steps are NOT in this test driver — a down service is
    reported here, not emailed."""
    now = datetime.now(TZ)
    try:
        store = get_store()
        status = store.status()
        store_ok = bool(status.get("configured"))
    except StoreUnavailable:
        return jsonify({"ok": False, "error": "session store unavailable"}), 503
    entry = {"employee_id": "aria", "action": "heartbeat",
             "client_id": "__service__", "role_id": "front-desk",
             "created_at": now.isoformat(),
             "detail": {"check": "daily_heartbeat",
                        "note": "keeps the store warm"}}
    try:
        store.list_append(ACTIVITY_LOG_KEY, entry, max_len=1000)
    except StoreUnavailable:
        return jsonify({"ok": False, "error": "session store unavailable"}), 503
    return jsonify({"ok": store_ok,
                    "store_ok": store_ok,
                    "calendar_connected": calendar_connected(),
                    "forwarding_check_due": now.weekday() == 0,
                    "ts": now.isoformat()})


@app.post("/api/test/reset")
def test_reset():
    """Reset all test state (sessions, queues, DNC, logs) — bench
    battery only. Requires the LUNA_TEST_RESET_KEY env var to be set
    AND the matching X-Test-Reset-Key header. When the env var is
    unset (the default on a public deployment) this endpoint 404s —
    nobody can wipe the test state from the outside. The key is set
    in the host env vars, never in the repo."""
    key = os.environ.get("LUNA_TEST_RESET_KEY", "")
    if not key or request.headers.get("X-Test-Reset-Key") != key:
        return jsonify({"error": "not found"}), 404
    store = get_store()
    for session_entry in store.list_get(SESSION_INDEX_KEY):
        store.delete(_session_key(session_entry.get("session_id") or ""))
    for k in (SESSION_INDEX_KEY, CALLBACK_KEY[5:], DNC_KEY[5:], WALKIN_KEY[5:],
              APPROVAL_KEY[5:], TESTER_LOG_KEY, ACTIVITY_LOG_KEY, CALL_END_LOG_KEY):
        store.delete(k)
    return jsonify({"reset": True})


# ------------------------------------------------------------------ main
if __name__ == "__main__":
    # Local runs only (the WSGI file imports `app` directly). Memory backend
    # unless UPSTASH/KV env is set; GROQ_API_KEY from the environment.
    print(f"Luna test driver (PythonAnywhere build) listening on http://{HOST}:{PORT}", flush=True)
    print(f"Store backend: {get_store().backend_name} "
          f"(configured={get_store().status().get('configured')})", flush=True)
    print("Voice: Groq Orpheus 'hannah' — NOT Kokoro af_heart (see index.py header)", flush=True)
    app.run(host=HOST, port=PORT, threaded=True)
