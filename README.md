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
  short strike in percent and in sigma, and the 21-DTE flag.
- **Performance** — win rate, expectancy and P&L per buying-power-day, sliced by
  strategy type, underlying, DTE at entry, IV rank at entry and delta at entry.
  Every figure reports its sample size.
- **Rules** — how often you actually manage at 50%, close or roll at 21 DTE, and
  stop at 2× credit; P&L when you followed the rule versus when you did not.
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

### Connecting Claude

```bash
claude mcp add tasty-desk -- uv --directory /path/to/DashboardV3 run python -m tastydesk.mcp.server
```

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

## Development

```bash
./dev.sh                 # Vite with hot reload + FastAPI
uv run pytest -q         # the engine test suite
uv run ruff check src tests
cd web && npm run typecheck
```
