"""Google Calendar booking — PYTHONANYWHERE version (OAuth 2.0, user grant).

Same public API as the Vercel version (``check_free`` / ``create_event`` /
``get_event`` / ``delete_event`` / ``book_appointment`` + ``CalendarError``),
so ``call_handler.py`` and ``approval_queue.py`` need no changes. The
transport is different:

  * NO service account, NO service-account key env var — that path
    is gone entirely (no code in this repo reads a key file). The
    calendar is accessed with the Google account that
    signed in through the app's own /authorize flow: the refresh token
    lives server-side in Upstash KV (``luna:google_oauth``), put there
    when Ting taps Allow once. The app never sees a key file and Ting
    never pastes one.
  * Scope: ``https://www.googleapis.com/auth/calendar.events`` — create
    / read / delete events on existing calendars (no whole-calendar
    management). Freebusy is tried first; if it refuses, events are
    listed in the window (same shape as the Vercel version).
  * Credentials are refreshed on demand; a refresh that fails with
    ``invalid_grant`` (revoked / expired grant) deletes the stored
    token and raises CalendarError telling the caller the calendar is
    not connected — the driver page then shows "Connect Google
    Calendar" again. Any other auth failure also becomes CalendarError
    (``call_handler`` only catches CalendarError — a raw auth
    exception here would 500 a test call). Bookings fall back to
    message-taking — never a fake booking.
  * Proxy handling (PythonAnywhere free routes egress through its
    proxy; the bench proxy must NOT be used for localhost): when the
    local discovery override is set (bench test hook) the API client
    goes direct; otherwise it uses the platform proxy env vars when
    present. Token refreshes ride ``requests`` (which honours
    NO_PROXY for localhost).

Local test hook (documented, bench-only): setting
``GOOGLE_API_DISCOVERY_URL`` points the client at a local discovery
document, and ``LUNA_TEST_OAUTH_JSON`` holds the full credentials
JSON (refresh_token, client_id, client_secret, token_uri, scopes,
access_token, expiry) — so a fake Calendar API server can prove the
request/response code path without a real Google account. Neither
variable is ever set on a public deployment; KV is the only
production source of the token.

Requires: google-api-python-client, google-auth (see requirements.txt).
"""

from __future__ import annotations

import functools
import json
import os
from datetime import datetime, timedelta, timezone

from google.auth.exceptions import GoogleAuthError, RefreshError
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

_SCOPES = ["https://www.googleapis.com/auth/calendar.events"]
_TOKEN_URI_DEFAULT = "https://oauth2.googleapis.com/token"
_OAUTH_KV_KEY = "luna:google_oauth"


class CalendarError(Exception):
    """Calendar unavailable — caller must fall back, never invent a booking."""


def _kv_get_oauth() -> dict | None:
    """Stored OAuth record from KV (production source). None when
    absent or unreadable — never raises."""
    try:
        from kvstore import get_store
    except ImportError:  # standalone import (tests): repo root is the parent
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        from kvstore import get_store
    try:
        data = get_store().get_json(_OAUTH_KV_KEY)
    except Exception:
        return None
    if data and data.get("refresh_token"):
        return data
    return None


def _test_oauth() -> dict | None:
    """Bench-only hook: full credentials JSON from env. Never set on
    a public deployment."""
    raw = os.environ.get("LUNA_TEST_OAUTH_JSON", "").strip()
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise CalendarError(
            "LUNA_TEST_OAUTH_JSON is not valid JSON.") from exc
    if not data.get("refresh_token"):
        return None
    return data


def _clear_stored_oauth() -> None:
    """Delete the stored token (revoked grant). Best effort."""
    try:
        from kvstore import get_store
        get_store().delete(_OAUTH_KV_KEY)
    except Exception:
        pass


def _build_credentials(data: dict) -> Credentials:
    expiry = None
    if data.get("expiry"):
        try:
            expiry = datetime.fromisoformat(
                str(data["expiry"]).replace("Z", "+00:00"))
        except ValueError:
            expiry = None
    if expiry is not None and expiry.tzinfo is not None:
        # google-auth's utcnow() is deliberately NAIVE (see
        # google/auth/_helpers.py), and Credentials.expired compares
        # the two — an aware expiry raises TypeError on every
        # creds.valid check and breaks all bookings. Normalize to
        # naive UTC, which is what google-auth expects.
        expiry = expiry.astimezone(timezone.utc).replace(tzinfo=None)
    return Credentials(
        token=data.get("access_token"),
        refresh_token=data["refresh_token"],
        token_uri=data.get("token_uri") or _TOKEN_URI_DEFAULT,
        client_id=data.get("client_id"),
        client_secret=data.get("client_secret"),
        scopes=data.get("scopes") or _SCOPES,
        expiry=expiry,
    )


