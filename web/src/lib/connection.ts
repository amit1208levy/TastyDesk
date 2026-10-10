import { Capacitor } from '@capacitor/core'

/* Where requests go, and with what proof.

   In a browser on the Mac the dashboard is served by the same process it
   calls, so a path is enough and nothing is added — exactly as before. In the
   iPhone app the pages are bundled into the app, so every call names the Mac's
   address and carries the pairing token. The token lives in the iOS Keychain,
   on this device only, and nowhere in the page's own storage. */

export const onPhone = Capacitor.isNativePlatform()

const KEY_URL = 'mac-url'
const KEY_TOKEN = 'mac-token'

let base = ''
let token: string | null = null
let onDisconnect: ((why: 'refused' | 'forgotten') => void) | null = null

export type Pairing = { url: string; token: string }

async function storage() {
  const { SecureStorage, KeychainAccess } = await import('@aparajita/capacitor-secure-storage')
  return { SecureStorage, KeychainAccess }
}

/** Read the saved pairing, if any, and start using it. */
export async function loadPairing(): Promise<boolean> {
  if (!onPhone) return true
  const { SecureStorage } = await storage()
  // get() to match set(): set() stores JSON, and getItem() would hand it back
  // still quoted. Date conversion off — these are strings, never dates.
  const [url, saved] = await Promise.all([
    SecureStorage.get(KEY_URL, false),
    SecureStorage.get(KEY_TOKEN, false),
  ])
  if (typeof url !== 'string' || typeof saved !== 'string' || !url || !saved) return false
  base = url
  token = saved
  return true
}

export async function savePairing(p: Pairing): Promise<void> {
  const { SecureStorage, KeychainAccess } = await storage()
  // Never synced to iCloud, never restored onto another phone.
  await SecureStorage.setSynchronize(false)
  const access = KeychainAccess.whenUnlockedThisDeviceOnly
  await SecureStorage.set(KEY_URL, p.url, false, false, access)
  await SecureStorage.set(KEY_TOKEN, p.token, false, false, access)
  base = p.url
  token = p.token
}

export async function forgetPairing(): Promise<void> {
  base = ''
  token = null
  if (!onPhone) return
  const { SecureStorage } = await storage()
  await Promise.all([SecureStorage.remove(KEY_URL), SecureStorage.remove(KEY_TOKEN)])
  onDisconnect?.('forgotten')
}

/** Called when the Mac turns the phone away, or the phone forgets the Mac,
    so the app can ask to pair again. */
export function whenDisconnected(handler: (why: 'refused' | 'forgotten') => void) {
  onDisconnect = handler
}

/** "https://my-mac.tail1234.ts.net#<token>", as `tastydesk pair` hands it out. */
export function parsePairingCode(code: string): Pairing | null {
  let u: URL
  try {
    u = new URL(code.trim())
  } catch {
    return null
  }
  const t = decodeURIComponent(u.hash.slice(1))
  if (u.protocol !== 'https:' || !u.hostname || !/^[A-Za-z0-9_-]{32,}$/.test(t)) return null
  if ((u.pathname && u.pathname !== '/') || u.search || u.username || u.password) return null
  return { url: u.origin, token: t }
}

/** Try a pairing without keeping it: 'ok', 'refused' or 'unreachable'. */
export async function checkPairing(p: Pairing): Promise<'ok' | 'refused' | 'unreachable'> {
  try {
    const res = await fetch(`${p.url}/api/health`, {
      headers: { Accept: 'application/json', Authorization: `Bearer ${p.token}` },
    })
    if (res.status === 401) return 'refused'
    return res.ok ? 'ok' : 'unreachable'
  } catch {
    return 'unreachable'
  }
}

/** fetch() for the app's own API: a path such as '/api/health'. */
export async function apiFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers)
  if (token) headers.set('Authorization', `Bearer ${token}`)
  const res = await fetch(`${base}${path}`, { ...init, headers })
  if (onPhone && res.status === 401) onDisconnect?.('refused')
  return res
}
