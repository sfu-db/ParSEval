from __future__ import annotations

import logging
import queue
import signal
import threading
import time
import traceback
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable, Optional


@dataclass
class Task:
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    project_id: int
    run_id: int
    payload: Any

    attempt: int = 0
    enqueued_at: float = field(default_factory=time.monotonic)

    def __repr__(self) -> str:
        return f"Task(id={self.id}, attempt={self.attempt}, payload={self.payload!r})"


class Stats:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counts: dict[str, int] = defaultdict(int)

    def inc(self, key: str, n: int = 1) -> None:
        with self._lock:
            self._counts[key] += n

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return dict(self._counts)

    def __str__(self) -> str:
        snap = self.snapshot()
        return (
            f"enqueued={snap.get('enqueued', 0)}  "
            f"done={snap.get('done', 0)}  "
            f"retried={snap.get('retried', 0)}  "
            f"dead={snap.get('dead', 0)}"
        )


class DeadLetterQueue:
    """Thread-safe store for tasks that exhausted all retries."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: list[tuple[Task, str]] = []  # (task, last_error)

    def put(self, task: Task, error: str) -> None:
        with self._lock:
            self._items.append((task, error))

    def all(self) -> list[tuple[Task, str]]:
        with self._lock:
            return list(self._items)

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)


_SENTINEL = object()  # poison pill — never put user payloads equal to this


class Runtime:
    def __init__(
        self,
        handler: Callable[[Any], None],
        num_workers: int = 4,
        maxsize: int = 0,
        max_retries: int = 3,
        retry_delay: float = 1.0,
        drain_timeout: float = 10.0,
    ):
        self._handler = handler
        self._num_workers = num_workers
        self._max_retries = max_retries
        self._retry_delay = retry_delay
        self._drain_timeout = drain_timeout

        self._queue: queue.Queue[Any] = queue.Queue(maxsize=maxsize)
        self._stop_event = (
            threading.Event()
        )  # signals workers to stop accepting new tasks
        self._kill_event = threading.Event()  # signals workers to exit immediately
        self._threads: list[threading.Thread] = []
        self._started = False

        self.stats = Stats()
        self.dlq = DeadLetterQueue()

        # last-seen heartbeat per worker name → for stall detection
        self._heartbeat: dict[str, float] = {}
        self._hb_lock = threading.Lock()

    def start(self):
        if self._started:
            raise RuntimeError("Runtime already started")
        self._started = True

        for i in range(self._num_workers):
            t = threading.Thread(
                target=self._worker_loop, name=f"worker-{i}", daemon=True
            )
            t.start()
            self._threads.append(t)

        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)

    def submit(self, payload: Any) -> None:
        if not self._started:
            raise RuntimeError("Runtime not started")
        self._queue.put(Task(payload=payload))
        self.stats.inc("enqueued")

    def stop(self, timeout: Optional[float] = None):
        """Stop accepting new tasks and wait for workers to finish."""
        self._stop_event.set()
        start_time = time.monotonic()
        for t in self._threads:
            remaining = (
                None
                if timeout is None
                else max(0, timeout - (time.monotonic() - start_time))
            )
            t.join(timeout=remaining)
        if timeout is not None and time.monotonic() - start_time >= timeout:
            logging.warning("Stop timeout reached; some workers may still be running")

    def join(self):
        self._queue.join()
