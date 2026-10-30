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
| `parity/` | The same-numbers check: a fixed fake tastytrade account through the Mac's Python and through the phone engine, every value compared. |
| `devices/` | The sharing check: what is set by hand on the Mac reaches the phone engine, and back, through encrypted files in a folder standing in for iCloud Drive. |
| `layout/` | The fit check: every tab at 393 × 852, nothing wider than the screen, no figure cut short. |
| `probe/` | The first feasibility test: the Mac test suite run inside Pyodide. |

## Build the engine

```bash
cd ios && npm install
npm run bundle   # -> web/public/engine (about 26 MB)
npm run check    # the bundled engine starts and answers
npm run parity   # Mac and phone give the same numbers
npm run devices  # Mac and phone share what is set by hand
npm run layout   # every tab fits an iPhone (web/ needs npm ci first)
```

## Same on the Mac and the iPhone

What is typed into the app (named strategies and their trades, links,
pairing and roll answers, plans, settings, the questions thread) is shared
through the folder **iCloud Drive › Tasty Desk Sync** (`src/tastydesk/core/devicesync.py`).

- The Mac creates the folder and shares every 20 seconds while Tasty Desk runs.
- On the iPhone: Health → Same on every device → Choose the Tasty Desk Sync
  folder (once). It then shares on opening, every 30 seconds and right after a
  change. The folder access is kept as a bookmark (`web/plugins/icloud-folder`);
  no iCloud entitlement is needed.
- Each device writes only its own file; the latest change to each item wins;
  deletions travel too. Files are AES-256-GCM encrypted with a key derived
  from the tastytrade client secret, and a file from another tastytrade
  account is ignored.

## The parity check

`parity/fake_tastytrade.py` serves a small fixed account (a put credit spread, a
strangle, an iron condor, and a closed strangle) at the HTTP level, so both runs
go through the real tastytrade library, sync, grouping, pricing, P&L, risk,
database and API. `parity/compare.py` then requires every value to be identical,
apart from timestamps. A one-cent difference fails it.

On 19 Oct 2026: identical on all 15 endpoints checked (Positions: summary,
greeks, open strategies, open legs, settings, fields; also closed trades,
performance, loss shape and the what-if curve).

## Library versions

The phone uses the same versions as the Mac's `uv.lock`, except for compiled
packages that have no WebAssembly build at the Mac's version: pydantic 2.14.1
(Mac 2.13.5), numpy 2.4.6 (Mac 2.5.3), pandas 3.0.2 (Mac 3.0.5) and
cryptography 47.0.0 (Mac 50.0.1, used only for the sharing files; `npm run
devices` checks files cross between the two). See `engine/lock.mjs`.

## Getting it onto the iPhone (TestFlight)

GitHub builds the app on one of its Macs and uploads it to TestFlight. Your Mac is
not needed. Workflow: `.github/workflows/ios.yml`; the build itself:
`scripts/testflight.sh`. No TestFlight build is made unless the parity check passes
on the same commit.

### One time, in App Store Connect

1. **developer.apple.com → Membership**: note your **Team ID** (10 characters).
2. **appstoreconnect.apple.com → Apps → + → New App**: platform iOS, name *Tasty Desk*,
   bundle ID **`com.tastydesk.app`**, SKU anything. If the bundle ID is taken, change it
   in `web/capacitor.config.ts` and in the Xcode project (`PRODUCT_BUNDLE_IDENTIFIER`).
3. **TestFlight → Internal Testing → +**: make a group and add yourself.
4. **Users and Access → Integrations → App Store Connect API → +**: a key with the
   **App Manager** role. Download the `.p8` file (Apple offers it once). Note the
   **Key ID** and the **Issuer ID**.

### One time, on GitHub

**Repository → Settings → Secrets and variables → Actions → New repository secret**,
four times:

| Name | Value |
|---|---|
| `APPLE_TEAM_ID` | the Team ID |
| `ASC_KEY_ID` | the Key ID |
| `ASC_ISSUER_ID` | the Issuer ID |
| `ASC_KEY_P8` | the whole text of the `.p8` file |

Type or paste them only there, never into a chat, a file or a commit. GitHub hides
them in logs. Then delete the downloaded `.p8` from your Downloads folder.

### Each build

**Actions → iPhone app → Run workflow**. About 20 minutes later the build appears in
the TestFlight app on the iPhone.

### First run on the phone

1. Open Tasty Desk. It starts its engine (a few seconds) and shows the setup screen.
2. Enter the client secret and refresh token (read-scope grant). They go into the iPhone
   Keychain, this device only.
3. On the Mac: `./scripts/export-for-phone.sh`. AirDrop the file to the phone, then
   **Choose the database file** on the setup screen. Check the row counts against what
   the Mac printed, then delete the file from the Mac's Desktop.
4. Compare the Positions screen with the Mac's. They run the same code; any difference
   is a bug to report.

## What is not on the phone yet

- **Yahoo Finance charts** (`core/prices.py`): Yahoo does not allow calls from a web
  page, so the regime and scanner charts are empty on the phone for now. Positions,
  P&L, risk and performance do not use them.
- **TradingView indicators and the Claude connector (MCP)** stay on the Mac.
- **Live DXLink quotes** are refused for this account on the Mac too; both use REST quotes.
