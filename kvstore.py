"""Key-value store for the Vercel driver — sessions, queues, logs, rate limits.

The bench/Space driver keeps all of this in process memory or on local
disk (``sessions`` dict, ``_req_log``, JSONL queue files). A Vercel
function has neither: instances are stateless, scale to many, and lose
local disk between invocations. Everything stateful therefore lives in
an external store, behind this one small interface:

  get(key) / set(key, value, ex=None) / delete(key) / incr(key, ex=None)
  get_json(key) / set_json(key, value, ex=None)
  list_get(key) / list_set(key, items) / list_append(key, item, max_len)

Backends (chosen at first use, from env):
  * "rest"   — Upstash Redis via its REST API (Vercel KV is retired;
               new projects install Redis from the Marketplace).
               Active when UPSTASH_REDIS_REST_URL +
               UPSTASH_REDIS_REST_TOKEN are set (what the Upstash
               integration injects), or the legacy KV_REST_API_URL /
               KV_REST_API_TOKEN on migrated stores. One POST per
               command, JSON command array body, bearer token. No
               persistent connection, no extra dependency (plain
               ``requests``).
  * "memory" — in-process dict with per-key expiry. Local testing only.
               Active when no KV env is set AND we are not on Vercel.
  * "missing"— on Vercel (VERCEL=1) with no KV env: every operation
               raises StoreUnavailable. A public deployment must never
               silently run on per-instance memory (sessions would 404
               across instances and bookings would leak) — it fails
               closed instead, and /health reports store_ok:false.

Race note: list operations are read-modify-write of one JSON value.
Two concurrent writers can lose each other's append. Test volume is
a handful of testers; the battery is sequential. A production build
would use Redis transactions / Lua scripts here.

No secrets are ever stored. Values are JSON-serialisable state only.
"""

from __future__ import annotations

import json
import os
import threading
import time

import requests


class StoreUnavailable(Exception):
    """The store cannot serve this operation (not provisioned / unreachable)."""


def _rest_env() -> tuple[str, str] | None:
    """Upstash Redis REST credentials from env. A new Upstash
    integration injects UPSTASH_REDIS_REST_URL / UPSTASH_REDIS_REST_TOKEN;
    stores migrated from retired Vercel KV keep KV_REST_API_URL /
    KV_REST_API_TOKEN. Prefer the Upstash names."""
    url = os.environ.get("UPSTASH_REDIS_REST_URL") or os.environ.get("KV_REST_API_URL")
    token = os.environ.get("UPSTASH_REDIS_REST_TOKEN") or os.environ.get("KV_REST_API_TOKEN")
    if url and token:
        return url, token
    return None


def _backend_name() -> str:
    if _rest_env():
        return "rest"
    if os.environ.get("VERCEL"):
        return "missing"
    return "memory"


class _MemoryBackend:
    """Dict + per-key expiry. Local testing only."""

    def __init__(self) -> None:
        self._data: dict[str, tuple[str, float | None]] = {}
        # RLock, not Lock: incr() calls get()/set() while holding the
        # lock. A plain Lock deadlocks there forever (found Nov 7 by
        # the bench test-client suite — the first rate-limited /api/
        # call hung with no error and no timeout).
        self._lock = threading.RLock()

    def get(self, key: str) -> str | None:
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return None
            value, expires_at = entry
            if expires_at is not None and time.time() > expires_at:
                del self._data[key]
                return None
            return value

    def set(self, key: str, value: str, ex: int | None = None) -> None:
        with self._lock:
            self._data[key] = (value, time.time() + ex if ex else None)

    def delete(self, key: str) -> None:
        with self._lock:
            self._data.pop(key, None)

    def incr(self, key: str, ex: int | None = None) -> int:
        with self._lock:
            current = self.get(key)
            value = int(current or 0) + 1
            self.set(key, str(value), ex=ex)
            return value


