import { useEffect, useRef, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { native } from './engine'

/* The pieces an iPhone app is made of, for the phone's layout of the pages:
   a sheet that slides up, a segmented control, the activity spinner, a pull
   to refresh, and a tap you can feel. The Mac's layout uses none of them. */

// ------------------------------------------------------------------- haptics

type Tap = 'select' | 'light' | 'success'

/** A tap you can feel, in the app only: a tab chosen, a sheet opened, a refresh done. */
export function haptic(kind: Tap = 'select') {
  if (!native) return
  void (async () => {
    try {
      const { Haptics, ImpactStyle, NotificationType } = await import('@capacitor/haptics')
      if (kind === 'select') await Haptics.selectionChanged()
      else if (kind === 'light') await Haptics.impact({ style: ImpactStyle.Light })
      else await Haptics.notification({ type: NotificationType.Success })
    } catch {
      // No haptics on this device: nothing is lost.
    }
  })()
}

// ------------------------------------------------------------------- spinner

/** The iOS activity indicator. `turn` (0 to 1) draws it part way, for a pull. */
export function Spinner({ turn, className = '' }: { turn?: number; className?: string }) {
  const spokes = 8
  const shown = turn === undefined ? spokes : Math.round(Math.min(Math.max(turn, 0), 1) * spokes)
  return (
    <svg
      aria-hidden
      viewBox="0 0 24 24"
      className={`h-5 w-5 text-faint ${turn === undefined ? 'ios-spin' : ''} ${className}`}
    >
      {Array.from({ length: spokes }, (_, i) => (
        <line
          key={i}
          x1="12"
          y1="3.5"
          x2="12"
          y2="8"
          stroke="currentColor"
          strokeWidth="2.2"
          strokeLinecap="round"
          transform={`rotate(${i * (360 / spokes)} 12 12)`}
          opacity={turn === undefined ? 0.25 + (0.75 * i) / (spokes - 1) : i < shown ? 0.85 : 0}
        />
      ))}
    </svg>
  )
}

// --------------------------------------------------------------------- sheet

/* A sheet from the bottom of the screen, as iOS shows a detail or a menu:
   the page dims behind it, it is dismissed by the Done button, a tap outside,
   or a drag down on its handle. */
export function Sheet({
  open,
  onClose,
  title,
  children,
}: {
  open: boolean
  onClose: () => void
  title: string
  children: ReactNode
}) {
  // Kept on screen for the moment it takes to slide away.
  const [mounted, setMounted] = useState(open)
  const [leaving, setLeaving] = useState(false)
  const [drag, setDrag] = useState(0)
  const start = useRef<number | null>(null)

  useEffect(() => {
    if (open) {
      setMounted(true)
      setLeaving(false)
      setDrag(0)
      return
    }
    if (!mounted) return
    setLeaving(true)
    const done = setTimeout(() => setMounted(false), 240)
    return () => clearTimeout(done)
  }, [open, mounted])

  // The page behind does not scroll while a sheet is up.
  useEffect(() => {
    if (!mounted) return
    const before = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      document.body.style.overflow = before
    }
  }, [mounted])

  if (!mounted) return null
  return createPortal(
    <div className="fixed inset-0 z-40" role="dialog" aria-modal="true" aria-label={title}>
      <div
        className={`absolute inset-0 bg-black/45 ${leaving ? 'sheet-dim-out' : 'sheet-dim-in'}`}
        onClick={onClose}
      />
      <div
        className={`sheet-panel absolute inset-x-0 bottom-0 flex max-h-[92dvh] flex-col rounded-t-[14px] bg-raised-solid shadow-[var(--shadow-lift)] ${
          leaving ? 'sheet-out' : 'sheet-in'
        }`}
        style={drag > 0 ? { transform: `translateY(${drag}px)`, transition: 'none' } : undefined}
      >
        <div
          className="shrink-0 touch-none px-4 pb-2 pt-2"
          onTouchStart={(e) => {
            start.current = e.touches[0].clientY
          }}
          onTouchMove={(e) => {
            if (start.current !== null) setDrag(Math.max(0, e.touches[0].clientY - start.current))
          }}
          onTouchEnd={() => {
            start.current = null
            if (drag > 90) onClose()
            else setDrag(0)
          }}
        >
          <div aria-hidden className="mx-auto mb-2 h-[5px] w-9 rounded-full bg-line-strong" />
          <div className="flex items-center gap-3">
            <div className="min-w-0 flex-1 truncate text-lead font-semibold">{title}</div>
            <button onClick={onClose} className="shrink-0 text-lead font-semibold text-accent">
              Done
            </button>
          </div>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-4 pb-[calc(env(safe-area-inset-bottom)+20px)] pt-1">
          {children}
        </div>
      </div>
    </div>,
    document.body,
  )
}

