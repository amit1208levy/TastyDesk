import type {
  AppEvent,
  DailyBrief,
  Health,
  PerformanceStats,
  PortfolioSummary,
  QuestionEntry,
  RollCandidate,
  RuleAdherence,
  StrategyView,
} from '../types'
import type { PayoffCurve } from '../components/PayoffChart'

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
  openStrategies: () => get<StrategyView[]>('/strategies/open'),
  closedStrategies: (limit = 200) => get<StrategyView[]>(`/strategies/closed?limit=${limit}`),
  payoff: (id: string) => get<PayoffCurve>(`/strategies/${encodeURIComponent(id)}/payoff`),
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
  performance: () => get<PerformanceStats>('/performance'),
  performanceByType: () => get<Record<string, PerformanceStats>>('/performance/by-strategy'),
  performanceByUnderlying: () => get<Record<string, PerformanceStats>>('/performance/by-underlying'),
  performanceByBucket: (dimension: string) =>
    get<Record<string, PerformanceStats>>(`/performance/by-bucket?dimension=${encodeURIComponent(dimension)}`),
  rules: () => get<RuleAdherence[] | Record<string, RuleAdherence>>('/performance/rules'),
  sync: async (): Promise<{ imported: number; strategies: number }> => {
    const res = await fetch('/api/sync', { method: 'POST' })
    if (!res.ok) throw new ApiError(`Sync failed (${res.status})`, res.status)
    return res.json()
  },
}
