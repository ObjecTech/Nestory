// Cache only public, synthetic application assets. Never cache private pages or API data.
const CACHE_NAME = "edge-monitor-public-v1";
const ASSETS = ["/app.css", "/app.js", "/assets/icons/icon-192.png", "/assets/icons/icon-512.png"];
self.addEventListener("install", event => { self.skipWaiting(); });
self.addEventListener("activate", event => { event.waitUntil(caches.keys().then(keys => Promise.all(keys.map(key => caches.delete(key))))); self.clients.claim(); });
self.addEventListener("fetch", event => {
  const url = new URL(event.request.url);
  if (event.request.method !== "GET" || url.origin !== self.location.origin || !ASSETS.includes(url.pathname)) return;
  event.respondWith(fetch(event.request));
});
