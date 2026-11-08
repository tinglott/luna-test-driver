#!/usr/bin/env python3
"""Fake Google Calendar API server — local test double for the Vercel
calendar_booking.py module (OAuth grant, direct Calendar API).

Why: the Vercel build calls the real googleapiclient library against
the real Google API shape, but local testing has no Google grant
(Ting's Allow tap comes later). This server proves the request/response
code path end-to-end with the REAL client library: discovery-doc fetch,
OAuth refresh-token grant to /token, freebusy/events request
construction, 404 mapping, read-back verification.

It is NOT Google: the only unproven parts after a pass here are the
real endpoint + the real OAuth grant — both proven later by
the public-URL battery run after Ting's taps.

Serves (stdlib only, port 18103):
  GET  /discovery/calendar-v3.json  — minimal Calendar v3 discovery doc
       (rootUrl/servicePath rewritten to this server)
  POST /token                      — dummy access token for the OAuth refresh grant
  POST /calendar/v3/freeBusy       — busy intervals for a calendar
  POST /calendar/v3/calendars/{cal}/events
  GET  /calendar/v3/calendars/{cal}/events/{id}
  DELETE /calendar/v3/calendars/{cal}/events/{id}
  GET  /calendar/v3/calendars/{cal}/events?timeMin&timeMax&singleEvents

Events live in memory (dict). A calendar id of "test-calendar" is the
test calendar; any other calendar id 404s on freebusy (like a calendar
the signed-in account has no access to).

Usage: python3 fake_calendar_api.py [port]  (default 18103)
State file: /tmp/luna-fake-calendar-state.json (so the battery can
inspect it independently of the app).
"""
from __future__ import annotations

import json
import os
import sys
import uuid
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 18103
STATE_FILE = "/tmp/luna-fake-calendar-state.json"

EVENTS: dict[str, dict] = {}  # event_id -> event dict (with calendarId)


def _save_state() -> None:
    with open(STATE_FILE, "w") as fh:
        json.dump({"events": EVENTS}, fh, indent=1)


