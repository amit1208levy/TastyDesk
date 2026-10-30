/* Changes made on the user's other device, arriving here.

   What is set by hand (named strategies, groups, plans, settings) is shared
   between the Mac and the iPhone through iCloud Drive; the server applies what
   arrives (core/devicesync.py) and says when it last did. This watches that
   and tells every page to read its data again, so a strategy named on the
   phone appears on the Mac's open page without a refresh. */

export const OTHER_DEVICE_CHANGED = 'tastydesk:other-device-changed'

const EVERY_MS = 20_000

export function watchOtherDevices() {
  let seen: string | null | undefined
  async function check() {
    try {
      const res = await fetch('/api/devicesync')
      if (!res.ok) return
      const { changed_at: changed } = (await res.json()) as { changed_at?: string | null }
      // The first answer is only the starting point, not news.
      if (seen !== undefined && changed && changed !== seen) {
        window.dispatchEvent(new Event(OTHER_DEVICE_CHANGED))
      }
      seen = changed ?? null
    } catch {
      // No server for a moment: try again next round.
    }
  }
  void check()
  setInterval(() => {
    if (document.visibilityState === 'visible') void check()
  }, EVERY_MS)
}
