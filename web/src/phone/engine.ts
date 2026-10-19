/* The iPhone app's side of the engine.

   On the phone there is no server: the Mac's Python runs in a worker
   (engine.worker.ts) and every /api call the pages make is answered by it.
   This file starts it, routes the calls, keeps the database file in the
   app's own storage, and moves the two secrets between the iOS Keychain and
   the engine. On the Mac none of this runs: `onPhone` is false and the pages
   call the local server as before. */

import type { Call } from './engine.worker.ts'
import type { EngineResponse, ImportReport } from './boot.ts'

type Native = { isNativePlatform?: () => boolean }

/** The iPhone app. `?engine` runs the same engine in a desktop browser, for testing. */
export const onPhone =
  Boolean((window as unknown as { Capacitor?: Native }).Capacitor?.isNativePlatform?.()) ||
  new URLSearchParams(location.search).has('engine')

const native = Boolean((window as unknown as { Capacitor?: Native }).Capacitor?.isNativePlatform?.())

const KEY_SECRET = 'tastydesk-client-secret'
const KEY_TOKEN = 'tastydesk-refresh-token'
const DB_FILE = 'tastydesk.db'
const SAVE_EVERY_MS = 15_000

export type EngineState =
  | { phase: 'starting' }
  | { phase: 'ready'; commit: string; hasKeys: boolean }
  | { phase: 'failed'; error: string }

let state: EngineState = { phase: 'starting' }
const listeners = new Set<(s: EngineState) => void>()
export const engineState = () => state
export function onEngineState(fn: (s: EngineState) => void): () => void {
  listeners.add(fn)
  return () => listeners.delete(fn)
}
function setState(next: EngineState) {
  state = next
  for (const fn of listeners) fn(next)
}

// ---------------------------------------------------------------- the worker

let worker: Worker | null = null
let nextId = 1
const pending = new Map<number, { resolve: (v: unknown) => void; reject: (e: Error) => void }>()

function call<T>(c: Call, transfer: Transferable[] = []): Promise<T> {
  if (!worker) return Promise.reject(new Error('The engine has not started'))
  const id = nextId++
  return new Promise<T>((resolve, reject) => {
    pending.set(id, { resolve: resolve as (v: unknown) => void, reject })
    worker!.postMessage({ id, call: c }, transfer)
  })
}

let ready: Promise<void> | null = null

export function startEngine(): Promise<void> {
  if (ready) return ready
  ready = (async () => {
    worker = new Worker(new URL('./engine.worker.ts', import.meta.url), { type: 'module' })
    worker.onmessage = (event: MessageEvent<{ id: number; ok: boolean; result?: unknown; error?: string }>) => {
      const waiting = pending.get(event.data.id)
      if (!waiting) return
      pending.delete(event.data.id)
      if (event.data.ok) waiting.resolve(event.data.result)
      else waiting.reject(new Error(event.data.error))
    }
    const [db, credentials] = await Promise.all([loadDb(), loadCredentials()])
    const { commit } = await call<{ commit: string }>({ op: 'boot', db, credentials })
    setState({ phase: 'ready', commit, hasKeys: credentials !== null })
    setInterval(() => void saveDb(), SAVE_EVERY_MS)
    // iOS may end the app any time after it leaves the screen.
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'hidden') void saveDb()
    })
  })().catch((error: unknown) => {
    setState({ phase: 'failed', error: error instanceof Error ? error.message : String(error) })
    throw error
  })
  return ready
}

// ---------------------------------------------------------------- requests

/** Answer the pages' /api calls from the engine; everything else goes out as usual. */
export function routeApiToEngine() {
  const real = window.fetch.bind(window)
  window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(typeof input === 'string' ? input : input instanceof URL ? input.href : input.url, location.href)
    if (url.origin !== location.origin || !url.pathname.startsWith('/api/')) return real(input, init)
    await startEngine()
    const headers = [...new Headers(init?.headers ?? (input instanceof Request ? input.headers : undefined))]
    const body = init?.body == null ? '' : typeof init.body === 'string' ? init.body : await new Response(init.body).text()
    const res = await call<EngineResponse>({
      op: 'request',
      method: init?.method ?? 'GET',
      path: url.pathname,
      query: url.search.replace(/^\?/, ''),
      headers,
      body,
    })
    // A write should not wait fifteen seconds to be kept.
    if ((init?.method ?? 'GET').toUpperCase() !== 'GET') void saveDb()
    return new Response(res.body, { status: res.status, headers: res.headers })
  }
}

// ---------------------------------------------------------------- the database file

