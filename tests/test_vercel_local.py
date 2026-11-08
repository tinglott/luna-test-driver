#!/usr/bin/env python3
"""Local bench tests for the VERCEL driver (Flask test client).

No real Google, no real Groq, no public URL. The fake Calendar API
(tests/fake_calendar_api.py) stands in for Google; OAuth credentials
are seeded via LUNA_TEST_OAUTH_JSON (bench-only hook); the store is
the in-process memory backend (no KV env set). GROQ_API_KEY is
deliberately unset — TTS is gracefully absent and the text replies
still answer (the booking/gate paths do not need Groq).

Run:  ~/workspace/venvs/luna-pa/bin/python tests/test_vercel_local.py
Exit 0 = all checks pass; exit 1 = any FAIL (listed). A check can be
BLOCKED when its prerequisite is unavailable — none are expected to
block on the bench.

Checks:
  1. /health shape (memory store, calendar connected via seeded creds)
  2. / tester gate (invited-only page vs driver page)
  3. PWA routes: /manifest.json, /sw.js (network-only /api/ rule),
     /icon-192.png, /icon-512.png
  4. /api/calendar/status (gated; connected true)
  5. /authorize: 302 to Google with offline access + consent prompt
  6. /oauth2callback: error / bad state / missing code all fail safe
  7. Session flow against the fake Calendar API: start -> turn
     (booking intent parsed) -> confirm (real event created + read
     back) -> end (booking verified deleted)
  8. Crisis turn carries 988 (gate intact through the HTTP path)
  9. invalid_grant: a revoked grant falls back to message-taking
     (no 500) and the stored token is cleared
 10. Service-account path is gone: GOOGLE_SERVICE_ACCOUNT_JSON set
     to garbage is ignored (health + booking still work)
 11. /api/test/reset: 404 without the key, works with it, and does
     NOT clear the OAuth token
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import requests

HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parent
FAKE_CAL_PORT = 18103
BAD_TOKEN_PORT = 18104
FAKE_STATE_FILE = Path("/tmp/luna-fake-calendar-state.json")

# --- env BEFORE importing the app (backend selection happens at first use)
os.environ["NO_PROXY"] = "localhost,127.0.0.1"
os.environ["no_proxy"] = "localhost,127.0.0.1"
os.environ["CALENDAR_ID"] = "test-calendar"
os.environ["GOOGLE_API_DISCOVERY_URL"] = (
    f"http://127.0.0.1:{FAKE_CAL_PORT}/discovery/calendar-v3.json")
os.environ["LUNA_TEST_OAUTH_JSON"] = json.dumps({
    "refresh_token": "fake-refresh-token",
    "client_id": "fake-client-id",
    "client_secret": "fake-client-secret",
    "token_uri": f"http://127.0.0.1:{FAKE_CAL_PORT}/token",
    "scopes": ["https://www.googleapis.com/auth/calendar.events"],
    "access_token": "fake-access-token",
    "expiry": "2030-01-01T00:00:00+00:00",
})
os.environ["GOOGLE_OAUTH_CLIENT_ID"] = "test-oauth-client-id"
os.environ["GOOGLE_OAUTH_CLIENT_SECRET"] = "test-oauth-client-secret"
os.environ["LUNA_TEST_RESET_KEY"] = "test-reset-key"
os.environ.pop("GROQ_API_KEY", None)
# The old service-account var, set to garbage on purpose: nothing in
# this build may read it. If anything did, the booking flow would fail.
os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"] = "this-is-not-json{"

sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "agent-service"))

import index as app_module  # noqa: E402
from kvstore import get_store  # noqa: E402

CHECKS: list[dict] = []


def log_check(name: str, ok: bool, detail: str = "") -> None:
    status = "PASS" if ok else "FAIL"
    CHECKS.append({"name": name, "ok": ok, "detail": detail})
    print(f"[{status}] {name}" + (f" — {detail}" if detail else ""))


class BadTokenHandler(BaseHTTPRequestHandler):
    """Tiny token endpoint that always answers invalid_grant."""

    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.dumps({"error": "invalid_grant",
                           "error_description": "Token has been expired "
                           "or revoked."}).encode("utf-8")
        self.send_response(400)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def start_fake_calendar() -> subprocess.Popen:
    if FAKE_STATE_FILE.exists():
        FAKE_STATE_FILE.unlink()
    proc = subprocess.Popen(
        [sys.executable, str(HERE / "fake_calendar_api.py"),
         str(FAKE_CAL_PORT)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.time() + 15
    while time.time() < deadline:
        try:
            resp = requests.get(
                f"http://127.0.0.1:{FAKE_CAL_PORT}/discovery/calendar-v3.json",
                timeout=2)
            if resp.status_code == 200:
                return proc
        except Exception:
            pass
        time.sleep(0.3)
    proc.kill()
    raise RuntimeError("fake Calendar API did not come up")


def fake_calendar_state() -> dict:
    if not FAKE_STATE_FILE.exists():
        return {}
    try:
        return json.loads(FAKE_STATE_FILE.read_text()).get("events", {})
    except ValueError:
        return {}


def main() -> int:
    client = app_module.app.test_client()
    tester_headers = {"X-Tester": "bench-battery"}

    # Seed the stored OAuth record — the same record exchange_code()
    # writes after a real Google sign-in. calendar_connected() reads
    # KV only (production source of truth); the env hook is only the
    # booking path's bench fallback.
    fake_oauth_record = json.loads(os.environ["LUNA_TEST_OAUTH_JSON"])
    get_store().set_json("luna:google_oauth", fake_oauth_record)

    # --- 1. /health ---------------------------------------------------
    resp = client.get("/health")
    health = resp.get_json() or {}
    log_check("health_ok", resp.status_code == 200 and health.get("ok") is True,
              json.dumps(health)[:200])
    log_check("health_store_memory_configured",
              health.get("store_ok") is True
              and health.get("store", {}).get("backend") == "memory",
              str(health.get("store")))
    log_check("health_calendar_connected_seeded",
              health.get("calendar_connected") is True,
              str(health.get("calendar_connected")))
    log_check("health_tts_mode_groq_orpheus",
              health.get("tts_mode") == "groq-orhpheus"
              and health.get("tts_worker") is False,
              "no GROQ_API_KEY on the bench — TTS gracefully absent")

    # --- 2. / tester gate ---------------------------------------------
    resp = client.get("/")
    log_check("index_invited_only_without_tester",
              b"invited testers only" in resp.data.lower(),
              f"status={resp.status_code}")
    resp = client.get("/?tester=bench-battery")
    log_check("index_driver_page_for_invited_tester",
              b"ARTISY AI Receptionist" in resp.data
              and b"Connect Google Calendar" in resp.data,
              f"status={resp.status_code}")

    # --- 3. PWA routes -------------------------------------------------
    resp = client.get("/manifest.json")
    manifest = resp.get_json(silent=True) or {}
    log_check("manifest_serves_luna_pwa",
              resp.status_code == 200 and "Luna" in manifest.get("name", "")
              and manifest.get("display") == "standalone"
              and manifest.get("start_url") == "/",
              json.dumps(manifest)[:160])
    resp = client.get("/sw.js")
    sw = resp.data.decode("utf-8", "replace")
    log_check("service_worker_network_only_api",
              resp.status_code == 200
              and '/api/' in sw and 'network' in sw.lower(),
              f"status={resp.status_code} len={len(sw)}")
    log_check("service_worker_no_cache_header",
              "no-cache" in (resp.headers.get("Cache-Control") or ""),
              resp.headers.get("Cache-Control") or "")
    from PIL import Image
    import io as _io
    for path, size in (("/icon-192.png", (192, 192)),
                       ("/icon-512.png", (512, 512))):
        resp = client.get(path)
        try:
            img = Image.open(_io.BytesIO(resp.data))
            dims = img.size
        except Exception:
            dims = None
        log_check(f"icon_{size[0]}_serves_png",
                  resp.status_code == 200 and dims == size,
                  f"status={resp.status_code} dims={dims}")

    # --- 4. /api/calendar/status ---------------------------------------
    resp = client.get("/api/calendar/status")
    log_check("calendar_status_gated_anonymous",
              resp.status_code == 403, f"status={resp.status_code}")
    resp = client.get("/api/calendar/status", headers=tester_headers)
    log_check("calendar_status_connected",
              resp.status_code == 200
              and resp.get_json().get("connected") is True,
              resp.data.decode()[:120])

    # --- 5. /authorize --------------------------------------------------
    resp = client.get("/authorize")
    loc = resp.headers.get("Location") or ""
    parsed = urlparse(loc)
    qs = parse_qs(parsed.query)
    log_check("authorize_redirects_to_google",
              resp.status_code == 302
              and "accounts.google.com" in loc
              and qs.get("access_type") == ["offline"]
              and qs.get("prompt") == ["consent"]
              and qs.get("response_type") == ["code"]
              and "calendar.events" in (qs.get("scope") or [""])[0]
              and "oauth2callback" in (qs.get("redirect_uri") or [""])[0],
              loc[:220])
    log_check("authorize_pkce_off_no_code_challenge",
              "code_challenge" not in parsed.query,
              "PKCE challenge present — the callback's fresh Flow would "
              "send a different verifier and Google would refuse the code")
    state = (qs.get("state") or [""])[0]
    log_check("authorize_state_stored_server_side",
              bool(state) and get_store().get("luna:oauth_state:" + state) == "1",
              f"state={'present' if state else 'missing'}")

    # --- 6. /oauth2callback fails safe ----------------------------------
    resp = client.get("/oauth2callback?error=access_denied")
    log_check("callback_access_denied_safe",
              resp.status_code == 200 and b"not connected" in resp.data.lower(),
              f"status={resp.status_code}")
    resp = client.get("/oauth2callback?code=x&state=badstate")
    log_check("callback_bad_state_safe",
              resp.status_code == 200 and b"expired" in resp.data.lower(),
              f"status={resp.status_code}")
    resp = client.get("/oauth2callback")
    log_check("callback_missing_code_safe",
              resp.status_code == 200 and b"sign-in code" in resp.data.lower(),
              f"status={resp.status_code}")

    # --- 7. Session flow vs fake Calendar API ---------------------------
    fake_proc = start_fake_calendar()
    try:
        start = client.post("/api/session/start",
                            json={"scenario": "inbound",
                                  "caller_name": "Battery Tester",
                                  "caller_phone": "+1 555 010 0001",
                                  "caller_is_new": False,
                                  "client_id": "test-client",
                                  "tester": "bench-battery"},
                            headers=tester_headers)
        start_data = start.get_json() or {}
        sid = start_data.get("session_id") or ""
        log_check("session_start_greeting_discloses_ai",
                  start.status_code == 200 and "AI" in start_data.get("greeting", ""),
                  f"status={start.status_code} greeting={start_data.get('greeting', '')[:80]}")
        t = client.post("/api/session/turn",
                        json={"session_id": sid,
                              "text": "I want to book a check-up on Monday at 10am",
                              "tester": "bench-battery"},
                        headers=tester_headers)
        turn_data = t.get_json() or {}
        pending = turn_data.get("pending_booking") or {}
        start_iso = pending.get("start_iso") or ""
        is_monday_10am = False
        try:
            dt = datetime.fromisoformat(start_iso)
            is_monday_10am = dt.weekday() == 0 and dt.hour == 10
        except ValueError:
            pass
        log_check("booking_intent_parsed",
                  pending.get("service") == "Check-up" and is_monday_10am,
                  json.dumps(pending))
        booked = client.post("/api/session/confirm",
                             json={"session_id": sid, "tester": "bench-battery"},
                             headers=tester_headers)
        booked_data = booked.get_json() or {}
        event_id = booked_data.get("event_id") or ""
        log_check("booking_confirmed_real_event",
                  booked.status_code == 200 and bool(event_id)
                  and booked_data.get("service") == "Check-up",
                  f"status={booked.status_code} event={event_id}")
        events = fake_calendar_state()
        log_check("booking_read_back_in_calendar",
                  event_id in events
                  and "Check-up" in (events[event_id].get("summary") or ""),
                  f"events_in_fake_calendar={len(events)}")
        ended = client.post("/api/session/end",
                            json={"session_id": sid, "tester": "bench-battery"},
                            headers=tester_headers)
        ended_data = ended.get_json() or {}
        log_check("session_end_reports_verified_delete",
                  event_id in ended_data.get("deleted_bookings", [])
                  and not ended_data.get("delete_failures"),
                  f"deleted={ended_data.get('deleted_bookings')}")
        log_check("booking_actually_gone_after_end",
                  event_id not in fake_calendar_state(),
                  "event still present" if event_id in fake_calendar_state() else "gone")
        log_check("call_end_posted_201",
                  ended_data.get("post_status") == 201,
                  f"post_status={ended_data.get('post_status')}")

        # --- 8. Crisis turn carries 988 -------------------------------
        s2 = client.post("/api/session/start",
                         json={"scenario": "inbound",
                               "caller_name": "Battery Tester",
                               "caller_phone": "+1 555 010 0001",
                               "caller_is_new": False,
                               "client_id": "test-client",
                               "tester": "bench-battery"},
                         headers=tester_headers)
        sid2 = (s2.get_json() or {}).get("session_id") or ""
        t2 = client.post("/api/session/turn",
                         json={"session_id": sid2,
                               "text": "I feel like hurting myself",
                               "tester": "bench-battery"},
                         headers=tester_headers)
        reply2 = (t2.get_json() or {}).get("reply", "")
        log_check("crisis_turn_carries_988",
                  "988" in reply2,
                  reply2[:120])
        client.post("/api/session/end",
                    json={"session_id": sid2, "tester": "bench-battery"},
                    headers=tester_headers)

        # --- 9. invalid_grant falls back, clears token -----------------
        bad_server = ThreadingHTTPServer(("127.0.0.1", BAD_TOKEN_PORT),
                                         BadTokenHandler)
        thread = threading.Thread(target=bad_server.serve_forever, daemon=True)
        thread.start()
        try:
            get_store().set_json("luna:google_oauth", {
                "refresh_token": "bad-refresh-token",
                "client_id": "fake-client-id",
                "client_secret": "fake-client-secret",
                "token_uri": f"http://127.0.0.1:{BAD_TOKEN_PORT}/token",
                "scopes": ["https://www.googleapis.com/auth/calendar.events"],
                "access_token": None, "expiry": None,
            })
            s3 = client.post("/api/session/start",
                             json={"scenario": "inbound",
                                   "caller_name": "Battery Tester",
                                   "caller_phone": "+1 555 010 0001",
                                   "caller_is_new": False,
                                   "client_id": "test-client",
                                   "tester": "bench-battery"},
                             headers=tester_headers)
            sid3 = (s3.get_json() or {}).get("session_id") or ""
            t3 = client.post("/api/session/turn",
                             json={"session_id": sid3,
                                   "text": "I want to book a check-up on Monday at 10am",
                                   "tester": "bench-battery"},
                             headers=tester_headers)
            t3_data = t3.get_json() or {}
            log_check("invalid_grant_pending_booking_parsed",
                      (t3_data.get("pending_booking") or {}).get("service") == "Check-up",
                      json.dumps(t3_data.get("pending_booking") or {}))
            # The turn itself never touches the calendar (the brain is
            # absent on the bench, so it falls back to message-taking
            # first). The grant is exercised by CONFIRMING the booking.
            c3 = client.post("/api/session/confirm",
                             json={"session_id": sid3, "tester": "bench-battery"},
                             headers=tester_headers)
            c3_data = c3.get_json() or {}
            # A failed booking surfaces as 400 + the message-taking
            # fallback text (the confirm route returns {"error": reply}
            # for any non-booked outcome — see _book_pending).
            err = str(c3_data.get("error") or "")
            log_check("invalid_grant_confirm_falls_back_to_message_taking",
                      c3.status_code == 400
                      and ("take your name" in err.lower()
                           or "can't reach the diary" in err.lower()
                           or "confirm your time" in err.lower())
                      and not c3_data.get("event_id"),
                      f"status={c3.status_code} error={err[:100]}")
            log_check("invalid_grant_clears_stored_token",
                      get_store().get_json("luna:google_oauth") is None,
                      "token still stored" if get_store().get_json("luna:google_oauth") else "cleared")
            # calendar_connected() reads KV only, so it is false now
            # even though the bench env hook is still set.
            resp = client.get("/api/calendar/status", headers=tester_headers)
            log_check("calendar_status_false_after_revoke",
                      resp.get_json().get("connected") is False,
                      resp.data.decode()[:100])
            # The bench-only env hook still feeds the booking path when
            # KV is empty (it never feeds the status page).
            import calendar_booking
            log_check("env_hook_fallback_for_booking_path",
                      calendar_booking._test_oauth() is not None,
                      "LUNA_TEST_OAUTH_JSON not picked up")
            # Re-connect: re-seed the good record (what a fresh Google
            # sign-in would write).
            get_store().set_json("luna:google_oauth", fake_oauth_record)
            resp = client.get("/api/calendar/status", headers=tester_headers)
            log_check("calendar_status_true_after_reconnect",
                      resp.get_json().get("connected") is True,
                      resp.data.decode()[:100])
            client.post("/api/session/end",
                        json={"session_id": sid3, "tester": "bench-battery"},
                        headers=tester_headers)
        finally:
            bad_server.shutdown()
            bad_server.server_close()

        # --- 10. SA path gone is proven by the run above ---------------
        # (GOOGLE_SERVICE_ACCOUNT_JSON was garbage for the whole run;
        # health + booking still worked.) Recorded explicitly:
        log_check("service_account_var_ignored",
                  os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON") == "this-is-not-json{"
                  and health.get("calendar_connected") is True,
                  "garbage SA var present for the whole run; bookings worked")

        # --- 11. /api/test/reset ---------------------------------------
        get_store().set_json("luna:google_oauth", {
            "refresh_token": "fake-refresh-token",
            "client_id": "fake-client-id",
            "client_secret": "fake-client-secret",
            "token_uri": f"http://127.0.0.1:{FAKE_CAL_PORT}/token",
            "scopes": ["https://www.googleapis.com/auth/calendar.events"],
            "access_token": "fake-access-token",
            "expiry": "2030-01-01T00:00:00+00:00",
        })
        resp = client.post("/api/test/reset", json={"tester": "bench-battery"},
                           headers=tester_headers)
        log_check("test_reset_404_without_key",
                  resp.status_code == 404, f"status={resp.status_code}")
        resp = client.post("/api/test/reset", json={"tester": "bench-battery"},
                           headers={**tester_headers,
                                    "X-Test-Reset-Key": "test-reset-key"})
        log_check("test_reset_works_with_key",
                  resp.status_code == 200
                  and resp.get_json().get("reset") is True,
                  f"status={resp.status_code}")
        log_check("test_reset_keeps_oauth_token",
                  get_store().get_json("luna:google_oauth") is not None,
                  "OAuth token was wiped by reset" if get_store().get_json("luna:google_oauth") is None else "kept")
    finally:
        fake_proc.kill()

    failed = [c for c in CHECKS if not c["ok"]]
    print(f"\n{len(CHECKS) - len(failed)}/{len(CHECKS)} checks passed")
    if failed:
        print("FAILURES:")
        for c in failed:
            print(f"  - {c['name']}: {c['detail']}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
