import { useCallback, useEffect, useState } from 'react'
import { OTHER_DEVICE_CHANGED } from './devices'

export interface AsyncState<T> {
  data: T | null
  error: Error | null
  loading: boolean
  reload: () => void
}

/** Minimal data hook. Keeps the previous value visible while refetching so the
    dashboard does not flash empty on every poll. */
export function useAsync<T>(fn: () => Promise<T>, deps: unknown[] = [], pollMs?: number): AsyncState<T> {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<Error | null>(null)
  const [loading, setLoading] = useState(true)
  const [tick, setTick] = useState(0)

  const reload = useCallback(() => setTick((t) => t + 1), [])

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    fn()
      .then((d) => {
        if (cancelled) return
        setData(d)
        setError(null)
      })
      .catch((e) => {
        if (cancelled) return
        setError(e instanceof Error ? e : new Error(String(e)))
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick])

  // Another device changed something set by hand (lib/devices.ts): read again.
  useEffect(() => {
    window.addEventListener(OTHER_DEVICE_CHANGED, reload)
    return () => window.removeEventListener(OTHER_DEVICE_CHANGED, reload)
  }, [reload])

  useEffect(() => {
    if (!pollMs) return
    const id = setInterval(reload, pollMs)
    return () => clearInterval(id)
  }, [pollMs, reload])

  return { data, error, loading, reload }
}