def _persist_refreshed(creds: Credentials, data: dict) -> None:
    """Save a refreshed access token back to KV so the next process
    does not re-refresh immediately. Best effort — a failure here
    only costs one extra refresh later, never a booking."""
    try:
        from kvstore import get_store
        updated = dict(data)
        updated["access_token"] = creds.token
        if creds.expiry is not None:
            updated["expiry"] = creds.expiry.isoformat()
        get_store().set_json(_OAUTH_KV_KEY, updated)
    except Exception:
        pass


def _refresh_request():
    """Transport for token refresh — plain requests (honours the
    platform proxy env vars and NO_PROXY for localhost)."""
    from google.auth.transport.requests import Request
    return Request()


def _proxy_info():
    """httplib2 proxy for the API client. Direct when the local
    discovery override is set (bench fake server on localhost —
    httplib2 does not honour no_proxy, so a proxy here would break
    it); otherwise the platform proxy env vars when present
    (PythonAnywhere free requires the proxy for external hosts)."""
    if os.environ.get("GOOGLE_API_DISCOVERY_URL", "").strip():
        return None
    proxy_url = (os.environ.get("https_proxy")
                 or os.environ.get("http_proxy") or "").strip()
    if not proxy_url:
        return None
    from urllib.parse import urlparse
    parsed = urlparse(proxy_url)
    if not parsed.hostname:
        return None
    import httplib2
    return httplib2.ProxyInfo(httplib2.socks.PROXY_TYPE_HTTP,
                              parsed.hostname, parsed.port or 3128)


def _get_service():
    """Build the Calendar API client from the stored OAuth grant.
    A fresh service per call (no module-level cache): the discovery
    doc is cached on disk, credentials refresh on demand, and a
    revoked grant is noticed on the next call instead of lingering
    in a cached client. Raises CalendarError when the calendar is
    not connected or the grant fails."""
    data = _kv_get_oauth()
    from_kv = data is not None
    if data is None:
        data = _test_oauth()
    if data is None:
        raise CalendarError(
            "Google Calendar is not connected — open the app and tap "
            "Connect Google Calendar. Calendar bookings fall back to "
            "message-taking.")
    creds = _build_credentials(data)
    if not creds.valid:
        try:
            creds.refresh(_refresh_request())
        except RefreshError as exc:
            if "invalid_grant" in str(exc):
                if from_kv:
                    _clear_stored_oauth()
                raise CalendarError(
                    "Google Calendar is not connected — the grant was "
                    "revoked or expired. Open the app and tap Connect "
                    "Google Calendar again.") from exc
            raise CalendarError(
                f"calendar sign-in refresh failed: {exc}") from exc
        except GoogleAuthError as exc:
            raise CalendarError(
                f"calendar sign-in failed: {exc}") from exc
        if from_kv:
            _persist_refreshed(creds, data)
    kwargs = {"cache_discovery": False}
    discovery_url = os.environ.get("GOOGLE_API_DISCOVERY_URL", "").strip()
    if discovery_url:  # local test hook only; see module docstring
        kwargs["discoveryServiceUrl"] = discovery_url
    try:
        import httplib2
        from google_auth_httplib2 import AuthorizedHttp

        auth_http = AuthorizedHttp(
            creds, http=httplib2.Http(proxy_info=_proxy_info()))
        return build("calendar", "v3", http=auth_http, **kwargs)
    except Exception as exc:  # noqa: BLE001 - discovery failures are outages
        raise CalendarError(f"Calendar API client build failed: {exc}") from exc


