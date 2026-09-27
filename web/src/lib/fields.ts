import { money, moneyCompact, pct, decimals, fullDate, num, EM_DASH } from './format'

/* Printing a value the catalogue described.

   The backend says what a field *is* — money, a percentage, a count of days —
   and this turns that into the one string the app prints for it. Keeping the
   two apart is what lets the user reorder and swap columns without any page
   knowing what a "vega" is. */

export interface FieldSpec {
  id: string
  label: string
  hint: string
  /** The paragraph behind the heading, shown on a two-second hover. */
  help: string
  format: string
  align: string
  tone: string
  group: string
  default: boolean
  width: number | null
  /** Adds up down the legs of one position. */
  sums?: boolean
  /** Adds up down a page of positions. */
  book_sums?: boolean
}

export type FieldValue = string | number | null | Record<string, unknown>

export function formatField(spec: FieldSpec, value: FieldValue): string {
  if (value === null || value === undefined || value === '') return EM_DASH
  if (typeof value === 'object') return EM_DASH
  switch (spec.format) {
    case 'money':
      return money(value, { sign: spec.tone === 'signed' })
    case 'money0':
      return money(value, { sign: spec.tone === 'signed', cents: false })
    case 'percent':
      return pct(value, 0, spec.tone === 'signed')
    case 'delta':
      return decimals(value, 2)
    case 'number': {
      const n = num(value)
      if (n === null) return EM_DASH
      if (Number.isInteger(n)) return String(n)
      return Math.abs(n) >= 1000 ? decimals(n, 2) : decimals(n, n < 1 ? 3 : 2)
    }
    case 'days': {
      const n = num(value)
      return n === null ? EM_DASH : `${n}d`
    }
    case 'date':
      return fullDate(String(value))
    case 'compact':
      return moneyCompact(value)
    default:
      return String(value)
  }
}

/* What a column comes to across the rows under it.

   Only where adding up means something. Money adds up, and so do the greeks
   once they are in dollars; a strike does not, a date does not, and a
   percentage of one trade's credit has nothing to do with the next one's, so
   those columns get no total rather than a made-up one. A count of days is the
   same: the sum of four expiries is not a date, and the figure worth having —
   the nearest one — is already the DTE column sorted.

   Returning null means "this column does not total", which is different from
   "it totals to nothing". */
export function summarise(
  spec: FieldSpec,
  values: FieldValue[],
  where: 'legs' | 'book' = 'legs',
): number | null {
  if (!(where === 'legs' ? spec.sums : spec.book_sums)) return null
  let total = 0
  let seen = 0
  for (const value of values) {
    if (value === null || value === undefined || typeof value === 'object') continue
    const n = num(value)
    if (n === null) continue
    total += n
    seen += 1
  }
  return seen > 0 ? total : null
}

/** Colour, but only where the number has a direction worth colouring. */
export function toneClass(spec: FieldSpec, value: FieldValue): string {
  if (typeof value === 'object' && value !== null) return ''
  const n = num(value)
  if (spec.tone === 'none' || n === null) return ''
  if (spec.tone === 'inverse') return n <= 0 ? 'text-muted' : 'text-profit'
  return n > 0 ? 'text-profit' : n < 0 ? 'text-loss' : 'text-muted'
}

/** Catalogue grouped for the picker, in catalogue order. */
export function byGroup(fields: FieldSpec[]): [string, FieldSpec[]][] {
  const groups: Record<string, FieldSpec[]> = {}
  const order: string[] = []
  for (const f of fields) {
    if (!groups[f.group]) {
      groups[f.group] = []
      order.push(f.group)
    }
    groups[f.group].push(f)
  }
  return order.map((g) => [g, groups[g]])
}
