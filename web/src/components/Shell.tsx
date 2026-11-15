import type { ReactNode } from 'react'
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { onPhone } from '../phone/engine'
import { useNarrow } from '../lib/useMeasure'
import { api } from '../lib/api'
import { FRESH_DATA } from '../lib/devices'
import { haptic, PULL_TRIGGER, Segmented, Sheet, Spinner, usePullToRefresh } from '../phone/ui'

export type TabId = 'today' | 'positions' | 'performance' | 'plan' | 'scanner' | 'legs' | 'activity' | 'health'

/* Five places, in the order a trading day asks: how am I doing and what needs
   me, what I hold, how I have done, the plan I trade by, and what to trade
   next. The log and the connection sit apart behind a divider. */
const TABS: { id: TabId; label: string; hint: string; group?: 'later' }[] = [
  { id: 'today', label: 'Today', hint: 'How the account is doing, and what needs you' },
  { id: 'positions', label: 'Positions', hint: 'What you hold, by position and by strategy' },
  { id: 'performance', label: 'Performance', hint: 'How each strategy has done, and every closed trade' },
  { id: 'plan', label: 'Plan', hint: "Your book against Tom King's plan, the allocation, what-if, and your rules" },
  { id: 'scanner', label: 'Scanner', hint: "The market through Tom King's entry rules, sized to your account" },
  { id: 'legs', label: 'Legs & grouping', hint: 'Every open leg, and which legs belong to the same trade', group: 'later' },
  { id: 'activity', label: 'Activity', hint: 'What changed, and when', group: 'later' },
  { id: 'health', label: 'Health', hint: 'The connection to tastytrade', group: 'later' },
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
  const narrow = useNarrow()

  // How much the tab bar takes at the bottom, for what else sits there
  // (the phone's "saved numbers" bar).
  useEffect(() => {
    document.documentElement.style.setProperty('--tabbar', narrow ? '62px' : '12px')
  }, [narrow])

  // A phone gets an iPhone app's frame around the same pages.
  if (narrow)
    return (
      <PhoneShell tab={tab} onTab={onTab} status={status} theme={theme} setTheme={setTheme}>
        {children}
      </PhoneShell>
    )

  return (
    <div className="flex min-h-full flex-col">
      {/* The frosted bar. It sits over the content rather than pushing it down,
          and the blur is what tells you there is a page moving underneath. */}
      <header
        // The status bar's height on an iPhone, nothing on the Mac.
        className="sticky top-0 z-20 border-b border-line pt-[env(safe-area-inset-top)]"
        style={{
          background: 'var(--glass-nav)',
          backdropFilter: 'var(--glass-blur)',
          WebkitBackdropFilter: 'var(--glass-blur)',
        }}
      >
        <div className="mx-auto flex max-w-[1400px] flex-wrap items-center gap-x-3 gap-y-2 px-4 py-3 sm:gap-x-6 sm:px-8 md:flex-nowrap md:py-4">
          <div className="flex items-baseline gap-2.5">
            <span className="display gold-text whitespace-nowrap text-title sm:text-title">Tasty Desk</span>
            <span className="label hidden sm:inline">
              read only
            </span>
          </div>

          <div className="order-last w-full min-w-0 md:order-none md:w-auto md:flex-1">
            <TabBar tab={tab} onTab={onTab} />
          </div>

          <div className="ml-auto flex shrink-0 items-center gap-3">
            {status}
            <button
              onClick={() => setTheme(theme === 'dark' ? 'light' : theme === 'light' ? 'system' : 'dark')}
              title={`Theme: ${theme}. Click to change.`}
              className="rounded-full px-3 py-1.5 text-note uppercase tracking-[0.08em] text-faint hover:bg-hover hover:text-ink"
            >
              {theme === 'dark' ? 'Dark' : theme === 'light' ? 'Light' : 'Auto'}
            </button>
          </div>
        </div>
      </header>

      {/* Keyed on the tab so the page it contains arrives rather than blinks. */}
      <main key={tab} className="rise mx-auto w-full max-w-[1400px] flex-1 px-4 py-6 sm:px-8 sm:py-16">
        {children}
      </main>

      <footer className="mt-8 px-4 py-8 sm:mt-16 sm:px-8 sm:py-12">
        <div className="mx-auto max-w-[1400px] border-t border-line pt-4 text-note text-faint">
          {onPhone
            ? 'Runs on this iPhone. Credentials live in its Keychain, the API key is read-only, and the app talks only to tastytrade (and Yahoo Finance, for charts).'
            : 'Runs on this Mac. Credentials live in the macOS Keychain, the API key is read-only, and nothing leaves the machine.'}
        </div>
      </footer>
    </div>
  )
}

