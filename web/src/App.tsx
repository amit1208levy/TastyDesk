import { useState } from 'react'
import { Shell, type TabId } from './components/Shell'
import { Positions } from './pages/Positions'
import { Performance } from './pages/Performance'
import { Rules } from './pages/Rules'
import { Health } from './pages/Health'
import { History } from './pages/History'
import { Brief } from './pages/Brief'
import { Ask } from './pages/Ask'
import { Activity } from './pages/Activity'
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
      className={`hidden items-center gap-1.5 rounded-full border px-2 py-0.5 text-[11px] sm:inline-flex ${
        good ? 'border-line bg-sunken text-muted' : 'border-danger/40 bg-danger-soft text-danger'
      }`}
    >
      <span className={`h-1.5 w-1.5 rounded-full ${good ? 'bg-ok' : 'bg-danger'}`} />
      {good ? relativeTime(data.last_sync) : 'not connected'}
    </span>
  )
}

export default function App() {
  const [tab, setTab] = useState<TabId>('positions')

  return (
    <Shell tab={tab} onTab={setTab} status={<ConnectionPill />}>
      {tab === 'brief' && <Brief />}
      {tab === 'ask' && <Ask />}
      {tab === 'positions' && <Positions />}
      {tab === 'performance' && <Performance />}
      {tab === 'rules' && <Rules />}
      {tab === 'history' && <History />}
      {tab === 'activity' && <Activity />}
      {tab === 'health' && <Health />}
    </Shell>
  )
}
