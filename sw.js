/* Luna Pages service worker — shell cache only, modelled on Willow Tales.
 *
 * Rules (do not weaken):
 *  - API calls are NEVER intercepted. The pages call the Python backend
 *    cross-origin; this worker only handles same-origin shell requests.
 *    A cached turn, booking or calendar status would be stale at best
 *    and a wrong booking at worst — so the worker never touches them.
 *  - App shell is network-first with a cache fallback, so the pages
 *    still open when the connection drops.
 *  - The worker itself is served with Cache-Control: no-cache by
 *    GitHub Pages defaults for sw.js? No — Pages does not set that, so
 *    the worker version in CACHE is bumped on every change instead.
 */
const CACHE = "luna-pages-v1";
const CORE = ["./", "./index.html", "./talk.html", "./calendar.html",
  "./callback.html", "./desk.html", "./style.css", "./api-config.js",
  "./manifest.json"];

self.addEventListener("install", e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(CORE)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", e => {
  e.waitUntil(caches.keys().then(keys =>
    Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
  ).then(() => self.clients.claim()));
});

self.addEventListener("fetch", e => {
  const url = new URL(e.request.url);
  // Only same-origin shell requests. Everything else (including every
  // call to the Python backend) goes to the network untouched.
  if (url.origin !== self.location.origin) return;
  if (e.request.method !== "GET") return;
  e.respondWith(
    fetch(e.request).then(res => {
      if (res.ok) {
        const copy = res.clone();
        caches.open(CACHE).then(c => c.put(e.request, copy));
      }
      return res;
    }).catch(() => caches.match(e.request).then(hit => hit || caches.match("./index.html")))
  );
});
