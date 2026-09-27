import type {
  AppEvent,
  ScenarioResult,
  DailyBrief,
  MatchReport,
  NamedStrategy,
  OpenLeg,
  Health,
  LossShapeReport,
  PerformanceStats,
  PairCandidate,
  PairDecision,
  PortfolioSummary,
  QuestionEntry,
  RollCandidate,
  RuleAdherence,
  UnsettledTrade,
  StrategyView,
  GreekTotals,
  Period,
  PeriodIndex,
  Settings,
  FieldCatalogue,
} from '../types'
import type { PayoffCurve } from '../components/PayoffChart'

/** Turn a period (and any extra params) into a query string. */
function query(p?: Period, extra?: Record<string, string>): string {
  const params = new URLSearchParams(extra)
  if (p?.from) params.set('from', p.from)
  if (p?.to) params.set('to', p.to)
  const s = params.toString()
  return s ? `?${s}` : ''
}

export class ApiError extends Error {
  status: number
  detail?: string

  constructor(message: string, status: number, detail?: string) {
    super(message)
    this.status = status
    this.detail = detail
  }
}

async function get<T>(path: string): Promise<T> {
  let res: Response
  try {
    res = await fetch(`/api${path}`, { headers: { Accept: 'application/json' } })
  } catch {
    // The backend runs on this machine, so a network failure here almost always
    // means the server is not running rather than anything to do with tastytrade.
    throw new ApiError('Tasty Desk backend is not reachable', 0, 'Is the server running?')
  }
  if (!res.ok) {
    let detail: string | undefined
    try {
      detail = (await res.json())?.detail
    } catch {
      detail = undefined
    }
    throw new ApiError(`Request failed (${res.status})`, res.status, detail)
  }
  return res.json() as Promise<T>
}

