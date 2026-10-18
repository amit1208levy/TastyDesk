# iPhone probe: can the Python code run on the phone?

The iPhone app would run the Mac app's own Python code in
[Pyodide](https://pyodide.org) (Python 3.14 compiled to WebAssembly), so the
numbers come from the same code. This folder checks that before anything is
built.

```bash
cd ios/probe && npm install
npm run tests   # the Mac test suite, run inside Pyodide
npm run net     # tastytrade REST reachable from Pyodide (no credentials sent)
```

## Result (18 Oct 2026)

- **632 of 633 tests pass** in Pyodide. One is skipped (`mcp`, Mac only).
  One fails: `test_the_thread_reads_newest_first`. Two questions asked within
  the same clock tick tie on `asked_at`, so their order is undefined. This is
  in the questions thread, not P&L or risk. The fix (`ORDER BY asked_at DESC,
  id DESC` in `core/db.py`) is waiting for Amit's go-ahead.
- **pydantic and the tastytrade library work.** Pyodide ships pydantic-core,
  pandas and numpy built for WebAssembly.
- **REST quotes are reachable.** On Pyodide the tastytrade library's HTTP client
  switches to `fetch`. tastytrade's API allows any origin (CORS `*`). Without a
  token the quotes endpoint answers 401 and OAuth answers 400, as expected.
  The DXLink stream isn't needed: it is refused for this account anyway.

## What the app must change (the probe uses stand-ins)

| Mac | iPhone | Probe stand-in |
|---|---|---|
| `aiosqlite` (runs SQLite on a thread) | Pyodide has no threads: `sqlite3` called inline | `shim/aiosqlite.py`; the Mac suite passes 633/633 with it too |
| `keyring` | iOS Keychain, through the app shell | a stub that finds nothing |
| Yahoo daily charts (`core/prices.py`) | Yahoo sends no CORS headers: needs the app's native HTTP | not tested |
| the database file | must be saved to the app's own storage, not Pyodide's in-memory disk | not tested |

`shim/simpleloop.py` exists only so pytest-asyncio can run inside Pyodide. The
app itself uses Pyodide's normal event loop.
