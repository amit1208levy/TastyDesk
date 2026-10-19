"""Thread-free stand-in for aiosqlite: the same API, with sqlite3 called inline.

Pyodide has no threads, and aiosqlite runs SQLite on one. Everything in the
phone engine runs on a single event loop, so calling sqlite3 directly is safe.
Only what `core/db.py` uses is provided; the Mac suite passes with it too
(see ios/probe).
"""
import sqlite3

Row = sqlite3.Row
Error = sqlite3.Error


class Cursor:
    def __init__(self, cur):
        self._cur = cur

    @property
    def lastrowid(self):
        return self._cur.lastrowid

    @property
    def rowcount(self):
        return self._cur.rowcount

    async def fetchone(self):
        return self._cur.fetchone()

    async def fetchall(self):
        return self._cur.fetchall()

    def __aiter__(self):
        return self

    async def __anext__(self):
        row = self._cur.fetchone()
        if row is None:
            raise StopAsyncIteration
        return row

    async def close(self):
        self._cur.close()


class _Pending:
    """Awaitable and async-context-manager, like aiosqlite's execute()."""

    def __init__(self, fn):
        self._fn = fn
        self._cursor = None

    def __await__(self):
        async def run():
            return Cursor(self._fn())
        return run().__await__()

    async def __aenter__(self):
        self._cursor = Cursor(self._fn())
        return self._cursor

    async def __aexit__(self, *exc):
        await self._cursor.close()


class Connection:
    def __init__(self, conn):
        self._conn = conn

    @property
    def row_factory(self):
        return self._conn.row_factory

    @row_factory.setter
    def row_factory(self, value):
        self._conn.row_factory = value

    @property
    def total_changes(self):
        return self._conn.total_changes

    def execute(self, sql, params=()):
        return _Pending(lambda: self._conn.execute(sql, params))

    def executemany(self, sql, seq):
        return _Pending(lambda: self._conn.executemany(sql, seq))

    def executescript(self, script):
        return _Pending(lambda: self._conn.executescript(script))

    async def commit(self):
        self._conn.commit()

    async def rollback(self):
        self._conn.rollback()

    async def close(self):
        self._conn.close()


class _Connect:
    def __init__(self, *a, **k):
        self._a, self._k = a, k

    def __await__(self):
        async def run():
            return Connection(sqlite3.connect(*self._a, **self._k))
        return run().__await__()


def connect(*a, **k):
    return _Connect(*a, **k)
