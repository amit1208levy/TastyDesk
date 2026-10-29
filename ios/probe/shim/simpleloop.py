"""Probe only: a plain asyncio loop with no I/O selector, for running the
async unit tests under Pyodide (whose WebLoop can't be driven synchronously)."""
import asyncio
import time


class _NoSelector:
    def select(self, timeout=None):
        if timeout:
            time.sleep(min(timeout, 0.05))
        return []

    def close(self):
        pass


class SimpleLoop(asyncio.BaseEventLoop):
    def __init__(self):
        super().__init__()
        self._selector = _NoSelector()

    def _process_events(self, event_list):
        pass

    def _write_to_self(self):
        pass


class SimplePolicy(asyncio.DefaultEventLoopPolicy):
    def new_event_loop(self):
        return SimpleLoop()
