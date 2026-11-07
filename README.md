# Luna test driver — Vercel build

The ARTISY AI receptionist (Luna) test site, rebuilt for Vercel so Ting
can test it from a public link, then the nine invited affiliates.

**Status (Nov 7, 2026): build complete and verified locally — 40/44
battery checks pass, 0 fail, 4 blocked on one owner tap (Groq Orpheus
terms acceptance). NOT YET DEPLOYED: the samanlott API token cannot
create projects or upload files (both 403) — see BUILD-REPORT.md.**

## What changed vs the Space build (HOST-ASSESSMENT-NOV7.md)

| Area | Space build | This build |
|---|---|---|
| Calendar | Reverse relay to the bench | Google service account, direct Calendar API (`agent-service/calendar_booking.py`) |
| TTS | Kokoro sidecar (af_heart) | Groq Canopy Orpheus, voice `hannah` (`groq_tts.py`) — **voice changes; this test does NOT validate the real voice** |
| State | Process memory + local disk | Upstash Redis via REST (`kvstore.py`) — sessions, queues, DNC, logs, rate limits |
| Stub server | Local stub captures call-end posts | Call-end payload written to the KV activity log (returns 201) |
| Relay | WebSocket relay hub | Deleted |
| Layout | `api/index.py` (legacy) | Root `index.py` — Vercel Flask framework preset (auto-detected) |

## Deploy

Vercel project: **`luna-test-driver`**, team **zigg1**, account
**samanlott** ONLY. Never the tinglott@gmail.com Vercel account
(stopped Oct 31 by Ting's word).

**Deploy route — BLOCKED on the API token (see BUILD-REPORT.md).**
The token reads identity (`/v2/user` 200) but `POST /v10/projects`
403s and file upload 403s — it is read-scoped. The privsep daemon
is the WRONG account (tingsterlott12) — do not use it. The remaining
route is the Vercel dashboard (browser task, samanlott identity,
switch-identity-first): create project `luna-test-driver` under team
zigg1, import this folder from GitHub (push it first), set the env
vars below, deploy. No `vercel.json` is needed (Flask preset).

## Environment variables (Vercel dashboard — Ting pastes secrets himself)

| Variable | Value / source |
|---|---|
| `GROQ_API_KEY` | Ting's Groq key (paste himself; never in chat/repo) |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | Service-account JSON key contents (paste himself) |
| `CALENDAR_ID` | Test calendar id shared with the service account |
| `UPSTASH_REDIS_REST_URL` + `UPSTASH_REDIS_REST_TOKEN` | Injected automatically by the Upstash Redis Marketplace integration (one click). Legacy `KV_REST_API_URL`/`KV_REST_API_TOKEN` also accepted |
| `LUNA_TEST_RESET_KEY` | Optional — random string; enables POST /api/test/reset for repeat battery runs. Leave unset and the endpoint 404s |

## Owner taps (all Ting's — see BUILD-REPORT.md for the exact list)

1. **Groq console: accept the Orpheus terms** (org admin) — until
   then every TTS call 400s `model_terms_required` and replies are
   text-only. Link: https://console.groq.com/playground?model=canopylabs%2Forpheus-v1-english
2. Google Cloud console: confirm which account owns the test calendar,
   enable the Calendar API, create the service account + JSON key,
   share the test calendar with it; note the calendar id.
3. Vercel dashboard: paste `GROQ_API_KEY` + the service-account JSON;
   install Upstash Redis from the Marketplace (one click); optionally
   set `LUNA_TEST_RESET_KEY`.
4. Hobby-vs-Pro call: Vercel Hobby is non-commercial only; this
   affiliate test for a paid service is commercial — Hobby risks a
   pause, Pro $20/mo/seat removes it. Do NOT buy Pro without his word.

## Local testing

```bash
python3 -m venv ~/workspace/venvs/luna-vercel
~/workspace/venvs/luna-vercel/bin/pip install -r requirements.txt
# test doubles (bench only — never deployed):
python3 tests/fake_calendar_api.py 18103   # fake Calendar API + discovery
python3 tests/mock_kv_server.py 18098 test-token  # mock Upstash REST
# app (env in /tmp/luna-vercel-local.env; GROQ surrogate + test SA key):
/tmp/run-luna-vercel-local.sh
# battery:
LUNA_BASE=http://127.0.0.1:18102 LUNA_TEST_RESET_KEY=local-battery-reset-key \
  CALENDAR_ID=test-calendar \
  GOOGLE_API_DISCOVERY_URL=http://127.0.0.1:18103/discovery/calendar-v3.json \
  GOOGLE_SERVICE_ACCOUNT_JSON="$(cat /tmp/luna-test-sa.json)" \
  GROQ_API_KEY="$(python3 -c "...dynamic_credential_entry('custom.groq')['surrogate']...")" \
  ~/workspace/venvs/luna-vercel/bin/python tests/test_vercel_e2e.py
# gates:
~/workspace/venvs/luna-vercel/bin/python tests/luna-gate-probe.py
```

## Files

- `index.py` — Flask app (entrypoint; Vercel imports `app`)
- `kvstore.py` — Upstash Redis REST / memory / fail-closed store
- `groq_tts.py` — Groq Orpheus TTS (200-char chunks, WAV stitch)
- `groq_whisper.py` — Groq Whisper STT (in-process + CLI)
- `booking_parse.py`, `driver.html` — unchanged from the Space build
  (driver.html: `audio_url` → `audio_data` data URI)
- `agent-service/` — brain, gates (Nov 7 FIXED, v1.0.3), call handler
  (+ session serialization), service-account calendar, KV-backed
  queues, role loader, sales leads, business hours
- `roles/` — role configs v1.0.3 (from the bench — the Space copies
  were STALE pre-fix v1.0.2)
- `tests/` — battery, gate probe, fake Calendar API, mock KV server
- `test-evidence/` — battery results + battery log

## Guardrails (kept from the bench)

- Tester gate: only the nine invited affiliates + bench-battery.
- Hard gates: Nov 7 fixed gates, 0 failures on the probe.
- Fail closed: no AI disclosure → no session; invalid config →
  message-taking only; calendar down → message-taking, never a fake
  booking; store down → 503, never a fake session.
- Test bookings are deleted on session end (verified, not assumed);
  abandoned sessions are purged on the next session start.
- No secrets in this repo (verified by grep). No live Whop/Gumroad/
  JVZoo changes. No affiliate messages — Ting tests first.
