# Luna test driver — Vercel build (primary) + PythonAnywhere (second home)

The ARTISY AI receptionist (Luna) test site. One codebase, two
deploys — Ting's call Nov 7, 2026 ("do both", hybrid, work smarter
not harder): the same OAuth + PWA app runs on **Vercel (primary)** and
**PythonAnywhere (second home / backup)**, sharing one Upstash store,
so one Allow tap connects the calendar on both. The sibling tree
`../pythonanywhere-driver/` is the same code adapted for the PA
WSGI entry point (plus `DEPLOY.md` for the PA host steps).

**Status (Nov 7, 2026, evening): OAuth + PWA port complete and
bench-verified — see BUILD-REPORT.md. The Vercel project
`luna-test-driver` (team zigg1, account samanlott) is LIVE; this
port pushes to `main` and Vercel auto-deploys. The service-account
path is GONE from this build — no key file anywhere.**

## What changed vs the Space build (HOST-ASSESSMENT-NOV7.md)

| Area | Space build | This build |
|---|---|---|
| Calendar | Reverse relay to the bench | Google OAuth 2.0 web flow (`/authorize` + `/oauth2callback`), refresh token in Upstash KV (`luna:google_oauth`); direct Calendar API (`agent-service/calendar_booking.py`) |
| TTS | Kokoro sidecar (af_heart) | Groq Canopy Orpheus, voice `hannah` (`groq_tts.py`) — **voice changes; this test does NOT validate the real voice** |
| State | Process memory + local disk | Upstash Redis via REST (`kvstore.py`) — sessions, queues, DNC, logs, rate limits |
| Stub server | Local stub captures call-end posts | Call-end payload written to the KV activity log (returns 201) |
| Relay | WebSocket relay hub | Deleted |
| Phone install | Plain web page | PWA (`/manifest.json`, `/sw.js`, icons, `display: standalone`) — modelled on Willow Tales |
| Layout | `api/index.py` (legacy) | Root `index.py` — Vercel Flask framework preset (auto-detected) |

## Deploy

Vercel project: **`luna-test-driver`**, team **zigg1**, account
**samanlott** ONLY. Never the tinglott@gmail.com Vercel account
(stopped Oct 31 by Ting's word). Push this folder to GitHub repo
`tinglott/luna-test-driver` on `main` — Vercel auto-deploys from
main. The PWA icons are NOT in git (the GitHub push tool is
text-only); `vercel.json` runs `tools/make_icons.py` at build time
(`pip install pillow` first) so `/icon-192.png` and `/icon-512.png`
resolve on the live deployment.

## Environment variables (Vercel dashboard — secrets pasted there only, never in chat/repo)

| Variable | Value / source |
|---|---|
| `GROQ_API_KEY` | Ting's Groq key (already set — copied from the `artisy-guide-api` project's `aira` var) |
| `GOOGLE_OAUTH_CLIENT_ID` | From the Google Cloud OAuth client (copy at creation; one client carries BOTH redirect URIs — Vercel + PythonAnywhere) |
| `GOOGLE_OAUTH_CLIENT_SECRET` | From the Google Cloud OAuth client (copy at creation) |
| `CALENDAR_ID` | `tinglott@gmail.com` (already set) |
| `UPSTASH_REDIS_REST_URL` + `UPSTASH_REDIS_REST_TOKEN` | Injected automatically by the Upstash Redis Marketplace integration (already set). Legacy `KV_REST_API_URL`/`KV_REST_API_TOKEN` also accepted — **the same store the PythonAnywhere deploy uses, so one Allow tap serves both hosts** |
| `LUNA_TEST_RESET_KEY` | Optional — random string; enables POST /api/test/reset for repeat battery runs. Leave unset and the endpoint 404s |

## Owner taps (all Ting's — see BUILD-REPORT.md for the exact list)

1. **Google Cloud console: ONE OAuth client** (project
   `luna-test-514021`) — consent screen External, Web client with
   BOTH redirect URIs (Vercel stable URL + PythonAnywhere URL), test
   users = Ting + the 4 affiliates with emails on file, publish to
   Production if the console allows.
2. **One Allow tap** — open the app, tap Connect Google Calendar,
   sign in as tinglott@gmail.com, Allow (Advanced → Go to continue
   if the unverified-app warning appears), then Install app. The
   refresh token lands in Upstash and BOTH hosts read it.
3. PythonAnywhere signup needs Ting's password (his step, secure
   card) — see `../pythonanywhere-driver/DEPLOY.md`.
4. Hobby-vs-Pro call: Vercel Hobby is non-commercial only; this
   affiliate test for a paid service is commercial — Hobby risks a
   pause, Pro $20/mo/seat removes it. Do NOT buy Pro without his word.
   Also watch: Hobby functions time out at 10 s — the slowest Luna
   request (TTS + brain + booking) is only proven live.

## Local testing

```bash
~/workspace/venvs/luna-vercel/bin/pip install -r requirements.txt
# test-client suite (starts its own fake Calendar API):
~/workspace/venvs/luna-vercel/bin/python tests/test_vercel_local.py
# full HTTP battery (mock KV + fake Calendar API + driver):
tests/run_vercel_battery.sh
# gates:
~/workspace/venvs/luna-vercel/bin/python tests/luna-gate-probe.py
```

## Files

- `index.py` — Flask app (entrypoint; Vercel imports `app`)
- `kvstore.py` — Upstash Redis REST / memory / fail-closed store
- `groq_tts.py` — Groq Orpheus TTS (200-char chunks, WAV stitch)
- `groq_whisper.py` — Groq Whisper STT (in-process + CLI)
- `booking_parse.py`, `driver.html` — driver page (`audio_url` →
  `audio_data` data URI; calendar connect card + SW registration)
- `manifest.json`, `sw.js` — PWA shell (`/api/*` network-only, never cached)
- `tools/make_icons.py` — generates the PWA icons at Vercel build time
- `agent-service/` — brain, gates (Nov 7 FIXED, v1.0.3), call handler
  (+ session serialization), OAuth calendar, `google_oauth.py`
  (`/authorize` + `/oauth2callback`), KV-backed queues, role loader,
  sales leads, business hours
- `roles/` — role configs v1.0.3
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
- No service-account key anywhere in this build. After OAuth is
  verified working on BOTH hosts, delete both live SA keys
  (`8e534b066af5`, `b674f2078718`) — see BUILD-REPORT.md.
