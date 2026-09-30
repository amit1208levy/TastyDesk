import { useState } from 'react'
import { Shell, type TabId } from './components/Shell'
import { Positions } from './pages/Positions'
import { Performance } from './pages/Performance'
import { Rules } from './pages/Rules'
import { Health } from './pages/Health'
import { History } from './pages/History'
import { Ask } from './pages/Ask'
import { Activity } from './pages/Activity'
import { Grouping } from './pages/Grouping'
import { Legs } from './pages/Legs'
import { Strategies } from './pages/Strategies'
import { WhatIf } from './pages/WhatIf'
import { api } from './lib/api'
import { useAsync } from './lib/useAsync'
import { relativeTime } from './lib/format'

function ConnectionPill() {
  const { data } = useAsync(() => api.health(), [], 60_000)
  if (!data) return null
  const good = data.credentials_present && data.session_ok
  return (
    <span
      title={data.last_error ?? `Synced ${relativeTime(data.last_sync)}`}
      className={`hidden items-center gap-1.5 rounded-full border px-2 py-0.5 text-[13px] sm:inline-flex ${
        good ? 'border-line bg-sunken text-muted' : 'border-danger/40 bg-danger-soft text-danger'
      }`}
    >
      <span className={`h-1.5 w-1.5 rounded-full ${good ? 'bg-ok' : 'bg-danger'}`} />
      {good ? relativeTime(data.last_sync) : 'not connected'}
    </span>
  )
}

/* Legs and grouping are one job — which legs belong together — so they share
   a tab, with the two views side by side as sub-tabs. */
function LegsAndGrouping() {
  const [view, setView] = useState<'legs' | 'grouping'>('legs')
  return (
    <div>
      <div className="mb-5 flex gap-1 border-b border-line">
        {(
          [
            ['legs', 'Open legs'],
            ['grouping', 'Grouping'],
          ] as const
        ).map(([id, label]) => (
          <button
            key={id}
            onClick={() => setView(id)}
            className={`-mb-px border-b-2 px-4 py-2 text-[15px] transition-colors ${
              view === id
                ? 'border-accent font-medium text-accent'
                : 'border-transparent text-muted hover:text-ink'
            }`}
          >
            {label}
          </button>
        ))}
      </div>
      {view === 'legs' ? <Legs /> : <Grouping />}
    </div>
  )
}

export default function App() {
  const [tab, setTab] = useState<TabId>('positions')

  return (
    <Shell tab={tab} onTab={setTab} status={<ConnectionPill />}>
      {tab === 'ask' && <Ask />}
      {tab === 'legs' && <LegsAndGrouping />}
      {tab === 'strategies' && <Strategies />}
      {tab === 'positions' && <Positions />}
      {tab === 'whatif' && <WhatIf />}
      {tab === 'performance' && <Performance />}
      {tab === 'rules' && <Rules />}
      {tab === 'history' && <History />}
      {tab === 'activity' && <Activity />}
      {tab === 'health' && <Health />}
    </Shell>
  )
}
