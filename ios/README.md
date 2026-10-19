# Tasty Desk on the iPhone

The iPhone app runs the Mac app's own Python, unchanged, inside the app, using
[Pyodide](https://pyodide.org) (Python compiled to WebAssembly). The screens are the
Mac's React pages. They call `/api/...` as they do on the Mac, and the app answers
each call from the same FastAPI app, in process. No Mac and no server are involved:
the phone talks to tastytrade directly, read-only.

```
React pages ──/api/...──▶ web worker ──▶ Pyodide ──▶ tastydesk (same code as the Mac)
                                                     └──▶ tastytrade REST (read scope)
```

## Folders

| Path | What it is |
|---|---|
| `engine/py/` | The only phone-specific Python: stand-ins for `keyring` and `aiosqlite`, and `tastydesk_phone.py`, the bridge between the app and the FastAPI app. |
| `engine/pyodide-lock.json` | Every Python package the phone loads, with its version and SHA-256. Made by `npm run lock`. |
| `engine/bundle.mjs` | Copies Pyodide, the locked packages (hash-checked) and the Python sources into `web/public/engine`. |
| `engine/check.mjs` | Starts the bundled engine the way the app does, and checks it. |
| `probe/` | The first feasibility test: the Mac test suite run inside Pyodide. |

## Build the engine

```bash
cd ios && npm install
npm run bundle   # -> web/public/engine (about 26 MB)
npm run check    # the bundled engine starts and answers
```

## Library versions

The phone uses the same versions as the Mac's `uv.lock`, except for compiled
packages that have no WebAssembly build at the Mac's version: pydantic 2.14.1
(Mac 2.13.5), numpy 2.4.6 (Mac 2.5.3) and pandas 3.0.2 (Mac 3.0.5). See
`engine/lock.mjs`.
