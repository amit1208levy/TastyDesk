import { money, num, EM_DASH } from '../lib/format'

export interface BarDatum {
  label: string
  value: number | null
  /** Sample size. A big bar over three trades is not a finding. */
  n?: number
  note?: string
}

/* Horizontal bars diverging from a zero baseline.

   Horizontal because the category labels are words of very different lengths
   ("Put Credit Spread", "Naked Put") and vertical bars would either truncate
   them or rotate them. Diverging because P&L has a natural, meaningful zero and
   the reader's first question is "which of these lost money".

   Colour is never the only cue: the bar's side of the baseline says the same
   thing, and every bar carries a signed direct label. */
export function DivergingBars({
  data,
  format = (v) => money(v, { sign: true, cents: false }),
  emptyLabel = 'Nothing to show yet.',
}: {
  data: BarDatum[]
  format?: (v: number) => string
  emptyLabel?: string
}) {
  const withValues = data.filter((d) => d.value !== null)
  if (withValues.length === 0) {
    return <div className="px-1 py-6 text-xs text-faint">{emptyLabel}</div>
  }

  const max = Math.max(...withValues.map((d) => Math.abs(d.value!)), 1)
  const anyNegative = withValues.some((d) => d.value! < 0)
  // With no losses the zero line belongs at the left edge, not the middle —
  // a centred axis would waste half the width on empty space.
  const zeroAt = anyNegative ? 50 : 0
  const halfWidth = anyNegative ? 50 : 100

  return (
    <div className="space-y-1">
      {data.map((d) => {
        const v = d.value
        const pctWidth = v === null ? 0 : (Math.abs(v) / max) * halfWidth
        const negative = (v ?? 0) < 0
        const thin = (d.n ?? Infinity) < 5

        return (
          <div key={d.label} className="group grid grid-cols-[130px_1fr_88px] items-center gap-2">
            <div className="truncate text-xs text-muted" title={d.label}>
              {d.label}
            </div>

            <div className="relative h-5">
              <div
                className="absolute inset-y-0 w-px bg-chart-grid"
                style={{ left: `${zeroAt}%` }}
                aria-hidden
              />
              {v !== null && (
                <div
                  className={`absolute top-1/2 h-3 -translate-y-1/2 ${
                    negative ? 'rounded-l-[4px] bg-chart-loss' : 'rounded-r-[4px] bg-chart-profit'
                  } ${thin ? 'opacity-55' : ''}`}
                  style={
                    negative
                      ? { right: `${100 - zeroAt}%`, width: `${pctWidth}%` }
                      : { left: `${zeroAt}%`, width: `${pctWidth}%` }
                  }
                  title={d.note}
                />
              )}
            </div>

            <div className="num text-right text-xs">
              <span className={v === null ? 'text-faint' : negative ? 'text-loss' : 'text-profit'}>
                {v === null ? EM_DASH : format(v)}
              </span>
              {d.n !== undefined && (
                <span className="ml-1 text-[10px] text-faint" title={`${d.n} trades`}>
                  n={d.n}
                </span>
              )}
            </div>
          </div>
        )
      })}
      {data.some((d) => (d.n ?? Infinity) < 5) && (
        <div className="pt-1 text-[10px] text-faint">
          Faded bars rest on fewer than 5 trades — too few to read as a result.
        </div>
      )}
    </div>
  )
}

export function toBars(
  stats: Record<string, { total_pnl: string; trades: number; expectancy: string | null; pnl_per_bp_day: string | null }>,
  metric: 'total_pnl' | 'expectancy' | 'pnl_per_bp_day',
): BarDatum[] {
  return Object.entries(stats)
    .map(([label, s]) => ({ label, value: num(s[metric]), n: s.trades }))
    .filter((d) => d.n > 0)
    .sort((a, b) => (b.value ?? -Infinity) - (a.value ?? -Infinity))
}
