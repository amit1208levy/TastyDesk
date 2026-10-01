import { useState, type ComponentType } from 'react'
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
import { WhatIf } from './pages/WhatIf'
import { Tom } from './pages/Tom'
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

/* Two pages that belong together, in one tab with a sub-tab each. */
function SubTabs<T extends string>({ views }: { views: [T, string, ComponentType][] }) {
  const [view, setView] = useState<T>(views[0][0])
  const Page = views.find(([id]) => id === view)![2]
  return (
    <div>
      <div className="mb-5 flex gap-1 border-b border-line">
        {views.map(([id, label]) => (
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
      <Page />
    </div>
  )
}

export default function App() {
  const [tab, setTab] = useState<TabId>('positions')

  return (
    <Shell tab={tab} onTab={setTab} status={<ConnectionPill />}>
      {tab === 'legs' && (
        <SubTabs
          views={[
            ['legs', 'Open legs', Legs],
            ['grouping', 'Grouping', Grouping],
          ]}
        />
      )}
      {tab === 'strategies' && <Strategies />}
      {tab === 'positions' && <Positions />}
      {tab === 'tom' && <Tom />}
      {tab === 'whatif' && <WhatIf />}
      {tab === 'performance' && <Performance />}
      {tab === 'rules' && <Rules />}
      {tab === 'history' && (
        <SubTabs
          views={[
            ['history', 'Closed trades', History],
            ['health', 'Health', Health],
          ]}
        />
      )}
      {tab === 'activity' && <Activity />}
    </Shell>
  )
}
