// Push notification subscription management
async function urlBase64ToUint8Array(base64String) {
    const padding = '='.repeat((4 - base64String.length % 4) % 4);
    const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
    const raw = atob(base64);
    return Uint8Array.from([...raw].map(c => c.charCodeAt(0)));
}

async function getSubscriptionPayload(sub) {
    const key = sub.getKey('p256dh');
    const auth = sub.getKey('auth');
    return {
        endpoint: sub.endpoint,
        p256dh: btoa(String.fromCharCode(...new Uint8Array(key))),
        auth: btoa(String.fromCharCode(...new Uint8Array(auth))),
    };
}

async function initNotificationButton() {
    const btn = document.getElementById('notify-toggle');
    if (!btn || !('serviceWorker' in navigator) || !('PushManager' in window)) {
        btn?.remove();
        return;
    }

    const reg = await navigator.serviceWorker.ready;
    const existing = await reg.pushManager.getSubscription();
    btn.dataset.subscribed = existing ? 'true' : 'false';
    updateNotifyButton(btn, existing ? 'subscribed' : Notification.permission);

    btn.addEventListener('click', async () => {
        try {
            const sub = await reg.pushManager.getSubscription();
            if (sub) {
                await sub.unsubscribe();
                await fetch('/push/unsubscribe', { method: 'DELETE', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(await getSubscriptionPayload(sub)) });
                btn.dataset.subscribed = 'false';
                updateNotifyButton(btn, 'default');
            } else {
                if (Notification.permission === 'denied') {
                    alert('Notifications are blocked in your browser. Check your site settings to allow them.');
                    return;
                }
                const res = await fetch('/push/public-key');
                if (!res.ok) throw new Error(`Failed to fetch public key: ${res.status}`);
                const { publicKey } = await res.json();
                const newSub = await reg.pushManager.subscribe({
                    userVisibleOnly: true,
                    applicationServerKey: await urlBase64ToUint8Array(publicKey),
                });
                await fetch('/push/subscribe', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(await getSubscriptionPayload(newSub)) });
                btn.dataset.subscribed = 'true';
                updateNotifyButton(btn, 'granted');
            }
        } catch (err) {
            console.error('Notification toggle failed:', err);
            alert(`Notification error: ${err.message}`);
        }
    });
}

function updateNotifyButton(btn, state) {
    const on = state === 'subscribed' || state === 'granted';
    btn.title = on ? 'Disable notifications' : 'Enable notifications';
    btn.setAttribute('aria-label', btn.title);
    btn.classList.toggle('notify-active', on);
}

// Service worker registration + notification button init
if ('serviceWorker' in navigator && 'PushManager' in window) {
    navigator.serviceWorker.register('/sw.js')
        .then(() => initNotificationButton())
        .catch(err => console.error('SW registration failed:', err));
} else {
    document.addEventListener('DOMContentLoaded', () => {
        document.getElementById('notify-toggle')?.remove();
    });
}

// Light/dark theme toggle
document.addEventListener('DOMContentLoaded', () => {
    const btn = document.getElementById('theme-toggle');
    if (!btn) return;

    btn.addEventListener('click', () => {
        const dark = document.documentElement.getAttribute('data-theme') !== 'dark';
        if (dark) {
            document.documentElement.setAttribute('data-theme', 'dark');
            localStorage.setItem('theme', 'dark');
        } else {
            document.documentElement.removeAttribute('data-theme');
            localStorage.setItem('theme', 'light');
        }
    });
});

// Alpine.js countdown component for deadline tasks
document.addEventListener('alpine:init', () => {
    Alpine.data('countdown', (deadline) => ({
        timeRemaining: '',
        deadlineDate: new Date(deadline),
        
        init() {
            this.updateCountdown();
            setInterval(() => this.updateCountdown(), 1000);
        },
        
        updateCountdown() {
            const now = new Date();
            const diff = this.deadlineDate - now;
            
            if (diff <= 0) {
                this.timeRemaining = 'OVERDUE';
                return;
            }
            
            const days = Math.floor(diff / (1000 * 60 * 60 * 24));
            const hours = Math.floor((diff % (1000 * 60 * 60 * 24)) / (1000 * 60 * 60));
            const minutes = Math.floor((diff % (1000 * 60 * 60)) / (1000 * 60));
            const seconds = Math.floor((diff % (1000 * 60)) / 1000);
            
            if (days > 0) {
                this.timeRemaining = `${days}d ${hours}h`;
            } else if (hours > 0) {
                this.timeRemaining = `${hours}h ${minutes}m`;
            } else {
                this.timeRemaining = `${minutes}m ${seconds}s`;
            }
        }
    }));
});

