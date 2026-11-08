#!/usr/bin/env python3
"""Mock Upstash Redis REST server — local test double for kvstore.py's
REST backend (the backend a PythonAnywhere deployment uses).

Implements the Upstash REST command shape the driver uses: POST /
with a JSON command array body (["GET", key], ["SET", key, value,
"EX", secs], ["DEL", key], ["INCR", key], ["EXPIRE", key, secs])
and header Authorization: Bearer <token>; responds {"result": ...}.
Values are strings; expiry is honoured (SET EX / EXPIRE).

Usage: python3 mock_kv_server.py [port] [token]
  default port 18098, token "test-token".
State file: /tmp/luna-mock-kv-state.json (inspection only).
"""
from __future__ import annotations

import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 18098
TOKEN = sys.argv[2] if len(sys.argv) > 2 else "test-token"
STATE_FILE = "/tmp/luna-mock-kv-state.json"

DATA: dict[str, tuple[str, float | None]] = {}  # key -> (value, expires_at)


def _save() -> None:
    with open(STATE_FILE, "w") as fh:
        json.dump({k: v[0] for k, v in DATA.items()}, fh, indent=1)


def _get(key: str) -> str | None:
    entry = DATA.get(key)
    if entry is None:
        return None
    value, expires_at = entry
    if expires_at is not None and time.time() > expires_at:
        del DATA[key]
        return None
    return value


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, status: int, payload) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.headers.get("Authorization") != f"Bearer {TOKEN}":
            self._send(401, {"error": "unauthorized"})
            return
        length = int(self.headers.get("Content-Length") or 0)
        try:
            command = json.loads(self.rfile.read(length).decode("utf-8"))
        except ValueError:
            self._send(400, {"error": "bad JSON"})
            return
        if not isinstance(command, list) or not command:
            self._send(400, {"error": "expected a command array"})
            return
        op = str(command[0]).upper()
        try:
            if op == "GET" and len(command) == 2:
                result = _get(command[1])
            elif op == "SET" and len(command) >= 3:
                ex = None
                if len(command) == 5 and str(command[3]).upper() == "EX":
                    ex = int(command[4])
                DATA[command[1]] = (str(command[2]),
                                    time.time() + ex if ex else None)
                result = "OK"
            elif op == "DEL":
                removed = sum(1 for k in command[1:] if DATA.pop(k, None) is not None)
                result = removed
            elif op == "INCR" and len(command) == 2:
                current = int(_get(command[1]) or 0) + 1
                DATA[command[1]] = (str(current), DATA.get(command[1], (None, None))[1])
                result = current
            elif op == "EXPIRE" and len(command) == 3:
                entry = DATA.get(command[1])
                if entry is None:
                    result = 0
                else:
                    DATA[command[1]] = (entry[0], time.time() + int(command[2]))
                    result = 1
            else:
                self._send(400, {"error": f"unsupported command: {command}"})
                return
        except (ValueError, TypeError) as exc:
            self._send(400, {"error": str(exc)})
            return
        _save()
        self._send(200, {"result": result})


if __name__ == "__main__":
    print(f"mock KV (Upstash REST shape) on http://127.0.0.1:{PORT} "
          f"(token {TOKEN!r}, state {STATE_FILE})", flush=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
