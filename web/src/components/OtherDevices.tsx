import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import { useAsync } from '../lib/useAsync'
import { relativeTime } from '../lib/format'
import {
  forgetSyncFolder,
  onPhone,
  onSharing,
  pickSyncFolder,
  sharingState,
  syncDevices,
  type SharingState,
} from '../phone/engine'

/* Named strategies, groups, plans and settings, the same on the Mac and the
   iPhone. Both write to "Tasty Desk Sync" in iCloud Drive: the Mac finds it on
   its own, the iPhone is pointed at it once. This says whether it is working,
   when it last did, and what it can see. */

const button =
  'rounded-sm border border-line px-3 py-2 text-[14px] transition-colors hover:bg-hover disabled:opacity-50'

function useSharing(): SharingState {
  const [state, setState] = useState(sharingState)
  useEffect(() => onSharing(setState), [])
  return state
}

export function OtherDevices() {
  const { data } = useAsync(() => api.deviceSync(), [], 20_000)
  const sharing = useSharing()
  const [busy, setBusy] = useState(false)

  const report = onPhone ? (sharing.report ?? data) : data
  const where = onPhone ? (sharing.folder ? `iCloud Drive › ${sharing.folder}` : null) : (data?.where ?? null)
  const others = report?.devices?.filter((d) => d.same_account) ?? []
  const problems = [...(report?.problems ?? []), ...(onPhone && sharing.error ? [sharing.error] : [])]

  async function act(fn: () => Promise<unknown>) {
    setBusy(true)
    try {
      await fn()
    } catch {
      // The state carries the reason.
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="sheened space-y-3 rounded-card border border-line bg-raised p-4 shadow-[var(--shadow-sm)]">
      <p className="text-[14px] text-muted">
        Named strategies and their trades, links, pairing and roll answers, plans, settings and questions are
        kept the same on your Mac and your iPhone. Positions and prices come from tastytrade on each.
      </p>

      {where ? (
        <div className="text-[15px]">
          <span className="text-muted">Shared through </span>
          {where}
          {report?.at && <span className="text-muted"> · last {relativeTime(report.at)}</span>}
        </div>
      ) : onPhone ? (
        <p className="text-[15px]">
          Not shared yet. On the Mac, Tasty Desk keeps a folder called{' '}
          <strong className="font-medium">Tasty Desk Sync</strong> in iCloud Drive. Choose it here once: Browse
          → iCloud Drive → Tasty Desk Sync → Open.
        </p>
      ) : (
        <p className="text-[15px]">
          iCloud Drive is off on this Mac, so nothing is shared. Turn it on in System Settings → your name →
          iCloud → iCloud Drive, then restart Tasty Desk.
        </p>
      )}

      {others.length > 0 && (
        <ul className="text-[14px] text-muted">
          {others.map((d) => (
            <li key={`${d.label}-${d.written_at}`}>
              {d.label}
              {d.written_at ? `, last ${relativeTime(d.written_at)}` : ''}
            </li>
          ))}
        </ul>
      )}
      {where && others.length === 0 && report?.state === 'ok' && (
        <p className="text-[14px] text-muted">
          No other device here yet. {onPhone ? 'Open Tasty Desk on the Mac.' : 'Point the iPhone app at this folder.'}
        </p>
      )}
      {onPhone && sharing.waiting > 0 && (
        <p className="text-[14px] text-muted">iCloud is still bringing {sharing.waiting} file(s) down.</p>
      )}
      {problems.length > 0 && (
        <ul className="space-y-1 text-[14px] text-danger">
          {problems.map((p) => (
            <li key={p}>{p}</li>
          ))}
        </ul>
      )}

      {onPhone && (
        <div className="flex flex-wrap gap-2">
          {sharing.folder ? (
            <>
              <button className={button} disabled={busy} onClick={() => act(syncDevices)}>
                {busy ? 'Sharing…' : 'Share now'}
              </button>
              <button className={button} disabled={busy} onClick={() => act(pickSyncFolder)}>
                Choose another folder
              </button>
              <button className={`${button} text-danger`} disabled={busy} onClick={() => act(forgetSyncFolder)}>
                Stop sharing
              </button>
            </>
          ) : (
            <button
              className={`${button} border-accent text-accent`}
              disabled={busy}
              onClick={() => act(pickSyncFolder)}
            >
              Choose the Tasty Desk Sync folder
            </button>
          )}
        </div>
      )}
    </div>
  )
}
