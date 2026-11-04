/* The iPhone app's side of the engine.

   On the phone there is no server: the Mac's Python runs in a worker
   (engine.worker.ts) and every /api call the pages make is answered by it.
   This file starts it, routes the calls, keeps the database file in the
   app's own storage, and moves the two secrets between the iOS Keychain and
   the engine. On the Mac none of this runs: `onPhone` is false and the pages
   call the local server as before. */

import type { Call } from './engine.worker.ts'
import { FRESH_DATA } from '../lib/devices'
import type { DeviceSyncReport, DeviceSyncResult, EngineResponse, EngineStatus, ImportReport } from './boot.ts'

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
// Sharing with the Mac through iCloud Drive: this often while the app is open,
// and soon after anything is changed here.
const SHARE_EVERY_MS = 30_000
const SHARE_AFTER_CHANGE_MS = 2_000
// A page request still unanswered after this long gets an error saying what
// the engine is waiting for, instead of a page that loads forever.
const REQUEST_TIMEOUT_MS = 90_000

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
    worker.onmessage = (
      event: MessageEvent<
        | { id: number; ok: boolean; result?: unknown; error?: string }
        | { nativeGet: number; url: string; timeoutMs: number; headers?: Record<string, string> }
      >,
    ) => {
      if ('nativeGet' in event.data) {
        const { nativeGet: id, url, timeoutMs, headers } = event.data
        void nativeGet(url, timeoutMs, headers).then((reply) => worker!.postMessage({ nativeReply: id, reply }))
        return
      }
      const waiting = pending.get(event.data.id)
      if (!waiting) return
      pending.delete(event.data.id)
      if (event.data.ok) waiting.resolve(event.data.result)
      else waiting.reject(new Error(event.data.error))
    }
    const [db, credentials] = await Promise.all([loadDb(), loadCredentials()])
    const { commit } = await call<{ commit: string }>({ op: 'boot', db, credentials })
    setState({ phase: 'ready', commit, hasKeys: credentials !== null })
    updateSavedBar()
    setInterval(() => void saveDb(), SAVE_EVERY_MS)
    // iOS may end the app any time after it leaves the screen.
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'hidden') void saveDb()
      else void syncDevices()
    })
    setTimeout(() => void syncDevices(), 3_000)
    setInterval(() => {
      if (document.visibilityState === 'visible') void syncDevices()
    }, SHARE_EVERY_MS)
  })().catch((error: unknown) => {
    setState({ phase: 'failed', error: error instanceof Error ? error.message : String(error) })
    throw error
  })
  return ready
}

// ---------------------------------------------------------------- Yahoo's charts

/* Two addresses, GET only: Yahoo's charts, and tastytrade's market metrics,
   the one tastytrade address that refuses the page's CORS check (403). This is
   not a way for the engine to reach anything else without the page's rules,
   and only tastytrade's address may carry the engine's headers (its token). */
