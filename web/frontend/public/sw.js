/**
 * MiroShark Service Worker — Browser Push Notifications
 *
 * Handles incoming Web Push events and displays browser notifications
 * when a simulation completes. Notification clicks focus the browser
 * tab (or open a new one) and navigate to the simulation results.
 */

// ── Install: skip waiting so the SW activates immediately ────────────────────
self.addEventListener('install', () => {
  self.skipWaiting()
})

// ── Activate: claim all clients immediately ──────────────────────────────────
self.addEventListener('activate', (event) => {
  event.waitUntil(self.clients.claim())
})

// ── Push: show a notification when a simulation completes ───────────────────
self.addEventListener('push', (event) => {
  let payload = { title: 'MiroShark', body: 'Simulation update', url: '/' }

  if (event.data) {
    try {
      payload = { ...payload, ...event.data.json() }
    } catch (_) {
      payload.body = event.data.text()
    }
  }

  const options = {
    body: payload.body,
    icon: '/favicon.ico',
    badge: '/favicon.ico',
    tag: payload.simulation_id ? `sim-${payload.simulation_id}` : 'miroshark',
    renotify: false,
    requireInteraction: false,
    data: { url: payload.url || '/' },
  }

  event.waitUntil(
    self.registration.showNotification(payload.title, options)
  )
})

// ── Notification click: focus existing tab or open new window ────────────────
self.addEventListener('notificationclick', (event) => {
  event.notification.close()

  const targetUrl = (event.notification.data && event.notification.data.url) || '/'

  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((clientList) => {
      // Focus any existing window that is already on this origin
      for (const client of clientList) {
        try {
          const clientUrl = new URL(client.url)
          if (clientUrl.origin === self.location.origin) {
            client.navigate(targetUrl)
            return client.focus()
          }
        } catch (_) { /* ignore parse errors */ }
      }
      // No matching window — open a new one
      return self.clients.openWindow(targetUrl)
    })
  )
})
