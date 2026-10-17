/* The demo page: the dashboard served from a saved snapshot instead of the
   local server. A page that carries `window.__DEMO__` (path -> response)
   answers every /api call from it; the real app never has it, so this does
   nothing there. Writes are accepted and change nothing. */

type Snapshot = Record<string, unknown>

const snapshot = (window as unknown as { __DEMO__?: Snapshot }).__DEMO__

if (snapshot) {
  const real = window.fetch.bind(window)
  window.fetch = (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(typeof input === 'string' ? input : input instanceof URL ? input.href : input.url, location.href)
    if (!url.pathname.startsWith('/api')) return real(input, init)
    const path = url.pathname.replace(/^\/api/, '')
    const method = (init?.method ?? 'GET').toUpperCase()
    // The exact request first (a payoff is per strategy), then the page alone.
    const exact = path + url.search
    const body = exact in snapshot ? snapshot[exact] : path in snapshot ? snapshot[path] : method === 'GET' ? null : {}
    const status = body === null ? 404 : 200
    return Promise.resolve(
      new Response(JSON.stringify(body ?? { detail: 'Not in this demo' }), {
        status,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
  }
}

export {}
