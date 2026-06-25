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
        data: data.url || '/'
    };
    
    event.waitUntil(
        self.registration.showNotification(title, options)
    );
});

// Notification click handler
self.addEventListener('notificationclick', (event) => {
    event.notification.close();
    event.waitUntil(
        clients.openWindow(event.notification.data)
    );
});
