self.addEventListener('push', event => {
  let data = {};
  try { data = event.data ? event.data.json() : {}; } catch (_) {}
  event.waitUntil(self.registration.showNotification(data.title || 'SmartCam Edge AI', {
    body: data.body || 'A video event needs review.',
    tag: 'smartcam-review',
    data: { url: '/' }
  }));
});
self.addEventListener('notificationclick', event => {
  event.notification.close();
  event.waitUntil(clients.matchAll({ type: 'window', includeUncontrolled: true }).then(windows => {
    const found = windows.find(w => new URL(w.url).origin === self.location.origin);
    if (found) return found.focus();
    return clients.openWindow('/');
  }));
});
