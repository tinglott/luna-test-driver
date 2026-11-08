"""Owner-approval queue for new-client bookings — DESIGN.md Appendix H.

The strongest idea in the NovaCal paste, built properly here. The paste's
"Approve & Sync to Google" button was a toast with no Calendar API call
behind it. This queue is the opposite: approving an item performs a REAL
Google Calendar write (via calendar_booking.book_appointment), and denying
one records the denial. Nothing is a toast.

How it fits the design:
  * Role config switch: ``bookingRules.newClientBookingsRequireApproval``.
    When true, a first-time caller's booking is PROVISIONAL: the agent tells
    the caller the practice will confirm, the item waits here as ``pending``,
    and the owner approves or denies. Returning callers book directly.
  * Outcome: ``pending_approval`` (added to the activity_log outcome check
    in migration 001). It is NOT counted as a booking in the monthly report
    until the owner approves it — a pending booking is not a booking.
  * Surface: the W2 owner summary flags the pending item, and the monthly
    report / dashboard view reads the same rows. The queue file itself is
    JSONL on the VM (one file per client); Supabase remains the durable
    record via activity_log. PocketBase (Appendix G) could host this queue
    per client instead — same item shape, different sink.

State is file-backed, never in-memory: a refresh wipes nothing.
"""

from __future__ import annotations

import json
from pathlib import Path


def _read_all(queue_file: str | Path) -> list[dict]:
     # HOSTED: "kv://<key>" reads the whole list from the KV store
    # (the host has no shared disk); plain paths keep file behaviour.
    if str(queue_file).startswith("kv://"):
        from kvstore import get_store
        return get_store().list_get(str(queue_file)[5:])
    path = Path(queue_file)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _write_all(queue_file: str | Path, items: list[dict]) -> None:
     # HOSTED: "kv://<key>" writes the whole list to the KV store.
    if str(queue_file).startswith("kv://"):
        from kvstore import get_store
        get_store().list_set(str(queue_file)[5:], list(items))
        return
    path = Path(queue_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(i) + "\n" for i in items), encoding="utf-8")


def request_approval(queue_file: str | Path, *, client_id: str, call_id: str,
                     caller_name: str, caller_phone: str, service: str,
                     start: str, end: str, calendar_id: str = "primary") -> dict:
    """A first-time caller asked to book. Record it as pending. No calendar
    write happens here — that is the point of the queue."""
    items = _read_all(queue_file)
    item = {
        "id": f"appr-{len(items) + 1:04d}",
        "client_id": client_id,
        "call_id": call_id,
        "caller_name": caller_name,
        "caller_phone": caller_phone,
        "service": service,
        "start": start,
        "end": end,
        "calendar_id": calendar_id,
        "status": "pending",  # pending | approved | denied
        "booking_event_id": None,
        "decided_by": None,
    }
    items.append(item)
    _write_all(queue_file, items)
    return item


def pending_items(queue_file: str | Path) -> list[dict]:
    return [i for i in _read_all(queue_file) if i["status"] == "pending"]


def all_items(queue_file: str | Path) -> list[dict]:
    return _read_all(queue_file)


def approve(queue_file: str | Path, item_id: str, decided_by: str) -> dict:
    """Approve a pending item: REAL calendar write, then record the event id.

    Raises KeyError (unknown item), ValueError (not pending), CalendarError
    (calendar refused — the item stays pending so it can be retried).
    """
    from calendar_booking import book_appointment  # deferred: avoids a cycle

    items = _read_all(queue_file)
    for item in items:
        if item["id"] == item_id:
            if item["status"] != "pending":
                raise ValueError(f"item {item_id} is already {item['status']}")
            business = item["client_id"]
            summary = f"TEST — AI Receptionist prototype booking: {item['service']} ({business})"
            description = (
                f"Prototype test booking (owner-approved from the approval queue).\n"
                f"Caller: {item['caller_name']} ({item['caller_phone']})\n"
                f"Service: {item['service']}\nCall ID: {item['call_id']}"
            )
            event = book_appointment(
                item["calendar_id"], summary, item["start"], item["end"], description
            )
            item["status"] = "approved"
            item["booking_event_id"] = event["id"]
            item["decided_by"] = decided_by
            _write_all(queue_file, items)
            return item
    raise KeyError(f"approval item not found: {item_id}")


def deny(queue_file: str | Path, item_id: str, decided_by: str) -> dict:
    """Deny a pending item. No calendar write, ever. The denial is recorded."""
    items = _read_all(queue_file)
    for item in items:
        if item["id"] == item_id:
            if item["status"] != "pending":
                raise ValueError(f"item {item_id} is already {item['status']}")
            item["status"] = "denied"
            item["decided_by"] = decided_by
            _write_all(queue_file, items)
            return item
    raise KeyError(f"approval item not found: {item_id}")
