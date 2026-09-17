# Tasty Desk

A strategy-aware options journal and risk dashboard for a tastytrade premium
seller. Runs entirely on your own Mac.

## Why it exists

Every position tool measures risk one leg at a time. That is wrong for the way
you trade. If a put credit spread is down 150% of the credit it collected, the
short put on its own might show −300% — because the long put gained at the same
moment. The 300% is arithmetic, not information.

Tasty Desk measures **everything at the strategy level**:

| | |
|---|---|
| A 5-wide put credit spread, $1.00 credit | |
| Down 150% of credit | which is **37.5% of max loss** |
| Short put alone down 300% | shown as leg detail, never as an alarm |

The same loss on an undefined-risk short strangle scores strictly higher, and
the dashboard says why in words.

## What it answers

- **Positions** — open strategies sorted by what actually needs a decision, with
  DTE, % of credit, % of max loss, worst short-strike delta, distance to the
  short strike in percent and in sigma, and the 21-DTE flag. Each expands to
  its legs and a payoff diagram showing where the structure makes and loses
  money at expiration.
- **Performance** — win rate, expectancy and P&L per buying-power-day, sliced by
  strategy type, underlying, DTE at entry, IV rank at entry and delta at entry.
  Every figure reports its sample size.
- **Rules** — how often you actually manage at 50%, close or roll at 21 DTE, and
  stop at 2× credit; P&L when you followed the rule versus when you did not.
  A winner held past the target counts as a violation — ending green does not
  make it the trade the plan called for.
- **Rolls** — a roll filled as one order is linked automatically. One executed
  as two orders is *proposed* for linking, never assumed, because closing one
  trade and opening another the same afternoon is ordinary behaviour.
- **In conversation** — the MCP server exposes the same engine to Claude, so you
  can ask "what is at risk today?" and get an answer from live account data.

## Setup

Requires [uv](https://docs.astral.sh/uv/) and Node.

```bash
./scripts/setup-credentials.sh   # prompts with the screen hidden
./run.sh                         # http://127.0.0.1:8787
```

Create the OAuth application at **my.tastytrade.com → Manage → My Profile → API
→ OAuth Applications**, with the **`read` scope only**, then *Create Grant* for
the refresh token.

Useful commands:

```bash
uv run tastydesk doctor     # checks credentials and the tastytrade connection
uv run tastydesk sync       # pull new activity and rebuild trades
uv run tastydesk snapshot   # record today's marks (see below)
```

Install the daily snapshot job once — max adverse excursion cannot be
reconstructed after the fact, so a day not snapshotted is a day the 2×-stop
report can never speak to:

```bash
./scripts/install-daily-snapshot.sh
```

### The AI half

The dashboard owns the arithmetic; the judgment is Claude's. That split is
deliberate — every number here has to match tastytrade to the cent and read the
same on every refresh, while "which of these matters today" is a reading of the
situation that a hardcoded threshold does badly.

`uv run tastydesk facts` prints the whole book as a judgment-free fact sheet:
every leg with its greeks, distances to the short strikes in percent and in
sigma, IV rank now versus at entry, earnings and ex-dividend dates, and flags
for your three rules. Nothing in it says "danger". That is the input.

Two things read it:

- **The Brief tab** — a scheduled Claude session runs each weekday at 15:45
  local (just before the US open), reads the account, and writes the brief to
  `~/Library/Application Support/TastyDesk/briefs/`. No API key lives on this
  machine and there is no per-call cost. Change the time in the Scheduled
  section of the sidebar.
- **The Ask tab** — type a question into the app and a Claude session answers it,
  reading the fact sheet as it stood when you asked. Queued rather than live,
  for the same reason: no key. A session checks at 16:00, 18:00, 20:00 and
  22:00 local on weekdays, and the interface says plainly that answers are not
  instant instead of imitating a chat window that would never reply.
- **Chat** — ask Claude directly and it reads the same fact sheet over MCP.

### Connecting Claude

`.mcp.json` in this folder already registers the server, so opening the project
in Claude Code picks it up. Then ask things like *"what's at risk today?"* or
*"how did my strangles do this year?"* and the answer comes from live account
data — the same engine the dashboard uses, so the two can never disagree.

## Security

- **`read` scope only.** The credential cannot place, change or cancel an order.
- **Secrets live in the macOS Keychain**, never in a file, a log, an environment
  variable or a commit. Do not paste them into a chat — a transcript keeps them.
- **Binds to 127.0.0.1.** Nothing is exposed to the network.
- **Local SQLite, mode 0600.** No cloud, no telemetry, no third party.
- Outbound traffic goes to tastytrade and its market-data feed, nothing else.

Revoke access at any time from the OAuth application manager in your tastytrade
account.

## Layout

```
src/tastydesk/
  core/        the engine — pure, tested, no I/O in the maths
    models.py    domain model and the sign conventions  ← read this first
    pnl.py       strategy-level P&L, max profit/loss, breakevens, payoff
    risk.py      danger scoring (strategy level; two physical leg alarms)
    classify.py  leg shapes → named strategies
    grouping.py  transactions → trades, including rolls
    analytics.py win rate, expectancy, P&L per BP-day, rule adherence
    db.py        SQLite; money stored as TEXT, never as float
    client.py    read-only tastytrade access, rate limited
    marks.py     live marks and greeks; a missing price stays missing
  service.py   the application layer both front ends share
  api/         local HTTP server + the built dashboard
  mcp/         the same engine, exposed to Claude
web/           React dashboard
```

## Known limits

- A roll executed as two orders is proposed, not detected — you confirm it.
- Futures-option multipliers are derived from the fill (`|value| = price ×
  quantity × multiplier`). An unusual contract that matches no known multiplier
  falls back to convention rather than guessing.
- The 2×-stop adherence report needs snapshot history, so it reports "not
  measurable" for trades that closed before snapshots began rather than
  inventing a worst point.
- This folder is on an iCloud-synced Desktop. `run.sh` marks `.venv`,
  `node_modules` and `logs` as ignore-by-iCloud, because iCloud otherwise
  leaves `"name 2.ext"` duplicates inside them and breaks the environment.
  Moving the project off the Desktop is the durable fix.

## Development

```bash
./dev.sh                 # Vite with hot reload + FastAPI
uv run pytest -q         # the engine test suite
uv run ruff check src tests
cd web && npm run typecheck
```
