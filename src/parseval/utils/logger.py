"""
logger.py
Configurable logger for qrank with per-sub-logger file routing.

Usage::

    log = Logger(
        level     = logging.DEBUG,
        log_file  = "logs/main.log",
        log_files = {
            "dataset": "logs/dataset.jsonl",
            "db":      "logs/db.jsonl",
            "smt":     "logs/smt.log",
        },
        structured_logs = {"dataset", "db"},   # bare %(message)s — JSON-ready
        verbose   = {"dataset": True, "db": True, "smt": True},
    )

    log.main.info("started")
    log.db().debug('{"query": "SELECT 1"}')
    log.dataset().debug('{"question_id": 1, "db_id": "spider"}')
    log.smt().debug("solver called")
"""

import logging
from pathlib import Path
from typing import Dict, Optional, Set


_FULL_FORMAT = (
    "[%(asctime)s] %(levelname)s [%(name)s] [%(filename)s:%(lineno)d]: %(message)s"
)
_BARE_FORMAT = "%(message)s"


class Logger:
    """Configurable logger for qrank with per-sub-logger file routing.

    Loggers:
        - main
        - smt
        - dataset
        - eq
        - db
        - metrics

    Each sub-logger can be:
    - **enabled / disabled** via the ``verbose`` dict.
    - **routed to its own file** via the ``log_files`` dict.
    - **marked as structured** via ``structured_logs`` for bare JSON output.

    If a sub-logger has no dedicated file it shares the handler used by the
    main logger (``log_file``, or stdout if that is also unset).
    """

    BASE_NAME = "qrank"

    _SUB_LOGGERS = {
        "ranking": "Ranking decisions",
        "smt": "SMT solver expressions",
        "coverage": "coverage ",
        "dataset": "Dataset processing details",
        "eq": "SQL equivalence comparison details",
        "db": "Database operations",
        "metrics": "Evaluation metrics",
    }

    def __init__(
        self,
        *,
        level: int = logging.INFO,
        log_file: Optional[str] = None,
        log_files: Optional[Dict[str, str]] = None,
        structured_logs: Optional[Set[str]] = None,
        forbidden: Optional[Dict[str, bool]] = None,
    ):
        """
        Args:
            level:
                Logging level applied to every logger (``logging.DEBUG``,
                ``logging.INFO``, etc.).
            log_file:
                Default output destination for all loggers that do **not**
                have a dedicated entry in *log_files*. Writes to stdout when
                ``None``.
            log_files:
                Per-sub-logger file overrides. A sub-logger listed here
                writes **only** to its own file — not to the shared handler.
                Example::

                    {"db": "logs/db.jsonl", "smt": "logs/smt.log"}

            structured_logs:
                Sub-loggers that write bare ``%(message)s`` output, suitable
                for JSON lines. Must be a subset of ``log_files`` keys since
                structured output only makes sense in a dedicated file.
                Example::

                    {"db", "dataset"}

            forbidden:
                Which sub-loggers to disenable. Disabled sub-loggers still have
                a handler attached but their ``disabled`` flag suppresses all
                output.
                Example::
                    {"db": True, "ranking": True}
        """
        log_files = log_files or {}
        structured_logs = structured_logs or set()
        forbidden = forbidden or {}

        # Validate all keys upfront — typos silently do nothing without this.
        invalid_keys = (set(forbidden) | set(log_files) | structured_logs) - set(
            self._SUB_LOGGERS
        )
        if invalid_keys:
            raise ValueError(
                f"Unknown logger key(s): {invalid_keys}. "
                f"Valid keys: {set(self._SUB_LOGGERS)}"
            )

        # structured_logs must be a subset of log_files — bare format only
        # makes sense when the logger has its own dedicated file.
        structured_without_file = structured_logs - set(log_files)
        if structured_without_file:
            raise ValueError(
                f"structured_logs {structured_without_file} have no entry in "
                f"log_files. Structured output requires a dedicated file."
            )

        self.level = level
        self.log_file = log_file
        self.log_files = log_files
        self.structured_logs = structured_logs
        self.forbidden = forbidden

        # Shared handler — created lazily, reused by main + sub-loggers that
        # have no dedicated file.
        self._shared_handler: Optional[logging.Handler] = None

        # Dedicated per-logger handlers — cached so the same file is never
        # opened more than once.
        self._dedicated_handlers: Dict[str, logging.Handler] = {}

        self._loggers: Dict[str, logging.Logger] = {}

        self._configure_root()
        self._configure_sub_loggers()

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    def _make_handler(
        self, log_file: Optional[str] = None, *, bare_format: bool = False
    ) -> logging.Handler:
        """Build a formatted handler writing to *log_file* or stdout.

        Args:
            log_file: path to write to, or ``None`` for stdout.
            bare_format: if ``True``, use ``%(message)s`` only — suitable
                for structured (JSON) output. Otherwise use the full format
                with timestamp, level, and source location.
        """
        if log_file:
            Path(log_file).parent.mkdir(parents=True, exist_ok=True)
            handler: logging.Handler = logging.FileHandler(log_file)
        else:
            handler = logging.StreamHandler()

        fmt = _BARE_FORMAT if bare_format else _FULL_FORMAT
        handler.setFormatter(logging.Formatter(fmt))
        handler.setLevel(self.level)
        return handler

    def _get_shared_handler(self) -> logging.Handler:
        """Return the shared fallback handler, creating it on first call."""
        if self._shared_handler is None:
            self._shared_handler = self._make_handler(self.log_file, bare_format=False)
        return self._shared_handler

    def _get_handler_for(self, name: str) -> logging.Handler:
        """Return the appropriate handler for sub-logger *name*.

        - Dedicated file in ``log_files`` → a cached ``FileHandler``.
          Format is bare if *name* is in ``structured_logs``, full otherwise.
        - No dedicated file → the shared fallback handler.
        """
        if name in self.log_files:
            if name not in self._dedicated_handlers:
                self._dedicated_handlers[name] = self._make_handler(
                    self.log_files[name],
                    bare_format=name in self.structured_logs,
                )
            return self._dedicated_handlers[name]
        return self._get_shared_handler()

    def _configure_root(self) -> None:
        """Configure the top-level ``qrank`` logger."""
        self.main = logging.getLogger(self.BASE_NAME)
        self.main.setLevel(self.level)
        self.main.propagate = False
        self.main.handlers.clear()
        self.main.addHandler(self._get_shared_handler())

    def _configure_sub_loggers(self) -> None:
        """Configure every sub-logger defined in ``_SUB_LOGGERS``."""
        for name in self._SUB_LOGGERS:
            log = logging.getLogger(f"{self.BASE_NAME}.{name}")
            log.setLevel(self.level)
            log.propagate = False
            log.handlers.clear()
            log.addHandler(self._get_handler_for(name))
            log.disabled = name in self.forbidden
            self._loggers[name] = log

    # ------------------------------------------------------------------
    # Runtime controls
    # ------------------------------------------------------------------

    def _get_sub_logger(self, name: str) -> logging.Logger:
        """Validated internal accessor used by all public methods."""
        if name not in self._loggers:
            raise KeyError(f"Unknown logger '{name}'. Valid: {list(self._loggers)}")
        return self._loggers[name]

    def enable(self, name: str) -> None:
        """Enable a sub-logger at runtime."""
        self._get_sub_logger(name).disabled = False

    def disable(self, name: str) -> None:
        """Disable a sub-logger at runtime."""
        self._get_sub_logger(name).disabled = True

    def set_level(self, level: int) -> None:
        """Change the logging level for all loggers and their handlers."""
        self.level = level
        self.main.setLevel(level)
        for handler in self.main.handlers:
            handler.setLevel(level)
        for log in self._loggers.values():
            log.setLevel(level)
        for handler in self._dedicated_handlers.values():
            handler.setLevel(level)
        if self._shared_handler:
            self._shared_handler.setLevel(level)

    def get(self, name: str) -> logging.Logger:
        """Generic accessor — useful when the name is a variable."""
        return self._get_sub_logger(name)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def router(self) -> logging.Logger:
        return self._get_sub_logger("router")

    def smt(self) -> logging.Logger:
        return self._get_sub_logger("smt")

    def dataset(self) -> logging.Logger:
        return self._get_sub_logger("dataset")

    def eq(self) -> logging.Logger:
        return self._get_sub_logger("eq")

    def db(self) -> logging.Logger:
        return self._get_sub_logger("db")