def _load_state() -> None:
    if os.path.exists(STATE_FILE):
        try:
            EVENTS.update(json.load(open(STATE_FILE)).get("events", {}))
        except (OSError, ValueError):
            pass


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _discovery_doc(port: int) -> dict:
    base = f"http://127.0.0.1:{port}/"
    return {
        "kind": "discovery#restDescription",
        "name": "calendar",
        "version": "v3",
        "rootUrl": base,
        "servicePath": "calendar/v3/",
        "batchPath": "batch/calendar/v3",
        "parameters": {},
        "schemas": {
            "FreeBusyRequest": {"id": "FreeBusyRequest", "type": "object",
                                "properties": {"timeMin": {"type": "string"},
                                               "timeMax": {"type": "string"},
                                               "items": {"type": "array",
                                                         "items": {"type": "object",
                                                                   "properties": {"id": {"type": "string"}}}}}},
            "FreeBusyResponse": {"id": "FreeBusyResponse", "type": "object",
                                 "properties": {"calendars": {"type": "object"}}},
            "Event": {"id": "Event", "type": "object",
                      "properties": {"id": {"type": "string"},
                                     "summary": {"type": "string"},
                                     "description": {"type": "string"},
                                     "location": {"type": "string"},
                                     "status": {"type": "string"},
                                     "start": {"type": "object"},
                                     "end": {"type": "object"}}},
            "Events": {"id": "Events", "type": "object",
                       "properties": {"items": {"type": "array",
                                                "items": {"$ref": "Event"}}}},
        },
        "resources": {
            "freebusy": {"methods": {"query": {
                "id": "calendar.freebusy.query", "path": "freeBusy",
                "httpMethod": "POST",
                "request": {"$ref": "FreeBusyRequest"},
                "response": {"$ref": "FreeBusyResponse"}}}},
            "events": {"methods": {
                "insert": {"id": "calendar.events.insert",
                           "path": "calendars/{calendarId}/events",
                           "httpMethod": "POST",
                           "parameters": {"calendarId": {"type": "string",
                                                         "location": "path",
                                                         "required": True}},
                           "request": {"$ref": "Event"},
                           "response": {"$ref": "Event"}},
                "get": {"id": "calendar.events.get",
                        "path": "calendars/{calendarId}/events/{eventId}",
                        "httpMethod": "GET",
                        "parameters": {"calendarId": {"type": "string",
                                                      "location": "path",
                                                      "required": True},
                                       "eventId": {"type": "string",
                                                   "location": "path",
                                                   "required": True}},
                        "response": {"$ref": "Event"}},
                "delete": {"id": "calendar.events.delete",
                           "path": "calendars/{calendarId}/events/{eventId}",
                           "httpMethod": "DELETE",
                           "parameters": {"calendarId": {"type": "string",
                                                         "location": "path",
                                                         "required": True},
                                          "eventId": {"type": "string",
                                                      "location": "path",
                                                      "required": True}}},
                "list": {"id": "calendar.events.list",
                         "path": "calendars/{calendarId}/events",
                         "httpMethod": "GET",
                         "parameters": {"calendarId": {"type": "string",
                                                       "location": "path",
                                                       "required": True},
                                        "timeMin": {"type": "string",
                                                    "location": "query"},
                                        "timeMax": {"type": "string",
                                                    "location": "query"},
                                        "singleEvents": {"type": "boolean",
                                                         "location": "query"}},
                         "response": {"$ref": "Events"}},
            }},
        },
    }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # keep stdout clean
        pass

    def _send(self, status: int, payload) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except ValueError:
            return {}

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/discovery/calendar-v3.json":
            self._send(200, _discovery_doc(PORT))
            return
        parts = [unquote(p) for p in parsed.path.split("/") if p]
        # /calendar/v3/calendars/{cal}/events/{id}  (6 parts)
        if len(parts) == 6 and parts[:2] == ["calendar", "v3"] and parts[2] == "calendars" and parts[4] == "events":
            cal, event_id = parts[3], parts[5]
            event = EVENTS.get(event_id)
            if event is None or event.get("calendarId") != cal:
                self._send(404, {"error": {"code": 404, "message": "Not Found"}})
                return
            self._send(200, event)
            return
        # /calendar/v3/calendars/{cal}/events  (5 parts, list)
        if len(parts) == 5 and parts[:2] == ["calendar", "v3"] and parts[2] == "calendars" and parts[4] == "events":
            cal = parts[3]
            qs = parse_qs(parsed.query)
            time_min = qs.get("timeMin", [None])[0]
            time_max = qs.get("timeMax", [None])[0]
            items = [e for e in EVENTS.values() if e.get("calendarId") == cal]
            if time_min and time_max:
                t0, t1 = _parse_iso(time_min), _parse_iso(time_max)
                items = [e for e in items
                         if _parse_iso(e["start"]["dateTime"]) < t1
                         and _parse_iso(e["end"]["dateTime"]) > t0]
            self._send(200, {"items": items})
            return
        self._send(404, {"error": {"code": 404, "message": "Not Found"}})

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/token":
            self._send(200, {"access_token": "fake-token-for-local-tests",
                             "expires_in": 3600, "token_type": "Bearer"})
            return
        if parsed.path == "/calendar/v3/freeBusy":
            body = self._read_json()
            calendars = {}
            for item in body.get("items", []):
                cal = item.get("id")
                if cal != "test-calendar":
                    self._send(404, {"error": {"code": 404, "message": "Not Found"}})
                    return
                t0, t1 = _parse_iso(body["timeMin"]), _parse_iso(body["timeMax"])
                busy = [{"start": e["start"]["dateTime"], "end": e["end"]["dateTime"]}
                        for e in EVENTS.values()
                        if e.get("calendarId") == cal
                        and _parse_iso(e["start"]["dateTime"]) < t1
                        and _parse_iso(e["end"]["dateTime"]) > t0]
                calendars[cal] = {"busy": busy}
            self._send(200, {"calendars": calendars})
            return
        parts = [unquote(p) for p in parsed.path.split("/") if p]
        if len(parts) == 5 and parts[:2] == ["calendar", "v3"] and parts[2] == "calendars" and parts[4] == "events":
            cal = parts[3]
            body = self._read_json()
            event_id = "evt-" + uuid.uuid4().hex[:16]
            event = {"id": event_id, "calendarId": cal, "status": "confirmed",
                     "summary": body.get("summary", ""),
                     "description": body.get("description", ""),
                     "location": body.get("location", ""),
                     "start": body.get("start", {}), "end": body.get("end", {})}
            EVENTS[event_id] = event
            _save_state()
            self._send(200, event)
            return
        self._send(404, {"error": {"code": 404, "message": "Not Found"}})

    def do_DELETE(self):
        parts = [unquote(p) for p in urlparse(self.path).path.split("/") if p]
        if len(parts) == 6 and parts[:2] == ["calendar", "v3"] and parts[2] == "calendars" and parts[4] == "events":
            cal, event_id = parts[3], parts[5]
            event = EVENTS.get(event_id)
            if event is None or event.get("calendarId") != cal:
                self._send(404, {"error": {"code": 404, "message": "Not Found"}})
                return
            del EVENTS[event_id]
            _save_state()
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self._send(404, {"error": {"code": 404, "message": "Not Found"}})


if __name__ == "__main__":
    _load_state()
    print(f"fake Calendar API on http://127.0.0.1:{PORT} "
          f"(discovery /discovery/calendar-v3.json, state {STATE_FILE})", flush=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
