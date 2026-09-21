"""Reentrant MT5 access with orders ahead of queued dashboard reads."""
import threading
import time
from contextlib import contextmanager


class TradingLock:
    def __init__(self):
        self._condition = threading.Condition()
        self._owner = None
        self._depth = 0
        self._orders_waiting = 0

    def acquire(self, blocking=True, timeout=-1, *, order=False):
        ident = threading.get_ident()
        deadline = None if timeout < 0 else time.monotonic() + timeout
        with self._condition:
            if self._owner == ident:
                self._depth += 1
                return True
            if order:
                self._orders_waiting += 1
            try:
                while self._owner is not None or (not order and self._orders_waiting):
                    if not blocking:
                        return False
                    remaining = None if deadline is None else deadline - time.monotonic()
                    if remaining is not None and remaining <= 0:
                        return False
                    self._condition.wait(remaining)
                self._owner, self._depth = ident, 1
                return True
            finally:
                if order:
                    self._orders_waiting -= 1
                    self._condition.notify_all()

    def release(self):
        with self._condition:
            if self._owner != threading.get_ident():
                raise RuntimeError('MT5 lock is not owned by this thread')
            self._depth -= 1
            if not self._depth:
                self._owner = None
                self._condition.notify_all()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *exc):
        self.release()

    @contextmanager
    def order(self):
        self.acquire(order=True)
        try:
            yield self
        finally:
            self.release()