class _RestBackend:
    """Upstash Redis REST API. One POST per command.

    POST {KV_REST_API_URL} with JSON body ["GET", key] (or SET / DEL /
    INCR / EXPIRE) and header Authorization: Bearer {KV_REST_API_TOKEN}.
    Response: {"result": ...}. A non-2xx or an "error" field raises
    StoreUnavailable — the caller fails closed, never fakes a write.
    """

    def __init__(self, url: str, token: str) -> None:
        self._url = url.rstrip("/")
        self._token = token

    def _cmd(self, *command: str):
        try:
            resp = requests.post(
                self._url,
                json=list(command),
                headers={"Authorization": f"Bearer {self._token}"},
                timeout=10,
            )
        except Exception as exc:  # noqa: BLE001 - network failure is store failure
            raise StoreUnavailable(f"KV REST unreachable: {exc}") from exc
        if resp.status_code >= 400:
            raise StoreUnavailable(f"KV REST HTTP {resp.status_code}: {resp.text[:200]}")
        try:
            payload = resp.json()
        except ValueError as exc:
            raise StoreUnavailable(f"KV REST bad JSON: {resp.text[:200]}") from exc
        if isinstance(payload, dict) and payload.get("error"):
            raise StoreUnavailable(f"KV REST error: {payload['error']}")
        return payload.get("result") if isinstance(payload, dict) else payload

    def get(self, key: str) -> str | None:
        result = self._cmd("GET", key)
        return None if result is None else str(result)

    def set(self, key: str, value: str, ex: int | None = None) -> None:
        if ex:
            self._cmd("SET", key, value, "EX", str(ex))
        else:
            self._cmd("SET", key, value)

    def delete(self, key: str) -> None:
        self._cmd("DEL", key)

    def incr(self, key: str, ex: int | None = None) -> int:
        value = int(self._cmd("INCR", key))
        if ex and value == 1:
            self._cmd("EXPIRE", key, str(ex))
        return value


class _MissingBackend:
    """Placeholder that refuses every operation — see module docstring."""

    def get(self, key: str):
        raise StoreUnavailable(
            "Upstash Redis is not provisioned on this deployment — sessions, "
            "queues and logs have nowhere to live. Install Redis from the "
            "Vercel Marketplace (one click) and redeploy."
        )

    def set(self, key: str, value: str, ex: int | None = None):
        self.get(key)

    def delete(self, key: str):
        self.get(key)

    def incr(self, key: str, ex: int | None = None):
        self.get(key)


class KVStore:
    """The store the driver uses. Backed by _MemoryBackend, _RestBackend
    or _MissingBackend (see _backend_name)."""

    def __init__(self) -> None:
        backend = _backend_name()
        self.backend_name = backend
        if backend == "rest":
            url, token = _rest_env()  # type: ignore[misc]
            self._b: object = _RestBackend(url, token)
        elif backend == "memory":
            self._b = _MemoryBackend()
        else:
            self._b = _MissingBackend()

    # -- raw string ops -------------------------------------------------
    def get(self, key: str) -> str | None:
        return self._b.get(key)  # type: ignore[attr-defined]

    def set(self, key: str, value: str, ex: int | None = None) -> None:
        self._b.set(key, value, ex=ex)  # type: ignore[attr-defined]

    def delete(self, key: str) -> None:
        self._b.delete(key)  # type: ignore[attr-defined]

    def incr(self, key: str, ex: int | None = None) -> int:
        return self._b.incr(key, ex=ex)  # type: ignore[attr-defined]

    # -- JSON helpers ----------------------------------------------------
    def get_json(self, key: str):
        raw = self.get(key)
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except ValueError:
            return None

    def set_json(self, key: str, value, ex: int | None = None) -> None:
        self.set(key, json.dumps(value), ex=ex)

    # -- list helpers (whole-list JSON values) ---------------------------
    def list_get(self, key: str) -> list:
        value = self.get_json(key)
        return value if isinstance(value, list) else []

    def list_set(self, key: str, items: list) -> None:
        self.set_json(key, list(items))

    def list_append(self, key: str, item, max_len: int | None = None) -> None:
        items = self.list_get(key)
        items.append(item)
        if max_len is not None:
            items = items[-max_len:]
        self.list_set(key, items)

    # -- status ----------------------------------------------------------
    def status(self) -> dict:
        """Honest report for /health: which backend, and whether it answers."""
        if self.backend_name == "missing":
            return {"backend": "missing", "configured": False,
                    "error": "Upstash Redis not provisioned"}
        try:
            self._b.get("__luna_health__")  # type: ignore[attr-defined]
        except StoreUnavailable as exc:
            return {"backend": self.backend_name, "configured": False,
                    "error": str(exc)}
        return {"backend": self.backend_name, "configured": True}


_STORE: KVStore | None = None
_STORE_LOCK = threading.Lock()


def get_store() -> KVStore:
    """Process-wide singleton. Backend is fixed at first use."""
    global _STORE
    if _STORE is None:
        with _STORE_LOCK:
            if _STORE is None:
                _STORE = KVStore()
    return _STORE


def reset_store_singleton() -> None:
    """Tests only: drop the singleton so a new backend can be selected."""
    global _STORE
    with _STORE_LOCK:
        _STORE = None