// ----------------------------------------------------------- segmented control

/* Two or three views of one page, as iOS switches between them: one control,
   the chosen part raised, sliding to the next. */
export function Segmented<T extends string>({
  options,
  value,
  onChange,
  label,
}: {
  options: [T, string][]
  value: T
  onChange: (next: T) => void
  label?: string
}) {
  const at = Math.max(
    0,
    options.findIndex(([id]) => id === value),
  )
  return (
    <div
      role="tablist"
      aria-label={label}
      className="segmented relative grid"
      style={{ gridTemplateColumns: `repeat(${options.length}, minmax(0, 1fr))` }}
    >
      <span
        aria-hidden
        className="segmented-thumb"
        style={{ width: `calc((100% - 4px) / ${options.length})`, transform: `translateX(${at * 100}%)` }}
      />
      {options.map(([id, text]) => (
        <button
          key={id}
          role="tab"
          aria-selected={id === value}
          onClick={() => {
            if (id === value) return
            haptic('select')
            onChange(id)
          }}
          className={`relative z-[1] truncate px-2 text-[13px] leading-8 ${
            id === value ? 'font-semibold text-ink' : 'font-medium text-ink/80'
          }`}
        >
          {text}
        </button>
      ))}
    </div>
  )
}

// ------------------------------------------------------------ pull to refresh

const PULL_TO = 64

/* Pull the page down from its top to read everything again. Returns how far
   it is pulled (pixels, eased) and whether the refresh is running; the shell
   draws the spinner and makes the room. */
export function usePullToRefresh(run: () => Promise<void>, enabled: boolean): { pull: number; busy: boolean } {
  const [pull, setPull] = useState(0)
  const [busy, setBusy] = useState(false)
  const state = useRef({ startY: null as number | null, startX: 0, dist: 0, busy: false, armed: false })
  const action = useRef(run)
  action.current = run

  useEffect(() => {
    if (!enabled) return
    const s = state.current
    const start = (e: TouchEvent) => {
      const quiet = window.scrollY <= 0 && !s.busy && !document.querySelector('[role="dialog"]')
      s.startY = quiet ? e.touches[0].clientY : null
      s.startX = e.touches[0].clientX
      s.dist = 0
      s.armed = false
    }
    const move = (e: TouchEvent) => {
      if (s.startY === null) return
      const dy = e.touches[0].clientY - s.startY
      const dx = Math.abs(e.touches[0].clientX - s.startX)
      // A sideways swipe (the row of tiles) or a scroll up is not a pull.
      if (dy <= 0 || window.scrollY > 0 || (s.dist === 0 && dx > dy)) {
        if (s.dist === 0) s.startY = dy < 0 || dx > dy ? null : s.startY
        return
      }
      s.dist = Math.min(dy * 0.5, 96)
      if (s.dist >= PULL_TO && !s.armed) {
        s.armed = true
        haptic('light')
      }
      setPull(s.dist)
      if (e.cancelable) e.preventDefault()
    }
    const end = () => {
      if (s.startY === null) return
      s.startY = null
      const go = s.dist >= PULL_TO
      s.dist = 0
      setPull(0)
      if (!go) return
      s.busy = true
      setBusy(true)
      void action
        .current()
        .then(() => haptic('success'))
        .catch(() => {})
        .finally(() => {
          s.busy = false
          setBusy(false)
        })
    }
    window.addEventListener('touchstart', start, { passive: true })
    window.addEventListener('touchmove', move, { passive: false })
    window.addEventListener('touchend', end)
    window.addEventListener('touchcancel', end)
    return () => {
      window.removeEventListener('touchstart', start)
      window.removeEventListener('touchmove', move)
      window.removeEventListener('touchend', end)
      window.removeEventListener('touchcancel', end)
    }
  }, [enabled])

  return { pull, busy }
}

export const PULL_TRIGGER = PULL_TO
