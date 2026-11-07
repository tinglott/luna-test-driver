"""Live walk-in queue — DESIGN.md Appendix H.

A second queue surface for businesses that take walk-ins. A walk-in joins
the queue; their estimated wait is their position in the queue times the
slot length (15 minutes by default — the same 15-minute unit as the
booking buffer). No invented numbers: the estimate is arithmetic on the
queue itself, and it is quoted as an estimate, never a promise.

State is file-backed JSONL (one file per client), never in-memory arrays:
a refresh wipes nothing. The queue is a view over data the business owns;
in production it rides in Supabase (or the per-client PocketBase file,
Appendix G) with this same item shape.
"""

from __future__ import annotations

import json
from pathlib import Path

DEFAULT_SLOT_MINUTES = 15


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


def add_walkin(queue_file: str | Path, name: str, service: str,
               slot_minutes: int = DEFAULT_SLOT_MINUTES) -> dict:
    """Join the queue. Returns the entry with its position and estimated wait."""
    items = _read_all(queue_file)
    waiting = [i for i in items if i["status"] == "waiting"]
    entry = {
        "id": f"walk-{len(items) + 1:04d}",
        "name": name,
        "service": service,
        "status": "waiting",  # waiting | serving | served | left
        "position": len(waiting) + 1,
        "estimated_wait_minutes": (len(waiting) + 1) * slot_minutes,
        "slot_minutes": slot_minutes,
    }
    items.append(entry)
    _write_all(queue_file, items)
    return entry


def queue_status(queue_file: str | Path) -> list[dict]:
    """Waiting entries in order, positions and waits recomputed from the queue."""
    waiting = [i for i in _read_all(queue_file) if i["status"] == "waiting"]
    for pos, entry in enumerate(waiting, start=1):
        entry["position"] = pos
        entry["estimated_wait_minutes"] = pos * entry.get("slot_minutes", DEFAULT_SLOT_MINUTES)
    return waiting


def serve_next(queue_file: str | Path) -> dict | None:
    """Call the next walk-in forward. Returns the entry, or None if empty."""
    items = _read_all(queue_file)
    for entry in items:
        if entry["status"] == "waiting":
            entry["status"] = "serving"
            _write_all(queue_file, items)
            return entry
    return None


def complete(queue_file: str | Path, entry_id: str) -> dict:
    items = _read_all(queue_file)
    for entry in items:
        if entry["id"] == entry_id:
            entry["status"] = "served"
            _write_all(queue_file, items)
            return entry
    raise KeyError(f"walk-in entry not found: {entry_id}")
