# Luna test driver — Vercel rebuild: BUILD REPORT

**Date:** Nov 7, 2026 (evening)
**Builder:** subagent session 9fa5547b (parent af3934d3)
**Spec:** `~/workspace/n8n-agent-service/space-build/HOST-ASSESSMENT-NOV7.md`
**Project:** `~/workspace/n8n-agent-service/vercel-driver/`
**Bench driver untouched** (gunicorn :7860 + relay + TTS + stub still
running as the reference; nothing in it was modified).

## Verdict

**Build complete and verified locally. NOT deployed — the deploy is
blocked on the Vercel API token (see §4). No public URL exists yet.**

- Gate probe: **0 failures** (10/10 configs: 9/9 crisis, 12/12
  medical, 7/7 negative, 2/2 curly each).
- Battery (local Flask run, mock Upstash REST + fake Calendar API +
  real Groq brain/Whisper): **40/44 pass, 0 fail, 4 blocked** — the 4
  blocked are all TTS-dependent, blocked by ONE owner tap (Groq
  Orpheus terms acceptance, §3). Everything else passes, including
  the full booking cycle (real event create + read-back + verified
  delete), callback/DNC flows, approval queue, walk-ins, sales,
  invalid-config fail-closed, Twilio bridge 25/25.
- Statelessness proven: a session survived a full app-process restart
  (turn + confirm + end, real event created and verified deleted
  after the restart).
- Security checks pass: tester gate, /api 403 for strangers, reset
  endpoint 404s without/with a wrong key. No secrets in the repo.

## 1. What was built

Vercel Flask framework-preset layout (root `index.py`, no vercel.json
rewrite needed — verified from Vercel's own docs by the research
child, agent f536cb69):

- **`index.py`** — the Flask app. Sessions serialized to KV between
  requests (`CallSession.to_state` / `from_state` in call_handler.py);
  every mutating endpoint saves before returning. Queues/DNC via
  `kv://` paths (patched `_read_all`/`_write_all` in the four queue
  modules). Call-end payload → KV activity log (201). Session index
  + opportunistic purge of expired sessions (deletes their bookings).
  Rate limits via KV INCR. Guarded test-reset endpoint (404s unless
  `LUNA_TEST_RESET_KEY` is set AND the header matches).
- **`kvstore.py`** — Upstash Redis REST backend (POST command arrays,
  bearer token; accepts `UPSTASH_REDIS_REST_*` or legacy `KV_*`),
  in-process memory backend for local runs, and a fail-closed
  "missing" backend on Vercel without Redis (every op raises
  StoreUnavailable; /health reports store_ok:false).
- **`agent-service/calendar_booking.py`** — REWRITTEN: service-account
  credentials → Google Calendar API directly (freebusy → create →
  read-back). Same public API as the bench version. A role config's
  literal `"primary"` resolves to `CALENDAR_ID` and FAILS CLOSED when
  it is unset (a service account's own primary is not the test
  calendar). Bench quirk documented in code: httplib2's default
  proxy_info is a per-request function that ignores no_proxy — the
  client uses `AuthorizedHttp` on a `proxy_info=None` http (direct,
  always; harmless on Vercel).
- **`groq_tts.py`** — Groq `/audio/speech`, model
  `canopylabs/orpheus-v1-english`, voice `hannah` (female). 200-char
  sentence chunks, WAV stitch by PCM (header parsed at runtime —
  Groq documents no sample rate), 429 retry with 6s backoff, 4-chunk
  cap (Vercel 4.5 MB response guard). Returns a base64 data URI.
- **`groq_whisper.py`** — refactored to expose in-process
  `transcribe_file()` (CLI kept).
- **`driver.html`** — `audio_url` → `audio_data` (data URI, played
  from memory); no `/audio/<name>` route.
