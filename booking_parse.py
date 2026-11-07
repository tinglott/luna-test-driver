#!/usr/bin/env python3
"""Booking-intent parser for the bench test driver.

Turns a caller's plain words ("I'd like a check-up on Monday at 10am")
into a concrete (service, start, end) slot — or a reason it cannot be
booked. The driver keeps the parsing HERE, outside the agent-service
code, so the call handler itself stays untouched: the handler only ever
books what the caller has confirmed (confirmation-by-repetition).

Rules, all from the role config + DESIGN.md:
* service names match hyphen-insensitively ("checkup" == "check-up") —
  Groq Whisper drops hyphens in transcripts, so the match must not need
  them (this is what FIX #2 in gates.py also repairs on the lookup side).
* day words: today / tomorrow / weekday names. A weekday named for today
  books today only when the time is still ahead; otherwise next week.
* time: "10am", "10:30am", "2pm". Slot length 30 minutes.
* NEVER book outside the business's opening hours (config business.hours,
  e.g. "Mon–Fri 9am–5pm"). Out-of-hours requests come back as a reason,
  and the agent takes a message instead of confirming a booking.
* All times are America/New_York (the test client's timezone).
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/New_York")
SLOT_MINUTES = 30

WEEKDAYS = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}

_DAY_NAMES = ("monday", "tuesday", "wednesday", "thursday", "friday",
              "saturday", "sunday")
_TIME_RE = re.compile(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)", re.IGNORECASE)
_DAY_ABBR = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
_HOURS_RE = re.compile(
    r"(monday|tuesday|wednesday|thursday|friday|saturday|sunday|mon|tue|wed|thu|fri|sat|sun)"
    r"\s*(?:to|[–—-])\s*"
    r"(monday|tuesday|wednesday|thursday|friday|saturday|sunday|mon|tue|wed|thu|fri|sat|sun)"
    r"[^0-9]*?"
    r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)\s*(?:to|[–—-])\s*"
    r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)",
    re.IGNORECASE,
)


def _norm(text: str) -> str:
    """Lowercase, hyphens/spaces collapsed — 'check-up' == 'checkup'."""
    return re.sub(r"[\s\-]+", "", text.lower())


def parse_business_hours(hours_text: str) -> tuple[set[int], int, int]:
    """Parse 'Monday to Friday, 9am to 5pm' (or 'Mon–Fri 9am–5pm').

    Returns (open weekdays, open minutes, close minutes).
    Falls back to Mon–Fri 9:00–17:00 when the text does not parse.
    """
    match = _HOURS_RE.search(hours_text or "")
    if not match:
        return set(range(0, 5)), 9 * 60, 17 * 60

    def _day_index(token: str) -> int:
        token = token.lower()
        if token in _DAY_ABBR:
            return _DAY_ABBR[token]
        return _DAY_NAMES.index(token)

    start_i, end_i = _day_index(match.group(1)), _day_index(match.group(2))
    days = set()
    day = start_i
    while True:
        days.add(day)
        if day == end_i:
            break
        day = (day + 1) % 7

    def _mins(hour: str, minute: str | None, ampm: str) -> int:
        hour_i = int(hour) % 12
        if ampm.lower() == "pm":
            hour_i += 12
        return hour_i * 60 + int(minute or 0)

    open_m = _mins(match.group(3), match.group(4), match.group(5))
    close_m = _mins(match.group(6), match.group(7), match.group(8))
    return days, open_m, close_m


def find_service(text: str, services: list) -> str | None:
    """Match the caller's words to a service name, hyphen-insensitively.

    Config services are plain strings ("Check-up", "Hygiene appointment");
    dicts with a "name" key are accepted too.
    """
    norm = _norm(text)
    names = []
    for service in services or []:
        if isinstance(service, dict):
            names.append(service.get("name") or "")
        else:
            names.append(str(service))
    for name in names:
        if name and _norm(name) in norm:
            return name
    if "checkup" in norm:
        for name in names:
            if _norm(name) == "checkup":
                return name
    return None


def parse_booking_intent(text: str, config: dict,
                         now: datetime | None = None) -> tuple[dict | None, str | None]:
    """Parse a booking request. Returns (slot, None) or (None, reason).

    slot = {"service": str, "start": datetime, "end": datetime,
            "start_iso": str, "end_iso": str} (America/New_York aware).
    """
    now = now or datetime.now(TZ)
    low = text.lower()
    services = (config.get("business") or {}).get("services") or []
    service = find_service(text, services)
    if not service:
        return None, "no service named"

    time_match = _TIME_RE.search(text)
    if not time_match:
        return None, "no time given"
    hour = int(time_match.group(1)) % 12
    if time_match.group(3).lower() == "pm":
        hour += 12
    minute = int(time_match.group(2) or 0)

    day = None
    if "tomorrow" in low:
        day = (now + timedelta(days=1)).date()
    elif "today" in low:
        day = now.date()
    else:
        for name, weekday in WEEKDAYS.items():
            if re.search(rf"\b{name}\b", low):
                delta = (weekday - now.weekday()) % 7
                candidate = now.date() + timedelta(days=delta)
                # Same weekday, time already past -> next week, not today.
                if delta == 0 and (hour * 60 + minute) <= (now.hour * 60 + now.minute):
                    candidate += timedelta(days=7)
                day = candidate
                break
    if day is None:
        return None, "no day given"

    start = datetime(day.year, day.month, day.day, hour, minute, tzinfo=TZ)
    end = start + timedelta(minutes=SLOT_MINUTES)
    hours_text = (config.get("business") or {}).get("hours") or ""
    open_days, open_m, close_m = parse_business_hours(hours_text)
    if start.weekday() not in open_days:
        return None, f"closed that day (open {hours_text})"
    if start.hour * 60 + start.minute < open_m or end.hour * 60 + end.minute > close_m:
        return None, f"outside opening hours ({hours_text})"
    return {
        "service": service,
        "start": start,
        "end": end,
        "start_iso": start.isoformat(),
        "end_iso": end.isoformat(),
    }, None


CONFIRM_RE = re.compile(
    r"^\s*(yes|yeah|yep|yup|sure|ok|okay|that's right|thats right|correct|"
    r"confirm|book it|do it|go ahead|please)\b", re.IGNORECASE
)


def is_confirmation(text: str) -> bool:
    """True when the caller is confirming the pending booking."""
    return bool(CONFIRM_RE.search(text.strip()))
