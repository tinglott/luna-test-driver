/* api-config.js — the ONE place that knows where Luna's Python backend lives.
 *
 * Hybrid split: GitHub Pages hosts these static pages; the Python backends
 * (Vercel + PythonAnywhere) do the brain, the calendar, and the TTS.
 * Default to Vercel (primary). If a call fails at the NETWORK level
 * (the host is down, or returns 502/503/504), retry once against the
 * PythonAnywhere backend (second home). A 4xx answer is a real answer
 * (e.g. 403 = not an invited tester) — it is never retried elsewhere.
 * The tester gate is enforced BY THE BACKEND on every call; these pages
 * only pass the tester name through (X-Tester header + body field).
 */
const LUNA_API = {
  primary: "https://luna-test-driver.vercel.app",
  fallback: "https://tinglott.pythonanywhere.com",
};

const $ = id => document.getElementById(id);
let _audioEl = null;

function tester() {
  const v = ($('tester') ? $('tester').value : '') || '';
  return (v || '').trim() || 'anonymous';
}
function headers() { return { 'Content-Type': 'application/json', 'X-Tester': tester() }; }

function _shouldFallback(res, err) {
  if (err) return true;                    // network failure (host down, DNS, offline)
  return res && (res.status === 502 || res.status === 503 || res.status === 504);
}

async function _doFetch(base, path, body, method) {
  return fetch(base + path, {
    method: method || (body ? 'POST' : 'GET'),
    headers: headers(),
    body: body ? JSON.stringify({ ...body, tester: tester() }) : undefined,
  });
}

/* Call the backend. Returns the parsed JSON, or throws Error(data.error || HTTP n). */
async function api(path, body, method) {
  let res, triedFallback = false;
  try {
    res = await _doFetch(LUNA_API.primary, path, body, method);
  } catch (e) {
    res = await _doFetch(LUNA_API.fallback, path, body, method);
    triedFallback = true;
  }
  if (!triedFallback && _shouldFallback(res, null)) {
    res = await _doFetch(LUNA_API.fallback, path, body, method);
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || ('HTTP ' + res.status));
  return data;
}

/* GET with no body (health, status lists). Same fallback rule. */
async function apiGet(path) { return api(path, null, 'GET'); }

/* Multipart POST (voice transcription). Same fallback rule; the tester
 * travels in the X-Tester header (FormData carries no JSON body). */
async function apiForm(path, formData) {
  let res, triedFallback = false;
  const go = base => fetch(base + path, { method: 'POST', headers: { 'X-Tester': tester() }, body: formData });
  try {
    res = await go(LUNA_API.primary);
  } catch (e) {
    res = await go(LUNA_API.fallback);
    triedFallback = true;
  }
  if (!triedFallback && _shouldFallback(res, null)) {
    res = await go(LUNA_API.fallback);
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || ('HTTP ' + res.status));
  return data;
}

function playAudio(url) {
  // url is a data:audio/wav;base64,... URI from the turn JSON (Groq Orpheus).
  if (!url) return; // TTS failed server-side; the text reply is shown anyway.
  if (_audioEl) { _audioEl.pause(); }
  _audioEl = new Audio(url);
  _audioEl.play().catch(() => { /* browser may need a tap first; text is shown anyway */ });
}
function escapeHtml(s) { return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }

/* Tester name: ?tester= (or ?t=) pre-fills, then localStorage remembers it.
 * Call initTester() on every page that has a #tester input. */
function initTester() {
  const el = $('tester');
  if (!el) return;
  const params = new URLSearchParams(location.search);
  const fromLink = params.get('tester') || params.get('t');
  const saved = localStorage.getItem('artisy-tester');
  el.value = fromLink || saved || '';
  el.addEventListener('change', () => localStorage.setItem('artisy-tester', el.value.trim()));
}

/* Backend health line for the footer of each page (shows which host answered). */
async function showBackendHealth(elId) {
  const el = $(elId);
  if (!el) return;
  try {
    const h = await apiGet('/health');
    el.innerHTML = 'Backend: <span class="ok">online</span> — store ' + (h.store_ok ? 'ok' : 'DOWN') +
      ' · calendar ' + (h.calendar_connected ? 'connected ✓' : 'not connected yet') +
      ' · voice ' + (h.tts_worker ? 'ready' : 'text-only');
  } catch (e) {
    el.innerHTML = 'Backend: <span class="bad">unreachable</span> — ' + escapeHtml(e.message);
  }
}

/* PWA: register the shell service worker (same-origin shell files only;
 * API calls always go to the network — never cached, never served stale). */
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('sw.js').catch(() => { /* offline shell is a bonus, not a requirement */ });
  });
}