- **Gates/roles from the BENCH, not the Space copy** — the Space
  copies were STALE (gates pre-FIX #11, roles v1.0.2). Vercel build
  carries the fixed gates.py (FIX #11: medical phrases + curly
  apostrophes) and roles v1.0.3. Verified by the probe (§2).
- **Deleted:** relay.py, tts_worker.py, stub_server.py, all relay
  routes, sidecar startup, `/audio/<name>`.

Research corrections from the docs (agent f536cb69, all read from
official pages Nov 7):
- **Vercel KV is retired** — new projects install Upstash Redis from
  the Marketplace; env vars are `UPSTASH_REDIS_REST_URL` /
  `UPSTASH_REDIS_REST_TOKEN` (legacy `KV_*` survives only on migrated
  stores). kvstore.py accepts both.
- **Orpheus is a Preview model** ("intended for evaluation purposes
  only… may be discontinued at short notice") and costs $22/1M chars
  (English). Free tier: 10 RPM / 100 RPD / 1.2K TPM / 3.6K TPD (table
  as rendered; Groq's prose is ambiguous about the tier — exact
  per-org limits at console.groq.com/settings/limits). 100 req/day is
  the binding constraint for a multi-tester day.
- **Hobby max duration is 300s** (Fluid compute), not 60s. Bundle
  500 MB Python (this build: whole venv 167 MB). **Response 4.5 MB
  max** — hence the 4-chunk audio cap.
- Only two Groq TTS models exist for this org (`canopylabs/
  orpheus-v1-english`, `...-arabic-saudi`) — PlayAI TTS is not listed.

## 2. Verification (local, Nov 7 evening)

All runs against the local Flask app on :18102 with the mock KV
server (:18098, Upstash REST shape) + fake Calendar API (:18103,
real googleapiclient library, minimal discovery doc) + Groq
surrogate key for brain/Whisper.

| Check | Result |
|---|---|
| Gate probe (`tests/luna-gate-probe.py`) | **0 failures** — 10 configs × (9 crisis + 12 medical + 7 negative + 2 curly) |
| Battery (`tests/test_vercel_e2e.py`) | **40/44 pass, 0 fail, 4 blocked** (all 4 TTS: greeting_tts_wav_nonempty, stt_roundtrip_transcribes, tts_audio_to_mulaw_8k, mulaw_decode_wav_readable) — see §3 |
| Booking cycle | Real event `evt-13b40ea524284ac6` created, read back, verified deleted on session end; fake calendar empty after |
| Approval queue | New-caller hold → approve writes real event `evt-6a26267335a7422c`, read back; deny records no event |
| Callback/DNC | Greeting Part A verbatim; opt-out → DNC (verified behaviourally: sweep suppresses); late-night deferred; calling-window units pass |
| Sales | Qualifies + follow-up due; front-desk price never qualifies |
| Walk-in | Waits 15/30; serve recomputes |
| Invalid config | Driver refuses (500, "does not disclose the AI"); CallSession message-taking-only |
| Twilio bridge | 25/25 (bench unit test, unchanged module) |
| Statelessness | Session survived an app-process restart (turn → confirm → real event → end → verified delete) |
| Tester gate | Unknown tester → invited-only page; /api without tester → 403 |
| Reset endpoint | 404 without key, 404 with wrong key |
| Secret scan | Clean (only the mock server's literal "test-token" default) |
| Calendar module (direct) | free/book/get/delete/read-back all correct; `primary` without CALENDAR_ID fails closed |
| TTS stitching/chunking (offline) | 2 WAVs stitch to one valid WAV (24000 frames); corrupt chunk fails closed; 450-char sentence hard-splits to 3 chunks |

Battery results file: `test-evidence/test-vercel-e2e-results.json`
(first full run; a second run after the datetime fix is the 40/44
above — the first run crashed at the booking turn on a datetime in
the pending-booking slot, fixed by stripping datetimes to ISO
strings in `session_turn`).

**Observation (not a failure, same on the bench):** the brain's
reply to an unknown question re-states the greeting before refusing
("Hello, thanks for calling… I'm sorry, we don't sell…"). Cosmetic;
the brain and system prompt are unchanged from the bench.

## 3. Blockers — owner taps (ONE list)

1. **Groq Orpheus terms (BLOCKS the voice check + all TTS).**
   Groq returns 400 `model_terms_required` for
   `canopylabs/orpheus-v1-english` until the org admin accepts the
   terms. Ting (Groq org admin, tinglott@gmail.com): open
   https://console.groq.com/playground?model=canopylabs%2Forpheus-v1-english
   and accept. Until then every reply is text-only (audio_data null)
   and 4 battery checks stay blocked. The TTS code path itself is
   proven except the live Groq call (chunking + stitching unit-tested
   offline). After acceptance, re-run the battery — expect 44/44.
2. **Google service account + test calendar.** Confirm which Google
   account owns the test calendar the bench CLI uses; in Google Cloud
   console enable the Calendar API, create a service account + JSON
   key, share the test calendar with the service-account email; note
   the calendar id (that is `CALENDAR_ID`).
3. **Vercel dashboard pastes (Ting himself, never in chat).**
   `GROQ_API_KEY`; the service-account JSON contents as
   `GOOGLE_SERVICE_ACCOUNT_JSON`; `CALENDAR_ID`. Install Upstash
   Redis from the Marketplace (one click — injects the REST env
   vars). Optionally set `LUNA_TEST_RESET_KEY` to a random string
   (only needed for repeat battery runs against the live URL).
4. **Deploy route (parent's call — see §4).** The project must be
   created in Vercel under samanlott/zigg1; the code must reach
   Vercel (GitHub import needs a repo decision + push).
5. **Hobby vs Pro (Ting's call).** Vercel Hobby is non-commercial
   only (Fair Use Guidelines); this affiliate test for a paid service
   is commercial. Hobby risks a pause; Pro $20/mo/seat removes it.
   Do NOT buy Pro without his word — build on Hobby, report the risk.

## 4. Deploy — BLOCKED on the API token

The `custom.vercel-samanlott` token (owner samanlott@gmail.com,
username samanlott-4574, defaultTeamId
team_WRen3DmNFWgpU9GGmJxxv5NP, `"limited": true`):

- `GET /v2/user` → 200 (identity reads work; the Oct 30 403
  invalidToken is resolved for reads).
- `GET /v2/teams`, `GET /v2/teams/<default>` → 403 team_unauthorized.
- `GET /v9/projects` (no team / teamId=default / teamId=zigg1) →
  200, empty list in all cases. `GET /v9/projects/artisy-guide-api`
  → 404 (cannot read the guide project either).
- `POST /v10/projects?teamId=zigg1 {"name":"luna-test-driver"}` →
  403 "You don't have permission to create the project."
- **File upload `POST /v2/now/files` → 403 "You don't have
  permission to create the resource" (fileUpload).**

The token is effectively read-scoped for identity only. No project
was created (nothing to clean up). The privsep daemon is the WRONG
account (tingsterlott12@gmail.com, team_9Ay6eBsQvQD1HnkdrfkT0s4N) —
do NOT use it for this task.

**Remaining deploy route:** a browser task on the Vercel dashboard
(samanlott identity, switch-identity-first): create project
`luna-test-driver` under team zigg1, import this folder from GitHub
(push it first — repo placement needs Ting's/parent's word: new repo
`luna-test-driver`, or a folder in tinglott/artisy-store), set the
env vars in §3, deploy. After the project exists, re-test whether
the token can create deployments (upload already 403s, so the
dashboard/Git route is the likely deploy path throughout).

**Terms flag (do not bury):** Vercel Hobby is non-commercial only;
this affiliate test for a paid service is commercial. Hobby risks a
pause; Pro $20/mo/seat removes it. Build on Hobby; Ting decides.

## 5. Voice change — say it plainly

Kokoro `af_heart` is GONE in this build. The test voice is Groq
Orpheus `hannah` — a different voice, a different engine, and a
Preview model. This test validates the call flow, the gates, the
booking and the queues. It does NOT validate the real voice. Ting
and the affiliates must be told that before they listen.

## 6. Files

- `README.md` (this report's short form), `index.py`, `kvstore.py`,
  `groq_tts.py`, `groq_whisper.py`, `booking_parse.py`, `driver.html`,
  `requirements.txt`, `.python-version` (3.12)
- `agent-service/` (brain, gates v1.0.3, call_handler + serialization,
  calendar_booking [service account], KV-backed queues, role_loader,
  sales_lead, business_hours), `roles/` (v1.0.3)
- `tests/` (battery, gate probe, fake Calendar API, mock KV server)
- `test-evidence/` (battery results + battery log)
- Local-only, NOT in the repo: `/tmp/luna-test-sa.json` (throwaway
  test service-account key), `/tmp/luna-vercel-local.env` (local env
  incl. the battery reset key), `/tmp/run-luna-vercel-local.sh`
  (local launcher). The bench driver, the fake calendar server
  (:18103) and the mock KV server (:18098) are still running on the
  bench for reference; the local app (:18102) is also still up.
