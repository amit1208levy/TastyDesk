# Tasty Desk

One app, two places: the Mac (`src/tastydesk`, served on 127.0.0.1:8787, with
the pages in `web/`) and the iPhone app (`ios/`, `web/ios/`), which runs the
very same Python and the very same pages inside the phone. A change to the Mac
app is a change to the iPhone app; there is no second copy to update.

## Every change

- Pages (`web/src`): they must also fit an iPhone (393px wide). Use Tailwind's
  `sm:` break for the Mac layout and make the unprefixed classes the phone's:
  smaller padding, figures that are never cut short, nothing wider than the
  screen (a grid with only `md:`/`xl:` columns needs `grid-cols-1` too). Where a
  phone needs a different arrangement, not a smaller one, `useNarrow()` in
  `web/src/lib/useMeasure.ts` switches it (the positions table becomes cards).
  Check with `cd ios && npm run layout`.
- Python (`src/tastydesk`): the phone must give the same numbers to the cent.
  Check with `cd ios && npm run bundle && npm run parity`. A new dependency
  must also exist for Pyodide; see `ios/README.md`.
- CI (`.github/workflows/ios.yml`) runs both on every pull request.

## Rules that hold everywhere

- Read-only: never place, change or cancel an order.
- No secrets in code, git or logs.
- Risk is per strategy, never per leg.
- A null is not a zero: a missing price shows as not quoted, never $0.