/* ---------------------------------------------------------------------------
   The phone's frame: what an iPhone app puts around its pages.

   A navigation bar whose large title shrinks into the bar as the page scrolls
   under it, a tab bar at the bottom for the four places of a trading day, the
   rest in a sheet behind More, and a pull down from the top to read everything
   again. The pages inside are the Mac's own.
   --------------------------------------------------------------------------- */
const BOTTOM: TabId[] = ['today', 'positions', 'performance', 'plan']

/** Short names: a tab bar has room for one word. */
const PHONE_TITLE: Record<TabId, string> = {
  today: 'Today',
  positions: 'Positions',
  performance: 'Performance',
  plan: 'Plan',
  scanner: 'Scanner',
  legs: 'Legs',
  activity: 'Activity',
  health: 'Settings',
}

const ICONS: Record<TabId | 'more', ReactNode> = {
  today: <path d="M4 12a8 8 0 1 0 16 0a8 8 0 1 0 -16 0M12 7v5l3 2" />,
  positions: <path d="M4 6h16M4 12h16M4 18h10" />,
  performance: <path d="M4 19h16M6 15l4-4 3 3 5-6" />,
  plan: <path d="M6 4h12v16H6zM9 9h6M9 13h6M9 17h3" />,
  scanner: <path d="M10 10m-6 0a6 6 0 1 0 12 0a6 6 0 1 0 -12 0M15 15l5 5" />,
  legs: <path d="M5 5h6v6H5zM13 13h6v6h-6zM11 8h4v5" />,
  activity: <path d="M3 12h4l3-7 4 14 3-7h4" />,
  health: (
    <path d="M12 9a3 3 0 1 0 0 6a3 3 0 1 0 0 -6M19 12a7 7 0 0 0 -.1 -1.2l2 -1.5l-2 -3.4l-2.3 .9a7 7 0 0 0 -2 -1.2L14.2 3h-4.4l-.4 2.6a7 7 0 0 0 -2 1.2l-2.3 -.9l-2 3.4l2 1.5a7 7 0 0 0 0 2.4l-2 1.5l2 3.4l2.3 -.9a7 7 0 0 0 2 1.2l.4 2.6h4.4l.4 -2.6a7 7 0 0 0 2 -1.2l2.3 .9l2 -3.4l-2 -1.5a7 7 0 0 0 .1 -1.2z" />
  ),
  more: <path d="M5 12h.01M12 12h.01M19 12h.01" />,
}

function Icon({ id, className = 'h-6 w-6' }: { id: TabId | 'more'; className?: string }) {
  return (
    <svg
      aria-hidden
      viewBox="0 0 24 24"
      className={className}
      fill="none"
      stroke="currentColor"
      strokeWidth={id === 'more' ? 3 : id === 'health' ? 1.5 : 1.8}
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      {ICONS[id]}
    </svg>
  )
}

