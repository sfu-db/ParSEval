import logging
from pathlib import Path
from typing import Optional, Dict


class Logger:
    """Logger class for ParSEval.

    Loggers:
        - main
        - coverage
        - symbolic
        - smt
        - db

    Users can enable each logger via verbose flags.
    Logs go to console by default, or to a file if path is provided.
    """

    BASE_NAME = "parseval"

    _SUB_LOGGERS = {
        "coverage": "Coverage constraints",
        "symbolic": "Symbolic expressions",
        "smt": "SMT solver expressions",
        "db": "Database operations",
    }

    def __init__(
        self,
        *,
        level: int = logging.INFO,
        log_file: Optional[str] = None,
        verbose: Optional[Dict[str, bool]] = None,
    ):
        """
        Args:
            level: logging level (INFO, DEBUG, etc.)
            log_file: optional file path to write logs
            verbose: dict to enable sub-loggers, e.g.
                     {"coverage": True, "symbolic": False}
        """
        self.level = level
        self.log_file = log_file
        self.verbose = verbose or {}

        self._configure_root()
        self._configure_sub_loggers()

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    def _configure_root(self):
        """Configure main logger."""
        self.main = logging.getLogger(self.BASE_NAME)
        self.main.setLevel(self.level)
        self.main.propagate = False

        if not self.main.handlers:
            handler = self._create_handler()
            self.main.addHandler(handler)

    def _configure_sub_loggers(self):
        """Configure sub-loggers based on verbosity."""
        self._loggers = {}

        for name in self._SUB_LOGGERS:
            logger = logging.getLogger(f"{self.BASE_NAME}.{name}")
            logger.setLevel(self.level)
            enabled = self.verbose.get(name, False)
            logger.disabled = not enabled
            logger.propagate = False
            if enabled and not logger.handlers:
                logger.addHandler(self._create_handler())
            self._loggers[name] = logger

    def _create_handler(self) -> logging.Handler:
        """Create console or file handler."""
        if self.log_file:
            Path(self.log_file).parent.mkdir(parents=True, exist_ok=True)
            handler = logging.FileHandler(self.log_file)
        else:
            handler = logging.StreamHandler()
        # [%(asctime)s] %(levelname)s [%(filename)s:%(lineno)d]: %(message)s
        
            # "[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s"
        formatter = logging.Formatter(
            "[%(asctime)s] %(levelname)s [%(filename)s:%(lineno)d]: %(message)s"
        )
        handler.setFormatter(formatter)
        handler.setLevel(self.level)
        return handler

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def coverage(self) -> logging.Logger:
        return self._loggers["coverage"]

    def symbolic(self) -> logging.Logger:
        return self._loggers["symbolic"]

    def smt(self) -> logging.Logger:
        return self._loggers["smt"]

    def db(self) -> logging.Logger:
        return self._loggers["db"]