def _auth_errors(fn):
    """Decorator: any Google auth failure inside a calendar call
    becomes CalendarError (call_handler only catches CalendarError).
    An invalid_grant also clears the stored token so the next call
    reports 'not connected' instead of retrying a dead grant."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except RefreshError as exc:
            if "invalid_grant" in str(exc):
                _clear_stored_oauth()
                raise CalendarError(
                    "Google Calendar is not connected — the grant was "
                    "revoked or expired. Open the app and tap Connect "
                    "Google Calendar again.") from exc
            raise CalendarError(
                f"calendar sign-in failed: {exc}") from exc
        except GoogleAuthError as exc:
            raise CalendarError(
                f"calendar sign-in failed: {exc}") from exc
    return wrapper


def _resolve_calendar_id(calendar_id: str) -> str:
    """A role config's literal "primary" means the configured test
    calendar — never the signed-in account's own primary unless that
    IS the configured calendar. Fail closed when no test calendar
    is configured."""
    configured = os.environ.get("CALENDAR_ID", "").strip()
    if calendar_id and calendar_id != "primary":
        return calendar_id
    if configured:
        return configured
    raise CalendarError(
        "CALENDAR_ID is not set — the test calendar has not been "
        "configured yet. Calendar bookings fall back to message-taking."
    )


@_auth_errors
def check_free(calendar_id: str, time_min: str, time_max: str) -> bool:
    """True if nothing occupies [time_min, time_max). Freebusy first,
    list-events fallback — same shape as the Vercel version."""
    service = _get_service()
    cal = _resolve_calendar_id(calendar_id)
    try:
        fb = service.freebusy().query(body={
            "timeMin": time_min,
            "timeMax": time_max,
            "items": [{"id": cal}],
        }).execute()
        busy = (fb.get("calendars", {}).get(cal, {}).get("busy") or [])
        return not busy
    except HttpError:
        # FreeBusy refused (e.g. the calendar id is not a freebusy
        # target) — fall back to listing events in the window.
        try:
            events = service.events().list(
                calendarId=cal, timeMin=time_min, timeMax=time_max,
                singleEvents=True,
            ).execute()
        except HttpError as exc:
            raise CalendarError(
                f"calendar free-check failed (HTTP {exc.resp.status})"
            ) from exc
        return not (events.get("items") or [])


@_auth_errors
def create_event(calendar_id: str, summary: str, start: str, end: str,
                 description: str = "", location: str = "") -> dict:
    """Create a calendar event. Returns the event dict; raises
    CalendarError if the API refuses or returns no id."""
    service = _get_service()
    cal = _resolve_calendar_id(calendar_id)
    body = {
        "summary": summary,
        "start": {"dateTime": start},
        "end": {"dateTime": end},
        "description": description,
    }
    if location:
        body["location"] = location
    try:
        data = service.events().insert(calendarId=cal, body=body).execute()
    except HttpError as exc:
        raise CalendarError(
            f"calendar create failed (HTTP {exc.resp.status}): "
            f"{exc.content.decode('utf-8', 'replace')[:200]}"
        ) from exc
    if not data.get("id"):
        raise CalendarError("calendar create returned no event id")
    return data


@_auth_errors
def get_event(calendar_id: str, event_id: str) -> dict | None:
    """Fetch an event by id. None if it does not exist (404) or was
    cancelled; CalendarError for any other failure."""
    service = _get_service()
    cal = _resolve_calendar_id(calendar_id)
    try:
        event = service.events().get(calendarId=cal, eventId=event_id).execute()
    except HttpError as exc:
        if exc.resp.status == 404:
            return None
        raise CalendarError(
            f"calendar get failed (HTTP {exc.resp.status})"
        ) from exc
    if event.get("status") == "cancelled":
        return None
    return event


@_auth_errors
def delete_event(calendar_id: str, event_id: str) -> bool:
    """Delete an event, then verify it is really gone. Returns True if
    no such event remains (a 404 on delete is already-gone, not an
    error); False only when the event still reads back afterwards."""
    service = _get_service()
    cal = _resolve_calendar_id(calendar_id)
    try:
        service.events().delete(calendarId=cal, eventId=event_id).execute()
    except HttpError as exc:
        if exc.resp.status != 404:
            raise CalendarError(
                f"calendar delete failed (HTTP {exc.resp.status})"
            ) from exc
    return get_event(calendar_id, event_id) is None


@_auth_errors
def book_appointment(calendar_id: str, summary: str, start: str, end: str,
                     description: str = "", location: str = "",
                     buffer_minutes: int = 0) -> dict:
    """FreeBusy -> create -> read back. The read-back is the proof the
    booking exists; a create without a read-back is not a booking.
    Same signature and semantics as the Vercel version.

    Buffer (DESIGN.md Appendix H): availability is checked over the slot
    EXPANDED by the buffer on both sides, so back-to-back bookings can
    never collide. The event itself is written with the caller's
    start/end only. ``start``/``end`` are ISO 8601 with offset.
    """
    start_dt = datetime.fromisoformat(start)
    end_dt = datetime.fromisoformat(end)
    check_start = (start_dt - timedelta(minutes=buffer_minutes)).isoformat()
    check_end = (end_dt + timedelta(minutes=buffer_minutes)).isoformat()
    if not check_free(calendar_id, check_start, check_end):
        raise CalendarError("requested slot is busy (including buffer)")
    created = create_event(calendar_id, summary, start, end, description, location)
    verified = get_event(calendar_id, created["id"])
    if verified is None:
        raise CalendarError(
            f"event {created['id']} was created but could not be read back"
        )
    return verified
