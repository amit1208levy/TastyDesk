import { useState, type FormEvent } from 'react'
import { forgetCredentials, importDatabase, saveCredentials } from './engine'
import { useEngineState } from './useEngineState'
import type { ImportReport } from './boot'
import { EngineActivity } from './EngineActivity'

/* The iPhone's own setup: the two tastytrade secrets, and the trade database
   brought over from the Mac. On the Mac the Keychain prompt in a terminal does
   this; the phone has no terminal, so it is a form, and the values go from
   the form straight into the iOS Keychain (this device only). They are never
   shown again, never logged, and never leave the phone except to tastytrade. */

const field =
  'mono mt-1 w-full rounded-sm border border-line bg-sunken px-2.5 py-2.5 text-[16px] outline-none focus:border-accent'
const button =
  'rounded-sm border border-line px-3 py-2 text-[15px] transition-colors hover:bg-hover disabled:opacity-50'

function Keys({ hasKeys }: { hasKeys: boolean }) {
  const [editing, setEditing] = useState(!hasKeys)
  const [secret, setSecret] = useState('')
  const [token, setToken] = useState('')
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<string | null>(null)

  async function save(event: FormEvent) {
    event.preventDefault()
    if (!secret.trim() || !token.trim()) return
    setBusy(true)
    setMessage(null)
    try {
      await saveCredentials(secret.trim(), token.trim())
      setEditing(false)
      setMessage('Saved in the iPhone Keychain. Open Health and press Sync.')
    } catch {
      setMessage('Could not save them. Nothing was kept.')
    } finally {
      // Out of the page's memory as soon as they are in the Keychain.
      setSecret('')
      setToken('')
      setBusy(false)
    }
  }

  async function forget() {
    setBusy(true)
    try {
      await forgetCredentials()
      setEditing(true)
      setMessage('Removed from this iPhone.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="rounded-card border border-line bg-raised p-5">
      <h3 className="text-[17px] font-semibold">tastytrade connection</h3>
      {!editing ? (
        <>
          <p className="mt-1 text-[15px] text-muted">
            The client secret and refresh token are in this iPhone's Keychain.
          </p>
          <div className="mt-3 flex gap-2">
            <button className={button} disabled={busy} onClick={() => setEditing(true)}>
              Replace them
            </button>
            <button className={`${button} text-danger`} disabled={busy} onClick={forget}>
              Remove from this iPhone
            </button>
          </div>
        </>
      ) : (
        <form onSubmit={save} className="mt-2 space-y-3" autoComplete="off">
          <p className="text-[14px] text-muted">
            From my.tastytrade.com → Manage → My Profile → API → OAuth Applications. Use an application
            whose grant has the <strong className="font-medium text-ink">read</strong> scope only: with it,
            this app cannot place, change or cancel an order.
          </p>
          <label className="block text-[15px]">
            Client secret
            <input
              type="password"
              className={field}
              value={secret}
              onChange={(e) => setSecret(e.target.value)}
              autoComplete="off"
              autoCapitalize="off"
              autoCorrect="off"
              spellCheck={false}
            />
          </label>
          <label className="block text-[15px]">
            Refresh token
            <input
              type="password"
              className={field}
              value={token}
              onChange={(e) => setToken(e.target.value)}
              autoComplete="off"
              autoCapitalize="off"
              autoCorrect="off"
              spellCheck={false}
            />
          </label>
          <div className="flex gap-2">
            <button type="submit" className={`${button} border-accent text-accent`} disabled={busy || !secret || !token}>
              {busy ? 'Saving…' : 'Save in Keychain'}
            </button>
            {hasKeys && (
              <button type="button" className={button} onClick={() => setEditing(false)}>
                Cancel
              </button>
            )}
          </div>
          <p className="text-[13px] text-faint">
            Kept on this iPhone only: never synced to iCloud Keychain, never moved to another phone, never
            shown again.
          </p>
        </form>
      )}
      {message && <p className="mt-3 text-[14px] text-muted">{message}</p>}
    </section>
  )
}

const COUNT_LABELS: Record<string, string> = {
  strategies: 'Strategies',
  named_strategies: 'Named strategies',
  named_strategy_members: 'Trades in named strategies',
  trade_plans: 'Trade plans',
  settings: 'Settings',
  transactions: 'Transactions',
}

function Database() {
  const [busy, setBusy] = useState(false)
  const [report, setReport] = useState<ImportReport | null>(null)

  async function pick(file: File | undefined) {
    if (!file) return
    setBusy(true)
    setReport(null)
    try {
      setReport(await importDatabase(file))
    } catch {
      setReport({ ok: false, reason: 'The file could not be read.' })
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="rounded-card border border-line bg-raised p-5">
      <h3 className="text-[17px] font-semibold">Your trade database</h3>
      <p className="mt-1 text-[14px] text-muted">
        Named strategies, plans, groups and settings live in the Mac's database, and much of it cannot be
        rebuilt from tastytrade. To bring it over: on the Mac run{' '}
        <code className="mono rounded-sm bg-sunken px-1">./scripts/export-for-phone.sh</code>, AirDrop the
        file to this iPhone, then choose it here. It replaces what is on the phone; the Mac keeps its own.
      </p>
      <label className={`${button} mt-3 inline-block cursor-pointer`}>
        {busy ? 'Importing…' : 'Choose the database file'}
        <input
          type="file"
          className="hidden"
          disabled={busy}
          onChange={(e) => {
            void pick(e.target.files?.[0])
            e.target.value = ''
          }}
        />
      </label>
      {report && !report.ok && <p className="mt-3 text-[15px] text-danger">{report.reason}</p>}
      {report?.ok && report.counts && (
        <div className="mt-3">
          <p className="text-[15px]">Imported. Check these match what the Mac printed:</p>
          <table className="num mt-2 text-[15px]">
            <tbody>
              {Object.entries(report.counts).map(([table, n]) => (
                <tr key={table}>
                  <td className="pr-6 text-muted">{COUNT_LABELS[table] ?? table}</td>
                  <td className="text-right">{n}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}

export function PhoneSetup() {
  const state = useEngineState()
  if (state.phase !== 'ready') return null
  return (
    <div className="mx-auto max-w-2xl space-y-4">
      <Keys hasKeys={state.hasKeys} />
      <EngineActivity />
      <Database />
      <p className="px-1 text-[13px] text-faint">
        Engine: the Mac app's own code, built from commit <span className="mono">{state.commit}</span>. Read
        only: it never places, changes or cancels an order.
      </p>
    </div>
  )
}
