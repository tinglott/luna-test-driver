# DEPLOY.md — Luna on PythonAnywhere (free) + Google OAuth

Ordered checklist. Steps marked **[BROWSER]** need the live browser
(the builder subagent cannot do them). Steps marked **[TING]** need
Ting himself. Everything else is bench work. **$0 only — never add a
card or pay for anything.** Never mint, read, or paste a
service-account private key (a previous attempt was blocked — do not
retry it in any form). Never put any secret value in any report,
file, or message.

## 0. Pre-commit live checks (do these FIRST — they decide the build)

1. **[BROWSER] PythonAnywhere account.** Create the free Beginner
   account: username `tinglott` if available, email
   `tinglott@gmail.com`. **The signup needs a password — Ting sets it
   himself (Secure Vault / owner tap). If a card or payment is
   demanded at ANY point, STOP and report — do not proceed.**
   Handle the email-confirmation step via Ting's connected Gmail
   (tinglott@gmail.com) without asking.
2. **[BROWSER] Upstash reachability from the free console.** In the
   PythonAnywhere Bash console, run a no-auth GET against the
   Upstash REST URL (read the URL from the Vercel dashboard env vars
   as `KV_REST_API_URL` — the URL only, never the token):
   `curl -s -o /dev/null -w "%{http_code}" https://<id>.upstash.io/ping`
   - **HTTP 401 = PASS** (the host is allowlisted; 401 means "reached
     Upstash, no token given"). Continue.
   - **Connection/proxy block = FAIL.** Stop and report — do not
     switch stores on your own. (Fallback: ask PythonAnywhere
     support for a whitelist addition.)
3. **[BROWSER] Google Cloud — Calendar API.** In project
   `luna-test-514021` (signed in as tinglott@gmail.com), verify the
   Google Calendar API is enabled (it was enabled Nov 7 for the
   service account — confirm it is still on).

## 1. Google Cloud — OAuth client (builder, browser)

In the same project `luna-test-514021`:

1. **OAuth consent screen** (Google Auth Platform): user type
   **External**, app name e.g. `Luna — ARTISY AI Receptionist`,
   support email tinglott@gmail.com. Add the scope
   `https://www.googleapis.com/auth/calendar.events` with a
   justification (book test appointments on the owner's calendar).
   Add the `openid` and `email` scopes too (the app verifies the
   signed-in account is the calendar owner).
2. **Create the OAuth client** (Credentials → Create Credentials →
   OAuth client ID → Web application). Authorized redirect URI —
   **exact, from the real PythonAnywhere username from step 0**:
   `https://tinglott.pythonanywhere.com/oauth2callback`
   (scheme, case and trailing slash must match exactly — a mismatch
   gives `redirect_uri_mismatch`).
3. **Copy the Client ID + Client Secret immediately at creation**
   into the PythonAnywhere env vars (step 2 below). These are short
   strings — paste them into PythonAnywhere only, NEVER into chat,
   any report, or any file.
4. **Test users** (while the app is in Testing mode): add
   tinglott@gmail.com + the three affiliate emails on file:
   ribeiro.elianeguimaraes@gmail.com, ldproducoes48@gmail.com,
   biz.ayush26@gmail.com. The other six affiliates have NO email on
   file (ledger says "via JVZoo Message Center" / TBD) — they cannot
   be added until their emails are collected.
5. **Publish the OAuth app to Production** before the pilot (OAuth
   consent screen → Publish app). In Testing mode refresh tokens
   expire after 7 days; in Production they do not. While unverified,
   testers see a "Google hasn't verified this app" warning and click
   Advanced → Go to continue — expected, not an error.

## 2. PythonAnywhere — web app (builder, browser)

1. Web tab → Add a new web app → **Manual configuration** →
   Python **3.12** → domain `tinglott.pythonanywhere.com`.
2. Virtualenv: create `/home/tinglott/luna-venv` (or
   `/home/tinglott/.virtualenvs/luna`) from `requirements.txt`
   (`pip install -r requirements.txt` in a Bash console).
3. Code: `git clone` the repo `tinglott/luna-test-driver`, branch
   **`pythonanywhere`**, into `/home/tinglott/luna-test-driver`
   (the Vercel app deploys from `main` and must keep working).
   Then generate the PWA icons (binary files are not in the repo —
   the push tool is text-only): in the app virtualenv,
   `pip install pillow` then `python3 tools/make_icons.py`
   (writes `icon-192.png` + `icon-512.png` in the project root).
4. WSGI file: replace its contents with
   `pythonanywhere_wsgi.py` from this repo (adjust the username if
   it differs from tinglott).
5. Env vars (Web tab → Environment variables):
   `GROQ_API_KEY` (same key the Vercel app uses — read it from the
   Vercel `artisy-guide-api` project's `aira` var via the browser,
   paste into PythonAnywhere only), `GOOGLE_OAUTH_CLIENT_ID`,
   `GOOGLE_OAUTH_CLIENT_SECRET`, `CALENDAR_ID=tinglott@gmail.com`,
   `UPSTASH_REDIS_REST_URL`, `UPSTASH_REDIS_REST_TOKEN`,
   `LUNA_REQUIRE_STORE=1`, `LUNA_TEST_RESET_KEY` (optional, for the
   bench battery).
6. Reload the web app. Verify: the public URL opens and `/health`
   responds `ok:true`, `store_ok:true`,
   `calendar_connected:false` (false until Ting taps Allow).

## 3. Ting's step — one Allow tap (when free)

1. Open `https://tinglott.pythonanywhere.com/?tester=ting`
   (or the plain link — the driver page shows the calendar card).
2. Tap **Connect Google Calendar**, sign in as tinglott@gmail.com,
   tap **Allow** (and Advanced → Go to continue if the unverified-app
   warning appears).
3. The page shows "Calendar connected ✓". Then Chrome menu →
   **Install app** / **Add to Home screen** — Luna lives on the
   phone like Willow.
4. A test booking is made and verified on his calendar (builder
   verifies end-to-end after the tap).

## 4. Verify end-to-end (builder, after Ting's tap)

- `/health`: `calendar_connected:true`.
- A real booking through the driver creates a real calendar event
  on tinglott@gmail.com and is deleted when the test call ends.
- Sessions persist across reloads (Upstash, not memory).
- App opens from the Android home screen after install; manifest +
  service worker serve correctly.

## 5. Cleanup — ONLY after OAuth is verified working

Delete BOTH service-account keys for
`luna-test-driver@luna-test-514021.iam.gserviceaccount.com` in the
Google Cloud console (Keys page): `8e534b066af5` and `b674f2078718`.
(Both key files are lost; neither is needed once OAuth works.) Keep
the Groq key and the Upstash database as-is. The Vercel deployment
stays untouched as fallback — do not modify or delete it.

## 6. Monthly note (owner)

The PythonAnywhere free web app expires after one month unless the
owner logs in and clicks one "run for another month" button (a
warning email arrives a week ahead). If the site goes dark, re-enable
it from the Web tab — no rebuild needed.
