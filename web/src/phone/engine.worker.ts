/* The phone engine's own thread. Python runs here so a sync or a big report
   never freezes the screen. The page talks to it in messages (engine.ts);
   tastytrade is called from here directly, with the browser's fetch. */

import { bootEngine, type Engine } from './boot.ts'

export type Call =
  | { op: 'boot'; db: Uint8Array | null; credentials: { clientSecret: string; refreshToken: string } | null }
  | { op: 'request'; method: string; path: string; query: string; headers: [string, string][]; body: string }
  | { op: 'export'; force?: boolean }
  | { op: 'credentials'; clientSecret: string; refreshToken: string }
  | { op: 'forget' }
  | { op: 'import'; data: Uint8Array }
  | { op: 'status' }
  | { op: 'devicesync'; files: Record<string, Uint8Array> }

const INDEX_URL = new URL('/engine/', self.location.href).href

/* Yahoo's charts and tastytrade's market metrics, fetched by the app's main
   side through iOS (engine.ts): both refuse requests from a web page, and this
   worker is one. Python calls it from the phone bridge
   (tastydesk_phone._install_native_charts, _install_native_metrics). */
type NativeReply = { status: number; body?: string; error?: string }
let nextNativeId = 1
const nativeWaiting = new Map<number, (reply: NativeReply) => void>()
;(self as unknown as { tastydeskNativeGet: unknown }).tastydeskNativeGet = (
  url: string,
  timeoutMs: number,
  headers?: Record<string, string>,
) =>
  new Promise<NativeReply>((resolve) => {
    const id = nextNativeId++
    const timer = setTimeout(() => {
      nativeWaiting.delete(id)
      resolve({ status: 0, error: 'No answer from Yahoo in time' })
    }, timeoutMs + 2_000)
    nativeWaiting.set(id, (reply) => {
      clearTimeout(timer)
      resolve(reply)
    })
    self.postMessage({ nativeGet: id, url, timeoutMs, headers })
  })

let engine: Engine | null = null

async function handle(call: Call): Promise<unknown> {
  if (call.op === 'boot') {
    const [{ loadPyodide }, asm] = await Promise.all([
      import(/* @vite-ignore */ `${INDEX_URL}pyodide.js`),
      import(/* @vite-ignore */ `${INDEX_URL}pyodide.asm.js`),
    ])
    engine = await bootEngine({
      indexURL: INDEX_URL,
      loadPyodide,
      createPyodideModule: asm.default,
      readFile: async (url) => new Uint8Array(await (await fetch(url)).arrayBuffer()),
    })
    if (call.db) engine.restoreDb(call.db)
    if (call.credentials) engine.setCredentials(call.credentials.clientSecret, call.credentials.refreshToken)
    await engine.start()
    return { commit: engine.commit }
  }
  if (!engine) {
    if (call.op === 'status') return null
    throw new Error('The engine has not started')
  }
  switch (call.op) {
    case 'request':
      return engine.request(call.method, call.path, call.query, call.headers, call.body)
    case 'export':
      return engine.exportDb(call.force)
    case 'credentials':
      engine.setCredentials(call.clientSecret, call.refreshToken)
      // The session is opened once and kept, so new keys need a fresh start.
      await engine.restart()
      return null
    case 'forget':
      engine.clearCredentials()
      await engine.restart()
      return null
    case 'import':
      return engine.importDb(call.data)
    case 'status':
      return engine.status()
    case 'devicesync':
      return engine.deviceSync(call.files)
  }
}

self.onmessage = async (
  event: MessageEvent<{ id: number; call: Call } | { nativeReply: number; reply: NativeReply }>,
) => {
  if ('nativeReply' in event.data) {
    nativeWaiting.get(event.data.nativeReply)?.(event.data.reply)
    nativeWaiting.delete(event.data.nativeReply)
    return
  }
  const { id, call } = event.data
  try {
    const result = await handle(call)
    const transfer = result instanceof Uint8Array ? [result.buffer] : []
    self.postMessage({ id, ok: true, result }, { transfer })
  } catch (error) {
    // Only the message: a Python traceback can carry values from the request.
    const message = error instanceof Error ? error.message.split('\n').filter(Boolean).pop() : String(error)
    self.postMessage({ id, ok: false, error: message ?? 'Engine error' })
  }
}
