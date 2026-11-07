"""Sales lead qualification + follow-up tasks — the "sales support" door
of the bundle (DESIGN.md §10).

Honest scope, written into the role config and the offer: this door
QUALIFIES a lead, BOOKS the sales call, and FOLLOWS UP. It never closes
a sale, never takes a payment, never promises results or sales numbers.
Closing is the owner's job; the agent's job is to make sure the owner
never misses a warm lead.

Qualification is DETERMINISTIC (no LLM): a call qualifies when all three
hold — the caller showed buying intent in their own words, gave a name,
and gave a phone number — and no gate escalated the call. A qualified
lead gets a follow-up task due the next business day at the time in the
role config's ``followUp`` block (default 10:00 local). The flag
(``sales_qualified``) and the due date (``follow_up_due``) ride the
call-end payload into ``activity_log`` (migration 002); the working list
of follow-up tasks lives in a file-backed JSONL on the VM, same pattern
as ``approval_queue.py``.

The sales door only exists on sales roles (``roleId`` starting with
``sales-``). A front-desk price question ("how much is a check-up?") is
not a sales lead — it is answered from verified knowledge by the
front-desk role and never qualifies.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

# Buying intent, in the caller's own words. Price questions alone do NOT
# qualify — "how much is a check-up?" is a front-desk lookup, not a lead.
BUYING_INTENT = [
    r"\bi want to buy\b", r"\bbuying\b", r"\bpurchase\b",
    r"\bsign (me )?up\b", r"\bget started\b",
    r"\binterested in (buying|getting|joining|signing up)\b",
    r"\bcan i book a sales call\b", r"\btalk to someone about buying\b",
    r"\bi'?d like to (buy|purchase|sign up|join)\b",
    r"\bready to (buy|purchase|sign up|join)\b",
]

ESCALATION_FLAG_PREFIXES = ("gate_escalate", "gate_refuse", "crisis", "spam_close")


def _has_buying_intent(texts: list[str]) -> bool:
    joined = " ".join(texts).lower()
    return any(re.search(p, joined) for p in BUYING_INTENT)


def qualify_lead(role_id: str, caller_name: str, caller_phone: str,
                 caller_texts: list[str], flags: list[str]) -> dict:
    """Decide whether a call is a sales-qualified lead. Deterministic.

    Returns {"sales_qualified": bool, "reason": str, "missing": [...]}.
    A call qualifies only on a sales role, with buying intent in the
    caller's words, a name, a phone number, and no escalation.
    """
    missing: list[str] = []
    if not (role_id or "").startswith("sales-"):
        return {"sales_qualified": False, "reason": "not a sales role",
                "missing": ["sales_role"]}
    if any(f.startswith(ESCALATION_FLAG_PREFIXES) for f in (flags or [])):
        return {"sales_qualified": False, "reason": "call escalated",
                "missing": ["no_escalation"]}
    if not _has_buying_intent(caller_texts or []):
        missing.append("buying_intent")
    if not (caller_name or "").strip() or caller_name.strip().lower() in ("not given",):
        missing.append("caller_name")
    if not (caller_phone or "").strip() or caller_phone.strip().lower() in ("not given",):
        missing.append("caller_phone")
    if missing:
        return {"sales_qualified": False,
                "reason": "missing: " + ", ".join(missing), "missing": missing}
    return {"sales_qualified": True, "reason": "buying intent + name + phone",
            "missing": []}


def follow_up_due_iso(now: datetime, due_time: str = "10:00",
                      tz_name: str = "America/New_York") -> str:
    """Next business day (Mon–Fri) at `due_time` in `tz_name`, as ISO 8601
    with offset. `now` may be naive (treated as UTC) or aware."""
    tz = ZoneInfo(tz_name)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    local = now.astimezone(tz)
    hour, minute = (int(p) for p in due_time.split(":"))
    due = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if due <= local:
        due = due + timedelta(days=1)
    while due.weekday() >= 5:  # Saturday / Sunday -> Monday
        due = due + timedelta(days=1)
    return due.isoformat()


# -- follow-up task store (file-backed JSONL, same pattern as approval_queue)

def _read_all(store_file: str | Path) -> list[dict]:
    # VERCEL: "kv://<key>" reads the whole list from the KV store
    # (Vercel has no shared disk); plain paths keep file behaviour.
    if str(store_file).startswith("kv://"):
        from kvstore import get_store
        return get_store().list_get(str(store_file)[5:])
    path = Path(store_file)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _write_all(store_file: str | Path, items: list[dict]) -> None:
    # VERCEL: "kv://<key>" writes the whole list to the KV store.
    if str(store_file).startswith("kv://"):
        from kvstore import get_store
        get_store().list_set(str(store_file)[5:], list(items))
        return
    path = Path(store_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(i) + "\n" for i in items), encoding="utf-8")


def create_follow_up(store_file: str | Path, *, client_id: str, role_id: str,
                     call_id: str, caller_name: str, caller_phone: str,
                     reason: str, due: str) -> dict:
    """Create a follow-up task for a qualified lead. The owner (or Ting)
    works the task; completing it records who did it and when."""
    items = _read_all(store_file)
    item = {
        "id": f"fu-{len(items) + 1:04d}",
        "client_id": client_id,
        "role_id": role_id,
        "call_id": call_id,
        "caller_name": caller_name,
        "caller_phone": caller_phone,
        "reason": reason,
        "due": due,
        "status": "pending",  # pending | done
        "completed_by": None,
        "completed_at": None,
    }
    items.append(item)
    _write_all(store_file, items)
    return item


def pending_follow_ups(store_file: str | Path) -> list[dict]:
    return [i for i in _read_all(store_file) if i["status"] == "pending"]


def all_follow_ups(store_file: str | Path) -> list[dict]:
    return _read_all(store_file)


def complete_follow_up(store_file: str | Path, follow_up_id: str,
                       completed_by: str) -> dict:
    """Mark a follow-up done. Raises KeyError (unknown) / ValueError (done)."""
    items = _read_all(store_file)
    for item in items:
        if item["id"] == follow_up_id:
            if item["status"] != "pending":
                raise ValueError(f"follow-up {follow_up_id} is already {item['status']}")
            item["status"] = "done"
            item["completed_by"] = completed_by
            item["completed_at"] = datetime.now(timezone.utc).isoformat()
            _write_all(store_file, items)
            return item
    raise KeyError(f"follow-up not found: {follow_up_id}")
