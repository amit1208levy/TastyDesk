"""Command line entry points.

``doctor`` exists because the most likely failure is not a bug in this code but
a credential that was never set up or a grant that was revoked, and the fastest
path back to working is a check that says which one it is.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from tastydesk.core.auth import SETUP_INSTRUCTIONS, CredentialError, credentials_present


def _service():
    from tastydesk.api.main import build_service

    return build_service()


async def _sync(full: bool) -> int:
    service = _service()
    await service.start()
    try:
        result = await service.sync(full=full)
    finally:
        await service.stop()

    print(
        f"Imported {result.transactions_imported} transactions, "
        f"rebuilt {result.strategies_built} strategies "
        f"({result.open_strategies} open, {result.quoted_strategies} fully priced) "
        f"in {result.duration_seconds:.1f}s"
    )
    for warning in result.warnings:
        print(f"  warning: {warning}", file=sys.stderr)
    return 0


async def _snapshot() -> int:
    """Record today's mark for every open strategy.

    Run this daily. Max adverse excursion cannot be reconstructed from
    transactions after the fact, so a day not snapshotted is a day the 2x-stop
    adherence report can never speak to.
    """
    service = _service()
    await service.start()
    try:
        await service.sync()
        written = await service.snapshot()
    finally:
        await service.stop()
    print(f"Recorded {written} position snapshots")
    return 0


async def _doctor() -> int:
    print("Tasty Desk — checks\n")

    have = credentials_present()
    print(f"  credentials in Keychain   {'yes' if have else 'NO'}")
    if not have:
        print()
        print(SETUP_INSTRUCTIONS)
        return 1

    service = _service()
    await service.start()
    try:
        health = await service.health()
    finally:
        await service.stop()

    print(f"  tastytrade session        {'ok' if health['session_ok'] else 'FAILED'}")
    print(f"  accounts visible          {health['account_count']}")
    print(f"  strategies in local db    {health['strategies_loaded']} ({health['open_strategies']} open)")
    print(f"  last sync                 {health['last_sync'] or 'never'}")
    if health["last_error"]:
        print(f"\n  error: {health['last_error']}")
        return 1
    print("\nAll good.")
    return 0


def _serve(port: int) -> int:
    import uvicorn

    uvicorn.run("tastydesk.api.main:app", host="127.0.0.1", port=port)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tastydesk", description="Tasty Desk — options journal")
    sub = parser.add_subparsers(dest="command", required=True)

    p_sync = sub.add_parser("sync", help="pull transactions and rebuild trades")
    p_sync.add_argument("--full", action="store_true", help="re-import the whole available history")

    sub.add_parser("snapshot", help="record today's mark for every open strategy (run daily)")
    sub.add_parser("doctor", help="check credentials and the connection to tastytrade")

    p_serve = sub.add_parser("serve", help="run the local dashboard")
    p_serve.add_argument("--port", type=int, default=8787)

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

    try:
        if args.command == "sync":
            return asyncio.run(_sync(args.full))
        if args.command == "snapshot":
            return asyncio.run(_snapshot())
        if args.command == "doctor":
            return asyncio.run(_doctor())
        if args.command == "serve":
            return _serve(args.port)
    except CredentialError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
