/* Starting the phone engine: the Mac app's Python, running in Pyodide.

   Shared by the app (engine.worker.ts) and by the checks in ios/ that run the
   same start-up in Node, so what is tested is what ships. No imports and no
   DOM: it is handed `loadPyodide` and the folder the bundle lives in. */

/* eslint-disable @typescript-eslint/no-explicit-any */
type Py = any

export type EngineResponse = { status: number; headers: [string, string][]; body: string }
export type ImportReport = { ok: boolean; reason?: string; counts?: Record<string, number> }

export type Engine = {
  commit: string
  /** The Pyodide instance itself, for the checks in ios/ only. */
  pyodide: Py
  start(): Promise<void>
  stop(): Promise<void>
  restart(): Promise<void>
  setCredentials(clientSecret: string, refreshToken: string): void
  clearCredentials(): void
  hasCredentials(): boolean
  request(method: string, path: string, query: string, headers: [string, string][], body: string): Promise<EngineResponse>
  /** A whole copy of the database, or null when nothing changed since the last one. */
  exportDb(force?: boolean): Uint8Array | null
  /** Before start(): put back the copy the app saved. */
  restoreDb(data: Uint8Array): void
  checkDb(data: Uint8Array): ImportReport
  importDb(data: Uint8Array): Promise<ImportReport & { saved?: Uint8Array | null }>
}

export type BootOptions = {
  /** Folder holding the bundle (web/public/engine), ending in "/". */
  indexURL: string
  loadPyodide: (options: Record<string, unknown>) => Promise<Py>
  /** The default export of pyodide.asm.js, imported by the caller (see bundle.mjs). */
  createPyodideModule: unknown
  /** Reads a file from the bundle. */
  readFile: (url: string) => Promise<Uint8Array>
  /** Extra folders for Python's path, ahead of the bundle (tests only). */
  extraPath?: string[]
  stdout?: (line: string) => void
  stderr?: (line: string) => void
}

const SOURCES = '/tastydesk'

export async function bootEngine(options: BootOptions): Promise<Engine> {
  const py: Py = await options.loadPyodide({
    indexURL: options.indexURL,
    createPyodideModule: options.createPyodideModule,
    lockFileURL: options.indexURL + 'pyodide-lock.json',
    // Nothing is fetched from the internet: every package is in the bundle.
    packageBaseUrl: options.indexURL,
    stdout: options.stdout,
    stderr: options.stderr,
  })
  // Every package in the lock. Its dependency lists miss what comes in through
  // extras (httpx2[ws] needs wsproto), so the names are taken from the lock.
  const lock = JSON.parse(new TextDecoder().decode(await options.readFile(options.indexURL + 'pyodide-lock.json')))
  await py.loadPackage(Object.keys(lock.packages), { messageCallback: () => {} })

  const sources = await options.readFile(options.indexURL + 'tastydesk.tar')
  py.unpackArchive(sources, 'tar', { extractDir: SOURCES })
  const path = py.pyimport('sys').path
  for (const dir of [...(options.extraPath ?? []), SOURCES].reverse()) path.insert(0, dir)

  const phone = py.pyimport('tastydesk_phone')
  const toJs = (value: Py) => {
    if (value === undefined || value === null || typeof value !== 'object' || !('toJs' in value)) return value
    try {
      return value.toJs({ dict_converter: Object.fromEntries, create_pyproxies: false })
    } finally {
      value.destroy()
    }
  }

  return {
    commit: toJs(phone.build_info()).commit,
    pyodide: py,
    start: () => phone.start(),
    stop: () => phone.stop(),
    restart: () => phone.restart(),
    setCredentials: (clientSecret, refreshToken) => phone.set_credentials(clientSecret, refreshToken),
    clearCredentials: () => phone.clear_credentials(),
    hasCredentials: () => phone.has_credentials(),
    request: async (method, path, query, headers, body) =>
      toJs(await phone.request(method, path, query, JSON.stringify(headers), body)),
    exportDb: (force = false) => toJs(phone.export_db(force)) ?? null,
    restoreDb: (data) => phone.restore_db(data),
    checkDb: (data) => toJs(phone.check_db(data)),
    importDb: async (data) => toJs(await phone.import_db(data)),
  }
}
