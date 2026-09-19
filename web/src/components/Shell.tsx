import type { ReactNode } from 'react'
import { useEffect, useState } from 'react'

export type TabId = 'brief' | 'ask' | 'legs' | 'strategies' | 'positions' | 'performance' | 'rules' | 'history' | 'grouping' | 'activity' | 'health'

/* Order is by what a trading day actually needs.

   What is open and what needs a decision comes first. The record of what
   already happened — closed trades, the event log — goes last, and the
   housekeeping that is only touched when something needs regrouping sits
   between them behind a divider. The app opens on Positions. */
const TABS: { id: TabId; label: string; hint: string; group?: 'later' }[] = [
  { id: 'positions', label: 'Positions', hint: 'What you hold, sorted by what needs attention' },
  { id: 'brief', label: 'Brief', hint: "This morning's read on the book" },
  { id: 'strategies', label: 'Strategies', hint: 'The strategies you named, and how they do' },
  { id: 'ask', label: 'Ask', hint: 'Ask Claude about your positions' },
  { id: 'performance', label: 'Performance', hint: 'Win rate and expectancy per strategy' },
  { id: 'rules', label: 'Rules', hint: 'How often you follow your own rules' },
  { id: 'legs', label: 'Legs', hint: 'Every open leg, one line each', group: 'later' },
  { id: 'grouping', label: 'Grouping', hint: 'Which legs belong to the same trade', group: 'later' },
  { id: 'history', label: 'History', hint: 'Closed trades', group: 'later' },
  { id: 'activity', label: 'Activity', hint: 'What changed, and when', group: 'later' },
  { id: 'health', label: 'Health', hint: 'Connection to tastytrade', group: 'later' },
]

function useTheme() {
  const [theme, setTheme] = useState<'system' | 'light' | 'dark'>(() => {
    try {
      return (localStorage.getItem('td-theme') as 'system' | 'light' | 'dark') ?? 'system'
    } catch {
      return 'system'
    }
  })

  useEffect(() => {
    const root = document.documentElement
    if (theme === 'system') root.removeAttribute('data-theme')
    else root.setAttribute('data-theme', theme)
    try {
      localStorage.setItem('td-theme', theme)
    } catch {
      /* private window — the choice just will not persist */
    }
  }, [theme])

  return { theme, setTheme }
}

export function Shell({
  tab,
  onTab,
  children,
  status,
}: {
  tab: TabId
  onTab: (t: TabId) => void
  children: ReactNode
  status?: ReactNode
}) {
  const { theme, setTheme } = useTheme()

  return (
    <div className="flex min-h-full flex-col">
      <header className="sticky top-0 z-20 bg-bg/80 backdrop-blur-xl">
        <div className="mx-auto flex max-w-[1400px] items-center gap-6 px-8 py-5">
          <div className="flex items-baseline gap-2.5">
            <span className="display gold-text text-[22px]">Tasty Desk</span>
            <span className="label hidden sm:inline">
              read only
            </span>
          </div>

          <nav className="flex items-center gap-0.5 overflow-x-auto">
            {TABS.map((t, i) => (
              <span key={t.id} className="flex items-center">
                {t.group === 'later' && TABS[i - 1]?.group !== 'later' && (
                  <span aria-hidden className="mx-2 h-4 w-px bg-line" />
                )}
                <button
                  onClick={() => onTab(t.id)}
                  title={t.hint}
                  className={`relative whitespace-nowrap px-3 py-1.5 text-[15px] ${
                    tab === t.id
                      ? 'font-medium text-ink'
                      : t.group === 'later'
                        ? 'text-faint hover:text-ink'
                        : 'text-muted hover:text-ink'
                  }`}
                >
                  {t.label}
                  {/* The mark under the live tab grows from the middle rather
                      than appearing. It is the only thing on the page allowed
                      to be gold and moving at the same time. */}
                  <span
                    aria-hidden
                    className={`absolute inset-x-2 -bottom-0.5 h-px origin-center bg-accent transition-transform duration-300 ease-[cubic-bezier(0.22,1,0.36,1)] ${
                      tab === t.id ? 'scale-x-100' : 'scale-x-0'
                    }`}
                  />
                </button>
              </span>
            ))}
          </nav>

          <div className="ml-auto flex items-center gap-3">
            {status}
            <button
              onClick={() => setTheme(theme === 'dark' ? 'light' : theme === 'light' ? 'system' : 'dark')}
              title={`Theme: ${theme}. Click to change.`}
              className="rounded-sm px-2.5 py-1 text-[13px] uppercase tracking-[0.1em] text-faint hover:bg-hover hover:text-ink"
            >
              {theme === 'dark' ? 'Dark' : theme === 'light' ? 'Light' : 'Auto'}
            </button>
          </div>
        </div>
        <div className="rule-gold" />
      </header>

      {/* Keyed on the tab so the page it contains arrives rather than blinks. */}
      <main key={tab} className="rise mx-auto w-full max-w-[1400px] flex-1 px-8 py-10">
        {children}
      </main>

      <footer className="mt-10 px-8 py-8">
        <div className="mx-auto max-w-[1400px] border-t border-line pt-4 text-[13px] text-faint">
          Runs on this Mac. Credentials live in the macOS Keychain, the API key is read-only, and
          nothing leaves the machine.
        </div>
      </footer>
    </div>
  )
}