export const api = {
  health: () => get<Health>('/health'),
  summary: () => get<PortfolioSummary>('/portfolio/summary'),
  greeks: () => get<GreekTotals>('/portfolio/greeks'),
  openStrategies: () => get<StrategyView[]>('/strategies/open'),
  closedStrategies: (limit = 200) => get<StrategyView[]>(`/strategies/closed?limit=${limit}`),
  payoff: (id: string) => get<PayoffCurve>(`/strategies/payoff?id=${encodeURIComponent(id)}`),
  openLegs: () => get<OpenLeg[]>('/legs/open'),
  namedStrategies: () => get<NamedStrategy[]>('/strategies/named'),
  strategyMatches: (id: string) =>
    get<MatchReport>(`/strategies/named/${encodeURIComponent(id)}/matches`),
  allMatches: () => get<Record<string, MatchReport>>('/strategies/named/matches'),
  createNamedStrategy: async (name: string, trade_ids: string[]) => {
    const res = await fetch('/api/strategies/named', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, trade_ids }),
    })
    if (!res.ok) {
      const body = await res.json().catch(() => ({}))
      throw new ApiError(body?.detail ?? `Could not create that (${res.status})`, res.status)
    }
    return res.json() as Promise<NamedStrategy>
  },
  adoptMatches: async (id: string, trade_ids: string[]) => {
    const res = await fetch(`/api/strategies/named/${encodeURIComponent(id)}/adopt`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ trade_ids }),
    })
    if (!res.ok) throw new ApiError(`Could not add those (${res.status})`, res.status)
    return res.json() as Promise<NamedStrategy>
  },
  dropMember: async (id: string, trade_id: string) => {
    const res = await fetch(`/api/strategies/named/${encodeURIComponent(id)}/drop`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ trade_id }),
    })
    if (!res.ok) throw new ApiError(`Could not remove that trade (${res.status})`, res.status)
    return res.json() as Promise<NamedStrategy>
  },
  deleteNamedStrategy: async (id: string) => {
    const res = await fetch(`/api/strategies/named/${encodeURIComponent(id)}`, { method: 'DELETE' })
    if (!res.ok) throw new ApiError(`Could not delete that (${res.status})`, res.status)
    return res.json()
  },
  pairingDecisions: () => get<PairDecision[]>('/pairing/decisions'),
  undoPairing: async (pattern: string) => {
    const res = await fetch('/api/pairing/undo', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ pattern }),
    })
    if (!res.ok) throw new ApiError(`Could not reopen that (${res.status})`, res.status)
    return res.json()
  },
  pairingCandidates: () => get<PairCandidate[]>('/pairing/candidates'),
  decidePairing: async (pattern: string, decision: 'merge' | 'separate') => {
    const res = await fetch('/api/pairing/decide', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ pattern, decision }),
    })
    if (!res.ok) {
      const body = await res.json().catch(() => ({}))
      throw new ApiError(body?.detail ?? `Could not save that (${res.status})`, res.status)
    }
    return res.json()
  },
  needsReview: () => get<UnsettledTrade[]>('/needs-review'),
  events: (limit = 200, minSeverity?: string) =>
    get<AppEvent[]>(
      `/events?limit=${limit}${minSeverity ? `&min_severity=${minSeverity}` : ''}`,
    ),
  questionThread: (limit = 30) => get<QuestionEntry[]>(`/ask?limit=${limit}`),
  ask: async (question: string): Promise<{ id: number }> => {
    const res = await fetch('/api/ask', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question }),
    })
    if (!res.ok) {
      const body = await res.json().catch(() => ({}))
      throw new ApiError(body?.detail ?? `Could not queue that (${res.status})`, res.status)
    }
    return res.json()
  },
  brief: () => get<{ available: boolean; brief: DailyBrief | null }>('/brief'),
  rollCandidates: () => get<RollCandidate[]>('/grouping/roll-candidates'),
  decideRoll: async (closed_id: string, opened_id: string, decision: 'linked' | 'separate') => {
    const res = await fetch('/api/grouping/roll-decision', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ closed_id, opened_id, decision }),
    })
    if (!res.ok) throw new ApiError(`Could not record that (${res.status})`, res.status)
    return res.json()
  },

  separateAllRolls: async () => {
    const res = await fetch('/api/grouping/roll-decision', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ all: 'separate' }),
    })
    if (!res.ok) throw new ApiError(`Could not record those (${res.status})`, res.status)
    return res.json() as Promise<{ separated: number }>
  },

  linkStrategies: async (strategy_ids: string[]): Promise<{ linked: number }> => {
    const res = await fetch('/api/grouping/link', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ strategy_ids }),
    })
    if (!res.ok) {
      const body = await res.json().catch(() => ({}))
      throw new ApiError(body?.detail ?? `Link failed (${res.status})`, res.status)
    }
    return res.json()
  },
  periods: () => get<PeriodIndex>('/performance/periods'),
  scenario: (price: number, iv: number, days: number, byBeta = true) =>
    get<ScenarioResult>(
      `/scenario?price=${price}&iv=${iv}&days=${days}&mode=${byBeta ? 'beta' : 'flat'}`,
    ),
  performance: (p?: Period) => get<PerformanceStats>(`/performance${query(p)}`),
  performanceByNamed: (p?: Period) =>
    get<Record<string, PerformanceStats>>(`/performance/by-named${query(p)}`),
  settings: () => get<Settings>('/settings'),
  fields: () => get<FieldCatalogue>('/fields'),
  setSetting: async (key: string, value: string | number) => {
    const res = await fetch('/api/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ key, value }),
    })
    if (!res.ok) {
      const body = await res.json().catch(() => ({}))
      throw new ApiError(body?.detail ?? `Could not save that (${res.status})`, res.status)
    }
    return res.json() as Promise<Settings>
  },
  performanceByType: (p?: Period) =>
    get<Record<string, PerformanceStats>>(`/performance/by-strategy${query(p)}`),
  performanceByUnderlying: (p?: Period) =>
    get<Record<string, PerformanceStats>>(`/performance/by-underlying${query(p)}`),
  performanceByBucket: (dimension: string, p?: Period) =>
    get<Record<string, PerformanceStats>>(
      `/performance/by-bucket${query(p, { dimension })}`,
    ),
  lossShape: (p?: Period) => get<LossShapeReport>(`/performance/loss-shape${query(p)}`),
  rules: () => get<RuleAdherence[] | Record<string, RuleAdherence>>('/performance/rules'),
  sync: async (): Promise<{ imported: number; strategies: number }> => {
    const res = await fetch('/api/sync', { method: 'POST' })
    if (!res.ok) throw new ApiError(`Sync failed (${res.status})`, res.status)
    return res.json()
  },
}
