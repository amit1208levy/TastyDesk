/* Mirrors src/tastydesk/core/models.py. Money arrives as strings so that
   Decimal precision survives JSON — parse with the helpers in lib/format,
   never with a bare Number() on a value you are about to display. */

export type Direction = 'Long' | 'Short'
export type OptionType = 'C' | 'P'
export type RiskProfile = 'Defined' | 'Undefined'
export type DangerLevel = 'OK' | 'Watch' | 'Tested' | 'Danger' | 'Critical'

export const DANGER_ORDER: DangerLevel[] = ['OK', 'Watch', 'Tested', 'Danger', 'Critical']

export interface Leg {
  symbol: string
  instrument_type: string
  underlying: string
  direction: Direction
  quantity: string
  multiplier: string
  option_type: OptionType | null
  strike: string | null
  expiration: string | null
  open_price: string
  mark: string | null
  bid: string | null
  ask: string | null
  delta: string | null
  gamma: string | null
  theta: string | null
  vega: string | null
  iv: string | null
}

export interface StrategyPnL {
  net_credit: string
  cost_to_close: string | null
  open_pnl: string | null
  /** +1.0 = the whole credit captured, -1.5 = down 150% of credit. */
  pct_of_credit: string | null
  max_profit: string | null
  /** null means undefined risk — not "no risk". */
  max_loss: string | null
  pct_of_max_profit: string | null
  pct_of_max_loss: string | null
  realized_pnl: string
  is_credit: boolean
  quoted_legs: number
  total_legs: number
}

export interface RiskReason {
  code: string
  level: DangerLevel
  message: string
}

export interface StrategyRisk {
  level: DangerLevel
  score: number
  reasons: RiskReason[]
  dte: number | null
  worst_short_delta: string | null
  distance_to_short_pct: string | null
  distance_to_short_sigma: string | null
  breached: boolean
  breached_side: string | null
  assignment_risk: boolean
  pin_risk: boolean
  pct_of_net_liq: string | null
}

export interface Strategy {
  id: string
  account_number: string
  underlying: string
  strategy_type: string
  risk_profile: RiskProfile
  legs: Leg[]
  opened_at: string
  closed_at: string | null
  net_credit: string
  closing_cash_flow: string
  fees: string
  order_ids: number[]
  roll_count: number
  iv_rank_at_entry: string | null
  underlying_price_at_entry: string | null
  dte_at_entry: number | null
  short_delta_at_entry: string | null
  buying_power_used: string | null
  notes: string | null
  is_open: boolean
  realized_pnl: string
}

/** A strategy plus everything computed about it. This is what the tables render. */
export interface StrategyView {
  strategy: Strategy
  pnl: StrategyPnL
  risk: StrategyRisk
  underlying_price: string | null
  iv_rank: string | null
}

export interface PortfolioSummary {
  account_number: string
  net_liquidating_value: string
  cash_balance: string
  buying_power_used: string
  buying_power_available: string
  maintenance_requirement: string
  open_strategies: number
  net_delta: string | null
  net_theta: string | null
  open_pnl: string | null
  realized_pnl_ytd: string | null
  as_of: string | null
}

export interface PerformanceStats {
  trades: number
  wins: number
  losses: number
  scratches: number
  win_rate: number | null
  avg_win: string | null
  avg_loss: string | null
  expectancy: string | null
  total_pnl: string
  profit_factor: number | null
  avg_days_in_trade: number | null
  avg_pct_of_max_profit_captured: number | null
  pnl_per_bp_day: string | null
  largest_win: string | null
  largest_loss: string | null
}

export interface RuleAdherence {
  rule: string
  description: string
  followed: number
  violated: number
  not_measurable: number
  adherence_rate: number | null
  pnl_when_followed: string | null
  pnl_when_violated: string | null
  counterfactual_pnl: string | null
  counterfactual_excluded: number
}

export interface Health {
  credentials_present: boolean
  session_ok: boolean
  account_count: number
  last_sync: string | null
  last_error: string | null
  checked_at: string
}

export interface RollCandidate {
  closed_id: string
  opened_id: string
  underlying: string
  confidence: 'high' | 'likely' | 'possible'
  reason: string
  gap_minutes: number
}

export interface DailyBrief {
  on: string
  markdown: string
  written_at: string
  is_stale: boolean
}
