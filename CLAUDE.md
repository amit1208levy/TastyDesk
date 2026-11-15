# Tasty Desk

One app, two places: the Mac (`src/tastydesk`, served on 127.0.0.1:8787, with
the pages in `web/`) and the iPhone and iPad app (`ios/`, `web/ios/`), which
runs the very same Python and the very same pages inside the device. A change to the Mac
app is a change to the iPhone app; there is no second copy to update.

## Every change

- Pages (`web/src`): they must also fit an iPhone (393px wide). Use Tailwind's
  `sm:` break for the Mac layout and make the unprefixed classes the phone's:
  smaller padding, figures that are never cut short, nothing wider than the
  screen (a grid with only `md:`/`xl:` columns needs `grid-cols-1` too). Where a
  phone needs a different arrangement, not a smaller one, `useNarrow()` in
  `web/src/lib/useMeasure.ts` switches it (the positions table becomes cards).
  A phone or an iPad also gets the app's frame around the pages (large titles,
  a tab bar at the bottom, sheets, pull to refresh: `useAppFrame()`, and
  `.app-frame` in `web/src/styles.css`); an iPad keeps the wide layouts inside
  it. Check with `cd ios && npm run layout` (iPhone, Pro Max, iPad both ways).
- Python (`src/tastydesk`): the phone must give the same numbers to the cent.
  Check with `cd ios && npm run bundle && npm run parity`. A new dependency
  must also exist for Pyodide; see `ios/README.md`.
- Something new the user sets by hand (a new table or setting): it must be
  shared between devices. Add the table to `KINDS` in
  `src/tastydesk/core/devicesync.py` (settings are shared already, except
  `last_sync` and `sync_*`), and check with `cd ios && npm run devices`.
- CI (`.github/workflows/ios.yml`) runs all of these on every pull request.

## Rules that hold everywhere

- Read-only: never place, change or cancel an order.
- No secrets in code, git or logs.
- Risk is per strategy, never per leg.
- A null is not a zero: a missing price shows as not quoted, never $0.
