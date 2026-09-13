import type { Health, PerformanceStats, PortfolioSummary, RuleAdherence, StrategyView } from '../types'

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
  performance: () => get<PerformanceStats>('/performance'),
  performanceByType: () => get<Record<string, PerformanceStats>>('/performance/by-strategy'),
  performanceByUnderlying: () => get<Record<string, PerformanceStats>>('/performance/by-underlying'),
  performanceByBucket: (dimension: string) =>
    get<Record<string, PerformanceStats>>(`/performance/by-bucket?dimension=${encodeURIComponent(dimension)}`),
  rules: () => get<RuleAdherence[]>('/performance/rules'),
  sync: async (): Promise<{ imported: number; strategies: number }> => {
    const res = await fetch('/api/sync', { method: 'POST' })
    if (!res.ok) throw new ApiError(`Sync failed (${res.status})`, res.status)
    return res.json()
  },
}