function PhoneShell({
  tab,
  onTab,
  children,
  status,
  theme,
  setTheme,
}: {
  tab: TabId
  onTab: (t: TabId) => void
  children: ReactNode
  status?: ReactNode
  theme: 'system' | 'light' | 'dark'
  setTheme: (t: 'system' | 'light' | 'dark') => void
}) {
  const title = PHONE_TITLE[tab]
  // Past the large title, its name moves up into the bar.
  const [scrolled, setScrolled] = useState(false)
  useEffect(() => {
    const read = () => setScrolled(window.scrollY > 38)
    read()
    window.addEventListener('scroll', read, { passive: true })
    return () => window.removeEventListener('scroll', read)
  }, [])
  // A new tab starts at its top.
  useLayoutEffect(() => {
    window.scrollTo(0, 0)
  }, [tab])

  /* Everything read again: new fills from tastytrade, then every page asks. */
  const refresh = useCallback(async () => {
    try {
      await api.sync()
    } finally {
      window.dispatchEvent(new Event(FRESH_DATA))
    }
  }, [])
  const { pull, busy } = usePullToRefresh(refresh, true)

  return (
    <div className="flex min-h-full flex-col pb-[49px]">
      <header
        className={`fixed inset-x-0 top-0 z-20 pt-[env(safe-area-inset-top)] transition-[background-color,border-color] duration-200 ${
          scrolled ? 'phone-bar border-b border-line' : 'border-b border-transparent'
        }`}
      >
        <div className="relative flex h-11 items-center justify-center px-4">
          <span
            className={`text-lead font-semibold transition-opacity duration-200 ${
              scrolled ? 'opacity-100' : 'opacity-0'
            }`}
          >
            {title}
          </span>
        </div>
      </header>

      <main className="w-full flex-1 px-4 pt-[calc(env(safe-area-inset-top)+44px)]">
        {/* The room a pull makes, with the spinner in it. */}
        <div
          aria-hidden={!busy}
          className="flex items-end justify-center overflow-hidden"
          style={{
            height: busy ? 44 : pull,
            transition: pull > 0 ? 'none' : 'height 220ms var(--ease)',
          }}
        >
          <div className="pb-3">
            {busy ? <Spinner /> : pull > 6 ? <Spinner turn={pull / PULL_TRIGGER} /> : null}
          </div>
        </div>

        <div className="flex items-end justify-between gap-3 pb-3">
          <h1 className="display text-[34px] font-bold leading-[41px] tracking-[-0.4px]">{title}</h1>
          <div className="flex shrink-0 items-center gap-2 pb-1.5">{status}</div>
        </div>

        {/* Keyed on the tab: a new page fades in. */}
        <div key={tab} className="phone-page pb-6">
          {children}
        </div>
      </main>

      <BottomTabs tab={tab} onTab={onTab} theme={theme} setTheme={setTheme} />
    </div>
  )
}

