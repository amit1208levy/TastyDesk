import { createContext, useContext, useState, type ReactNode } from 'react'
import { api, setEraSince } from './api'
import { useAsync } from './useAsync'
import type { FreshStart } from '../types'

/* New Levy: the account read as if it started on one day.

   Everything from before that day — and every strategy not carried into it —
   is the old life. It is hidden by default and never deleted: "Show the old
   self" puts the whole history back, everywhere at once. Open positions are
   never hidden by this; a risk that is still on the books is not the past. */

const KEY = 'tastydesk.oldSelf'

export interface Era {
  fresh: FreshStart | null
  /** True while the fresh start is in force (set, and the old self not shown). */
  active: boolean
  oldSelf: boolean
  setOldSelf: (on: boolean) => void
  since: string | null
  isKept: (namedId: string) => boolean
  /** A trade belongs to the new life: still open, or closed on or after the start. */
  inEra: (t: { is_open: boolean; closed: string | null }) => boolean
}

const EraContext = createContext<Era>({
  fresh: null,
  active: false,
  oldSelf: false,
  setOldSelf: () => {},
  since: null,
  isKept: () => true,
  inEra: () => true,
})

export function useEra(): Era {
  return useContext(EraContext)
}

function readOldSelf(): boolean {
  try {
    return localStorage.getItem(KEY) === '1'
  } catch {
    return false
  }
}

export function EraProvider({ children }: { children: (era: Era) => ReactNode }) {
  const settings = useAsync(() => api.settings(), [])
  const [oldSelf, setOld] = useState(readOldSelf)
  if (!settings.data && !settings.error) return null

  const fresh = settings.data?.fresh_start ?? null
  const active = fresh !== null && !oldSelf
  const since = active ? fresh!.since : null
  // Before any page asks for anything: every report starts on this day.
  setEraSince(since)

  const era: Era = {
    fresh,
    active,
    oldSelf,
    setOldSelf: (on) => {
      try {
        localStorage.setItem(KEY, on ? '1' : '0')
      } catch {
        /* a remembered toggle is a convenience */
      }
      setOld(on)
    },
    since,
    isKept: (id) => fresh === null || fresh.kept.includes(id),
    inEra: (t) => since === null || t.is_open || (t.closed !== null && t.closed.slice(0, 10) >= since),
  }
  return <EraContext.Provider value={era}>{children(era)}</EraContext.Provider>
}
