// Install event - skip waiting so the SW activates immediately
self.addEventListener('install', () => {
    self.skipWaiting();
});

// Activate event - take control of all clients immediately
self.addEventListener('activate', (event) => {
    event.waitUntil(self.clients.claim());
});

// Push notification handler
self.addEventListener('push', (event) => {
    const data = event.data ? event.data.json() : {};
    const title = data.title || 'Life Organiser';
    const options = {
        body: data.body || 'You have a task reminder',
        icon: '/static/icons/icon-192.png',
        badge: '/static/icons/icon-192.png',
        data: data.url || '/',
        tag: 'organiser',
        renotify: true,
        vibrate: [200, 100, 200],
    };

    event.waitUntil(
        self.registration.showNotification(title, options)
    );
});

// Notification click handler - focus an already-open window if there is one,
// otherwise open a new one.
self.addEventListener('notificationclick', (event) => {
    event.notification.close();
    const targetUrl = event.notification.data || '/';

    event.waitUntil(
        clients.matchAll({ type: 'window', includeUncontrolled: true }).then((clientList) => {
            for (const client of clientList) {
                if ('focus' in client) {
                    if ('navigate' in client && client.url !== targetUrl) {
                        client.navigate(targetUrl).catch(() => {});
                    }
                    return client.focus();
                }
            }
            if (clients.openWindow) {
                return clients.openWindow(targetUrl);
            }
        })
    );
});

// Re-subscribe if the push service (e.g. FCM on Android) rotates the
// subscription out from under us. Without this the stored subscription
// silently dies and reminders stop arriving.
self.addEventListener('pushsubscriptionchange', (event) => {
    event.waitUntil(
        (async () => {
            try {
                const res = await fetch('/push/public-key');
                if (!res.ok) return;
                const { publicKey } = await res.json();

                const newSub = await self.registration.pushManager.subscribe({
                    userVisibleOnly: true,
                    applicationServerKey: urlBase64ToUint8ArraySync(publicKey),
                });

                const key = newSub.getKey('p256dh');
                const auth = newSub.getKey('auth');
                await fetch('/push/subscribe', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        endpoint: newSub.endpoint,
                        p256dh: btoa(String.fromCharCode(...new Uint8Array(key))),
                        auth: btoa(String.fromCharCode(...new Uint8Array(auth))),
                    }),
                });
            } catch (err) {
                // Nothing we can do from here but log; the user can
                // re-subscribe manually via the bell if this fails.
                console.error('pushsubscriptionchange re-subscribe failed:', err);
            }
        })()
    );
});

// Duplicated from app/static/js/app.js since service workers can't import it.
function urlBase64ToUint8ArraySync(base64String) {
    const padding = '='.repeat((4 - base64String.length % 4) % 4);
    const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
    const raw = atob(base64);
    return Uint8Array.from([...raw].map(c => c.charCodeAt(0)));
}
