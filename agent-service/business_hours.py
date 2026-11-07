"""Business-hours parsing — callback rule "only inside the business's hours".

The role config stores hours as a plain sentence (for example
"Monday to Friday, 9am to 5pm. Closed weekends."). This module turns that
sentence into a check a callback sweep can run, with no guessing:

  * Supported: weekday ranges ("Monday to Friday", "Mon-Fri"),
    single days ("Saturday"), "Closed weekends" / "Closed Saturday and
    Sunday", and time ranges ("9am to 5pm", "9:30am-5:30pm", "09:00-17:00").
  * ``within_hours`` returns True / False, or None when the sentence
    cannot be parsed. None means DO NOT CALL — the callback is deferred
    and flagged for Ting, never attempted on an assumption.
  * ``next_open_time`` returns the next opening time at or after a given
    moment, so a deferred callback knows when it becomes due.

All datetimes passed in must already be in the business's local timezone
(the caller converts; the voice agent knows the client's timezone from
the role config's followUp.timezone or the install notes). Times are
compared as wall-clock times in that timezone.
"""

from __future__ import annotations

import re
from datetime import datetime, time, timedelta

DAY_NAMES = {
    "monday": 0, "mon": 0,
    "tuesday": 1, "tue": 1, "tues": 1,
    "wednesday": 2, "wed": 2,
    "thursday": 3, "thu": 3, "thur": 3, "thurs": 3,
    "friday": 4, "fri": 4,
    "saturday": 5, "sat": 5,
    "sunday": 6, "sun": 6,
}

_TIME_RE = re.compile(
    r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", re.IGNORECASE
)


def _parse_time(token: str) -> time | None:
    match = _TIME_RE.fullmatch(token.strip())
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    meridiem = (match.group(3) or "").lower()
    if meridiem == "pm" and hour < 12:
        hour += 12
    if meridiem == "am" and hour == 12:
        hour = 0
    if hour > 23 or minute > 59:
        return None
    return time(hour, minute)


def _parse_days(text: str) -> set[int] | None:
    """Parse the day part of an hours sentence. None = could not parse."""
    low = text.lower()
    days: set[int] = set()

    # "Closed weekends" / "closed saturday and sunday" removes Sat/Sun.
    closed_weekends = bool(re.search(r"closed\s+weekends?", low))
    closed_days = set()
    closed_match = re.search(r"closed\s+((?:saturday|sunday|sat|sun)(?:\s+and\s+(?:saturday|sunday|sat|sun))?)", low)
    if closed_match:
        for word in re.findall(r"[a-z]+", closed_match.group(1)):
            if word in DAY_NAMES:
                closed_days.add(DAY_NAMES[word])

    # Weekday range: "monday to friday" / "mon-fri" / "monday-friday".
    range_match = re.search(
        r"(monday|tuesday|wednesday|thursday|friday|saturday|sunday|mon|tue|wed|thu|fri|sat|sun)"
        r"\s*(?:to|-|–)\s*"
        r"(monday|tuesday|wednesday|thursday|friday|saturday|sunday|mon|tue|wed|thu|fri|sat|sun)",
        low,
    )
    if range_match:
        start, end = DAY_NAMES[range_match.group(1)], DAY_NAMES[range_match.group(2)]
        day = start
        while True:
            days.add(day)
            if day == end:
                break
            day = (day + 1) % 7
        if closed_weekends:
            days -= {5, 6}
        days -= closed_days
        return days

    # Single days named anywhere: "open saturday", "monday, wednesday".
    for word in re.findall(r"[a-z]+", low):
        if word in DAY_NAMES:
            days.add(DAY_NAMES[word])
    if days:
        if closed_weekends:
            days -= {5, 6}
        days -= closed_days
        return days

    # No day named at all: assume every day ("9am to 5pm" with no days).
    if closed_weekends:
        return {0, 1, 2, 3, 4}
    if closed_days:
        return set(range(7)) - closed_days
    return set(range(7))


def parse_business_hours(hours_text: str) -> tuple[set[int], time, time] | None:
    """Parse "Monday to Friday, 9am to 5pm. Closed weekends." into
    (open_days, open_time, close_time). None when it cannot be parsed."""
    if not hours_text or not isinstance(hours_text, str):
        return None
    low = hours_text.lower()
    time_match = re.search(
        r"(\d{1,2}(?::\d{2})?\s*(?:am|pm)?)\s*(?:to|-|–)\s*(\d{1,2}(?::\d{2})?\s*(?:am|pm)?)",
        low,
    )
    if not time_match:
        return None
    open_time = _parse_time(time_match.group(1))
    close_time = _parse_time(time_match.group(2))
    if open_time is None or close_time is None:
        return None
    days = _parse_days(hours_text)
    if days is None or not days:
        return None
    return days, open_time, close_time


def within_hours(hours_text: str, moment: datetime) -> bool | None:
    """True when `moment` (business local time) is inside opening hours.
    False when outside. None when the hours sentence cannot be parsed —
    the caller must treat None as "do not call, defer and flag"."""
    parsed = parse_business_hours(hours_text)
    if parsed is None:
        return None
    days, open_time, close_time = parsed
    if moment.weekday() not in days:
        return False
    now = moment.time()
    if open_time <= close_time:
        return open_time <= now < close_time
    return now >= open_time or now < close_time


def next_open_time(hours_text: str, moment: datetime) -> datetime | None:
    """The next opening time at or after `moment` (business local time).
    None when the hours sentence cannot be parsed."""
    parsed = parse_business_hours(hours_text)
    if parsed is None:
        return None
    days, open_time, close_time = parsed
    candidate = moment
    for _ in range(8):  # a week plus one day is always enough
        if candidate.weekday() in days and candidate.time() < open_time:
            return candidate.replace(hour=open_time.hour, minute=open_time.minute,
                                     second=0, microsecond=0)
        if candidate.weekday() in days and open_time <= candidate.time() < close_time:
            return candidate
        candidate = (candidate + timedelta(days=1)).replace(
            hour=open_time.hour, minute=open_time.minute, second=0, microsecond=0)
    return None
