# Luna — GitHub Pages frontend

One static page per Luna feature. GitHub Pages hosts the FRONTEND;
the Python backends (Vercel primary, PythonAnywhere second home) do
the brain, the calendar, and the TTS. The pages call the backend;
they never do any of that work themselves.

**Live site (once Pages is enabled): https://tinglott.github.io/luna-test-driver/**

## Pages

| Page | Feature | Backend routes it calls |
|---|---|---|
| `index.html` | Landing / install + tester entry + feedback | `GET /health`, `POST /api/feedback` |
| `talk.html` | Voice receptionist — start a call, talk (type or mic), confirm/cancel a booking, end, owner summary | `/api/session/start`, `/turn`, `/transcribe`, `/confirm`, `/cancel-booking`, `/end` |
| `calendar.html` | Calendar connect status + monthly report draft | `GET /api/calendar/status`, `/authorize` (link), `GET /api/report/monthly` |
| `callback.html` | Call-backs — request, sweep, answer | `/api/callback/request`, `/api/callback/sweep`, `/api/session/turn`, `/api/session/end` |
| `desk.html` | Front desk — walk-ins, owner approvals, W4 heartbeat | `/api/walkin/*`, `/api/approval/*`, `POST /api/heartbeat` |

## Shared files

- `api-config.js` — the ONE place that knows the API base URL.
  Vercel primary (`https://luna-test-driver.vercel.app`), PythonAnywhere
  fallback (`https://tinglott.pythonanywhere.com`). A call falls back
  only on a network failure or 502/503/504 — a 4xx is a real answer
  and is never retried. The tester gate is enforced BY THE BACKEND;
  the pages only pass the tester name through (`X-Tester` header).
- `style.css` — ARTISY navy/gold, mobile-first (same look as the
  backend test driver).
- `manifest.json` + `sw.js` — PWA shell (installable). The worker
  caches same-origin shell files only; API calls always go to the
  network, never cached. Icons are served from the Vercel backend
  (the push tool is text-only, so no binary icons live in this repo).

## Deploy

This directory is pushed to the `gh-pages` branch of
`tinglott/luna-test-driver` (it does NOT disturb Vercel's
main-branch deploy). GitHub Pages must be enabled once in the repo
Settings → Pages → source: `gh-pages` branch. After that, every push
to `gh-pages` republishes the site.

## Backend changes this frontend needs

CORS: the backends allow exactly one cross-origin caller —
`https://tinglott.github.io` (see `index.py` in `vercel-driver/` and
`pythonanywhere-driver/`). If the Pages URL ever changes, update
`ALLOWED_CORS_ORIGINS` in both drivers and redeploy.
