/* Number formatting. Two rules run through all of it:
   1. A value the backend could not compute arrives as null and renders as an
      em dash, never as 0. A zero and an unknown are different facts and this
      app must never conflate them.
   2. Money is parsed from a string only at the moment of display, so the
      Decimal the backend computed is the one the user reads. */

const EM_DASH = '—'

export function num(v: string | number | null | undefined): number | null {
  if (v === null || v === undefined || v === '') return null
  const n = typeof v === 'number' ? v : Number(v)
  return Number.isFinite(n) ? n : null
}

export function money(v: string | number | null | undefined, opts: { sign?: boolean; cents?: boolean } = {}): string {
  const n = num(v)
  if (n === null) return EM_DASH
  const { sign = false, cents = true } = opts
  const body = Math.abs(n).toLocaleString('en-US', {
    minimumFractionDigits: cents ? 2 : 0,
    maximumFractionDigits: cents ? 2 : 0,
  })
  const prefix = n < 0 ? '-$' : sign && n > 0 ? '+$' : '$'
  return prefix + body
}

/** Compact money for headline tiles: $12.4k, $1.21M. */
export function moneyCompact(v: string | number | null | undefined): string {
  const n = num(v)
  if (n === null) return EM_DASH
  const abs = Math.abs(n)
  const sign = n < 0 ? '-' : ''
  if (abs >= 1_000_000) return `${sign}$${(abs / 1_000_000).toFixed(2)}M`
  if (abs >= 10_000) return `${sign}$${(abs / 1000).toFixed(1)}k`
  return money(n)
}

/** A ratio (0.375) as a percentage (37.5%). */
export function pct(v: string | number | null | undefined, digits = 1, sign = false): string {
  const n = num(v)
  if (n === null) return EM_DASH
  const value = n * 100
  const prefix = value > 0 && sign ? '+' : ''
  return `${prefix}${value.toFixed(digits)}%`
}

/** Percent-of-credit, the premium seller's home metric. +100% = full credit captured. */
export function pctOfCredit(v: string | number | null | undefined): string {
  return pct(v, 0, true)
}

export function decimals(v: string | number | null | undefined, digits = 2): string {
  const n = num(v)
  return n === null ? EM_DASH : n.toFixed(digits)
}

export function signedClass(v: string | number | null | undefined): string {
  const n = num(v)
  if (n === null) return 'text-faint'
  if (n > 0) return 'text-profit'
  if (n < 0) return 'text-loss'
  return 'text-muted'
}

export function strike(v: string | null): string {
  const n = num(v)
  if (n === null) return ''
  return Number.isInteger(n) ? String(n) : n.toFixed(2).replace(/0$/, '')
}

export function shortDate(iso: string | null | undefined): string {
  if (!iso) return EM_DASH
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return EM_DASH
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
}

export function fullDate(iso: string | null | undefined): string {
  if (!iso) return EM_DASH
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return EM_DASH
  return d.toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' })
}

export function relativeTime(iso: string | null | undefined): string {
  if (!iso) return 'never'
  const then = new Date(iso).getTime()
  if (Number.isNaN(then)) return 'never'
  const secs = Math.round((Date.now() - then) / 1000)
  if (secs < 10) return 'just now'
  if (secs < 60) return `${secs}s ago`
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`
  if (secs < 86400) return `${Math.floor(secs / 3600)}h ago`
  return `${Math.floor(secs / 86400)}d ago`
}

/** "1 short put / 1 long put" style leg summary for a collapsed row. */
export function dteLabel(dte: number | null): string {
  if (dte === null) return EM_DASH
  if (dte < 0) return 'expired'
  if (dte === 0) return 'today'
  return `${dte}d`
}

export { EM_DASH }
