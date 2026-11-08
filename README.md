# Luna test driver — PythonAnywhere build (free Beginner account)

Rebuilt from `../vercel-driver/` for PythonAnywhere (Ting chose it Nov 7,
2026). Same AI receptionist test app, same guardrails (Nov 7 fixed
gates, role configs v1.0.3), same Groq brain/TTS — but the calendar
works the way other apps do: Ting opens the app, taps Allow once on
Google's sign-in, and the app stores its own refresh token. **No
service-account key file anywhere in this build** — the old path is
gone entirely (`calendar_booking.py` reads only the stored OAuth
grant from Upstash KV). And the app is a PWA modelled on Willow
Tales: open the link, Chrome menu → Install app, and it lives on the
phone with its own icon.

## What changed vs the Vercel build

| | Vercel build | This build |
|---|---|---|
| Calendar auth | Service-account key file pasted in a dashboard | Google OAuth 2.0 web flow (`/authorize` + `/oauth2callback`), refresh token in Upstash KV (`luna:google_oauth`) |
| Calendar scope | `https://www.googleapis.com/auth/calendar` | `https://www.googleapis.com/auth/calendar.events` (narrower) |
| Phone install | Plain web page | PWA (`/manifest.json`, `/sw.js`, icons, `display: standalone`) |
| Host | Vercel Hobby | PythonAnywhere free Beginner (`tinglott.pythonanywhere.com`) |
| Entry point | `index.py` (Vercel Flask preset) | `index.py` via WSGI (`wsgi.py` / `pythonanywhere_wsgi.py`) |
| Store | Upstash Redis (same database, same credentials) | Same — Upstash carries over unchanged |

Everything else is identical: Groq Orpheus TTS (voice `hannah`), Groq
brain, guardrails, gates, roles, tester gate (9 affiliates +
bench-battery), session/queue/DNC/approval flows, `/health`.

## Env vars (set in the PythonAnywhere Web tab — never in the repo)

| Var | Value / source |
|---|---|
| `GROQ_API_KEY` | Same Groq key the Vercel app uses (read it from the Vercel `artisy-guide-api` project's `aira` var via the browser; paste into PythonAnywhere only) |
| `GOOGLE_OAUTH_CLIENT_ID` | From the Google Cloud OAuth client (copy at creation) |
| `GOOGLE_OAUTH_CLIENT_SECRET` | From the Google Cloud OAuth client (copy at creation) |
| `CALENDAR_ID` | `tinglott@gmail.com` |
| `UPSTASH_REDIS_REST_URL` | Same Upstash store as the Vercel app (paste from the Vercel env vars) |
| `UPSTASH_REDIS_REST_TOKEN` | Same Upstash store as the Vercel app |
| `LUNA_REQUIRE_STORE` | `1` (fail closed if the store is missing — never run on memory) |
| `LUNA_TEST_RESET_KEY` | Optional; enables POST /api/test/reset for the bench battery |

## Run locally (bench)

```bash
python3 -m venv ~/workspace/venvs/luna-pa   # once; libs already installed there
~/workspace/venvs/luna-pa/bin/python -m pytest tests/  # (or run tests/test_pa_local.py directly)
~/workspace/venvs/luna-pa/bin/python tests/fake_calendar_api.py 18103  # fake Calendar API
LUNA_TEST_OAUTH_JSON='{"refresh_token":"fake",...,"token_uri":"http://127.0.0.1:18103/token"}' \
GOOGLE_API_DISCOVERY_URL=http://127.0.0.1:18103/discovery/calendar-v3.json \
CALENDAR_ID=test-calendar \
~/workspace/venvs/luna-pa/bin/python index.py
```

## Tests

- `tests/luna-gate-probe.py` — the 21 critical guardrail phrasings + controls, every role config (unchanged from the Vercel build).
- `tests/test_pa_local.py` — Flask test-client end-to-end: health, PWA routes, OAuth route shapes, session start/turn/confirm against the fake Calendar API, booking create + verified delete on session end, invalid_grant handling, and proof the service-account path is gone.

## Deploy

See `DEPLOY.md` — the ordered checklist for the builder + browser,
including the Google Cloud OAuth client steps and Ting's one Allow tap.
