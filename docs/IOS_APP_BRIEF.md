# Tasty Desk on iPhone: brief for a new Claude session

Give this whole file to the new session as its first message. It has
everything needed to start. Written 18 Oct 2026, at commit `fd0b454` on `main`.

## The goal

Amit wants Tasty Desk on his iPhone as a real app. It must work when his Mac
is off or has no internet. Today Tasty Desk is a Python (FastAPI) server
plus a React website that run only on his Mac at `http://127.0.0.1:8787`.

The phone app must give **the same numbers as the Mac app**. Accuracy matters
more than anything else. Same P&L, same risk, same Tom King checks, same
scanner, same performance figures.

## How to talk to Amit (his global rules)

- At most four lines per answer, in simple English. Lead with the answer.
- If you are unsure, ask. Don't guess. Accuracy comes first.
- Before editing a file he didn't name, say which file and why.
- Never write a token, password or key into a file, a commit or a chat.
  Don't ask him to paste one into the chat.

## Hard rules for the app

1. **Read-only.** The app must never place, change or cancel an order. Use the
   tastytrade `read` OAuth scope only.
2. **No secrets in code or git.** The client secret and refresh token go in the
   iOS Keychain, typed in by Amit on the phone (a settings screen with a
   secure text field). Never put them in the repo, a plist or logs.
3. **Risk is per strategy, never per leg.** See `src/tastydesk/core/pnl.py` and
   `risk.py`, and the README.
4. **A null is not a zero.** A missing price shows "not quoted", never $0.

## Is GitHub enough?

For the **code**, yes. The private repo `github.com/amit1208levy/TastyDesk` has
all of it, and its 633 tests describe exactly what every number must be.

These are **not** on GitHub and must be brought over separately. Xcode runs on
the Mac, so a session on this Mac can read them directly:

| What | Where on the Mac | Why it matters |
|---|---|---|
| Trade database | `~/Library/Application Support/TastyDesk/tastydesk.db` (SQLite) | Named strategies and the trades in each, trade plans, settings (New Levy fresh start, strategy groups such as Spec, rulebook, confidence bar, columns), IV history, snapshots, roll decisions, manual pairing overrides. Much of this can't be rebuilt from tastytrade. |
| Credentials | macOS Keychain, service names `tastydesk-client-secret` and `tastydesk-refresh-token`, account `tastydesk` | Amit types them into the phone himself. **Never read them out or copy them anywhere.** |
| Tom King's plan and strategy sheets | `~/Downloads/2026_Trading_Plan_v4.docx`, `Call_Cannon_Strategy.docx`, `DPMCC_Strategy.docx`, `11x_Bear_Trap_Campaign_Strategy.docx`, `11x_Bear_Trap_Spec_Trade_Strategies.docx`, `BA112_TRADE.docx`, `FB112_TRADE.docx` | The rules in `core/tom.py` and `core/scanner.py` come from these. |
| `COORDINATOR.md` | project root, not committed | Amit's coordinator instructions. |

## Recommended approach (confirm with Amit before building)

A native **SwiftUI** app that talks straight to the tastytrade API from the
phone, with no server.

- **Port the logic, then the screens.** The Python core in `src/tastydesk/core/`
  (about 21,000 lines with `service.py`) is the source of truth. Port it to
  Swift module by module. Start with `models`, `occ`, `pnl`, `risk`,
  `classify`, `grouping`, `pairing`, then `analytics`, `confidence`,
  `playbook`, `plans`, `rulebook`, `strategy_report`, `volgap`, `tom` and
  `scanner`.
- **Port the tests with them.** Each Python test in `tests/` becomes an XCTest
  with the same inputs and expected numbers. A module counts as done only when
  its tests pass in Swift. This is how "the same numbers" is proven.
- **Data:** a local SQLite database on the phone (GRDB.swift is a good fit),
  with the same tables as `core/db.py`.
- **Moving his data over:** write a small export on the Mac that dumps
  `tastydesk.db` into one file, and an import screen in the app. Send the file
  by AirDrop. Check the counts after import: named strategies, members, plans
  and settings must all match.
- **Market data:** quotes, greeks and option chains come from tastytrade (see
  `core/client.py` and `core/marks.py`). Daily charts for the regime and
  scanner come from Yahoo Finance (`core/prices.py`).
  Note: the DXLink greeks stream is currently refused for this account
  (`api_access_entitlement_not_granted`), so plan to use REST quotes, which do
  work.
- **Screens to rebuild** (React versions in `web/src/pages/`): Positions,
  Strategies (with groups such as Spec, and the New Levy fresh start), What if,
  Tom Analysis, Scanner, Performance, Rules, History, Legs, Grouping, Activity,
  Health.
- **TradingView indicators** (`src/tastydesk/tradingview.py`) can stay on the
  Mac for now.

## What to do first

1. Read `README.md`, `src/tastydesk/core/models.py`, `pnl.py` and `service.py`
   to learn how everything fits together.
2. Run `uv run pytest -q` to see the 633 tests pass. That's the baseline.
3. Give Amit a short plan: the order of modules, a rough time per stage, and
   what the first version on his phone will show. Wait for his yes.
4. Build a first slice that he can run on his phone through Xcode and his
   developer account. For example: Positions, with live P&L and risk per
   strategy, matching the Mac to the cent.
5. Then move module by module, comparing every screen with the Mac app before
   calling it done.

## Leave the Mac app alone

Keep the Mac app working while the phone app is built. Don't break the Python
code or its tests. Put the iPhone app in a new folder (for example `ios/`) in
the same repo.
