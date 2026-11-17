// "Include now": count every open trade as if closed at today's prices.
// One switch for the whole Performance page, offered wherever a report reads it.
export function IncludeNow({ on, onChange }: { on: boolean; onChange: (on: boolean) => void }) {
  return (
    <button
      onClick={() => onChange(!on)}
      title="Count every open trade as if you closed it at today's prices"
      className={`whitespace-nowrap rounded-card border px-4 py-2 text-copy font-medium transition-colors ${
        on ? 'border-accent bg-accent text-white' : 'border-line bg-raised text-ink hover:bg-hover'
      }`}
    >
      {on ? 'Including now ✓' : 'Include now'}
    </button>
  )
}
