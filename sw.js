/* Luna service worker — PWA shell cache, modelled on Willow Tales.
 *
 * Rules (do not weaken):
 *  - /api/* is NETWORK-ONLY. Never cache API calls or auth state —
 *    a cached turn, booking or calendar status would be stale at
 *    best and a wrong booking at worst.
 *  - App shell + icons/manifest are network-first with a cache
 *    fallback, so the page still opens when the connection drops.
 *  - The worker itself is served with Cache-Control: no-cache (see
 *    index.py), so an updated worker is picked up on the next visit.
 */
const CACHE = "luna-driver-v1";
const CORE = ["/", "/manifest.json", "/icon-192.png", "/icon-512.png"];

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
  // API calls and auth state: network only, never cached, never served stale.
  if (url.pathname.startsWith("/api/") || url.pathname === "/authorize"
      || url.pathname === "/oauth2callback") {
    return; // no respondWith — the request goes to the network as-is.
  }
  // Everything else: network-first, cache fallback.
  e.respondWith(
    fetch(e.request).then(res => {
      if (e.request.method === "GET" && res.ok) {
        const copy = res.clone();
        caches.open(CACHE).then(c => c.put(e.request, copy));
      }
      return res;
    }).catch(() => caches.match(e.request).then(hit => hit || caches.match("/")))
  );
});