function BottomTabs({
  tab,
  onTab,
  theme,
  setTheme,
}: {
  tab: TabId
  onTab: (t: TabId) => void
  theme: 'system' | 'light' | 'dark'
  setTheme: (t: 'system' | 'light' | 'dark') => void
}) {
  const [open, setOpen] = useState(false)
  const rest = TABS.filter((t) => !BOTTOM.includes(t.id))
  const inMore = rest.some((t) => t.id === tab)

  function go(id: TabId) {
    if (id !== tab) haptic('select')
    // The same tab again goes back to its top, as on any iPhone.
    else window.scrollTo({ top: 0, behavior: 'smooth' })
    onTab(id)
  }

  const item = (active: boolean) =>
    `flex h-[49px] min-w-0 flex-1 flex-col items-center justify-center gap-[3px] text-[10px] font-medium ${
      active ? 'text-accent' : 'text-faint'
    }`

  return (
    <>
      <nav
        data-tabs
        aria-label="Tabs"
        className="phone-bar safe-sides fixed inset-x-0 bottom-0 z-30 border-t border-line"
      >
        <div className="flex">
          {BOTTOM.map((id) => (
            <button
              key={id}
              data-tab={id}
              onClick={() => go(id)}
              aria-current={tab === id ? 'page' : undefined}
              className={item(tab === id)}
            >
              <Icon id={id} />
              {PHONE_TITLE[id]}
            </button>
          ))}
          <button
            data-more
            aria-expanded={open}
            onClick={() => {
              haptic('light')
              setOpen(true)
            }}
            className={item(inMore)}
          >
            <Icon id={inMore ? tab : 'more'} />
            {inMore ? PHONE_TITLE[tab] : 'More'}
          </button>
        </div>
      </nav>

      <Sheet open={open} onClose={() => setOpen(false)} title="More">
        <div className="ios-list mt-1">
          {rest.map((t) => (
            <button
              key={t.id}
              data-tab={t.id}
              onClick={() => {
                go(t.id)
                setOpen(false)
              }}
              aria-current={tab === t.id ? 'page' : undefined}
              className="ios-row"
            >
              <span className="flex h-[30px] w-[30px] shrink-0 items-center justify-center rounded-[7px] bg-accent text-white">
                <Icon id={t.id} className="h-[18px] w-[18px]" />
              </span>
              <span className="min-w-0 flex-1">
                <span className="block text-lead leading-snug">{t.id === 'health' ? 'Settings & health' : t.label}</span>
                <span className="block truncate text-note text-faint">{t.hint}</span>
              </span>
              <svg aria-hidden viewBox="0 0 8 14" className="h-3.5 w-2 shrink-0 text-faint/70">
                <path d="M1 1l6 6-6 6" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </button>
          ))}
        </div>

        <div className="mb-1.5 mt-6 px-4 text-note uppercase tracking-wide text-faint">Appearance</div>
        <Segmented
          label="Appearance"
          options={[
            ['system', 'Automatic'],
            ['light', 'Light'],
            ['dark', 'Dark'],
          ]}
          value={theme}
          onChange={setTheme}
        />
        <p className="mt-6 px-4 text-note leading-relaxed text-faint">
          Runs on this iPhone. Your keys live in its Keychain, the API key is read-only, and the app talks only to
          tastytrade (and Yahoo Finance, for charts).
        </p>
      </Sheet>
    </>
  )
}

/* The tabs, on one line, never scrolled.

   The bar used to scroll sideways when the window was too narrow for every
   tab, with a scrollbar under it and tabs cut off at the edge. Now the tabs
   that do not fit move into a "More" menu, last first, so the ones used
   every day stay in view and nothing is ever half-shown. */
