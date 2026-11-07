"""Callback queue — the "call back" door of the bundle (DESIGN.md §10).

A missed call that reaches the AI ends with a message taken. When the
caller asked to be called back, that message becomes a callback request
in this queue — a file-backed JSONL on the VM, same pattern as
``approval_queue.py`` (state is file-backed, never in-memory: a refresh
wipes nothing).

Rules (non-negotiable, enforced here and mirrored in n8n workflow W5):
  * A callback is placed ONLY inside the business's stated hours
    (``business_hours.within_hours``). Outside hours it is DEFERRED to
    the next opening time — never attempted, never dropped.
  * A callback is placed ONLY inside the 8 a.m.–9 p.m. calling window
    in the RECIPIENT's local time (``within_calling_window``). Business
    hours say when the business is open; the window says when the person
    may be called — for an overseas client they are different clocks
    (DISCLAIMER.md rule 4). Outside the window it is DEFERRED.
  * A number on the client's do-not-call list is NEVER dialled, whatever
    else the queue says (``dnc_check`` / ``do_not_call_list`` table,
    migration 003; DISCLAIMER.md rule 3). The DNC check runs before any
    dial — before re-booking, before hours.
  * A caller who re-booked after asking for a callback is NEVER called
    back (the booking already solved it). The sweep checks with a
    caller-supplied ``rebooked_check`` — in production that reads
    Supabase ``activity_log`` for a later ``booked`` row for the same
    caller phone; in the test harness it reads the stub capture file.
  * A caller who asked NOT to be called is NEVER called back. Such a
    request is recorded as ``suppressed`` at intake (auditable) and the
    sweep never dials it.
  * Every call-back opens with the AI disclosure (DISCLAIMER.md Part A):
    the voice agent speaks the role config's ``persona.responses.
    callbackGreeting`` first — AI identity, business name, reason for
    the call, and the opt-out line — before anything else. See
    ``build_callback_greeting``.
  * Every state change posts a row to ``activity_log`` (via the
    caller-supplied ``post_log`` callable — in production that is the W1
    endpoint; in the test harness it is the local stub). The outbound
    dial itself is the voice agent's job (Twilio outbound through the
    same Pipecat stack); the ``dial`` callable passed to ``sweep`` is
    that step. In the sandbox there is no Twilio account, so the harness
    passes a recording stub dial and labels it SIMULATED — the queue,
    hours, suppression and logging logic is LIVE.

Outcomes written to activity_log (migration 002):
  callback_queued | callback_deferred | callback_attempted |
  callback_completed | callback_failed | callback_suppressed.
Suppression reasons: caller_rebooked | caller_asked_not_to_be_called |
on_do_not_call_list. Defer reasons: outside_business_hours |
outside_calling_window | business_hours_unparseable.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

from business_hours import next_open_time, within_hours

# The canonical call-opening disclosure (DISCLAIMER.md Part A). Every
# call-back opens with this — AI identity, business name, reason, and
# the opt-out line — before anything else is said.
CALLBACK_GREETING_DEFAULT = (
    "Hi, this is the AI receptionist for {business}, returning your call "
    "about {reason}. Say stop at any time and we will never call you again."
)
OPT_OUT_CLOSE = "Understood — I won't call you again. Goodbye."

# The calling window (DISCLAIMER.md rule 4): 8 a.m.–9 p.m. in the
# RECIPIENT's local time. 8:00 inclusive, 21:00 exclusive.
CALLING_WINDOW_OPEN = time(8, 0)
CALLING_WINDOW_CLOSE = time(21, 0)

OUTCOMES = {
    "callback_queued", "callback_deferred", "callback_attempted",
    "callback_completed", "callback_failed", "callback_suppressed",
}


def _read_all(queue_file: str | Path) -> list[dict]:
    # VERCEL: "kv://<key>" reads the whole list from the KV store
    # (Vercel has no shared disk); plain paths keep file behaviour.
    if str(queue_file).startswith("kv://"):
        from kvstore import get_store
        return get_store().list_get(str(queue_file)[5:])
    path = Path(queue_file)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _write_all(queue_file: str | Path, items: list[dict]) -> None:
    # VERCEL: "kv://<key>" writes the whole list to the KV store.
    if str(queue_file).startswith("kv://"):
        from kvstore import get_store
        get_store().list_set(str(queue_file)[5:], list(items))
        return
    path = Path(queue_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(i) + "\n" for i in items), encoding="utf-8")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def request_callback(queue_file: str | Path, *, client_id: str, role_id: str,
                     call_id: str, caller_name: str, caller_phone: str,
                     requested_at: str | None = None, best_time: str = "",
                     reason: str = "", do_not_call: bool = False) -> dict:
    """Record a callback request from a message taken on a missed call.

    No dial happens here — that is the sweep's job. A caller who asked
    not to be called is recorded as ``suppressed`` immediately (auditable,
    never dialled); everyone else starts ``queued``. ``reason`` is what
    the caller called about, taken from the original call — it is what
    the call-back greeting says back to them (DISCLAIMER.md Part A), and
    it is never invented: an empty reason stays empty.
    """
    items = _read_all(queue_file)
    item = {
        "id": f"cb-{len(items) + 1:04d}",
        "client_id": client_id,
        "role_id": role_id,
        "call_id": call_id,
        "caller_name": caller_name,
        "caller_phone": caller_phone,
        "best_time": best_time,
        "reason": reason,
        "requested_at": requested_at or _now_iso(),
        "status": "suppressed" if do_not_call else "queued",
        "suppression_reason": (
            "caller_asked_not_to_be_called" if do_not_call else None
        ),
        "attempts": 0,
        "last_attempt_at": None,
        "next_attempt_at": None,
        "dial_result": None,
    }
    items.append(item)
    _write_all(queue_file, items)
    return item


def pending_callbacks(queue_file: str | Path) -> list[dict]:
    """Queued + deferred items — the work the sweep has to do."""
    return [i for i in _read_all(queue_file)
            if i["status"] in ("queued", "deferred")]


def all_callbacks(queue_file: str | Path) -> list[dict]:
    return _read_all(queue_file)


def callback_payload(item: dict, outcome: str, flags: list[str] | None = None,
                     duration_seconds: int = 0) -> dict:
    """The activity_log row for a callback state change. Column names and
    types follow migration 002 (supabase/migrations/002-...sql)."""
    if outcome not in OUTCOMES:
        raise ValueError(f"unknown callback outcome: {outcome!r}")
    return {
        "employee_id": "aria",
        "action": f"call_{outcome}",
        "client_id": item["client_id"],
        "role_id": item["role_id"],
        "call_id": item["call_id"],
        "outcome": outcome,
        "booking_event_id": None,
        "duration_seconds": duration_seconds,
        "flags": flags or [],
        "escalated": False,
        "error_caught": outcome == "callback_failed",
        "detail": {
            "caller_name": item["caller_name"],
            "caller_phone": item["caller_phone"],
            "detected_language": None,
            "intent": "callback",
            "booking_or_message": (
                f"callback {outcome} for {item['caller_name']} "
                f"({item['caller_phone']})"
            ),
            "transcript": [],
            "callback_id": item["id"],
            "best_time": item.get("best_time") or "",
            "reason_for_call": item.get("reason") or "",
            "attempts": item.get("attempts", 0),
        },
    }


def _normalize_phone(phone: str) -> str:
    """Digits plus a leading + — so '+1 555-010-1001' and '+15550101001'
    are the same number on the do-not-call list."""
    return re.sub(r"[^0-9+]", "", phone or "")


def add_to_dnc_list(dnc_file: str | Path, *, client_id: str, caller_phone: str,
                    source: str, source_call_id: str | None = None,
                    note: str = "") -> dict:
    """Append a number to a client's do-not-call list (file-backed JSONL,
    same pattern as the callback queue; in production the list lives in
    Supabase — see migration 003, table ``do_not_call_list``).

    ``source`` is how the number got here: 'opt_out_on_call' (the caller
    said stop on a call), 'opt_out_email', 'owner_added', or
    'callback_request_do_not_call'. A number already on the list is not
    duplicated — the existing record is returned. Returns the record.
    """
    records = _read_all(dnc_file)
    normalized = _normalize_phone(caller_phone)
    for record in records:
        if (record.get("client_id") == client_id
                and record.get("caller_phone_normalized") == normalized):
            return record
    record = {
        "client_id": client_id,
        "caller_phone": caller_phone,
        "caller_phone_normalized": normalized,
        "source": source,
        "source_call_id": source_call_id,
        "added_at": _now_iso(),
        "note": note,
    }
    records.append(record)
    _write_all(dnc_file, records)
    return record


def dnc_numbers(dnc_file: str | Path, client_id: str) -> set[str]:
    """Normalized phones on one client's do-not-call list (empty set when
    the file does not exist yet)."""
    return {r.get("caller_phone_normalized", "")
            for r in _read_all(dnc_file)
            if r.get("client_id") == client_id}


def is_on_dnc(dnc_file: str | Path, client_id: str, phone: str) -> bool:
    """True when the number is on one client's do-not-call list.

    FIX #8 (bench battery, Nov 7): ``dnc_numbers`` returns NORMALIZED
    phones (+15550100002) but callers compared the RAW phone
    ("+1 555 010 0002") against that set, so the sweep dialled a number
    that had opted out. Every DNC comparison must go through here, which
    normalises the query the same way the list was written.
    """
    return _normalize_phone(phone) in dnc_numbers(dnc_file, client_id)


def build_callback_greeting(config: dict, item: dict) -> str:
    """The first thing spoken on an outbound call-back (DISCLAIMER.md Part A).

    Renders the role config's ``persona.responses.callbackGreeting`` with
    {business} (the config's business name) and {reason} (what the caller
    called about, from the queue item — never invented; when no reason was
    recorded the line reads "returning your call." with no reason claimed).
    If the config has no callback greeting, or the rendered line does not
    carry the AI disclosure, the canonical default is used instead — a
    call-back never goes out without the disclosure first. The voice agent
    calls this when it places the outbound call, before anything else.
    """
    business = (config.get("business") or {}).get("name", "the business")
    reason = (item.get("reason") or "").strip()
    template = ((config.get("persona") or {}).get("responses") or {}).get("callbackGreeting")
    for candidate in (template, CALLBACK_GREETING_DEFAULT):
        if not candidate:
            continue
        try:
            if not reason:
                rendered = candidate.replace(" about {reason}", "").format(business=business)
            else:
                rendered = candidate.format(business=business, reason=reason)
        except (KeyError, IndexError, ValueError):
            continue
        if rendered.startswith("Hi, this is the AI receptionist for"):
            return rendered
    # Unreachable in practice (the default always renders) — fail closed
    # with the disclosure line rather than speaking without it.
    return (f"Hi, this is the AI receptionist for {business}, returning your call. "
            "Say stop at any time and we will never call you again.")


def within_calling_window(moment: datetime) -> bool:
    """True when `moment` (recipient local time) is inside the 8 a.m.–9 p.m.
    calling window (DISCLAIMER.md rule 4). 8:00 inclusive, 21:00 exclusive.
    Business hours say when the business is open; this window says when
    the person may be called."""
    return CALLING_WINDOW_OPEN <= moment.time() < CALLING_WINDOW_CLOSE


def next_calling_window(moment: datetime) -> datetime:
    """The next 8:00 a.m. at or after `moment` (recipient local time) —
    when a call-back deferred for the window becomes due again."""
    candidate = moment.replace(hour=CALLING_WINDOW_OPEN.hour, minute=0,
                               second=0, microsecond=0)
    if candidate < moment:
        candidate = candidate + timedelta(days=1)
    return candidate


def sweep(queue_file: str | Path, *, hours_text: str, now_local: datetime,
          dial: Callable[[dict], dict],
          rebooked_check: Callable[[str, str], bool],
          post_log: Callable[[dict], None],
          dnc_check: Callable[[str], bool] | None = None,
          recipient_tz: ZoneInfo | None = None) -> list[dict]:
    """Run one callback sweep. `now_local` is an aware datetime in the
    business's local timezone. `dial(item)` places the outbound call and
    returns {"reached": bool, ...}; it raises on dial failure.
    `rebooked_check(caller_phone, requested_at_iso)` reports whether the
    caller booked after asking for a callback. `post_log(payload)` records
    each state change in activity_log (or its stand-in).
    `dnc_check(phone)` reports whether the number is on the client's
    do-not-call list — the production caller MUST pass one (W5 reads the
    `do_not_call_list` table on every sweep); a sweep run without one
    consults no list, and the harness's DNC checks always pass one.
    `recipient_tz` is the callers' timezone; when None it defaults to the
    business's timezone (the documented default — most callers are local
    to the business). The 8 a.m.–9 p.m. window is checked in the
    recipient's local time, never the business's.

    Returns one result dict per pending item processed. Items already
    attempted/completed/failed/suppressed are never touched again.
    """
    items = _read_all(queue_file)
    results: list[dict] = []
    changed = False
    for item in items:
        if item["status"] not in ("queued", "deferred"):
            continue
        # 0. Do-not-call: a number on the client's DNC list is never
        #    dialled, whatever else the queue says. Checked FIRST —
        #    before re-booking, before hours (DISCLAIMER.md rule 3).
        if dnc_check is not None and dnc_check(item["caller_phone"]):
            item["status"] = "suppressed"
            item["suppression_reason"] = "on_do_not_call_list"
            post_log(callback_payload(
                item, "callback_suppressed",
                flags=["callback_suppressed:on_do_not_call_list"]))
            results.append({"id": item["id"], "action": "suppressed",
                            "reason": "on_do_not_call_list"})
            changed = True
            continue
        # 1. Suppression: the caller solved it themselves by re-booking.
        if rebooked_check(item["caller_phone"], item["requested_at"]):
            item["status"] = "suppressed"
            item["suppression_reason"] = "caller_rebooked"
            post_log(callback_payload(
                item, "callback_suppressed",
                flags=["callback_suppressed:caller_rebooked"]))
            results.append({"id": item["id"], "action": "suppressed",
                            "reason": "caller_rebooked"})
            changed = True
            continue
        # 2. Hours: outside opening hours the callback waits — never dialled.
        inside = within_hours(hours_text, now_local)
        if inside is None:
            item["status"] = "deferred"
            item["next_attempt_at"] = None
            post_log(callback_payload(
                item, "callback_deferred",
                flags=["callback_deferred:business_hours_unparseable"]))
            results.append({"id": item["id"], "action": "deferred",
                            "reason": "business_hours_unparseable"})
            changed = True
            continue
        if not inside:
            nxt = next_open_time(hours_text, now_local)
            item["status"] = "deferred"
            item["next_attempt_at"] = nxt.isoformat() if nxt else None
            post_log(callback_payload(
                item, "callback_deferred",
                flags=["callback_deferred:outside_business_hours"]))
            results.append({"id": item["id"], "action": "deferred",
                            "reason": "outside_business_hours",
                            "next_attempt_at": item["next_attempt_at"]})
            changed = True
            continue
        # 2b. Calling window: 8 a.m.–9 p.m. in the RECIPIENT's local time
        #     (DISCLAIMER.md rule 4) — a separate guard from business
        #     hours, because for an overseas client they are different
        #     clocks. Outside the window the call-back waits, never dials.
        recipient_now = (now_local.astimezone(recipient_tz)
                         if recipient_tz is not None else now_local)
        if not within_calling_window(recipient_now):
            nxt = next_calling_window(recipient_now)
            item["status"] = "deferred"
            item["next_attempt_at"] = nxt.isoformat()
            post_log(callback_payload(
                item, "callback_deferred",
                flags=["callback_deferred:outside_calling_window"]))
            results.append({"id": item["id"], "action": "deferred",
                            "reason": "outside_calling_window",
                            "next_attempt_at": item["next_attempt_at"]})
            changed = True
            continue
        # 3. Inside hours, inside the window, not suppressed: place the call.
        #    The voice agent opens with the AI disclosure greeting
        #    (build_callback_greeting) before anything else is said.
        item["attempts"] = item.get("attempts", 0) + 1
        item["last_attempt_at"] = now_local.isoformat()
        try:
            dial_result = dial(item)
        except Exception as exc:  # noqa: BLE001 - dial failure is a state, not a crash
            item["status"] = "failed"
            item["dial_result"] = {"error": f"{type(exc).__name__}: {exc}"}
            post_log(callback_payload(
                item, "callback_failed",
                flags=["callback_failed:dial_error"]))
            results.append({"id": item["id"], "action": "failed",
                            "reason": "dial_error"})
            changed = True
            continue
        item["dial_result"] = dial_result
        if dial_result.get("reached"):
            item["status"] = "completed"
            post_log(callback_payload(
                item, "callback_completed",
                flags=["callback_completed:caller_reached"]))
            results.append({"id": item["id"], "action": "completed",
                            "reason": "caller_reached"})
        else:
            item["status"] = "attempted"
            post_log(callback_payload(
                item, "callback_attempted",
                flags=["callback_attempted:not_reached"]))
            results.append({"id": item["id"], "action": "attempted",
                            "reason": "not_reached"})
        changed = True
    if changed:
        _write_all(queue_file, items)
    return results
