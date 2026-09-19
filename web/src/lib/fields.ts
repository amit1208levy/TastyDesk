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
  format: string
  align: string
  tone: string
  group: string
  default: boolean
  width: number | null
}

export type FieldValue = string | number | null

export function formatField(spec: FieldSpec, value: FieldValue): string {
  if (value === null || value === undefined || value === '') return EM_DASH
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

/** Colour, but only where the number has a direction worth colouring. */
export function toneClass(spec: FieldSpec, value: FieldValue): string {
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