function TabBar({ tab, onTab }: { tab: TabId; onTab: (t: TabId) => void }) {
  const box = useRef<HTMLDivElement>(null)
  const ruler = useRef<HTMLDivElement>(null)
  const [fits, setFits] = useState(TABS.length)
  const [open, setOpen] = useState(false)

  const measure = useCallback(() => {
    const room = box.current?.clientWidth ?? 0
    const items = Array.from(ruler.current?.children ?? []) as HTMLElement[]
    if (!room || items.length === 0) return
    // +2 for the gap between pills.
    const widths = items.slice(0, TABS.length).map((el) => el.offsetWidth + 2)
    // The menu button carries the open tab's name when that tab is in the
    // menu, so it is as wide as the wider of "More" and that name. Measured
    // as "More" alone, a long name pushed the theme button off a phone.
    const more = Math.max(items[TABS.length]?.offsetWidth ?? 90, items[TABS.length + 1]?.offsetWidth ?? 0)
    const all = widths.reduce((a, b) => a + b, 0)
    if (all <= room) {
      setFits(TABS.length)
      return
    }
    let used = more
    let n = 0
    while (n < widths.length && used + widths[n] <= room) {
      used += widths[n]
      n += 1
    }
    setFits(n)
  }, [])

  // Measured after every render as well as on resize: the status pill beside
  // the bar arrives after the first paint and narrows it without the window
  // changing size, and a bar measured before that ran under it.
  useLayoutEffect(() => {
    measure()
  })
  useEffect(() => {
    const observer = new ResizeObserver(measure)
    if (box.current) observer.observe(box.current)
    window.addEventListener('resize', measure)
    return () => {
      observer.disconnect()
      window.removeEventListener('resize', measure)
    }
  }, [measure])

  useEffect(() => {
    if (!open) return
    const close = () => setOpen(false)
    window.addEventListener('click', close)
    return () => window.removeEventListener('click', close)
  }, [open])

  const shown = TABS.slice(0, fits)
  const hidden = TABS.slice(fits)
  const current = hidden.find((t) => t.id === tab)

  const pill = (active: boolean, later?: boolean) =>
    `relative whitespace-nowrap rounded-full px-3.5 py-1.5 text-copy ${
      active
        ? 'bg-accent font-medium text-white'
        : later
          ? 'text-faint hover:bg-hover hover:text-ink'
          : 'text-muted hover:bg-hover hover:text-ink'
    }`

  return (
    <div ref={box} className="relative min-w-0 flex-1">
      {/* Every tab drawn once, invisibly, to know how wide each one is. */}
      <div ref={ruler} aria-hidden className="pointer-events-none invisible absolute left-0 top-0 flex h-0 w-max overflow-hidden [&>*]:shrink-0">
        {TABS.map((t, i) => (
          <span key={t.id} className="flex items-center">
            {t.group === 'later' && TABS[i - 1]?.group !== 'later' && <span className="mx-2 h-4 w-px" />}
            <span className={`${pill(false)} font-medium`}>{t.label}</span>
          </span>
        ))}
        <span className={pill(false)}>More ▾</span>
        <span className={`${pill(false)} font-medium`}>{TABS.find((t) => t.id === tab)?.label} ▾</span>
      </div>

      <nav className="flex items-center gap-0.5">
        {shown.map((t, i) => (
          <span key={t.id} className="flex items-center">
            {t.group === 'later' && TABS[i - 1]?.group !== 'later' && (
              <span aria-hidden className="mx-2 h-4 w-px bg-line" />
            )}
            {/* The live tab is a filled pill rather than an underline —
                the segmented-control register, where the selection is a
                surface you moved to and not a mark under a word. */}
            <button
              onClick={() => onTab(t.id)}
              title={t.hint}
              aria-current={tab === t.id ? 'page' : undefined}
              className={pill(tab === t.id, t.group === 'later')}
            >
              {t.label}
            </button>
          </span>
        ))}

        {hidden.length > 0 && (
          <span className="relative">
            <button
              onClick={(e) => {
                e.stopPropagation()
                setOpen(!open)
              }}
              aria-expanded={open}
              className={pill(current !== undefined)}
            >
              {current ? current.label : 'More'} ▾
            </button>
            {open && (
              <div className="absolute left-0 top-full z-30 mt-2 min-w-[13rem] rounded-card border border-line-strong bg-raised-solid p-1.5 shadow-[var(--shadow-lift)]">
                {/* Labels only, on a solid surface: the descriptions and the
                    page showing through made the list hard to read. */}
                {hidden.map((t, i) => (
                  <div key={t.id}>
                    {i > 0 && t.group === 'later' && hidden[i - 1].group !== 'later' && (
                      <div aria-hidden className="mx-2 my-1 h-px bg-line" />
                    )}
                    <button
                      onClick={() => {
                        onTab(t.id)
                        setOpen(false)
                      }}
                      title={t.hint}
                      className={`block w-full whitespace-nowrap rounded-sm px-3 py-2 text-left text-copy ${
                        tab === t.id ? 'bg-accent-soft font-medium text-accent' : 'text-ink hover:bg-hover'
                      }`}
                    >
                      {t.label}
                    </button>
                  </div>
                ))}
              </div>
            )}
          </span>
        )}
      </nav>
    </div>
  )
}