async function filesystem() {
  const { Filesystem, Directory } = await import('@capacitor/filesystem')
  // Not backed up to iCloud: the Mac holds the full history, and a trade
  // journal has no business in a cloud backup it did not ask for.
  return { Filesystem, directory: Directory.LibraryNoCloud }
}

async function loadDb(): Promise<Uint8Array | null> {
  if (!native) return null
  const { Filesystem, directory } = await filesystem()
  // The .new copy only when the app stopped between writing it and moving it.
  for (const path of [DB_FILE, `${DB_FILE}.new`]) {
    try {
      const { data } = await Filesystem.readFile({ path, directory })
      return typeof data === 'string' ? fromBase64(data) : new Uint8Array(await data.arrayBuffer())
    } catch {
      // not there; try the next
    }
  }
  return null // first run: the engine creates an empty database
}

let saving: Promise<void> | null = null

async function saveDb(force = false): Promise<void> {
  if (!native || state.phase !== 'ready') return
  if (saving) return saving
  saving = (async () => {
    const data = await call<Uint8Array | null>({ op: 'export', force })
    if (data) await writeDb(data)
  })().finally(() => {
    saving = null
  })
  return saving
}

async function writeDb(data: Uint8Array): Promise<void> {
  const { Filesystem, directory } = await filesystem()
  // Written beside the real file and then moved over it, so a crash mid-write
  // leaves the previous copy rather than half of a new one.
  const temp = `${DB_FILE}.new`
  await Filesystem.writeFile({ path: temp, data: toBase64(data), directory })
  // Removed first: a rename onto an existing file is not guaranteed to replace it.
  await Filesystem.deleteFile({ path: DB_FILE, directory }).catch(() => {})
  await Filesystem.rename({ from: temp, to: DB_FILE, directory, toDirectory: directory })
}

/** Replace the phone's database with one exported from the Mac. */
export async function importDatabase(file: File): Promise<ImportReport> {
  await startEngine()
  const data = new Uint8Array(await file.arrayBuffer())
  const report = await call<ImportReport & { saved?: Uint8Array | null }>({ op: 'import', data }, [data.buffer])
  if (report.ok && report.saved && native) await writeDb(report.saved)
  return { ok: report.ok, reason: report.reason, counts: report.counts }
}

// ---------------------------------------------------------------- the secrets

async function keychain() {
  const { SecureStorage, KeychainAccess } = await import('@aparajita/capacitor-secure-storage')
  return { SecureStorage, KeychainAccess }
}

async function loadCredentials(): Promise<{ clientSecret: string; refreshToken: string } | null> {
  if (!native) return null
  const { SecureStorage } = await keychain()
  const [clientSecret, refreshToken] = await Promise.all([
    SecureStorage.get(KEY_SECRET, false, false),
    SecureStorage.get(KEY_TOKEN, false, false),
  ])
  if (typeof clientSecret !== 'string' || typeof refreshToken !== 'string' || !clientSecret || !refreshToken) return null
  return { clientSecret, refreshToken }
}

/** Keep the two secrets in the iOS Keychain (this device only) and start using them. */
export async function saveCredentials(clientSecret: string, refreshToken: string): Promise<void> {
  if (native) {
    const { SecureStorage, KeychainAccess } = await keychain()
    // Never synced to iCloud Keychain, never restored onto another phone.
    const access = KeychainAccess.whenUnlockedThisDeviceOnly
    await SecureStorage.set(KEY_SECRET, clientSecret, false, false, access)
    await SecureStorage.set(KEY_TOKEN, refreshToken, false, false, access)
  }
  await startEngine()
  await call({ op: 'credentials', clientSecret, refreshToken })
  if (state.phase === 'ready') setState({ ...state, hasKeys: true })
}

export async function forgetCredentials(): Promise<void> {
  if (native) {
    const { SecureStorage } = await keychain()
    await Promise.all([SecureStorage.remove(KEY_SECRET, false), SecureStorage.remove(KEY_TOKEN, false)])
  }
  await startEngine()
  await call({ op: 'forget' })
  if (state.phase === 'ready') setState({ ...state, hasKeys: false })
}

// ---------------------------------------------------------------- base64

function toBase64(bytes: Uint8Array): string {
  let binary = ''
  for (let i = 0; i < bytes.length; i += 0x8000) binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000))
  return btoa(binary)
}

function fromBase64(text: string): Uint8Array {
  const binary = atob(text)
  const out = new Uint8Array(binary.length)
  for (let i = 0; i < binary.length; i++) out[i] = binary.charCodeAt(i)
  return out
}
