"""Google Calendar booking — VERCEL version (service account, direct).

Same public API as the bench / Space ``calendar_booking.py``
(``check_free`` / ``create_event`` / ``get_event`` / ``delete_event`` /
``book_appointment`` + ``CalendarError``), so ``call_handler.py`` and
``approval_queue.py`` need no changes. The transport is different:

  * NO relay, NO hatch_gws_cli, NO bench subprocess. The Google
    Calendar API is called directly with a service-account key from
    the ``GOOGLE_SERVICE_ACCOUNT_JSON`` environment variable — the JSON
    key content Ting pastes in the Vercel dashboard himself (owner
    tap; never sent in chat, never printed, never written to a file).
  * The service account must be SHARED on the test calendar (owner
    tap in Google Calendar). The calendar id comes from ``CALENDAR_ID``
    (default: the role config's calendarId). A role config's literal
    "primary" is treated as "the configured test calendar" — a service
    account's own primary calendar is NOT the test calendar, and
    booking into it would be silently wrong, so a missing CALENDAR_ID
    fails closed instead of booking nowhere useful.
  * No fallback to listing events is silently trusted: freebusy is
    tried first; if it refuses, events are listed in the window (same
    shape as the bench fallback). Any other API failure raises
    CalendarError and the call falls back to message-taking — never
    a fake booking.

Local test hook (documented, bench-only): setting
``GOOGLE_API_DISCOVERY_URL`` points the client at a local discovery
document, so a fake Calendar API server can prove the request/response
code path without a real Google account. The live path (real
discovery + real service-account auth) is only proven by the
public-URL battery run after Ting's taps.

Requires: google-api-python-client, google-auth (see requirements.txt).
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

_SCOPES = ["https://www.googleapis.com/auth/calendar"]

_service = None


class CalendarError(Exception):
    """Calendar unavailable — caller must fall back, never invent a booking."""


def _get_service():
    """Build (once per process) the Calendar API client from the service
    account key in env. Raises CalendarError when the key is absent or
    unparseable — never a half-built client."""
    global _service
    if _service is not None:
        return _service
    raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    if not raw:
        raise CalendarError(
            "GOOGLE_SERVICE_ACCOUNT_JSON is not set — the service account "
            "key has not been pasted in the Vercel dashboard yet. "
            "Calendar bookings fall back to message-taking."
        )
    try:
        info = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CalendarError(
            "GOOGLE_SERVICE_ACCOUNT_JSON is not valid JSON — re-paste the "
            "service-account key file contents."
        ) from exc
    try:
        creds = service_account.Credentials.from_service_account_info(info, scopes=_SCOPES)
    except Exception as exc:  # noqa: BLE001 - key parse errors are config errors
        raise CalendarError(f"service-account key rejected: {exc}") from exc
    kwargs = {"cache_discovery": False}
    discovery_url = os.environ.get("GOOGLE_API_DISCOVERY_URL", "").strip()
    if discovery_url:  # local test hook only; see module docstring
        kwargs["discoveryServiceUrl"] = discovery_url
    try:
        # Proxy handling (bench quirk, harmless on Vercel): googleapiclient
        # talks through httplib2, whose default proxy_info is a
        # PER-REQUEST function reading the process proxy env vars — and
        # this httplib2 does not honour no_proxy, so even localhost
        # requests fail with "ProxiesUnavailableError" when a proxy is
        # set. Popping the vars does not help (the function reads them
        # at request time), and build() refuses both `http` and
        # `credentials`. The working shape: an AuthorizedHttp wrapping
        # the credentials, built on an httplib2.Http with proxy_info
        # explicitly None (direct, always — the Calendar API is reached
        # directly; on Vercel there is no proxy at all). That http is
        # passed as build()'s `http=` with no `credentials=` kwarg, and
        # token refreshes ride the same proxy-free http.
        import httplib2
        from google_auth_httplib2 import AuthorizedHttp

        auth_http = AuthorizedHttp(
            creds, http=httplib2.Http(proxy_info=None))
        _service = build("calendar", "v3", http=auth_http, **kwargs)
    except Exception as exc:  # noqa: BLE001 - discovery failures are outages
        raise CalendarError(f"Calendar API client build failed: {exc}") from exc
    return _service


def _resolve_calendar_id(calendar_id: str) -> str:
    """A role config's literal "primary" means the configured test
    calendar — never the service account's own primary. Fail closed
    when no test calendar is configured."""
    configured = os.environ.get("CALENDAR_ID", "").strip()
    if calendar_id and calendar_id != "primary":
        return calendar_id
    if configured:
        return configured
    raise CalendarError(
        "CALENDAR_ID is not set — the test calendar has not been shared "
        "with the service account yet. Calendar bookings fall back to "
        "message-taking."
    )


def check_free(calendar_id: str, time_min: str, time_max: str) -> bool:
    """True if nothing occupies [time_min, time_max). Freebusy first,
    list-events fallback — same shape as the bench version."""
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


def book_appointment(calendar_id: str, summary: str, start: str, end: str,
                     description: str = "", location: str = "",
                     buffer_minutes: int = 0) -> dict:
    """FreeBusy -> create -> read back. The read-back is the proof the
    booking exists; a create without a read-back is not a booking.
    Same signature and semantics as the bench version.

    Buffer (DESIGN.md Appendix H): availability is checked over the slot
    EXPANDED by the buffer on both sides, so back-to-back bookings can
    never collide. The event itself is written with the caller's
    start/end only. ``start``/``end`` are ISO 8601 with offset.
    """
    from datetime import datetime, timedelta

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
