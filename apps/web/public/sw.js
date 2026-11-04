const base = '/finevent/';
const cachePrefix = 'finevent-shell-';
let activeCache;

self.addEventListener('install', (event) => {
  event.waitUntil(
    (async () => {
      const response = await fetch(`${base}precache.json`, { cache: 'no-store' });
      if (!response.ok) throw new Error('Missing precache manifest');
      const manifest = await response.json();
      const cacheName = cachePrefix + manifest.version;
      const cache = await caches.open(cacheName);
      await cache.addAll(manifest.urls);
      activeCache = cacheName;
    })(),
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    (async () => {
      // Persist the cache name with this worker, without trusting the latest network manifest.
      const names = (await caches.keys()).filter((name) => name.startsWith(cachePrefix));
      activeCache ??= names.at(-1);
      await Promise.all(
        names.filter((name) => name !== activeCache).map((name) => caches.delete(name)),
      );
      await self.clients.claim();
    })(),
  );
});

self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);
  if (
    event.request.method !== 'GET' ||
    url.origin !== self.location.origin ||
    !url.pathname.startsWith(base) ||
    url.pathname.startsWith(`${base}api/`) ||
    url.pathname.endsWith('/healthz')
  )
    return;
  if (event.request.mode === 'navigate') {
    event.respondWith(
      fetch(event.request).catch(
        async () => (await caches.match(`${base}index.html`)) ?? Response.error(),
      ),
    );
  } else if (
    url.pathname.startsWith(`${base}assets/`) ||
    /\/icon[^/]*\.(svg|png)$/.test(url.pathname)
  ) {
    event.respondWith(caches.match(event.request).then((cached) => cached ?? fetch(event.request)));
  }
});

function database() {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open('finevent-receipts', 1);
    request.onupgradeneeded = () => request.result.createObjectStore('pending', { keyPath: 'key' });
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}
async function pending(action, value) {
  const db = await database();
  try {
    return await new Promise((resolve, reject) => {
      const tx = db.transaction('pending', 'readwrite');
      const store = tx.objectStore('pending');
      const request =
        action === 'put'
          ? store.put(value)
          : action === 'delete'
            ? store.delete(value)
            : store.getAll();
      tx.oncomplete = () => resolve(request.result);
      tx.onerror = () => reject(tx.error);
      tx.onabort = () => reject(tx.error);
    });
  } finally {
    db.close();
  }
}
async function flushReceipts() {
  for (const record of await pending('get')) {
    if (record.created < Date.now() - 24 * 60 * 60 * 1000) {
      await pending('delete', record.key);
      continue;
    }
    try {
      const response = await fetch(`${base}api/v1/push/receipt`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(record.body),
        signal: AbortSignal.timeout(10000),
      });
      if (response.ok || response.status === 404) await pending('delete', record.key);
    } catch {
      /* Retained locally; retry on next push, click, sync, or app open. */
    }
  }
}
async function acknowledge(data, kind) {
  await pending('put', {
    key: `${data.id}-${kind}`,
    created: Date.now(),
    body: { id: data.id, token: data.receipt_token, kind },
  });
  await flushReceipts();
  if ('sync' in self.registration) {
    try {
      await self.registration.sync.register('finevent-receipts');
    } catch {
      /* App-open retry is also available. */
    }
  }
}
self.addEventListener('push', (event) => {
  event.waitUntil(
    (async () => {
      if (!event.data) return;
      const data = event.data.json();
      if (
        !Number.isSafeInteger(data.id) ||
        typeof data.receipt_token !== 'string' ||
        data.expires * 1000 < Date.now()
      )
        return;
      await self.registration.showNotification(data.title, {
        body: data.body,
        icon: `${base}icon-192.png`,
        badge: `${base}icon-192.png`,
        tag: `finevent-${data.id}`,
        data,
        renotify: false,
      });
      await acknowledge(data, 'received');
    })(),
  );
});
async function openNotificationTarget(target) {
  try {
    // Invoke while the notification gesture is fresh. Chrome can route this into the installed PWA.
    const opened = await self.clients.openWindow(target.href);
    if (opened) {
      // The window has already opened; a focus rejection must not create a second window.
      return await opened.focus().catch(() => opened);
    }
  } catch {
    // An existing app window may still be usable when opening a new context is unavailable.
  }
  const clients = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
  for (const client of clients) {
    const url = new URL(client.url);
    if (url.origin !== self.location.origin || !url.pathname.startsWith(base)) continue;
    try {
      // Bring the app forward before a potentially slow or failing network navigation.
      const focused = await client.focus();
      return await focused.navigate(target.href).catch(() => focused);
    } catch {
      // A window can disappear between matchAll and focus; try the next app window.
    }
  }
  throw new Error('Could not open finevent from the notification');
}
self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const data = event.notification.data;
  if (typeof data?.url !== 'string') return;
  let target;
  try {
    target = new URL(data.url, self.location.origin);
  } catch {
    return;
  }
  if (target.origin !== self.location.origin || !target.pathname.startsWith(base)) return;
  event.waitUntil(openNotificationTarget(target));
  // A click is evidence of user interaction even if window activation fails. Keep the two independent.
  if (Number.isSafeInteger(data.id) && typeof data.receipt_token === 'string')
    event.waitUntil(acknowledge(data, 'clicked'));
});
self.addEventListener('message', (event) => {
  if (event.data === 'SKIP_WAITING') event.waitUntil(self.skipWaiting());
  if (event.data === 'FLUSH_RECEIPTS') event.waitUntil(flushReceipts());
});
self.addEventListener('sync', (event) => {
  if (event.tag === 'finevent-receipts') event.waitUntil(flushReceipts());
});
