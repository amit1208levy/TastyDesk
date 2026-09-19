import { useState } from 'react'
import { Loading, ErrorPanel, SectionHeading } from '../components/States'
import { api } from '../lib/api'
import { useAsync } from '../lib/useAsync'
import { relativeTime, fullDate } from '../lib/format'

function Row({ label, ok, value, hint }: { label: string; ok: boolean | null; value: string; hint?: string }) {
  return (
    <div className="flex items-baseline gap-3 border-b border-line/60 py-3 last:border-0">
      <span
        className={`mt-1 h-1.5 w-1.5 shrink-0 rounded-full ${
          ok === null ? 'bg-line-strong' : ok ? 'bg-ok' : 'bg-danger'
        }`}
      />
      <div className="min-w-0 flex-1">
        <div className="text-[16px]">{label}</div>
        {hint && <div className="text-[14px] text-faint">{hint}</div>}
      </div>
      <div className="num shrink-0 text-[16px] text-muted">{value}</div>
    </div>
  )
}

export function Health() {
  const { data, error, loading, reload } = useAsync(() => api.health(), [], 30_000)
  const [syncing, setSyncing] = useState(false)
  const [syncMsg, setSyncMsg] = useState<string | null>(null)

  async function runSync(full: boolean) {
    setSyncing(true)
    setSyncMsg(null)
    try {
      const r = await fetch(`/api/sync?full=${full}`, { method: 'POST' })
      const body = await r.json()
      setSyncMsg(
        r.ok
          ? `Imported ${body.transactions_imported} transactions, rebuilt ${body.strategies_built} strategies (${body.open_strategies} open).`
          : (body.detail ?? `Sync failed (${r.status})`),
      )
    } catch (e) {
      setSyncMsg(e instanceof Error ? e.message : String(e))
    } finally {
      setSyncing(false)
      reload()
    }
  }

  if (error) return <ErrorPanel error={error} onRetry={reload} />
  if (loading && !data) return <Loading />
  if (!data) return null

  return (
    <div className="max-w-2xl space-y-5">
      <section>
        <SectionHeading title="Connection" hint="what the app can and cannot reach right now" />
        <div className="rounded-card border border-line bg-raised px-4 py-1">
          <Row
            label="Credentials in the Keychain"
            ok={data.credentials_present}
            value={data.credentials_present ? 'present' : 'missing'}
            hint={data.credentials_present ? undefined : 'Run ./scripts/setup-credentials.sh'}
          />
          <Row
            label="tastytrade session"
            ok={data.session_ok}
            value={data.session_ok ? 'connected' : 'not connected'}
            hint="OAuth, read scope only"
          />
          <Row
            label="Accounts visible"
            ok={data.account_count > 0 ? true : null}
            value={String(data.account_count)}
          />
          <Row
            label="Last sync"
            ok={data.last_sync ? true : null}
            value={relativeTime(data.last_sync)}
            hint={data.last_sync ? fullDate(data.last_sync) : 'never run'}
          />
        </div>

        {data.last_error && (
          <div
            className={`mt-2.5 rounded-card border px-3 py-3 text-[14px] whitespace-pre-wrap ${
              data.credentials_present
                ? 'border-danger/40 bg-danger-soft text-danger'
                : 'border-line bg-sunken text-muted'
            }`}
          >
            {data.last_error}
          </div>
        )}
      </section>

      <section>
        <SectionHeading title="Sync" hint="pulls transactions, rebuilds trades, re-prices open positions" />
        <div className="rounded-card border border-line bg-raised p-4">
          <div className="flex flex-wrap gap-2">
            <button
              onClick={() => runSync(false)}
              disabled={syncing}
              className="rounded-sm border border-line-strong px-3 py-3.5 text-[14px] transition-colors hover:bg-hover disabled:opacity-50"
            >
              {syncing ? 'Syncing…' : 'Sync new activity'}
            </button>
            <button
              onClick={() => runSync(true)}
              disabled={syncing}
              className="rounded-sm border border-line px-3 py-3.5 text-[14px] text-muted transition-colors hover:bg-hover disabled:opacity-50"
            >
              Rebuild from full history
            </button>
          </div>
          {syncMsg && <div className="mt-2.5 text-[14px] text-muted">{syncMsg}</div>}
        </div>
      </section>

      <section>
        <SectionHeading title="Security" />
        <ul className="space-y-1.5 rounded-card border border-line bg-raised px-4 py-3 text-[14px] text-muted">
          <li>
            The API key carries the <strong className="font-medium text-ink">read</strong> scope only — it
            cannot place, change or cancel an order.
          </li>
          <li>
            Your client secret and refresh token live in the macOS Keychain, never in a file, a log or a
            commit.
          </li>
          <li>This server listens on 127.0.0.1 only. Nothing is reachable from the network.</li>
          <li>Your trading history is a local SQLite file readable only by your user account.</li>
        </ul>
      </section>
    </div>
  )
}
