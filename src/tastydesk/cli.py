"""Command line entry points.

``doctor`` exists because the most likely failure is not a bug in this code but
a credential that was never set up or a grant that was revoked, and the fastest
path back to working is a check that says which one it is.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import pathlib
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


async def _facts(pretty: bool) -> int:
    """Print the judgment-free fact sheet as JSON.

    Exists so a scheduled agent can read the book with nothing but a shell —
    no MCP wiring, no API key. It syncs first, because a brief written off
    yesterday's marks is worse than no brief.
    """
    import json

    from tastydesk.api.serialize import encode

    service = _service()
    await service.start()
    try:
        await service.sync()
        facts = service.position_facts()
    finally:
        await service.stop()

    print(json.dumps(encode(facts), indent=2 if pretty else None))
    return 0


def _write_brief(path: str | None) -> int:
    """Store a brief. Reads markdown from stdin, or from a file."""
    from tastydesk.core.briefs import BriefStore

    markdown = sys.stdin.read() if path in (None, "-") else pathlib.Path(path).read_text()
    if not markdown.strip():
        print("Refusing to store an empty brief.", file=sys.stderr)
        return 1
    store = BriefStore()
    written = store.write(markdown)
    store.prune()
    print(f"Brief stored at {written}")
    return 0


async def _questions() -> int:
    """Print unanswered dashboard questions as JSON, each with the book as it stood."""
    import json

    service = _service()
    await service.start()
    try:
        pending = await service.pending_questions()
    finally:
        await service.stop()

    rows = []
    for q in pending:
        context = q.get("context")
        rows.append(
            {
                "id": q["id"],
                "asked_at": q["asked_at"].isoformat() if q.get("asked_at") else None,
                "question": q["question"],
                "facts_when_asked": json.loads(context) if context else None,
            }
        )
    print(json.dumps(rows, indent=2))
    return 0


async def _answer(question_id: int, path: str | None) -> int:
    """Store an answer. Reads markdown from stdin, or from a file."""
    answer = sys.stdin.read() if path in (None, "-") else pathlib.Path(path).read_text()

    service = _service()
    await service.start()
    try:
        stored = await service.answer(question_id, answer)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    finally:
        await service.stop()

    if not stored:
        print(f"Question {question_id} is not open (already answered, or gone).", file=sys.stderr)
        return 1
    print(f"Answered question {question_id}")
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

    p_facts = sub.add_parser("facts", help="print the computed fact sheet as JSON (syncs first)")
    p_facts.add_argument("--pretty", action="store_true")

    p_brief = sub.add_parser("write-brief", help="store a daily brief (markdown on stdin)")
    p_brief.add_argument("path", nargs="?", default="-", help="file to read, or - for stdin")

    sub.add_parser("brief-path", help="print the directory briefs are stored in")

    sub.add_parser("questions", help="print unanswered dashboard questions as JSON")

    p_answer = sub.add_parser("answer", help="answer a dashboard question (markdown on stdin)")
    p_answer.add_argument("question_id", type=int)
    p_answer.add_argument("path", nargs="?", default="-", help="file to read, or - for stdin")
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
        if args.command == "facts":
            return asyncio.run(_facts(args.pretty))
        if args.command == "write-brief":
            return _write_brief(args.path)
        if args.command == "brief-path":
            from tastydesk.core.briefs import BriefStore

            print(BriefStore().directory)
            return 0
        if args.command == "questions":
            return asyncio.run(_questions())
        if args.command == "answer":
            return asyncio.run(_answer(args.question_id, args.path))
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