const CHART_URL = /^https:\/\/query[12]\.finance\.yahoo\.com\/v8\/finance\/chart\/[^/?#]+\?[^#]*$/
const METRICS_URL = /^https:\/\/api\.(tastyworks|tastytrade)\.com\/market-metrics\?[^#]*$/
const METRICS_HEADERS = new Set(['authorization', 'accept', 'accept-version', 'content-type'])

async function nativeGet(
  url: string,
  timeoutMs: number,
  given?: Record<string, string>,
): Promise<{ status: number; body?: string; error?: string }> {
  let headers: Record<string, string>
  if (CHART_URL.test(url)) {
    headers = { 'User-Agent': 'TastyDesk/0.1 (local, read-only)', Accept: 'application/json' }
  } else if (METRICS_URL.test(url)) {
    headers = Object.fromEntries(Object.entries(given ?? {}).filter(([name]) => METRICS_HEADERS.has(name.toLowerCase())))
  } else {
    return { status: 0, error: 'Not an address the app fetches itself' }
  }
  if (!native) return { status: 0, error: 'Needs the iPhone app' }
  try {
    const { CapacitorHttp } = await import('@capacitor/core')
    const res = await CapacitorHttp.get({
      url,
      headers,
      responseType: 'text',
      connectTimeout: timeoutMs,
      readTimeout: timeoutMs,
    })
    const body = typeof res.data === 'string' ? res.data : JSON.stringify(res.data)
    return { status: res.status, body }
  } catch {
    return { status: 0, error: 'Could not reach the server' }
  }
}

// ---------------------------------------------------------------- saved answers

/* The last answer to each page's request, kept on the phone so the app opens
   on numbers rather than on spinners. iOS ends the app soon after it leaves
   the screen, so most opens start the engine again (about six seconds), and
   the pages then wait on tastytrade for live prices.

   An answer is shown from here at once, and when it is more than a few
   seconds old the engine is asked again in the background; when the new one
   arrives every page reads again. While anything shown is older than that, a
   bar says from when, so an old number is never taken for a live one. Any
   change made in the app clears them all: a page read after a write waits
   for the real answer. Stored like the database: on this phone only, out of
   iCloud backup. */

type Saved = { at: number; status: number; headers: [string, string][]; body: string }

const ANSWERS_FILE = 'answers.json'
const FRESH_MS = 20_000
const MAX_ANSWERS = 80

// Status the app itself watches, which must always be live.
const NEVER_SAVED = new Set(['/api/devicesync'])

const answers = new Map<string, Saved>()
let answersDirty = false
let answersLoaded: Promise<void> | null = null
const refreshing = new Map<string, Promise<void>>()
// Saved answers on screen now that are older than FRESH_MS: what the bar is about.
const shownOld = new Set<string>()

export type SavedState = { showingFrom: number | null; updating: boolean }
let saved: SavedState = { showingFrom: null, updating: false }
const savedListeners = new Set<(s: SavedState) => void>()
export const savedState = () => saved
export function onSaved(fn: (s: SavedState) => void): () => void {
  savedListeners.add(fn)
  return () => savedListeners.delete(fn)
}
function setSaved(next: SavedState) {
  saved = next
  for (const fn of savedListeners) fn(next)
}

/** Read the saved answers from the phone, once; the app may open on them. */
export function loadSavedAnswers(): Promise<void> {
  if (answersLoaded) return answersLoaded
  answersLoaded = (async () => {
    if (!native) return
    try {
      const { Filesystem, directory } = await filesystem()
      const { data } = await Filesystem.readFile({ path: ANSWERS_FILE, directory, encoding: Encoding.UTF8 })
      const entries = JSON.parse(typeof data === 'string' ? data : await data.text()) as [string, Saved][]
      for (const [key, value] of entries) answers.set(key, value)
    } catch {
      // none yet, or unreadable: the app waits for real answers as before
    }
  })()
  return answersLoaded
}

export const hasSavedAnswers = () => answers.size > 0

async function writeSavedAnswers(): Promise<void> {
  if (!native || !answersDirty) return
  answersDirty = false
  const newest = [...answers.entries()].sort((a, b) => b[1].at - a[1].at).slice(0, MAX_ANSWERS)
  const { Filesystem, directory } = await filesystem()
  await Filesystem.writeFile({ path: ANSWERS_FILE, data: JSON.stringify(newest), directory, encoding: Encoding.UTF8 })
}

async function forgetSavedAnswers(): Promise<void> {
  answers.clear()
  shownOld.clear()
  answersDirty = true
  await writeSavedAnswers().catch(() => {})
}

/** The era header changes what a report reads, so it is part of what was asked. */
function answerKey(url: URL, headers: [string, string][]): string {
  const era = headers.find(([name]) => name.toLowerCase() === 'x-era')?.[1] ?? ''
  return `${url.pathname}${url.search}|${era}`
}

let pagesToldAt: ReturnType<typeof setTimeout> | undefined
function tellPagesToReadAgain() {
  clearTimeout(pagesToldAt)
  pagesToldAt = setTimeout(() => window.dispatchEvent(new Event(FRESH_DATA)), 300)
}

function updateSavedBar() {
  const shown = [...shownOld].map((key) => answers.get(key)?.at).filter((at): at is number => at !== undefined)
  const updating = refreshing.size > 0 || state.phase === 'starting'
  setSaved({ showingFrom: shown.length ? Math.min(...shown) : null, updating })
}

function refreshInBackground(key: string, ask: () => Promise<EngineResponse | Response>) {
  if (refreshing.has(key)) return
  const job = (async () => {
    try {
      const res = await ask()
      if (!(res instanceof Response) && res.status === 200) {
        answers.set(key, { at: Date.now(), status: res.status, headers: res.headers, body: res.body })
        answersDirty = true
        shownOld.delete(key)
        tellPagesToReadAgain()
      }
    } catch {
      // the saved answer stays, and so does the bar saying how old it is
    }
  })().finally(() => {
    refreshing.delete(key)
    updateSavedBar()
  })
  refreshing.set(key, job)
  updateSavedBar()
}

// ---------------------------------------------------------------- requests

async function askEngine(
  url: URL,
  method: string,
  headers: [string, string][],
  body: string,
): Promise<EngineResponse | Response> {
  await startEngine()
  try {
    const answer = call<EngineResponse>({
      op: 'request',
      method,
      path: url.pathname,
      query: url.search.replace(/^\?/, ''),
      headers,
      body,
    })
    const timedOut = new Promise<null>((resolve) => setTimeout(() => resolve(null), REQUEST_TIMEOUT_MS))
    const first = await Promise.race([answer, timedOut])
    if (first === null) {
      const waiting = describeWaiting(await engineStatus().catch(() => null))
      const detail = `No answer after ${REQUEST_TIMEOUT_MS / 1000} seconds for ${url.pathname}.${waiting ? ` Still waiting for: ${waiting}.` : ''}`
      return new Response(JSON.stringify({ detail }), { status: 504, headers: { 'Content-Type': 'application/json' } })
    }
    return first
  } catch (error) {
    // A failure inside the app, not a missing server: say what it was rather
    // than letting the page report "backend not reachable".
    const detail = `Engine: ${error instanceof Error ? error.message : String(error)}`
    return new Response(JSON.stringify({ detail }), { status: 500, headers: { 'Content-Type': 'application/json' } })
  }
}

/** Answer the pages' /api calls from the engine; everything else goes out as usual. */
export function routeApiToEngine() {
  const real = window.fetch.bind(window)
  window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(typeof input === 'string' ? input : input instanceof URL ? input.href : input.url, location.href)
    if (url.origin !== location.origin || !url.pathname.startsWith('/api/')) return real(input, init)
    const method = (init?.method ?? 'GET').toUpperCase()
    const headers = [...new Headers(init?.headers ?? (input instanceof Request ? input.headers : undefined))]
    const body = init?.body == null ? '' : typeof init.body === 'string' ? init.body : await new Response(init.body).text()

    if (method === 'GET' && !NEVER_SAVED.has(url.pathname)) {
      await loadSavedAnswers()
      const key = answerKey(url, headers)
      const hit = answers.get(key)
      if (hit) {
        if (Date.now() - hit.at > FRESH_MS) {
          shownOld.add(key)
          refreshInBackground(key, () => askEngine(url, method, headers, body))
        }
        updateSavedBar()
        return new Response(hit.body, { status: hit.status, headers: hit.headers })
      }
      const res = await askEngine(url, method, headers, body)
      if (res instanceof Response) return res
      if (res.status === 200) {
        answers.set(key, { at: Date.now(), status: res.status, headers: res.headers, body: res.body })
        answersDirty = true
      }
      updateSavedBar()
      return new Response(res.body, { status: res.status, headers: res.headers })
    }

    const res = await askEngine(url, method, headers, body)
    // After a change every saved answer may be out of date: the pages read
    // again from the engine itself.
    answers.clear()
    shownOld.clear()
    answersDirty = true
    updateSavedBar()
    // A write should not wait fifteen seconds to be kept, nor to reach the Mac.
    void saveDb()
    clearTimeout(shareSoon)
    shareSoon = setTimeout(() => void syncDevices(), SHARE_AFTER_CHANGE_MS)
    if (res instanceof Response) return res
    return new Response(res.body, { status: res.status, headers: res.headers })
  }
}

/** What the engine is busy with: for the loading screens and the setup page. */
export async function engineStatus(): Promise<EngineStatus | null> {
  if (!worker) return null
  // Python answers this between awaits; if it is stuck in a long computation
  // there is no answer, and saying nothing is better than hanging here too.
  const none = new Promise<null>((resolve) => setTimeout(() => resolve(null), 3_000))
  return Promise.race([call<EngineStatus | null>({ op: 'status' }), none])
}

/** "tastytrade quotes (12 s), accounts (3 s)", or "" when nothing is running. */
export function describeWaiting(status: EngineStatus | null): string {
  if (!status) return ''
  const parts = status.calls.map((c) => `tastytrade ${c.what} (${Math.round(c.seconds)} s)`)
  if (status.starting_for != null) parts.unshift(`starting up (${Math.round(status.starting_for)} s)`)
  return parts.join(', ')
}

// ---------------------------------------------------------------- other devices

/* What is set by hand is shared with the Mac through a folder in iCloud Drive,
   "Tasty Desk Sync", which the user picks once. The native side reads and
   writes the folder (web/plugins/icloud-folder); the engine does the rest,
   with the same code the Mac runs (core/devicesync.py). */

type FolderPlugin = {
  pick(): Promise<{ name?: string }>
  current(): Promise<{ name?: string }>
  list(): Promise<{ files: { name: string; data: string }[]; waiting: number }>
  write(options: { name: string; data: string }): Promise<void>
  forget(): Promise<void>
}

let folderPlugin: FolderPlugin | null = null
async function icloud(): Promise<FolderPlugin> {
  if (!folderPlugin) {
    const { registerPlugin } = await import('@capacitor/core')
    folderPlugin = registerPlugin<FolderPlugin>('ICloudFolder')
  }
  return folderPlugin
}

export type SharingState = {
  /** The folder picked, or null when none is. */
  folder: string | null
  report: DeviceSyncReport | null
  /** Files iCloud has not brought down to the phone yet. */
  waiting: number
  error: string | null
}

let sharing: SharingState = { folder: null, report: null, waiting: 0, error: null }
const sharingListeners = new Set<(s: SharingState) => void>()
export const sharingState = () => sharing
export function onSharing(fn: (s: SharingState) => void): () => void {
  sharingListeners.add(fn)
  return () => sharingListeners.delete(fn)
}
function setSharing(patch: Partial<SharingState>) {
  sharing = { ...sharing, ...patch }
  for (const fn of sharingListeners) fn(sharing)
}

let shareSoon: ReturnType<typeof setTimeout> | undefined
let syncingDevices: Promise<void> | null = null

/** One round: read the folder, let the engine take in what is new, write this phone's file. */
export function syncDevices(): Promise<void> {
  if (!native || state.phase !== 'ready' || !state.hasKeys) return Promise.resolve()
  if (syncingDevices) return syncingDevices
  syncingDevices = (async () => {
    const plugin = await icloud()
    const { name } = await plugin.current()
    if (!name) {
      setSharing({ folder: null, error: null })
      return
    }
    const listing = await plugin.list()
    const files: Record<string, Uint8Array> = {}
    for (const file of listing.files) files[file.name] = fromBase64(file.data)
    const result = await call<DeviceSyncResult>({ op: 'devicesync', files })
    if (result.outgoing) await plugin.write({ name: result.filename, data: toBase64(result.outgoing) })
    // What arrived, and what was recorded about it, is kept like any change;
    // and the saved answers no longer show it.
    if ((result.report.received ?? 0) > 0) {
      answers.clear()
      shownOld.clear()
      answersDirty = true
      tellPagesToReadAgain()
    }
    void saveDb()
    setSharing({ folder: name, report: result.report, waiting: listing.waiting, error: null })
  })()
    .catch((error: unknown) => {
      // The plugin's messages are its own and say nothing about the data.
      setSharing({ error: error instanceof Error ? error.message : 'Could not reach the sync folder.' })
    })
    .finally(() => {
      syncingDevices = null
    })
  return syncingDevices
}

/** Ask for the folder (the Files picker), then share at once. */
export async function pickSyncFolder(): Promise<string | null> {
  const { name } = await (await icloud()).pick()
  if (!name) return null
  setSharing({ folder: name, error: null })
  await syncDevices()
  return name
}

export async function forgetSyncFolder(): Promise<void> {
  await (await icloud()).forget()
  setSharing({ folder: null, report: null, waiting: 0, error: null })
}

// ---------------------------------------------------------------- the database file

async function filesystem() {
  const { Filesystem, Directory, Encoding: encoding } = await import('@capacitor/filesystem')
  Encoding = encoding
  // Not backed up to iCloud: the Mac holds the full history, and a trade
  // journal has no business in a cloud backup it did not ask for.
  return { Filesystem, directory: Directory.LibraryNoCloud }
}

let Encoding: typeof import('@capacitor/filesystem').Encoding

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
  void writeSavedAnswers().catch(() => {})
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
  if (report.ok) await forgetSavedAnswers()
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
  void syncDevices()
}

export async function forgetCredentials(): Promise<void> {
  if (native) {
    const { SecureStorage } = await keychain()
    await Promise.all([SecureStorage.remove(KEY_SECRET, false), SecureStorage.remove(KEY_TOKEN, false)])
  }
  await forgetSavedAnswers()
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
