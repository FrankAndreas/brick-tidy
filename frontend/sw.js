// Service worker: makes Brick Tidy installable and lets the app shell open without
// network. App files are fetched network-first so updates show up immediately;
// API calls and photos are never cached.
const CACHE = "brick-tidy-v1";
const SHELL = ["./", "index.html", "app.js", "style.css", "icon.svg", "manifest.webmanifest"];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(caches.keys()
    .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  const isShell = event.request.method === "GET" && url.origin === self.location.origin
    && !url.pathname.startsWith("/api/") && !url.pathname.startsWith("/media/");
  if (!isShell) return;
  event.respondWith(
    fetch(event.request)
      .then((response) => {
        const copy = response.clone();
        caches.open(CACHE).then((cache) => cache.put(event.request, copy));
        return response;
      })
      .catch(() => caches.match(event.request).then((hit) => hit ?? caches.match("index.html"))),
  );
});
