import { useState, type ReactNode } from 'react'
import { useNarrow } from '../lib/useMeasure'

/* The first few of a long list on a phone, and a button for the rest.

   A list of twenty cards is one screen on the Mac and twenty on a phone,
   and the section after it is never reached. On the Mac nothing is cut. */
export function useFirstFew<T>(items: T[], few = 5): [T[], ReactNode] {
  const narrow = useNarrow()
  const [all, setAll] = useState(false)
  if (!narrow || all || items.length <= few) return [items, null]
  const rest = items.length - few
  return [
    items.slice(0, few),
    <button
      key="show-more"
      onClick={() => setAll(true)}
      className="mt-3 w-full rounded-card border border-line bg-raised px-4 py-3 text-copy text-accent hover:bg-hover"
    >
      Show {rest} more
    </button>,
  ]
}

/* The same, for a page that returns early before its list: hooks cannot
   follow an early return, a component can. */
export function FirstFew<T>({
  items,
  few = 5,
  children,
}: {
  items: T[]
  few?: number
  children: (shown: T[]) => ReactNode
}) {
  const [shown, more] = useFirstFew(items, few)
  return (
    <>
      {children(shown)}
      {more}
    </>
  )
}
