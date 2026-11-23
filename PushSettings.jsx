import React, {useEffect, useState} from 'react'

const decodeKey = value => {
  const padding = '='.repeat((4 - value.length % 4) % 4)
  const raw = atob((value + padding).replace(/-/g, '+').replace(/_/g, '/'))
  return Uint8Array.from(raw, ch => ch.charCodeAt(0))
}

export default function PushSettings() {
  const [config, setConfig] = useState(null)
  const [token, setToken] = useState('')
  const [message, setMessage] = useState('')
  const [subscribed, setSubscribed] = useState(false)
  useEffect(() => {
    fetch('/api/push/config').then(r => r.json()).then(setConfig).catch(() => setMessage('Cannot reach backend'))
    if ('serviceWorker' in navigator) navigator.serviceWorker.getRegistration().then(async reg => {
      if (reg && 'pushManager' in reg) setSubscribed(Boolean(await reg.pushManager.getSubscription()))
    }).catch(() => {})
  }, [])
  async function update(enable) {
    setMessage('')
    try {
      if (!('serviceWorker' in navigator) || !('PushManager' in window)) throw Error('Push not supported in this browser.')
      if (!window.isSecureContext) throw Error('HTTPS is required on mobile devices.')
      if (!config?.enabled) throw Error('Configure VAPID and enrollment token in backend first.')
      if (!token.trim()) throw Error('Enter the enrollment token set by the administrator.')
      if (enable && Notification.permission === 'denied') throw Error('Notifications are blocked in browser settings.')
      const registration = await navigator.serviceWorker.register('/sw.js')
      await navigator.serviceWorker.ready
      let subscription = await registration.pushManager.getSubscription()
      if (enable) {
        if (Notification.permission !== 'granted') {
          const permission = await Notification.requestPermission()
          if (permission !== 'granted') throw Error('Notification permission not granted.')
        }
        if (!subscription) subscription = await registration.pushManager.subscribe({userVisibleOnly:true, applicationServerKey:decodeKey(config.public_key)})
        const response = await fetch('/api/push/subscribe', {method:'POST',headers:{'Content-Type':'application/json','X-Push-Enroll-Token':token},body:JSON.stringify({subscription:subscription.toJSON()})})
        if (!response.ok) throw Error((await response.json()).detail || 'Could not register phone')
        setSubscribed(true)
        setMessage('Push enabled on this device. Alerts can arrive while the app is closed, subject to browser and OS delivery.')
      } else {
        if (subscription) {
          const response = await fetch('/api/push/unsubscribe',{method:'POST',headers:{'Content-Type':'application/json','X-Push-Enroll-Token':token},body:JSON.stringify({subscription:subscription.toJSON()})})
          if (!response.ok) throw Error((await response.json()).detail || 'Could not unregister device')
          await subscription.unsubscribe()
        }
        setSubscribed(false)
        setMessage('Mobile push disabled on this device.')
      }
    } catch (error) { setMessage(error.message || 'Push setup failed') }
  }
  return <section className="panel history"><div className="panel-heading"><div><h2>Mobile push notifications</h2><p>Receive review warnings even after closing the dashboard, when your browser supports background push.</p></div></div>
    <div className="push-controls"><label>Enrollment token <input type="password" value={token} onChange={e=>setToken(e.target.value)} placeholder="Enter administrator token" autoComplete="off"/></label>
      <button className="primary" type="button" onClick={()=>update(true)}>Enable on this device</button>
      <button className="export" type="button" onClick={()=>update(false)}>Disable</button></div>
    <p className="subtle">Status: {subscribed ? 'Subscribed on this browser' : 'Not subscribed'} · {config?.enabled ? 'Backend configured' : 'Backend not configured'}</p>
    {message && <p role="status" className="subtle">{message}</p>}
    <p className="subtle">iPhone/iPad: install this website to Home Screen, open it from there, then enable notifications. Mobile devices need HTTPS and the backend must stay running. Only warning events trigger push; motion alerts are not proof of wrongdoing.</p>
  </section>
}
