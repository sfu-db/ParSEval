import threading
import queue
import random
import time, logging
import itertools
from typing import Callable, Any, Optional, List, Tuple
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DisproveResult:
    producer_id: int
    """Zero-based index of the producer thread that generated this database."""

    databases_checked: int
    """Databases inspected by the consumer before this counterexample."""

    elapsed_seconds: float
    """Wall-clock seconds from pipeline start to counterexample discovery."""

    q1_sql: str
    """The first SQL query string."""

    q2_sql: str
    """The second SQL query string."""

    q1_result: List[Tuple]
    """Sorted result rows returned by Q1 on the counterexample database."""

    q2_result: List[Tuple]
    """Sorted result rows returned by Q2 on the counterexample database."""

    schema_sql: str = ""
    """DDL of every user table in the counterexample database (may be empty if
    ``capture_counterexample_db=False``)."""

    table_data: Dict[str, List[Tuple]] = field(default_factory=dict)
    """Full contents of every user table (may be empty if
    ``capture_counterexample_db=False``)."""

    def format(self) -> str:
        """Return a human-readable summary string."""
        sep = "=" * 64
        lines = [
            sep,
            "COUNTEREXAMPLE FOUND",
            sep,
            f"  Producer       : {self.producer_id}",
            f"  DBs checked    : {self.databases_checked:,}",
            f"  Elapsed        : {self.elapsed_seconds:.3f}s",
            "",
            "  Q1:",
            *[f"    {ln}" for ln in self.q1_sql.strip().splitlines()],
            f"  Q1 result      : {self.q1_result}",
            "",
            "  Q2:",
            *[f"    {ln}" for ln in self.q2_sql.strip().splitlines()],
            f"  Q2 result      : {self.q2_result}",
        ]
        if self.schema_sql:
            lines += ["", "  Schema:"]
            lines += [f"    {ln}" for ln in self.schema_sql.splitlines()]
        if self.table_data:
            lines += ["", "  Table contents:"]
            for tbl, rows in self.table_data.items():
                lines.append(f"    {tbl}: {rows}")
        lines.append(sep)
        return "\n".join(lines)

    def __str__(self) -> str:  # pragma: no cover
        return self.format()


@dataclass(frozen=True)
class PipelineStats:
    """Runtime statistics collected by the pipeline."""

    databases_produced: int
    """Total populators pushed onto the queue by all producers."""

    databases_checked: int
    """Total databases evaluated by the consumer."""

    elapsed_seconds: float
    """Wall-clock time for the entire pipeline execution."""

    num_producers: int
    """Number of producer threads that were used."""

    stopped_by_timeout: bool
    """``True`` if the pipeline was cut short by the wall-clock limit."""

    def __str__(self) -> str:
        return (
            f"PipelineStats("
            f"produced={self.databases_produced:,}, "
            f"checked={self.databases_checked:,}, "
            f"elapsed={self.elapsed_seconds:.3f}s, "
            f"producers={self.num_producers}, "
            f"timeout={self.stopped_by_timeout})"
        )


@dataclass(frozen=True)
class DisproveOutcome:
    """
    Unified return value of :meth:`QueryDisprover.run`.

    ``result`` is ``None`` when no counterexample was found within the budget.
    """

    result: Optional[DisproveResult]
    """The counterexample, or ``None`` if the queries appear equivalent."""

    stats: PipelineStats
    """Runtime statistics for this pipeline run."""

    @property
    def found(self) -> bool:
        """``True`` when a counterexample was discovered."""
        return self.result is not None

    def __str__(self) -> str:
        if self.result is not None:
            return f"DisproveOutcome(FOUND)\n{self.result.format()}\n{self.stats}"
        return f"DisproveOutcome(NOT FOUND)\n{self.stats}"


class _PoisonPill:
    """Enqueued by the orchestrator to tell the consumer it can exit cleanly."""


_POISON = _PoisonPill()


class QueryDisprover:
    """
    Producer–consumer pipeline for disproving SQL query equivalence.

    Typical usage::

        from sql_disprover import QueryDisprover, DisproveConfig

        disprover = QueryDisprover(
            q1="SELECT * FROM t WHERE x > 0",
            q2="SELECT * FROM t WHERE x >= 1",
            db_generator=my_generator,
            config=DisproveConfig(num_producers=8, max_databases=200_000),
        )
        outcome = disprover.run()
        if outcome.found:
            print(outcome.result.format())

    A single :class:`QueryDisprover` instance is **not** reusable: call
    :meth:`run` exactly once.  Create a new instance for each search.
    """

    def __init__(self, q1: str, q2: str, dialect: str, queue_maxsize) -> None:
        self.q1 = q1
        self.q2 = q2
        self.dialect = dialect
        self._stop_event: threading.Event = threading.Event()
        self._db_queue: queue.Queue = queue.Queue(maxsize=queue_maxsize)
        self._result_holder: List[DisproveResult] = []
        self._error_bucket: List[Exception] = []
        # self._produced_count: _AtomicCounter = _AtomicCounter()
        # self._checked_count: _AtomicCounter = _AtomicCounter()
        self._stopped_by_timeout: bool = False
        self._ran: bool = False

    def run(self) -> DisproveOutcome:
        """
        Execute the search and block until it completes.

        Returns
        -------
        DisproveOutcome
            Always returned unless ``raise_on_found=True`` **and** a
            counterexample was found, in which case :class:`DisprovedError`
            is raised instead.

        Raises
        ------
        DisprovedError
            If ``config.raise_on_found`` is ``True`` and a counterexample
            is found.
        ProducerError
            If any producer thread raises an unhandled exception.
        ConsumerError
            If the consumer thread raises an unhandled exception.
        RuntimeError
            If :meth:`run` is called more than once on the same instance.
        """
        if self._ran:
            raise RuntimeError(
                "QueryDisprover.run() has already been called.  "
                "Create a new instance to run another search."
            )
        self._ran = True

    def _build_producer_threads(self, budget: int) -> List[threading.Thread]:
        return [
            threading.Thread(
                target=self._producer_worker,
                args=(i, budget),
                name=f"Producer-{i}",
                daemon=True,
            )
            for i in range(self._config.num_producers)
        ]

    def _build_consumer_thread(self, start_time: float) -> threading.Thread:
        return threading.Thread(
            target=self._consumer_worker,
            args=(start_time,),
            name="Consumer",
            daemon=True,
        )

    def _join_producers(
        self,
        producer_threads: List[threading.Thread],
        start_time: float,
    ) -> None:
        cfg = self._config
        deadline = (start_time + cfg.wall_time_limit) if cfg.wall_time_limit else None
        poll = 0.05  # seconds per tick

        for t in producer_threads:
            while t.is_alive():
                if self._stop_event.is_set():
                    break
                if deadline is not None and time.perf_counter() >= deadline:
                    logger.info("Wall-clock limit reached; stopping pipeline.")
                    self._stopped_by_timeout = True
                    self._stop_event.set()
                    break
                t.join(timeout=poll)

        # Unconditionally set stop_event so any still-running producers exit.
        self._stop_event.set()
