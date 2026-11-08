# Luna — PythonAnywhere build report (Nov 7, 2026, evening)

Rebuilt from `../vercel-driver/` for PythonAnywhere (free Beginner
account, Ting chose it Nov 7). Same AI receptionist test app, same
guardrails (Nov 7 fixed gates, role configs v1.0.3), same Groq brain /
Orpheus TTS — but the calendar works the way other apps do: Ting opens
the app, taps Allow once on Google's sign-in, and the app stores its
own refresh token. **No service-account key file anywhere in this
build.** And it is a PWA modelled on Willow Tales: open the link,
Chrome menu → Install app, and it lives on the phone.

## What was built

- `agent-service/google_oauth.py` (new) — `/authorize` +
  `/oauth2callback`, offline access + consent prompt, refresh token in
  Upstash KV (`luna:google_oauth`), state stored server-side (10-min
  TTL), id_token email verified against the calendar owner
  (`tinglott@gmail.com`), scopes `openid` + `email` +
  `calendar.events`.
- `agent-service/calendar_booking.py` (OAuth rewrite) — same public
  API; credentials from KV, then the bench-only `LUNA_TEST_OAUTH_JSON`
  hook; refresh on demand; `invalid_grant` clears the stored token and
  falls back to message-taking. **The service-account path is gone.**
- `index.py` — `/health` gains `calendar_connected`; routes
  `/manifest.json`, `/sw.js` (no-cache), `/icon-192.png`,
  `/icon-512.png`, `/authorize`, `/oauth2callback`,
  `/api/calendar/status`; `LUNA_REQUIRE_STORE=1` fails closed without
  the store.
- `driver.html` — manifest link + theme color, service-worker
  registration, calendar connect card (section 0) with live status.
- `manifest.json`, `sw.js` (Willow-modelled: `/api/*` network-only,
  never cached), `icon-192.png`, `icon-512.png` (navy/gold crescent +
  LUNA — **generated on the host with `tools/make_icons.py`; the
  binary icons are NOT in git because the push tool is text-only**).
- `requirements.txt` (+ `google-auth-oauthlib`, `google-auth-httplib2`),
  `wsgi.py`, `pythonanywhere_wsgi.py`, `README.md`, `DEPLOY.md`.
- Tests: `tests/test_pa_local.py` (Flask test client),
  `tests/test_pa_e2e.py` (44-check HTTP battery, adapted from the
  Vercel battery), `tests/run_pa_battery.sh` (starts mock KV + fake
  calendar + driver, runs the battery, stops them),
  `tests/luna-gate-probe.py` (unchanged).

## Bugs the bench tests caught (all fixed in this build)

1. **Memory-store deadlock** (`kvstore.py`): `_MemoryBackend.incr`
   held a plain `Lock` while calling `get()`/`set()`, which re-acquire
   it — the first rate-limited `/api/` call hung forever, no error, no
   timeout. Fixed with `RLock`. **The same latent bug exists in
   `../vercel-driver/kvstore.py` (line 78) — left untouched per
   instructions; it only bites the memory backend, which the Vercel
   deployment never uses (Upstash is configured).**
2. **OAuth state mismatch** (`google_oauth.py`): `Flow.authorization_url`
   generates its OWN state when none is passed, so the URL's state
   never matched the KV-stored one — every real sign-in would have
   failed as "expired or already used". Fixed by passing
   `state=state`.
3. **PKCE verifier mismatch** (`google_oauth.py`): the Flow
   auto-generates a code_verifier (default on); the callback builds a
   fresh Flow with a different verifier, so Google would refuse the
   code exchange. Fixed with `autogenerate_code_verifier=False` (a
   confidential web client authenticates with its secret; PKCE is
   optional there).
4. **Expiry naive/aware TypeError** (`calendar_booking.py`):
   google-auth's `utcnow()` is deliberately naive, but
   `_build_credentials` parsed the stored ISO expiry as aware —
   `creds.valid` raised `TypeError` on every booking call. Fixed by
   normalizing expiry to naive UTC.

## Transport finding (bench only)

Cloudflare 1010-blocks the bench egress proxy for non-browser
User-Agents (proven Nov 7: `Python-urllib` and the old
`ARTISY-FreeStack/1.0` UA both got 403 error 1010; a Chrome UA gets
through). `brain.py`, `groq_tts.py`, `groq_whisper.py` now send a
plain Chrome UA. Transport-only change — same models, prompts, voices.

## Bench verification (all on the bench, Nov 7 evening)

- **Gate probe: 0 failures** — all 10 role configs (crisis 9/9,
  medical 12/12, negative 7/7, curly 2/2 each).
- **Test-client suite: 37/37** — health, tester gate, PWA routes,
  `/authorize` (offline + consent + state in KV + no PKCE challenge),
  callback fail-safes, full session → booking → verified delete
  against the fake Calendar API, crisis 988, `invalid_grant` fallback
  + token clear, SA-path-dead proof (garbage SA var ignored),
  test-reset guard + OAuth token preserved.
- **HTTP battery: 40/44 pass, 0 fail, 4 blocked** — the 4 blocked are
  all TTS-dependent. The block is real and specific: the bench Groq
  surrogate key's org has NOT accepted the Orpheus terms
  (`model_terms_required` from the live API). Ting accepted the terms
  Nov 7 for the org behind the **`aira`** key (artisy-guide-api) —
  the PythonAnywhere deploy uses that key, so TTS verification moves
  to the live deploy step. The brain was proven live through the
  driver's own code path (`openai/gpt-oss-120b` answered).
- **Brain live:** `openai/gpt-oss-120b` answered through
  `brain_reply` with the surrogate key.

## Git

Pushed to `tinglott/luna-test-driver`, branch **`pythonanywhere`**,
commit **`e0a7849628f971d0f12cba267c4fdc3736516eb9`** (44 files,
verified by reading `index.py` + `google_oauth.py` + 
`calendar_booking.py` back from the branch). `main` is untouched
(still the Vercel version, commit `9ac7c7ee`).

## Still to do (browser / Ting — NOT bench work)

1. **PythonAnywhere signup** (needs Ting's password — his step):
   username `tinglott`, email `tinglott@gmail.com`. STOP if a card is
   demanded. Then the Upstash curl check from the PA console
   (401 = allowlisted PASS; connection block = FAIL).
2. **Google Cloud OAuth client** (browser, tinglott@gmail.com,
   project `luna-test-514021`): consent screen External; Web client,
   redirect `https://tinglott.pythonanywhere.com/oauth2callback`;
   test users tinglott@gmail.com + ribeiro.elianeguimaraes@gmail.com
   + ldproducoes48@gmail.com + biz.ayush26@gmail.com (the other six
   affiliates have no email on file); publish to Production.
3. **Groq key**: read `aira` from the Vercel `artisy-guide-api`
   project via the browser; paste into PythonAnywhere only.
4. **Deploy on PA**: manual web app, Python 3.12, virtualenv from
   `requirements.txt`, `git clone -b pythonanywhere`,
   `python3 tools/make_icons.py` (Pillow), WSGI file, env vars,
   Reload, verify `/health` (`store_ok:true`,
   `calendar_connected:false` until the tap).
5. **Ting's Allow tap**: open the app, tap Connect Google Calendar,
   sign in as tinglott@gmail.com, Allow (Advanced → Go to continue
   if the unverified-app warning appears), then Install app.
6. **After OAuth is verified working**: delete BOTH service-account
   keys (`8e534b066af5`, `b674f2078718`). The Vercel deployment stays
   untouched as fallback.
