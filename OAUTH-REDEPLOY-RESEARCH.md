# Luna — new home research (OAuth redeploy)

**Date:** 7 November 2026. **Status:** research only. No accounts created, nothing signed up for, no code changed, nothing bought.

**Why:** Luna (Flask app, repo `tinglott/luna-test-driver`) works on Vercel's free Hobby tier, but Ting — non-technical, Android phone only, no laptop — found Vercel too hard: setting environment variables and pasting a Google service-account key file from a phone was impossible, and he has decided Vercel was not a good idea. He wants the app to work "like other apps": open it on his phone, sign in with Google by tapping Allow, and use it — ideally installed to his home screen like a phone app (PWA). His later refinements, folded in below: model the rebuild on the Willow app (a PWA that lands on a device with all its components, which he opens on his phone and uses, never touching a dashboard); consider Base44; and make it "a html and python hybrid living on a phone using phones resources."

**Method:** parallel researchers read each provider's own docs and pricing pages (7 Nov 2026), plus a study of Willow's local source (`~/workspace/kids-app/`, live at https://brisk-pavo-65xrf6d.vercel.app, fetched live 7 Nov 2026). Every claim below carries its source URL. Anything that could not be verified on a primary source is marked **UNVERIFIED**. A second key fact the research confirmed: the fix Luna needs — Google OAuth sign-in instead of a service-account key file — is the same work on every host, and it is what makes the app "tap Allow and use it" for Ting. No host removes the Groq API key paste (a short string the builder pastes once); the key file is what disappears.

---

## Recommendation (ranked — revised after the Willow study)

### 1. TOP PICK, CONDITIONAL — Vercel, Willow-style (PWA + OAuth, builder-deployed)

**Why, in plain language:** the Willow study changed the answer. What made Willow work for Ting was never the host — it was the *pattern*: the builder deploys everything, the app lands on his phone as an installable app with all its components, and he never touches a dashboard. That pattern is achievable for Luna on Vercel, where Luna already lives: the app is already deployed there, Upstash is already wired in, and the browser has already proved it can deploy and paste short secrets on the samanlott account. The ordeal that made Ting say "Vercel wasn't a good idea" was the dashboard ordeal — pasting a multi-line key file, the identity code, the protection toggle. With OAuth the key file disappears; the remaining secrets are short strings the browser already proved it can paste; protection is already off; the identity code was one-time. The ordeal is gone. Ting's only step becomes tapping Allow once, and the app installs to his home screen like Willow.

**Why it is conditional:** it needs Ting's agreement to revisit Vercel. If his verdict is final ("no Vercel, period"), skip to #2 — the pattern still applies there, it just runs on a new host.

**Costs and catches:**
- The exact Willow deploy script cannot be reused: the privsep bench-deploy is account-bound to tingsterlott12, and Luna lives under samanlott/team zigg1; the samanlott API token is read-scoped (identity works, project create/upload 403). Deploys and env vars for Luna go through the browser instead — proven to work, and Ting still never touches the dashboard. The mechanism differs; the pattern holds.
- Vercel Hobby is free (4 hrs Active CPU, 1M invocations/mo, per Vercel's own page); Flask is zero-config; cold starts are typically 1–3s (secondary sources), with no Render-style 15-minute sleep and loading page. Hobby is non-commercial only — Luna is a test, so it fits.
- **One thing must be verified live:** Hobby's function timeout (secondary sources say 10s) against Luna's slowest request (TTS + brain + booking in one call). If a request exceeds it, the call fails. Test the full booking cycle on the deployed app.
- No monthly renewal tap, no migration, no new account.

Sources: Vercel Functions/Hobby — https://vercel.com/i/serverless-computing-platforms ; cold starts — https://mcpplaygroundonline.com/blog/free-mcp-server-hosting-cloudflare-vercel-guide ; https://www.runxbuild.com/blog/free-python-hosting/

### 2. TOP NEW HOME (if Vercel stays out) — PythonAnywhere (free Beginner account)

**Why, in plain language:** the only new-home candidate that is free with no card, runs the app without putting it to sleep, and is entirely browser-based — no laptop, no command line, no app install. The app stays awake, so testers opening it on their phones get an instant response, like a real app. It gives a free HTTPS address (`username.pythonanywhere.com`), which is what Google OAuth and phone install need. Groq (`api.groq.com`) and Google's APIs are confirmed on its free-tier allowlist. The Groq key and the Google OAuth client secret are pasted into web forms — the builder's job, not Ting's. Ting's only step is tapping Allow once.

**Costs and catches (all real, all manageable):**
- The free web app **expires after one month** unless the owner logs in and clicks one "run for another month" button (warning email arrives a week ahead). Miss it and the site goes dark until re-enabled. This is a monthly owner-tap, and it fits the pattern Ting already does.
- **One thing must be tested live before committing:** the free tier only reaches allowlisted sites. Upstash Redis is listed only as bare `upstash.io`; Luna's REST endpoint is a subdomain (`<id>.upstash.io`). Whether the bare entry covers subdomains is **UNVERIFIED** — a 30-second `curl` from a PythonAnywhere console settles it. If it is blocked, the fix is a whitelist request to PythonAnywhere support or the $10/mo Developer plan — either way, test first.
- Updates are manual (`git pull` in the browser console + click Reload) — builder work, not owner work. No custom domain on free. No background/scheduled tasks on free (irrelevant — Luna is request-driven). The one-time WSGI/virtualenv setup is technical work the builder does, not Ting.
- 100 CPU-seconds/day and "Low" bandwidth (no published number) — fine for 9 testers; exact web-app metering is **UNVERIFIED** from PythonAnywhere's own docs.

Sources: pricing/limits — https://www.pythonanywhere.com/pricing/ ; monthly expiry — https://blog.pythonanywhere.com/129 ; Flask setup — https://help.pythonanywhere.com/pages/Flask ; allowlist — https://www.pythonanywhere.com/whitelist/ ; no-card claim rests on third-party guides (official registration page could not be read — **UNVERIFIED** on an official page).

### 3. RUNNER-UP NEW HOME — Render (free tier)

**Why:** the cleanest Flask-from-GitHub path of all six (connect repo, `requirements.txt` + `gunicorn`, optional `render.yaml`, auto-redeploy on push), a genuinely permanent $0 tier (750 instance hours/month — one service running 24/7 is ~744 hrs, so it just fits), dashboard-pasted secrets, a public HTTPS `*.onrender.com` URL that satisfies Google OAuth, and Upstash carries over untouched.

**Why it is third, not first:** free web services **sleep after 15 minutes without traffic and take about a minute to wake**, showing a Render loading page first. For 9 sporadic testers that means almost every first launch of the day waits ~30–60 seconds — the opposite of "like other apps." And there is an **unresolved conflict over whether a card is required**: Render's own writing says no card is required, but independent testers report being asked for a card (with a $1 hold) to deploy a web service. That must be checked live at signup — if a card is demanded, Render is dead for Ting. Built-in free Key Value is unusable for sessions (in-memory only, data lost on restart), so Upstash must stay.

Sources: free tier/sleep/limits — https://render.com/docs/free ; no-card claim — https://render.com/articles/platforms-with-a-real-free-tier-for-developers-in-2026 ; card-required reports — https://flywp.com/blog/9769/best-free-docker-hosting-platforms/ ; https://stackwrite.com/blog/free-railway-alternatives-2026/

### 4. DISTANT FOURTH — Replit (Starter)

Only if the top three fail. Its free Starter tier is still described in Replit's docs, but it no longer appears on the public pricing page in Sept–Nov 2026 captures, its monthly credit amounts are unpublished, the one free published app **expires after 30 days** and must be re-published, code is public by default on Starter, and Autoscale sleeps after 15 idle minutes. Secrets must be pasted twice (dev + deployment). Its built-in "Sign in with Google" authenticates users to Replit, not to Google Calendar — the Google OAuth client work is still needed. **UNVERIFIED** whether Starter is still offered to a brand-new account or whether signup demands a card; needs a live check.

Sources: https://docs.replit.com/billing/plans/starter-plan ; https://docs.replit.com/billing/deployment-pricing ; https://www.usagepricing.com/blog/replit-ai (see note in report: https://www.usagepricing.com/blueprint/replit-ai)

### NOT RECOMMENDED

- **Hugging Face Spaces** — not free for Flask. A Flask app must run as a Docker Space, and creating one requires PRO ($9/mo). The free tier is Static sites plus Gradio-only ZeroGPU demos. The correct visibility for testers (Protected — code private, app reachable) is also PRO. Out as a free home.
  Sources: https://huggingface.co/docs/hub/en/spaces-overview ; https://huggingface.co/pro
- **Railway** — the free part is a 30-day $5 trial; after that the permanent Free plan gives only $1/mo of metered usage, not enough for an always-on app (needs sleep, with wake delays and possible 502s, or $5/mo Hobby + card). Also: an October 2026 breach exposed encrypted env vars, and whether signups have reopened is **UNVERIFIED** as of 7 Nov 2026.
  Sources: https://railway.com/pricing ; https://docs.railway.com/pricing/free-trial ; https://theproofdesk.com/railway-review
- **Fly.io** — no free tier for new accounts (7-day / $1 trial, trial Machines stop after 5 minutes, then a card is required; cheapest always-on Machine ~$2.19/mo). Decisively, deploys and secrets are CLI-only (`flyctl`), so a phone-only owner cannot deploy or rotate a key without a computer.
  Sources: https://docs.fly.io/about/cost-management ; https://docs.fly.io/about/free-trial ; https://fly.io/pricing/
- **Base44** — wrong tool for a voice receptionist (see its section below). No live phone-call handling, TTS is stored MP3 files rather than a live conversation, and the realistic floor is the Builder plan ($40/mo annual, $50 month-to-month) with metered credits, no top-up, and an export that does not run standalone. It would be good at prototyping the booking-form half only.
- **Pyodide / on-device Python shape** — wrong shape for Luna (see the hybrid section). Adds a 10MB+ download and complexity, solves nothing: the cloud APIs still need a server or exposed keys.
- **Briefcase / Kivy packaged Android app** — the most "lives on the phone" of the three hybrid shapes, but the worst fit for this owner (see the hybrid section): laptop + Android SDK + signing + store/sideload friction, full rewrite, and the Groq key baked into the APK is extractable.

### The decision for Ting, in one line

Vercel is where Luna already lives, and with the key file gone the ordeal that made you say no is gone too — the Willow way, same host, one Allow tap. If you'd rather never touch Vercel again, PythonAnywhere is the best new home; everything else about the rebuild (OAuth, PWA) stays exactly the same.

---

## Comparison table

| Host | Free, no card? | Flask from GitHub | Secrets | Google OAuth | Phone / PWA | Key-value store | Main catch |
|---|---|---|---|---|---|---|---|
| **Vercel (Willow-style)** ★ | Yes, Hobby $0, non-commercial only; no card (secondary sources) | Flask zero-config; Luna already deployed; deploys via browser (API token read-scoped) | Dashboard paste (browser-proven for short secrets) | Yes, HTTPS deployment URL | Yes, HTTPS + PWA | Upstash already wired in | Hobby function timeout (secondary: 10s) to verify; Ting's verdict to revisit |
| **PythonAnywhere** ★ | Yes, Beginner free (no-card per third-party guides; official page **UNVERIFIED**) | Yes, browser-based; manual `git pull` + Reload; WSGI file edit | Web-UI paste / file edit | Yes, HTTPS subdomain; allowlist covers Google APIs | Yes, HTTPS + PWA | No built-in KV; Upstash carries over **if** `<id>.upstash.io` reachable (test needed) | Web app expires monthly unless renewed with one click |
| **Render** ★ | Yes, permanent $0 tier; card-at-signup **CONFLICTING / UNVERIFIED** | Yes, easiest; `requirements.txt` + `gunicorn`, optional `render.yaml` | Dashboard paste; can self-generate values | Yes, `*.onrender.com` HTTPS | Yes, HTTPS + PWA | Built-in free KV is in-memory only (data lost on restart); Upstash carries over | Sleeps after 15 idle min (~1 min cold start, loading page) |
| Replit | Starter in docs, missing from pricing page; **UNVERIFIED** live | Yes, GitHub import; `.replit` + `requirements.txt` | Web-UI paste, twice (dev + deployment) | Yes, but built-in sign-in is Replit identity, not Calendar | Yes, HTTPS + PWA | Old KV deprecated; use Postgres (code change) or Upstash | Free published app dies after 30 days; code public by default |
| Hugging Face Spaces | **No** — Flask needs Docker Space = PRO $9/mo | Docker Space only; no native Flask SDK; GitHub sync needs HF token | Web-UI paste | HF's built-in OAuth is HF identity, not Google | HTTPS + PWA in principle; custom domain PRO-only | No built-in KV; Upstash carries over (HTTPS/443 allowed) | Paid for Flask; visibility dilemma (public vs private) |
| Railway | Trial only ($5 / 30 days), then $1/mo — not enough | Yes, auto-detect; `Procfile` + `requirements.txt` | Dashboard paste; bulk Raw Editor | Yes, `*.up.railway.app` HTTPS | Yes, HTTPS + PWA | Built-in Redis is metered (no free tier); Upstash carries over (except Limited Trial) | Free plan cannot host always-on; Oct 2026 breach, signup status **UNVERIFIED** |
| Fly.io | **No** — 7-day/$1 trial, then card + ~$2.19/mo | Yes, but CLI-only (`flyctl`) | CLI only (`fly secrets set`) | Yes, `*.fly.dev` HTTPS | Yes, HTTPS + PWA | No free built-in KV; Upstash carries over | CLI-only = unusable for a phone-only owner |
| Base44 | Free tier exists but is a trial (25 msg credits/mo, 100 integration credits) — not a usage plan | **No** — React + Base44 backend; Luna's Flask code cannot carry over; rebuild by prompting | Env vars in backend functions (Builder+ only) | Built-in Google sign-in; Calendar read/write built-in shared connector | Mobile browser + home-screen shortcut; store wrapper (Builder+) | Built-in database (entities); no Redis; Upstash replaced | Wrong tool for voice (no live telephony); Builder $40–50/mo floor; metered credits; export does not run standalone |

---

## The Willow pattern — what actually made Willow work for Ting

Studied from the local source (`~/workspace/kids-app/`, live at https://brisk-pavo-65xrf6d.vercel.app — fetched live 7 Nov 2026).

### What Willow is

- A **static PWA**: `app/` holds `index.html`, `manifest.json`, `sw.js`, `css/`, `js/`, `data/`, `assets/`. `vercel.json` is `{"public": true}`. No server code, no secrets, no env vars, no database.
- `manifest.json`: name "Willow Tales — Kids Reading Adventure", short_name "Willow Tales", `display: standalone`, `orientation: portrait`, `start_url: ./index.html`, theme/background `#1a6fa8`, icons 192 + 512.
- `index.html`: `<link rel="manifest" href="manifest.json">` plus `navigator.serviceWorker.register("sw.js")`.
- `sw.js`: versioned cache (`willow-tales-v12`), a CORE shell list cached on install, cache-first for `/assets/willow` (book pages/audio), network-first for the app shell with cache fallback.
- Deploy: `deploy-vercel-privsep.py` walks `app/`, sha1s each file, and calls `upload_file` + `create_deployment` on Vercel project `prj_XcZdWOrEH1p8BTzl1GjGcCX2mL4J` through the privsep socket (`/run/hatch/privsep/vercel.sock`, `privsep_client.py`). Deployed Oct 20 (440 files, 0 failures).
- Ting's experience: he opens the URL on his phone, installs it to his home screen, and uses it — the app lands with all its components (books, reader, read-aloud) and works offline-capable. He never touches a dashboard; updates arrive from bench deploys.

### What transfers to Luna — and what does not

- **Transfers:** the PWA files as a template (manifest + service worker + icons + registration); the experience shape (builder deploys, owner opens + installs + uses, zero dashboard steps for the owner); the service-worker caching discipline (network-first shell, cache-first static assets).
- **Does not transfer — the exact deploy script.** (a) The privsep daemon is account-bound to tingsterlott12 (per the samanlott connector doc, the system Vercel session is bound to tingsterlott12); Luna lives under samanlott/team zigg1, and the samanlott API token is read-scoped (identity works, project create/upload 403). The Willow script cannot deploy Luna. (b) Willow is static with no env vars; Luna is a Flask server app with secrets (Groq key, Google OAuth secret) and server-side state — a file-upload deploy cannot carry those; env vars must exist on the project. For Luna on Vercel, deploys and env vars go through the browser instead, which already proved it can do both on samanlott (project created + deployed, `GROQ_API_KEY` + `CALENDAR_ID` set). Ting still never touches the dashboard — the mechanism differs (browser, not privsep), the pattern holds.
- **Luna's PWA retrofit (concrete, from the actual code):** `index.py` serves `driver.html` via `send_file` at `/` (a single-page app; no templates dir). Add two routes, `/manifest.json` and `/sw.js` (send_file, with `Cache-Control: no-cache` on the worker); add `manifest.json` + `sw.js` + 192/512px icons; add the manifest link + service-worker registration to `driver.html`; `display: standalone`, `start_url: /`. The service worker **must** be network-first for `/api/*` (never cache API calls or auth state) and cache-first only for static assets. Willow's `sw.js` is the template, with the `/api/*` rule added.

### Verdict

The pattern is what matters, not the host. The Willow pattern on Vercel is the least-friction path for Luna — conditional on Ting accepting Vercel back now that the dashboard ordeal is gone. If Vercel stays out, the same pattern runs on PythonAnywhere (builder does the console work; Ting taps Allow once, plus one monthly renewal tap).

---

## Base44 — studied, verdict: wrong tool for Luna

Researched 7 Nov 2026 from Base44's official docs/pricing plus user reports. No accounts created.

### How creation works

You describe the app in plain words and Base44 builds it. Its pricing page: "You describe what you need in plain language, and Base44 builds the full thing: frontend, backend, database, user auth, payments, all of it. The apps run on real production infrastructure, not a sandbox." Included on every plan: AI-powered app building, integrated backend and database, responsive visual editor, analytics, multi-user editing, cloud storage, authentication and user management, marketing tools, debugging tools. Under the hood it is a React frontend plus a Base44-managed backend/database (MongoDB-compatible), with server-side logic as TypeScript "backend functions" on the Deno runtime — **not Python, so Luna's Flask code cannot be carried over; it would be a from-scratch rebuild by prompting**. Backend functions are capped at 50 per project, 5 minutes max per run.
Sources: https://base44.com/pricing ; https://docs.base44.com/developers/references/sdk/getting-started/third-party-apis

**Phone access:** "Your app works in any mobile browser without needing to be in an app store. Share the link to your published app… For the best experience, recommend that your app users add the app to their home screen… it appears alongside other apps and opens with a single tap, just like a native app." There is also a Base44 builder app for Android (Google Play) and iOS, so the owner can build/edit from his phone. App Store / Google Play files (.ipa/.aab) can be generated from the Builder plan up, but they are "a lightweight native wrapper around your web app that opens only your app's URL" — a web view, not a native app, with no in-app purchases and no full offline mode.
Source: https://docs.base44.com/Building-your-app/Mobile-experience

### Google sign-in and Google Calendar

Google sign-in is built in ("All new Base44 apps come with custom login pages built in… You can offer multiple ways for people to sign up, such as email, Google, or other providers"). Google login is enabled by default: either Base44's own credentials (login window branded base44.com) or a custom Google OAuth client ID/secret.
Source: https://docs.base44.com/Setting-up-your-app/Managing-login-and-registration

Google Calendar read/write is a built-in, no-code integration: "Sync events, schedules, and availability directly into your app… Build an appointment-booking app that checks a user's Google Calendar availability and automatically creates events with confirmations." Setup: "sign in with Google and approve access to your calendar; Base44 handles the sign-in flow and stores the credentials for you." Two modes: a **shared connector** (one account connected once, everyone using the app shares that connection and its data — with a "Read & write" option), which fits Luna because only the owner's calendar is booked; and an app-user connector (each tester connects their own calendar), which needs Builder+ (and Base44-managed OAuth needs Pro+). Caveats: whether shared Google connectors are available on the Free plan is **UNVERIFIED**; Google sign-in breaks inside Base44's store-wrapper apps (Google rejects embedded WebView sign-in, "Error 403: disallowed_useragent") — in a mobile browser / home-screen shortcut it should work normally.
Sources: https://base44.com/integrations/connectors/google-calendar ; https://docs.base44.com/Integrations/google-workspace-connectors

### Can a voice-receptionist app be built there?

**The web-form version of Luna — yes. A live phone-call-answering version — no evidence it can, and probably not.**

What exists: a built-in `InvokeLLM` (LLM calls from inside the app); `GenerateSpeech` (text to natural-sounding audio, returns a public MP3 URL; 29 languages; 1 integration credit per 50 characters, max 5,000 chars/call) — stored MP3 playback, **not live streaming voice**; a built-in `TranscribeAudio` integration; backend functions that "run on the server, so you can safely store API keys and secrets as environment variables" and can call external APIs (Groq has an OpenAPI-compatible API, but whether it works smoothly via Base44 backend functions is **UNVERIFIED**); scheduled/triggered automations (max 3 min/run, no closer than 5 min apart); cloud storage built in.
Sources: https://docs.base44.com/Integrations/built-in-integrations ; https://docs.base44.com/developers/references/sdk/getting-started/third-party-apis

What does **not** exist (no evidence found): **live phone-call handling.** Base44's catalog lists Twilio (SMS) only — "SMS alerts, reminders, and outreach flows" — not Twilio Voice / telephony. A comparison states it flatly: Base44 "Telephony/Call Handling: ✖ (Not applicable)". No Base44 telephony, SIP, PSTN, or live-voice-conversation feature was found in official docs. Whether a third-party voice agent (Vapi/Retell/ElevenLabs) could be wired in via backend-function webhooks is **UNVERIFIED** — and would be custom integration work a phone-only, non-technical owner could not maintain. Luna's Upstash Redis would be replaced by Base44's built-in database (entities, SDK/API only, no connection string, no Redis).
Sources: https://base44.com/use-cases/categories/entertainment ; https://top5-websitebuilders.com/articles/base44-vs-elevenlabs-which-ai-agent-is-best-for-small-businesses-in-2026/

### Free tier and costs beyond it

| Plan | Price | Message credits/mo (building) | Integration credits/mo (running the app) |
|---|---|---|---|
| Free | $0 | 25 (max 5/day) | 100 |
| Starter | $16/mo annual ($20 monthly) | 100 | 2,000 |
| Builder | $40/mo annual ($50 monthly) | 250 | 10,000 |
| Pro | $80/mo annual ($100 monthly) | 500 | 20,000 |
| Elite | $160/mo annual ($200 monthly) | 1,200 | 50,000 |

Two separate meters: message credits pay for building; integration credits pay for what the live app does (every email, LLM call, TTS generation, calendar sync, agent message) — end users of a published app spend the *owner's* integration credits. Free is a trial, not a usage plan (max 5 apps, 200 MB database per app, public apps only, no custom domain). When credits run out, actions that use them pause/fail — building pauses, integration-credit actions fail and the end user sees a generic error — and **there is no pay-as-you-go top-up**; the only fix is upgrading or waiting for the monthly reset. Unused credits expire each month. Scale check: an in-app agent on a strong model costs ~40 integration credits/message, so Starter's 2,000 credits ≈ 50 chat messages/month; `GenerateSpeech` is 1 credit per 50 characters. With 9 testers doing voice + brain + calendar actions, the Free plan's 100 integration credits would last hours, and even Starter could be tight — **Builder ($40–50/mo) is the realistic floor**, since backend functions are Builder-gated anyway. There is no per-action price preview ("there is no set credit amount used per message" — cost is calculated after the action runs).
Sources: https://base44.com/pricing ; https://www.totalum.app/blog/base44-pricing-2026 ; https://lerecul.com/en/tested-promises/base44-review-credits-pricing/

### Ownership, export, lock-in, stopping payment

Official claim (pricing-page FAQ): "Yes. You own everything you build on Base44: your code, your data, your users. Two-way GitHub sync exports the full source code to your own repo at any time. Nothing is locked to the platform." Independent technical analysis (from engineers who migrate apps off Base44) says: export (ZIP or GitHub) is **gated to the Builder plan or higher** — "the ability to leave is itself a paid feature." What you get is the React frontend code plus backend-function source files — but every data/auth/integration call goes through the closed-source `@base44/sdk`, which only works against base44.com: "The exported code won't run as a working app on its own without a replacement backend." The database comes out only as manual CSV exports per collection; no database dump, no connection string, no SQL schema export. Estimated migration effort: 1–3 weeks for a small app. If you stop paying: the workspace drops to the Free plan (up to 5 apps stay published, the rest are unpublished, not deleted); since backend functions are a Builder+ feature, a downgrade to Free would be expected to break any backend-function-dependent features (**UNVERIFIED** / inferred — no explicit official statement found).
Sources: https://base44.com/pricing ; https://www.base44devs.com/blog/base44-vendor-lock-in-deep-dive ; https://www.base44devs.com/migrate/base44-export-code-guide ; https://perpet.io/blog/base44-export-code/

### What users say

Most common praise: speed of the first working prototype ("a working prototype from the first prompt… That was mind-blowing"), and that a non-technical person can get a working app with logins, database and hosting and nothing else to set up. Best for internal tools, dashboards, booking apps, simple CRUD. Most common complaints: **credit burn / unpredictable costs** (the #1 pattern; Trustpilot ~3.1/5; no top-ups, no pre-flight cost preview); the AI breaks its own work in dead-end debug loops that eat credits; complex logic and multi-step workflows fail; lock-in / migration pain; poor customer support; a Feb 2026 outage of 2h 53m.
Sources: https://www.miaoda.io/reviews/base44 ; https://hedrick.io/post/base44-review ; https://allaboutcookies.org/base44-review

### Verdict — Base44

**Base44 is the wrong tool for rebuilding Luna as a voice receptionist.** The strongest reason is that it cannot do the "voice" half of the job at all: there is no native live phone-call handling — its Twilio integration is SMS-only, its text-to-speech produces stored MP3 files rather than a live conversational voice, and no source shows a working telephony path — so the defining feature of a receptionist would have to be bolted on through an external voice service via backend functions, which is exactly the kind of custom integration work a phone-only, non-technical owner cannot build or maintain. On top of that, the economics work against a small always-on service: Luna would need the Builder plan ($40/mo billed annually, $50 month-to-month) just to unlock backend functions and code export, every AI reply, spoken reply, and calendar action would burn metered integration credits with no top-up option and no price preview, and the app could never truly leave — the export is a React frontend that only runs against Base44's backend, so the owner would pay builder rent forever for a finished app. Base44 would be genuinely good at one narrower thing — quickly prototyping the booking-form half of Luna (form + AI brain + shared Google Calendar booking + spoken MP3 replies, all reachable from a phone browser) — but for the real Luna, rehosting the already-working Flask app preserves the Groq brain, the real calendar OAuth, and a portable codebase at a fraction of the ongoing cost, and Base44 solves none of the hard parts while adding a meter and a lock-in.

---

## The HTML + Python hybrid on the phone

Ting's words: "A html and python hybrid living on a phone using phones resources." He means: HTML on the front (installs on the phone like Willow) + Python doing the brain work, and the app living on the phone using the phone itself — its microphone and speaker for voice, its storage, its connection — rather than living on a distant server he has to configure. Three realistic shapes were evaluated.

### (a) Split hybrid — PWA frontend + small hosted Python backend ★ THE SHAPE

**What "uses the phone's resources" concretely covers:** the microphone via `getUserMedia` / `MediaRecorder` (audio is captured on the phone and sent to the backend for Whisper transcription); the speaker via Web Audio / `<audio>` (plays the Groq TTS reply); storage via Cache Storage (the app shell and assets live on the phone after install) and IndexedDB (drafts/history); the connection via `fetch`; installation via the manifest (home-screen icon, splash, standalone window).

**What breaks:** no offline brain, TTS, or booking — Groq and Google Calendar are cloud APIs, so with no network there is no booking; no background work (no push without a server + subscription); no live telephony (Luna handles call *details* through a web form, not live calls).

**How the Groq API key is handled:** it stays server-side, in the hosted backend's env vars. It never appears in client-side code. This is the only shape of the three where the key is safe by construction.

**Verdict:** this is the shape that delivers "lives on the phone, uses the phone" for a non-technical owner — and it is what Luna already is in structure (`driver.html` served by the Flask backend; mic/speaker/storage through the browser). The work is the PWA retrofit + OAuth, not a new architecture.

### (b) On-device Python — PWA + Pyodide / WebAssembly

**What it covers:** Python compute on the phone itself (small logic can run locally), storage via IndexedDB / OPFS. **What breaks:** the microphone and speaker are JavaScript APIs — Python reaches them only through JS bridges (browser kernels run in Web Workers, which cannot reach the Web Audio API; audio needs main-thread bridges). Pyodide is a ~10MB+ download (10–30MB) with a 4–5 second startup, runs 3–5× slower than native Python, has no subprocess/shell, a limited filesystem (browser storage only, not the phone's files), and limited support for packages with C extensions.

**How the Groq API key is handled:** it cannot be kept safe. Pyodide code is downloaded to the phone, so any API key in it is visible to anyone who opens the app. The Groq key would still need a tiny hosted proxy (which defeats "no server") or a per-user key (each of the 9 testers would need their own Groq key — absurd). The Google refresh token must not live in client-side code either, for the same reason.

**Verdict:** wrong shape for Luna. It adds a 10MB+ download and real complexity, solves nothing (the cloud APIs still need a server or exposed keys), and the mic/speaker still go through JavaScript anyway.

Sources: https://github.com/prosperousheart/basics-boot-camp/blob/HEAD/docs/tutorials/understanding-webassembly-and-pyodide.md ; https://www.ancisoft.com/blog/webassembly-javascript-and-other-languages/ ; https://pypi.org/project/browseraudio/

### (c) Packaged Python Android app — Briefcase / Kivy

**What it covers:** a real APK/AAB, a native install, full device APIs (mic, speaker, storage) through Python. **What breaks:** it needs a laptop plus Android Studio/JDK plus signing keys, a Play Store review for store distribution (or sideloading with "unknown sources" and a manual reinstall for every update); the whole Flask server would need rewriting as a Toga/Kivy GUI app (a full rebuild, not a retrofit); Google OAuth in a native app needs a different flow (custom-scheme redirect / AppAuth); updates are rebuild + reinstall, not a deploy.

**How the Groq API key is handled:** a key baked into the APK is extractable — the same client-side key problem as (b). It would still need a hosted proxy to be safe.

**Verdict:** the most "lives on the phone" of the three, but the worst fit for this owner — heavy builder tooling, store/sideload friction, full rewrite, and it still does not solve the key problem. Not recommended.

Sources: https://copyprogramming.com/howto/python-can-i-make-android-app-using-python (Briefcase 0.3.16+, `briefcase new/create/build/run/package android`, Android Studio/JDK, signed APK/AAB)

### Hybrid judgment

Shape (a) is the one that delivers "lives on the phone, uses the phone" for a non-technical owner. The PWA shell and cached assets live on the phone; the microphone, speaker, storage, and connection are the phone's; the Python brain stays hosted where the keys are safe. Luna is already shape (a) in structure — the rebuild is the PWA retrofit + OAuth, not a new architecture.

---

## What changes in the app (OAuth + PWA)

This part is host-independent — it is needed on whichever host is chosen.

### Google OAuth 2.0 (replaces the service-account key file)

- **Library:** `google-auth-oauthlib` + `google-auth` + `google-api-python-client` (the stack in Google's own Flask web-server guide). Do not use Flask-Dance for a new build (older, less maintained) or `oauth2client` (deprecated).
  Source: https://developers.google.com/identity/protocols/oauth2/web-server
- **Flow:** `/authorize` builds a `Flow` and redirects the user to Google (`https://accounts.google.com/o/oauth2/v2/auth`); the user taps Allow; Google redirects to `/oauth2callback?code=…`; the app verifies the `state` value, exchanges the code (`flow.fetch_token`), and checks the granted scopes actually include Calendar.
- **Refresh token (the critical detail):** request `access_type='offline'` **and** `prompt='consent'` on the first authorization — that is what makes Google return a refresh token. Store only the refresh token server-side in Upstash (key e.g. `google:oauth:<user>` → JSON with refresh token, client ID/secret, token URI, scopes). Never store it in the browser or in a file the owner must deliver. Do not re-run the login flow routinely — Google caps refresh tokens at 100 per account per client ID, and creating a new one silently invalidates the oldest.
- **Booking path:** load the refresh token from Upstash → `Credentials(refresh_token=…, client_id=…, client_secret=…, token_uri='https://oauth2.googleapis.com/token')` → `creds.refresh(Request())` if expired → `build('calendar', 'v3', …)` → `events().insert(calendarId='primary', …)`. On `RefreshError` / `invalid_grant`, delete the stored token and show a "Connect Google Calendar" button that re-runs the flow.
- **Scope:** `https://www.googleapis.com/auth/calendar.events` (view/edit events on existing calendars). Do not use the broader `https://www.googleapis.com/auth/calendar` unless Luna must manage whole calendars.
- **Google Cloud setup (builder's job):** in the GCP project, enable the Google Calendar API; configure the OAuth consent screen (Google Auth Platform, user type **External**, app name + support email, add the `calendar.events` scope with a justification); create the OAuth client (Web application) with the exact Authorized redirect URI, e.g. `https://<host>/oauth2callback` (scheme, case and trailing slash must match exactly — mismatch gives `redirect_uri_mismatch`); **copy the Client ID and Client Secret immediately at creation** into the host's env vars (the secret may not be retrievable later — **UNVERIFIED** whether it stays viewable, so copy at creation regardless).
- **Testing vs Production — the pilot trap:** in Testing mode only up to 100 listed test users can sign in, **and refresh tokens expire after 7 days**. For unattended booking the app must be switched to **In production** before the pilot. In Production while unverified, testers see a "Google hasn't verified this app" warning and click Advanced → Go to continue; that is expected behaviour for a personal-use app under 100 users, not an error. `calendar.events` is a **sensitive** scope (verification typically 3–5 business days, brand verification only — not the paid restricted-scope security assessment), and verification is optional for the pilot.
  Sources: https://developers.google.com/identity/protocols/oauth2/web-server ; https://support.google.com/cloud/answer/15549945?hl=ja ; https://support.google.com/cloud/answer/13464323 ; https://developers.google.com/identity/protocols/oauth2/production-readiness/sensitive-scope-verification
- **What Ting does:** opens the app link, taps Allow on Google's consent screen (and Advanced → Go to continue past the unverified-app warning, if shown). One tap, once. No files, no pasting.
- **What the builder does:** everything above, plus briefing the 9 testers on the exact taps.

### Phone installability (PWA) — the Willow retrofit for Luna

- **What the app needs:** HTTPS (host-provided on all candidates), a `manifest.json` (`name`, `short_name`, 192px + 512px icons, `start_url`, `display: standalone`) served from the root path (`/manifest.json`) and linked from every page, and a service worker served from the root (`/sw.js`) — root scope matters, not `/static/sw.js`. Flask routes can serve both from the static folder.
- **Luna's concrete retrofit (from the actual code):** `index.py` serves the single-page `driver.html` via `send_file` at `/` (no templates dir). Add two routes, `/manifest.json` and `/sw.js` (send_file, with `Cache-Control: no-cache` on the worker); add `manifest.json` + `sw.js` + 192/512px icons, modelled on Willow's files; add the manifest link + service-worker registration to `driver.html`; `display: standalone`, `start_url: /`. The service worker **must** be network-first for `/api/*` (never cache API calls or auth state) and cache-first only for static assets — Willow's `sw.js` is the template, with the `/api/*` rule added.
- **Install criteria (Chrome on Android):** HTTPS + manifest with name/icons/start_url/display; the user must have tapped the page at least once and spent at least 30 seconds on it, at any time. A service worker is no longer strictly required for install from the menu since Chrome 108, but is still needed for offline behaviour and the automatic install prompt.
- **What Ting/testers see:** Chrome menu → "Install app" / "Add to Home screen" → the app gets its own home-screen icon, splash screen and standalone window (a WebAPK on Android) — no address bar, no tabs. It is **not** a Play Store app: no store listing, no Play review, no native push without a service worker.
- **Builder cautions:** keep the first service worker minimal (offline fallback page + static-asset cache only, network-first for HTML/API calls) so it never serves stale pages or stale auth state; `Cache-Control: no-cache` on the worker itself. Verify in Chrome DevTools → Application → Manifest, which lists every failing criterion.
  Sources: https://web.dev/articles/install-criteria?hl=en&authuser=6 ; https://web.dev/learn/pwa/web-app-manifest?hl=en&authuser=00 ; https://developer.chrome.com/blog/update-install-criteria

### Upstash Redis — carries over to any host

- Upstash's REST API is plain HTTPS: any server that can make HTTPS requests can use `UPSTASH_REDIS_REST_URL` + `UPSTASH_REDIS_REST_TOKEN`. The existing database and credentials carry over unchanged when the host changes; only the env vars need re-entering on the new host. (Legacy Vercel KV names `KV_REST_API_URL` / `KV_REST_API_TOKEN` are the same store migrated from Vercel KV; prefer `UPSTASH_*` for new setups, accept both.)
- Free tier: $0, 250 MB, 500,000 commands/month, 10 GB bandwidth — orders of magnitude above Luna's use (one refresh token + a handful of keys, a few commands per booking).
- **Gotcha:** free-tier databases are archived after a minimum of 30 days of inactivity (warning emails first; data is backed up and restorable from the console). Any booking activity touches the DB, so a live pilot will not go idle; if the app ever sits unused for a month, add a tiny periodic keep-alive (a `PING` is not billed).
  Sources: https://upstash.com/docs/redis/features/restapi ; https://upstash.com/docs/redis/overall/billing ; https://upstash.com/docs/redis/help/faq

---

## Concrete next steps (in order)

1. **The decision (one word from Ting):** Vercel back, Willow-style — or a new home (PythonAnywhere)? Everything else about the rebuild (OAuth, PWA) stays exactly the same either way.
2. **Pre-commit live checks (before rebuilding anything):**
   a. **Vercel Hobby timeout (decisive for the top pick):** run the slowest Luna request (TTS + brain + booking) against the deployed Vercel app and confirm it finishes inside the Hobby function timeout (secondary sources say 10s).
   b. **PythonAnywhere Upstash test (decisive for the new-home pick):** create the free Beginner account, open a Bash console, run `curl -s https://<id>.upstash.io/ping -H "Authorization: Bearer <token>"` (or any GET against the REST URL). If it answers, Upstash works on free. If it is blocked, request a whitelist addition from PythonAnywhere support — or fall back to Render.
   c. **Render card check (decisive for the runner-up):** at signup, confirm whether a card is demanded to deploy a free web service. If yes, Render is out.
3. **Code changes (builder):** add `/authorize` and `/oauth2callback` routes (`google-auth-oauthlib`, `access_type='offline'`, `prompt='consent'`, state check, scope check); store the refresh token in Upstash server-side; rewrite the booking path to use `Credentials` + refresh-on-demand with `invalid_grant` handling (delete stored token, show "Connect Google Calendar"); remove the service-account JSON path. Add PWA: `manifest.json` + `sw.js` + 192/512px icons (modelled on Willow's), root routes `/manifest.json` and `/sw.js` in `index.py`, manifest link + service-worker registration in `driver.html`, `display: standalone`, network-first for `/api/*`. Add `requirements.txt` entries for the Google libraries. Keep the Groq key and Google client ID/secret as env vars.
4. **Google Cloud setup (builder, in the existing project `luna-test-514021`):** enable the Calendar API; consent screen (External, app name + support email, `calendar.events` scope with justification); create the Web-application OAuth client with the exact production redirect URI for the chosen host; copy Client ID + Secret immediately; add the 9 tester emails as test users; **publish the app (In production)** before the pilot so refresh tokens do not expire in 7 days.
5. **Deploy (builder):**
   - *Vercel path:* browser pastes the Google client ID/secret into the `luna-test-driver` project env vars (Groq key is already set), redeploys Production, verifies `/health`.
   - *PythonAnywhere path:* create the web app, virtualenv from `requirements.txt`, point the WSGI file at the app, set env vars (Groq key, Google client ID/secret, Upstash URL/token) in the Web-tab env vars or `.env`, `git pull` the repo, Reload. (Render fallback — connect the GitHub repo, plan Free, paste env vars, deploy.)
6. **Ting's step (one tap, when free):** open the app link, sign in with Google, tap Allow (and Advanced → Go to continue if the unverified-app warning appears). Then install it to his home screen from the Chrome menu, and a test booking is made and verified on his calendar.
7. **Verify end-to-end (builder):** booking creates a real calendar event; sessions persist in Upstash; app opens from the Android home screen after install; cold/wake behaviour acceptable; slowest request inside the function timeout.
8. **Cleanup (builder, only after OAuth is verified working):** delete both service-account keys for `luna-test-driver` (key IDs `8e534b066af5` and `b674f2078718` — both files are lost, neither is needed once OAuth works). Keep the Groq key and the Upstash database as-is.

## Open items to verify live before committing

- **Vercel:** does the slowest Luna request (TTS + brain + booking) finish inside the Hobby function timeout (secondary sources say 10s)? And Ting's agreement to revisit Vercel.
- PythonAnywhere: is `<id>.upstash.io` reachable from a free console (the decisive test)? Is a card really not required at signup (official page could not be read)? Is the Web-tab env-var UI present on free (third-party guides say yes; official confirmation **UNVERIFIED**)?
- Render: is a card demanded to deploy a free web service (sources conflict)? Can a free web service take a custom domain (sources conflict; not needed for OAuth/PWA)?
- Replit: is Starter still offered to a new account, and does signup demand a card?
- Railway: have signups reopened after the October 2026 env-var breach?
- Google: Calendar API quota numbers (secondary sources cite 10,000 req/min/project; **UNVERIFIED** — read the project's own quota page; pilot volume makes this non-blocking). Confirm `events.insert` accepts exactly the `calendar.events` scope on the official reference page.
- PWA: end-to-end install test on the chosen host's URL (no live deploy was done in this research-only pass).
- Design judgement (not doc-verified): whether to encrypt the refresh token at rest in Upstash (e.g. Fernet with a server-side key in host env vars) — prudent, but no Google requirement for it was found.

---

## What Ting does vs what the builder does

| | Ting | Builder (Ziggy + browser/crews) |
|---|---|---|
| Host account | Nothing (no signup by him) | Create the free account (new home only), deploy, set env vars |
| Secrets | Nothing to paste, no files | Paste Groq key + Google client ID/secret (short strings, shown on screen) into the host dashboard |
| Google sign-in | Tap Allow once (+ Advanced → Go to continue if the warning appears) | Create the OAuth client, consent screen, test users; publish to Production |
| Phone use | Open the link, Chrome menu → Install app / Add to Home screen | Add manifest + service worker to the Flask app (modelled on Willow's) |
| Monthly | Vercel: nothing. PythonAnywhere: one tap, "run for another month," when the warning email arrives | Vercel: nothing. PythonAnywhere: remind him if the renewal is missed; re-enable if the site goes dark |
| Old keys | Nothing | Delete both service-account keys after OAuth is verified |
