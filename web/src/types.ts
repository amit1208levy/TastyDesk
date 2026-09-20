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
  realized_pct_of_credit: string | null
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
  /** Set when this row is a strategy you named and grouped yourself. */
  named_id: string | null
  named_name: string | null
  /** How many of your trades were merged into this row. */
  parts: number
  /** What to do about it, and why. Drives the table's order. */
  verdict: Verdict | null
  /** Every field in the indicator catalogue, measured for this position. */
  values: Record<string, string | number | null>
  /** The same, per leg, in the order the legs are held. */
  leg_values: Record<string, string | number | null>[]
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

export interface QuestionEntry {
  id: number
  asked_at: string
  question: string
  answer: string | null
  answered_at: string | null
}

export interface AppEvent {
  id: number
  at: string
  kind: string
  severity: 'info' | 'notable' | 'warning' | 'error'
  summary: string
  strategy_id: string | null
  detail: Record<string, unknown> | null
}

export interface UnsettledTrade {
  id: string
  underlying: string
  structure: string
  closed: string | null
  recorded_pnl: string
  legs: string[]
  why: string
}

export interface PairLeg {
  side: 'Long' | 'Short'
  quantity: string
  right: 'C' | 'P' | 'shares'
  strike: string | null
  expiration: string | null
  dte_now: number | null
  open_price: string
  delta: string | null
}

export interface PairSide {
  id: string
  structure: string
  realized_pnl: string | null
  credit: string
  opened_at: string
  closed_at: string | null
  is_open: boolean
  days_held: number | null
  dte_at_entry: number | null
  roll_count: number
  legs: PairLeg[]
}

export interface PairLink {
  label: string
  value: string
  weight: 'strong' | 'neutral' | 'weak'
}

export interface PairCandidate {
  pattern: string
  left_id: string
  right_id: string
  underlying: string
  kind: string
  would_become: string
  gap_minutes: number
  reason: string
  combined_pnl: string
  others_like_it: number
  first_of_pattern: boolean
  links: PairLink[]
  sides: PairSide[]
}

export interface OpenLeg {
  leg_id: string
  trade_id: string
  account: string
  underlying: string
  product: string
  symbol: string
  side: 'Long' | 'Short'
  right: 'C' | 'P' | 'shares'
  strike: string | null
  expiration: string | null
  dte: number | null
  quantity: string
  open_price: string
  mark: string | null
  delta: string | null
  theta: string | null
  iv: string | null
  opened_at: string
  underlying_price: string | null
  trade_structure: string
  trade_open_pnl: string | null
  in_strategies: { id: string; name: string }[]
}

export interface NamedMember {
  id: string
  account: string
  underlying: string
  opened: string
  closed: string | null
  is_open: boolean
  structure: string
  credit: string
  realized_pnl: string
  open_pnl: string | null
  captured: string | null
  days_held: number | null
  dte_at_entry: number | null
  dte_at_close: number | null
  dte_now: number | null
  roll_count: number
  outcome: 'win' | 'loss' | 'scratch' | 'open'
  ending: 'open' | 'closed' | 'expired' | 'assigned' | 'unverified'
  legs: string[]
}

export interface NamedStrategy {
  id: string
  name: string
  product: string
  note: string | null
  name_reading: string | null
  shape: string
  signature: { legs: string[]; expiry_pattern: string; window_minutes: number }
  member_count: number
  members: NamedMember[]
  performance: PerformanceStats
}

export interface StrategyMatch extends NamedMember {
  trade_id: string
  confidence: number
  verdict: 'confident' | 'likely' | 'unsure'
  reasons: string[]
  misses: string[]
  unknowns: string[]
  not_applicable: string[]
  roll_of: string | null
  shape_score: number
  /** Kept for older callers; equal to `confidence`. */
  score: number
  confident: boolean
}

export interface MatchReport {
  strategy_id: string
  threshold: number
  /** Every trade that could belong, unfiltered — the slider does the cutting. */
  candidates: StrategyMatch[]
  confident: StrategyMatch[]
  review: StrategyMatch[]
  confident_pnl: string
}

export interface PairDecision {
  pattern: string
  decision: 'merge' | 'separate'
  decided_at: string | null
  note: string | null
}

export interface WorstTrade {
  id: string
  underlying: string
  structure: string
  realized_pnl: string
  opened: string
  closed: string | null
  days_held: number | null
}

export interface LossShapeGroup {
  group: string
  trades: number
  wins: number
  losses: number
  loss_rate: number | null
  avg_win: string | null
  avg_loss: string | null
  win_loss_ratio: string | null
  gross_won: string
  gross_lost: string
  net: string
  concentration: [number, number][]
  worst: WorstTrade[]
}

export interface LossShapeReport {
  overall: LossShapeGroup
  by_strategy: Record<string, LossShapeGroup>
}

export interface UnderlyingExposure {
  product: string
  beta: string | null
  underlying_price: string | null
  months: [string, string | null][]
  dollar_delta: string | null
  beta_weighted_delta: string | null
  theta: string | null
  vega: string | null
  strategies: number
  legs_total: number
  legs_missing_delta: number
  /** This product's share of the book's net delta. Can exceed 100%. */
  share_of_net: string | null
}

export interface GreekTotals {
  dollar_delta: string | null
  beta_weighted_dollars: string | null
  beta_weighted_delta: string | null
  theta: string | null
  vega: string | null
  reference_symbol: string
  reference_price: string | null
  legs_total: number
  legs_with_delta: number
  missing_delta: string[]
  missing_price: string[]
  missing_beta: string[]
  by_underlying: UnderlyingExposure[]
  dollars_per_spy_percent: string | null
  fully_measured: boolean
  /** The product carrying more than the whole book's net delta, if any. */
  dominant: UnderlyingExposure | null
}

/** An inclusive date window for a report. Both ends optional. */
export interface Period {
  from: string | null
  to: string | null
  label: string
}

export interface PeriodIndex {
  first_close: string | null
  last_close: string | null
  years: { year: number; trades: number }[]
  months: { month: string; trades: number }[]
}

export interface Settings {
  /** How sure the app must be before an old trade counts towards a strategy. */
  match_threshold: number
  match_threshold_default: number
  /** Which indicators the Positions table shows, in order. */
  position_columns: string[]
  /** Which indicators a leg row shows, in order. */
  leg_columns: string[]
}

export interface SettingsColumns {
  position_columns: string[]
  leg_columns: string[]
}

export interface Verdict {
  action: string
  reason: string
  /** 0 is the most urgent. The table sorts on this by default. */
  rank: number
  tone: 'act' | 'take' | 'watch' | 'none'
}

export interface FieldCatalogue {
  strategy: import('./lib/fields').FieldSpec[]
  leg: import('./lib/fields').FieldSpec[]
}
