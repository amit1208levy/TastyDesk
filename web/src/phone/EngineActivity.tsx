import { useEffect, useState } from 'react'
import { describeWaiting, engineStatus } from './engine'
import type { EngineStatus } from './boot'

/* What the engine inside the app is busy with, so a page that will not load
   says what it is waiting for. Labels and timings only, never data or keys. */

function useEngineStatus(everyMs = 2_000): EngineStatus | null {
  const [status, setStatus] = useState<EngineStatus | null>(null)
  useEffect(() => {
    let live = true
    const tick = async () => {
      const next = await engineStatus().catch(() => null)
      if (live) setStatus(next)
    }
    void tick()
    const id = setInterval(() => void tick(), everyMs)
    return () => {
      live = false
      clearInterval(id)
    }
  }, [everyMs])
  return status
}

/** One line under a loading screen, once it has been loading for a while. */
export function WaitingLine({ afterMs = 5_000 }: { afterMs?: number }) {
  const [shown, setShown] = useState(false)
  useEffect(() => {
    const id = setTimeout(() => setShown(true), afterMs)
    return () => clearTimeout(id)
  }, [afterMs])
  const status = useEngineStatus()
  if (!shown) return null
  const waiting = describeWaiting(status)
  const lastError = status?.recent.find((r) => r.error)
  return (
    <div className="space-y-1 text-[13px] text-faint">
      <div>{waiting ? `Waiting for ${waiting}` : 'Working…'}</div>
      {lastError && (
        <div className="text-danger/85">
          Last error, tastytrade {lastError.call}: {lastError.error}
        </div>
      )}
    </div>
  )
}

/** The full picture, on the setup page. */
export function EngineActivity() {
  const status = useEngineStatus()
  if (!status) return null
  return (
    <section className="rounded-card border border-line bg-raised p-5">
      <h3 className="text-[17px] font-semibold">What the app is doing</h3>
      <p className="mt-1 text-[14px] text-muted">
        {describeWaiting(status) ? `Waiting for ${describeWaiting(status)}.` : 'Nothing is waiting right now.'}
        {status.requests.length > 0 &&
          ` Pages waiting: ${status.requests.map((r) => `${r.what} (${Math.round(r.seconds)} s)`).join(', ')}.`}
      </p>
      {status.recent.length > 0 && (
        <table className="num mt-3 w-full text-[13px]">
          <tbody>
            {status.recent.slice(0, 10).map((r, i) => (
              <tr key={i} className="border-t border-line/60">
                <td className="py-1 pr-3">tastytrade {r.call}</td>
                <td className="py-1 pr-3 text-right text-muted">{r.seconds} s</td>
                <td className={`py-1 ${r.error ? 'text-danger' : 'text-ok'}`}>{r.error ?? 'ok'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  )
}
