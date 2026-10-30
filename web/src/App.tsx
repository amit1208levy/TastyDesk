import { useState, type ReactNode } from 'react'
import { Shell, type TabId } from './components/Shell'
import { Positions } from './pages/Positions'
import { Performance } from './pages/Performance'
import { Rules } from './pages/Rules'
import { Health } from './pages/Health'
import { History } from './pages/History'
import { Activity } from './pages/Activity'
import { Grouping } from './pages/Grouping'
import { Legs } from './pages/Legs'
import { Strategies } from './pages/Strategies'
import { AllocationPage } from './pages/AllocationPage'
import { Tom } from './pages/Tom'
import { Scanner } from './pages/Scanner'
import { Today } from './pages/Today'
import { api } from './lib/api'
import { useAsync } from './lib/useAsync'
import { relativeTime, shortDate } from './lib/format'
import { EraProvider, type Era } from './lib/era'

/* New Levy, or the old self: which life the whole app is reading. */
function EraSwitch({ era }: { era: Era }) {
  if (!era.fresh) return null
  return era.oldSelf ? (
    <button
      onClick={() => era.setOldSelf(false)}
      title="Back to the account as it is since the fresh start"
      className="rounded-full border border-tested/50 bg-tested-soft px-3 py-1 text-[13px] font-medium text-tested"
    >
      Back to New Levy
    </button>
  ) : (
    // Short, so the tabs keep their room: the date is in the tooltip.
    <button
      onClick={() => era.setOldSelf(true)}
      title={`New Levy — everything reads from ${shortDate(era.fresh.since)}. Click to show the old self.`}
      className="whitespace-nowrap rounded-full border border-line px-3 py-1 text-[13px] hover:bg-hover"
    >
      <span className="gold-text font-semibold">New Levy</span>
      <span className="ml-1.5 text-muted">· old self</span>
    </button>
  )
}

function ConnectionPill() {
  const { data, reload } = useAsync(() => api.health(), [], 60_000)
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)
  if (!data) return null
  const good = data.credentials_present && data.session_ok
  // Click to pull fresh data from tastytrade now, then redraw every page with it.
  async function refresh() {
    if (busy) return
    setBusy(true)
    setProblem(null)
    try {
      await api.sync()
      window.location.reload()
    } catch (e) {
      setProblem(e instanceof Error ? e.message : 'Refresh failed')
      setBusy(false)
      reload()
    }
  }
  return (
    <button
      onClick={() => void refresh()}
      disabled={busy}
      title={problem ?? data.last_error ?? `Synced ${relativeTime(data.last_sync)}. Click to refresh now.`}
      className={`hidden items-center gap-1.5 rounded-full border px-2 py-0.5 text-[13px] transition-colors sm:inline-flex ${
        good && !problem
          ? 'border-line bg-sunken text-muted hover:bg-hover hover:text-ink'
          : 'border-danger/40 bg-danger-soft text-danger'
      } ${busy ? 'cursor-wait' : 'cursor-pointer'}`}
    >
      <span className={`h-1.5 w-1.5 rounded-full ${good && !problem ? 'bg-ok' : 'bg-danger'} ${busy ? 'animate-pulse' : ''}`} />
      {busy ? 'Refreshing…' : problem ? 'refresh failed' : good ? relativeTime(data.last_sync) : 'not connected'}
    </button>
  )
}

/* Pages that belong together, in one tab with a sub-tab each. */
function SubTabs<T extends string>({ views, initial }: { views: [T, string, ReactNode][]; initial?: T }) {
  const [view, setView] = useState<T>(initial ?? views[0][0])
  const page = views.find(([id]) => id === view)![2]
  return (
    <div>
      <div className="mb-6 flex gap-1 overflow-x-auto border-b border-line">
        {views.map(([id, label]) => (
          <button
            key={id}
            onClick={() => setView(id)}
            className={`-mb-px whitespace-nowrap border-b-2 px-4 py-2 text-[15px] transition-colors ${
              view === id
                ? 'border-accent font-medium text-accent'
                : 'border-transparent text-muted hover:text-ink'
            }`}
          >
            {label}
          </button>
        ))}
      </div>
      {page}
    </div>
  )
}

export default function App() {
  const [tab, setTab] = useState<TabId>('today')
  // A position picked on Today opens on Positions, scrolled to its row.
  const [focus, setFocus] = useState<string | null>(null)

  function go(next: TabId) {
    setFocus(null)
    setTab(next)
  }

  return (
    <EraProvider>
    {(era) => (
    <Shell
      tab={tab}
      onTab={go}
      status={
        <>
          <EraSwitch era={era} />
          <ConnectionPill />
        </>
      }
    >
    {/* A new key when the era flips, so every page asks again for its own. */}
    <div key={era.active ? 'new' : 'old'}>
      {tab === 'today' && (
        <Today
          onOpen={(id) => {
            setFocus(id)
            setTab('positions')
          }}
          onTab={go}
        />
      )}
      {tab === 'positions' && (
        <SubTabs
          views={[
            ['positions', 'By position', <Positions initialFocus={focus} />],
            ['strategies', 'By strategy', <Strategies />],
            ['legs', 'Open legs', <Legs />],
            ['grouping', 'Grouping', <Grouping />],
          ]}
        />
      )}
      {tab === 'performance' && (
        <SubTabs
          views={[
            ['results', 'Results', <Performance />],
            ['history', 'Closed trades', <History />],
          ]}
        />
      )}
      {tab === 'plan' && (
        <SubTabs
          views={[
            ['tom', "Tom's plan", <Tom />],
            ['allocation', 'Allocation & what-if', <AllocationPage />],
            ['rules', 'Your rules', <Rules />],
          ]}
        />
      )}
      {tab === 'scanner' && <Scanner />}
      {tab === 'activity' && <Activity />}
      {tab === 'health' && <Health />}
    </div>
    </Shell>
    )}
    </EraProvider>
  )
}
